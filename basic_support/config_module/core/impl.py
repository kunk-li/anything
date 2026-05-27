from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
import yaml

from .base import BaseConfigManager
from ..utils.tool_functions import (
    collect_by_prefix,
    coerce_type,
    decrypt_value,
    deep_merge,
    encrypt_value,
    get_nested,
    has_nested,
    set_nested,
    substitute_env,
    walk_and_apply,
)

_DEFAULT_CONFIG_REL_PATH = os.path.join("config", "config.yaml")


class ConfigManager(BaseConfigManager):
    """
    配置管理具体实现类（YAML）。
    - 支持：加载/读取/更新（可持久化）/校验/备份恢复/敏感加密/远程配置合并
    - 环境变量注入：支持 "${ENV_VAR}" 形式替换
    - 热加载：非后台线程模式；在每次 get/update 时按间隔检测文件mtime
    """

    def __init__(self):
        self.config: Dict[str, Any] = {}
        self.config_path: Optional[str] = None

        self.hot_reload: bool = False
        self.hot_reload_interval: int = 5
        self._last_modify_time: float = 0.0
        self._last_hot_check_time: float = 0.0

        self.backup_dir: str = "config/backup"
        self.secret_key: Optional[str] = None

        self._update_log_path: Optional[str] = None

    # ---------------------------
    # Public APIs
    # ---------------------------

    def load_config(self, config_path: str | None = None) -> None:
        config_path = config_path or self._default_config_path()
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        self.config_path = os.path.abspath(config_path)
        self._update_log_path = os.path.join(os.path.dirname(self.config_path), "update.log")

        with open(self.config_path, "r", encoding="utf-8") as f:
            try:
                loaded = yaml.safe_load(f) or {}
            except Exception as e:
                raise ValueError(f"配置格式解析失败（yaml）: {e}") from e

        if not isinstance(loaded, dict):
            raise ValueError("配置文件顶层必须为字典结构")

        # 1) 环境变量注入（先替换）
        loaded = walk_and_apply(loaded, substitute_env)

        self.config = loaded

        # 2) 读取全局热加载开关
        self.hot_reload = bool(self.get_config("global.hot_reload", False))
        self.hot_reload_interval = int(self.get_config("global.hot_reload_interval", 5))

        # 3) 读取敏感配置密钥（可来自环境变量注入后的值）
        # 约定在 security.sensitive_config_secret
        sk = self.get_config("security.sensitive_config_secret", None)
        self.secret_key = sk if isinstance(sk, str) and sk and not sk.startswith("${") else self.secret_key

        # 4) 解密（若secret_key存在）
        if self.secret_key:
            self._decrypt_all_sensitive_in_place(self.secret_key)

        # 5) 校验（默认使用配置文件内 validate_rules）
        self.validate_config()

        # 6) 初始化mtime
        try:
            self._last_modify_time = os.path.getmtime(self.config_path)
        except OSError:
            self._last_modify_time = 0.0
        self._last_hot_check_time = time.time()

    def get_config(self, key: str, default: Any = None) -> Any:
        self._check_hot_reload()

        if key.endswith(".") or (key and key.endswith(".")):
            try:
                return collect_by_prefix(self.config, key)
            except KeyError:
                return default

        # 支持按前缀批量获取：如果 key 本身以 "." 结尾或明确要求前缀
        if key.endswith("."):
            try:
                return collect_by_prefix(self.config, key)
            except KeyError:
                return default

        try:
            return get_nested(self.config, key)
        except KeyError:
            # 额外支持：若传入 "vector_db." 以外的前缀读取（例如用户传入 "vector_db" 想要整个块）
            if has_nested(self.config, key) is False:
                try:
                    node = get_nested(self.config, key)
                    return node
                except Exception:
                    return default
            return default

    def get_effective_value(
        self,
        key: str,
        env_var: Optional[str] = None,
        default: Any = None,
        value_type: Optional[type] = None,
    ) -> Any:
        """按显式优先级解析配置项,返回最终生效值。

        优先级 (高 -> 低):
            1. 环境变量 (若 env_var 非空且环境中存在)
            2. 全局/局部 yaml 配置 (走 get_config)
            3. default 兜底

        参数:
            key: 配置 key (如 "rag.top_k_retrieve")
            env_var: 允许覆盖的环境变量名 (如 "ANYTHING_RAG_TOP_K_RETRIEVE")
            default: 兜底值
            value_type: 可选 — 把环境变量字符串值转为指定类型 (int/float/bool).
                yaml 与 default 已经是合适类型, 主要给环境变量用.
                bool 规则: str.lower() in ("1","true","yes","on") -> True

        示例:
            top_k = config.get_effective_value(
                "rag.top_k_retrieve",
                env_var="ANYTHING_RAG_TOP_K_RETRIEVE",
                default=50,
                value_type=int,
            )

        参考 docs/configuration-priority.md 第 2 节"5 个配置源 + 优先级"。
        """
        raw: Any = None
        from_env = False

        if env_var:
            env_val = os.environ.get(env_var)
            if env_val is not None and env_val != "":
                raw = env_val
                from_env = True

        if raw is None:
            raw = self.get_config(key, None)

        if raw is None:
            raw = default

        # 类型转换:主要给来自环境变量的 str 用; yaml/default 已经是合适类型,
        # 但二次确认也无害(int(50) == 50)
        if value_type is not None and raw is not None:
            try:
                if value_type is bool:
                    if isinstance(raw, bool):
                        return raw
                    raw_str = str(raw).strip().lower()
                    return raw_str in ("1", "true", "yes", "on")
                return value_type(raw)
            except (ValueError, TypeError):
                # 类型转换失败 -> 回退到 default 而非崩 (生产更安全)
                if from_env:
                    self.logger.warning(
                        f"[config] env_var={env_var} 值 '{raw}' 无法转为 {value_type.__name__}, 回退到 default"
                    ) if hasattr(self, "logger") else None
                return default

        return raw

    def update_config(self, key: str, value: Any, persist: bool = False) -> bool:
        self._check_hot_reload()

        try:
            old_value = self.get_config(key, None)
            set_nested(self.config, key, value)
            self._record_update_log(key, old_value, value)

            if persist:
                if not self.config_path:
                    raise ValueError("未加载配置，无法持久化写入")
                self._write_yaml(self.config_path, self.config)
                self._last_modify_time = os.path.getmtime(self.config_path)

            return True
        except Exception:
            return False

    def validate_config(self, validate_rules: Dict | None = None) -> bool:
        rules = validate_rules or self.config.get("validate_rules") or {}
        if rules is None:
            rules = {}
        if not isinstance(rules, dict):
            raise ValueError("validate_rules 必须为字典")

        for cfg_key, rule in rules.items():
            if not isinstance(rule, dict):
                raise ValueError(f"校验规则格式错误: {cfg_key} -> {rule}")

            required = bool(rule.get("required", False))
            type_name = rule.get("type")
            range_rule = rule.get("range")

            exists = has_nested(self.config, cfg_key)
            if required and not exists:
                raise ValueError(f"缺少必填配置项: {cfg_key}")
            if not exists:
                continue

            val = self.get_config(cfg_key)

            # 类型校验：若配置项来自解密，可能是字符串；此处只校验可转换性，必要时转换并写回内存
            if type_name:
                try:
                    coerced = coerce_type(val, str(type_name))
                except Exception as e:
                    raise ValueError(f"配置项类型校验失败: {cfg_key} 期望 {type_name}，实际 {type(val).__name__}") from e
                # 写回内存，保证后续使用类型正确
                set_nested(self.config, cfg_key, coerced)
                val = coerced

            # 范围校验
            if range_rule is not None:
                if not isinstance(range_rule, list) or len(range_rule) != 2 and len(range_rule) == 0:
                    # 允许字符串枚举列表：["development","test","production"]
                    pass
                if isinstance(val, (int, float)) and isinstance(range_rule, list) and len(range_rule) == 2:
                    lo, hi = range_rule
                    if val < lo or val > hi:
                        raise ValueError(f"配置项范围校验失败: {cfg_key}={val} 不在 [{lo}, {hi}]")
                elif isinstance(val, str) and isinstance(range_rule, list) and len(range_rule) >= 1:
                    # 枚举校验
                    if val not in range_rule:
                        raise ValueError(f"配置项枚举校验失败: {cfg_key}={val} 不在 {range_rule}")
                elif isinstance(val, bool) and isinstance(range_rule, list) and len(range_rule) >= 1:
                    if val not in range_rule:
                        raise ValueError(f"配置项枚举校验失败: {cfg_key}={val} 不在 {range_rule}")

        return True

    def backup_config(self, backup_path: str | None = None) -> str:
        if not self.config_path:
            raise ValueError("未加载配置，无法备份")

        # backup_path 既可传目录，也可传完整文件路径
        if backup_path is None:
            backup_dir = os.path.join(os.path.dirname(self.config_path), "backup")
            os.makedirs(backup_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = os.path.join(backup_dir, f"config_{ts}.yaml")
        else:
            if os.path.isdir(backup_path) or backup_path.endswith(os.sep):
                os.makedirs(backup_path, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                dst = os.path.join(backup_path, f"config_{ts}.yaml")
            else:
                os.makedirs(os.path.dirname(os.path.abspath(backup_path)), exist_ok=True)
                dst = os.path.abspath(backup_path)

        shutil.copy2(self.config_path, dst)
        return dst

    def restore_config(self, backup_path: str) -> bool:
        if not self.config_path:
            raise ValueError("未加载配置，无法恢复")
        if not os.path.exists(backup_path):
            raise FileNotFoundError(f"备份文件不存在: {backup_path}")

        # 恢复前先备份当前
        self.backup_config()

        shutil.copy2(backup_path, self.config_path)
        self.load_config(self.config_path)
        return True

    def encrypt_sensitive_config(self, keys: List[str], secret_key: str) -> None:
        if not self.config_path:
            raise ValueError("未加载配置，无法加密并持久化")
        if not secret_key:
            raise ValueError("secret_key不能为空")

        self.secret_key = secret_key

        for k in keys:
            if not has_nested(self.config, k):
                continue
            raw = self.get_config(k)
            enc = encrypt_value(raw, secret_key)
            set_nested(self.config, k, enc)
            self._record_update_log(k, raw, "***ENCRYPTED***")

        self._write_yaml(self.config_path, self.config)
        self._last_modify_time = os.path.getmtime(self.config_path)

        # 重新加载以触发自动解密并回写类型
        self.load_config(self.config_path)

    def load_remote_config(self, remote_url: str, config_type: str = "yaml") -> None:
        """
        从远程加载配置并合并。
        合并策略：remote 覆盖 local（remote 优先级更高）。
        """
        if not remote_url:
            raise ValueError("remote_url不能为空")
        if not self.config:
            # 允许先加载远程再加载本地的场景，这里不强制本地已加载
            self.config = {}

        resp = requests.get(remote_url, timeout=10)
        resp.raise_for_status()

        remote_cfg: Dict[str, Any]
        if config_type.lower() in ("yaml", "yml"):
            remote_cfg = yaml.safe_load(resp.text) or {}
        elif config_type.lower() == "json":
            remote_cfg = json.loads(resp.text or "{}")
        else:
            raise ValueError(f"不支持的远程配置格式: {config_type}")

        if not isinstance(remote_cfg, dict):
            raise ValueError("远程配置顶层必须为字典")

        remote_cfg = walk_and_apply(remote_cfg, substitute_env)

        # 合并：remote 覆盖 local
        self.config = deep_merge(self.config, remote_cfg)

        # 远程合并后重新校验
        self.validate_config()

    # ---------------------------
    # Internal helpers
    # ---------------------------

    def _default_config_path(self) -> str:
        # 默认以当前文件所在模块根目录为基准
        module_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(module_root, _DEFAULT_CONFIG_REL_PATH)

    def _write_yaml(self, path: str, data: Dict[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    def _check_hot_reload(self) -> None:
        if not self.hot_reload or not self.config_path:
            return

        now = time.time()
        if now - self._last_hot_check_time < self.hot_reload_interval:
            return
        self._last_hot_check_time = now

        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return

        if mtime > self._last_modify_time:
            # 文件已更新，重新加载
            self.load_config(self.config_path)

    def _record_update_log(self, key: str, old_value: Any, new_value: Any) -> None:
        if not self._update_log_path:
            return
        ts = datetime.now().isoformat(timespec="seconds")
        line = f"{ts}\t{key}\t{old_value!r}\t=>\t{new_value!r}\n"
        try:
            with open(self._update_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            # 记录失败不影响主流程
            pass

    def _decrypt_all_sensitive_in_place(self, secret_key: str) -> None:
        """
        解密所有以 ENC:: 开头的字符串值。
        注意：解密得到的是字符串；后续 validate_config 会按规则进行类型回写。
        """
        def _try_decrypt(v: Any) -> Any:
            if isinstance(v, str):
                try:
                    return decrypt_value(v, secret_key)
                except Exception:
                    return v
            return v

        self.config = walk_and_apply(self.config, _try_decrypt)
