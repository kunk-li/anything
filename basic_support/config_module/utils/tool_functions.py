from __future__ import annotations

import base64
import hashlib
import os
import re
from copy import deepcopy
from typing import Any, Dict, Tuple

from cryptography.fernet import Fernet


ENC_PREFIX = "ENC::"
ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    深度合并字典：override 覆盖 base（递归）
    """
    result = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


def split_key(key: str) -> Tuple[str, ...]:
    return tuple([p for p in key.split(".") if p])


def get_nested(config: Dict[str, Any], key: str) -> Any:
    cur: Any = config
    for part in split_key(key):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(key)
        cur = cur[part]
    return cur


def set_nested(config: Dict[str, Any], key: str, value: Any) -> None:
    parts = split_key(key)
    if not parts:
        raise ValueError("key不能为空")
    cur = config
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def has_nested(config: Dict[str, Any], key: str) -> bool:
    try:
        get_nested(config, key)
        return True
    except KeyError:
        return False


def collect_by_prefix(config: Dict[str, Any], prefix_key: str) -> Dict[str, Any]:
    """
    prefix_key 形如 "llm." 或 "llm"：返回该层字典（浅复制）
    """
    prefix_key = prefix_key[:-1] if prefix_key.endswith(".") else prefix_key
    if not prefix_key:
        return deepcopy(config)
    node = get_nested(config, prefix_key)
    if isinstance(node, dict):
        return deepcopy(node)
    # 不是 dict 也允许返回值
    return {split_key(prefix_key)[-1]: deepcopy(node)}


def derive_fernet_key(secret_key: str) -> bytes:
    """
    将任意字符串 secret_key 转换为 Fernet 可用的 32-byte urlsafe base64 key
    """
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_value(value: Any, secret_key: str) -> str:
    f = Fernet(derive_fernet_key(secret_key))
    token = f.encrypt(str(value).encode("utf-8")).decode("utf-8")
    return f"{ENC_PREFIX}{token}"


def decrypt_value(value: str, secret_key: str) -> str:
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    token = value[len(ENC_PREFIX):]
    f = Fernet(derive_fernet_key(secret_key))
    return f.decrypt(token.encode("utf-8")).decode("utf-8")


def substitute_env(value: Any) -> Any:
    """
    若 value 为形如 "${ENV_VAR}" 的字符串，则用环境变量替换（不存在则原样返回）
    """
    if isinstance(value, str):
        m = ENV_PATTERN.match(value.strip())
        if m:
            env_name = m.group(1)
            env_val = os.getenv(env_name)
            return env_val if env_val is not None else value
    return value


def walk_and_apply(config: Any, fn) -> Any:
    """
    递归遍历 config（dict/list/scalar），对 scalar 应用 fn
    """
    if isinstance(config, dict):
        return {k: walk_and_apply(v, fn) for k, v in config.items()}
    if isinstance(config, list):
        return [walk_and_apply(v, fn) for v in config]
    return fn(config)


def coerce_type(value: Any, type_name: str) -> Any:
    """
    校验/转换：用于解密后把字符串转回目标类型（尽量保守）
    """
    if type_name == "str":
        return str(value)
    if type_name == "int":
        return int(value)
    if type_name == "float":
        return float(value)
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            s = value.strip().lower()
            if s in ("true", "1", "yes", "y", "on"):
                return True
            if s in ("false", "0", "no", "n", "off"):
                return False
        raise ValueError(f"无法转换为bool: {value}")
    raise ValueError(f"不支持的type: {type_name}")
