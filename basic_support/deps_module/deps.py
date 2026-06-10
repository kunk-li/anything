# -*- coding: utf-8 -*-
"""
BasicDeps: 基础支撑层依赖容器

包含 ConfigManager / SystemLogger / CommonUtils / ExceptionHandler 四个基础组件
的单一实例引用。bootstrap 在装配阶段创建一次，向各上层模块注入；
各模块的 __init__ 优先使用注入的 deps，未注入时回退为自行构造（向后兼容）。

为什么不用 frozen=True dataclass?
    部分组件构造时会做"延迟加载配置 / 初始化日志句柄"等副作用，外部代码可能在
    构造后再次调用 load。冻结实例会阻断这种合法使用，因此采用普通 dataclass。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


class StartupError(RuntimeError):
    """系统启动阶段的关键依赖初始化失败异常。

    与运行时业务异常区分:启动期错误意味着系统不应进入服务态,
    应该在最早的时机暴露,而不是默默回退到占位实现让真实请求才挂。

    使用约定:
    - 配置缺失 / 模块未安装 / 关键组件初始化抛错 → 包装为 StartupError 抛出
    - 在 ANYTHING_DEV_MODE=1 环境下,允许部分组件回退到占位实现以便本地调试
    """

    def __init__(self, component: str, reason: str, hint: Optional[str] = None):
        self.component = component
        self.reason = reason
        self.hint = hint
        msg = f"[startup] {component} 初始化失败: {reason}"
        if hint:
            msg += f" | hint: {hint}"
        super().__init__(msg)


def is_dev_mode() -> bool:
    """是否启用开发模式(允许占位实现 / 静默回退)。

    优先级:
        1. 环境变量 ANYTHING_DEV_MODE in ("1","true","True","yes")
        2. 默认 False(生产/严格模式)
    """
    val = os.environ.get("ANYTHING_DEV_MODE", "")
    return val.lower() in ("1", "true", "yes", "on")


def handle_exception_to_envelope(
    exception_handler: Any,
    exception: BaseException,
    trace_id: Optional[str],
    fallback_code: str,
    fallback_message: str,
    stage: str,
    context: Optional[dict] = None,
    retryable_codes: Optional[set] = None,
) -> dict:
    """把异常统一包装为响应信封 dict (文档第 10 章七字段格式)。

    抽出原 SimpleRAG / SimpleAgent / SimpleOrchestrator / ConsoleApp 中
    重复 ~30 行的 _handle_exception 方法。

    参数:
        exception_handler: ExceptionHandler 实例 (来自 deps.exception_handler)
        exception: 触发的异常对象
        trace_id: 链路追踪 ID,统一原样透传到信封
        fallback_code: 当 exception_handler 不能识别异常码时使用的兜底码
            (如 "RAG_RUN_FAILED" / "AGENT_RUN_FAILED" / "ORCHESTRATOR_RUN_FAILED")
        fallback_message: 兜底 message
        stage: 调用方所在阶段(写入 details.stage),如 "rag" / "agent" / "orchestrator"
        context: 额外的上下文字段,会合并进 details
        retryable_codes: 视为 retryable 的错误码集合 (调用方可定制)

    返回:
        {code, message, data:None, trace_id, retryable, details}
    """
    context = context or {}
    retryable_codes = retryable_codes or set()

    try:
        # 兼容 ExceptionHandler 两种方法名 (handle / handle_exception)
        # 这里保留 hasattr 是因为 exception_module 的 ABC 没钉死接口,
        # 后续可在 Task #14 之外的清理任务中统一为单一方法名。
        if hasattr(exception_handler, "handle"):
            error_info = exception_handler.handle(exception, trace_id=trace_id)
        elif hasattr(exception_handler, "handle_exception"):
            error_info = exception_handler.handle_exception(exception)
        else:
            error_info = {}

        if not isinstance(error_info, dict):
            error_info = {}

        code = error_info.get("code", fallback_code)
        message = error_info.get("message", fallback_message)
        retryable = error_info.get("retryable", code in retryable_codes)
        details = error_info.get("details") or {"stage": stage, **context}

        return {
            "code": code,
            "message": message,
            "data": None,
            "trace_id": trace_id,
            "retryable": retryable,
            "details": details,
        }
    except Exception:
        # exception_handler 本身崩了 — 走最简兜底,确保系统永远有响应信封返回
        return {
            "code": fallback_code,
            "message": fallback_message,
            "data": None,
            "trace_id": trace_id,
            "retryable": False,
            "details": {"stage": stage, **context},
        }


@dataclass
class BasicDeps:
    """基础支撑层依赖容器。

    Fields (核心 4):
        config: ConfigManager 实例（已 load_config / load）
        logger: SystemLogger 实例
        utils: CommonUtils 实例
        exception_handler: ExceptionHandler 实例

    Fields (Task PP #76 — 7 cross-cutting registries via DI):
        hook_registry:    HookRegistry           (Task Z #60)
        skill_registry:   SkillRegistry          (Task AA #61)
        quota_guard:      QuotaGuard             (Task BB #62)
        audit_logger:     AuditLogger            (Task CC #63)
        project_memory:   ProjectMemory          (Task U #55)
        usage_tracker:    UsageTracker           (Task Y #59)
        health_tracker:   ModelHealthTracker     (Task HH #68; 见 build 内注: 不在此获取, 默认 None)

    用法 — 推荐 DI:
        agent = SimpleAgent(deps=deps); reg = deps.hook_registry
    用法 — back-compat 直接拿单例 (仍可用):
        from hooks_module import get_hook_registry; reg = get_hook_registry()
    两条路径访问的是同一个进程单例 (build_basic_deps 内部走 get_X 拿引用).
    """

    config: Any
    logger: Any
    utils: Any
    exception_handler: Any
    # PP (#76): 7 cross-cutting registries 通过 deps 注入, 替代各模块独立 import singleton.
    # default=None 让单元测试可以只构造 4 核心字段的 BasicDeps, 不强制注入这 7 个.
    hook_registry: Any = None
    skill_registry: Any = None
    quota_guard: Any = None
    audit_logger: Any = None
    project_memory: Any = None
    usage_tracker: Any = None
    health_tracker: Any = None


def _load_dotenv_if_exists() -> None:
    """启动期可选加载项目根目录的 .env 文件(简单实现,不引入 python-dotenv 依赖)。

    解析规则:
        - 跳过空行 / # 开头注释
        - "KEY=value" 格式 (= 两侧空格会 strip), value 两端的 " 或 ' 引号会被剥除
        - 已设置的环境变量不会被 .env 覆盖(env > .env)

    定位策略 (按顺序探, 找到第一个就加载停止):
        1. ANYTHING_DOTENV_PATH 环境变量显式指定
        2. cwd / .env
        3. cwd 向上找 (最多 5 层), 适配从 run/ 启动但 .env 在上一层的情况
        4. 项目根启发 — 找到含 'basic_support' 子目录的最近祖先, 在那里找 .env
        - 找不到也不报错(可选机制)
    """
    from pathlib import Path

    def _try_load(p: Path) -> bool:
        if not p.exists() or not p.is_file():
            return False
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass
        return True

    # 1. 显式指定
    explicit = os.environ.get("ANYTHING_DOTENV_PATH", "").strip()
    if explicit and _try_load(Path(explicit)):
        return

    # 2. cwd
    if _try_load(Path.cwd() / ".env"):
        return

    # 3. cwd 向上找 5 层
    cur = Path.cwd().resolve()
    for _ in range(5):
        parent = cur.parent
        if parent == cur:
            break
        if _try_load(parent / ".env"):
            return
        cur = parent

    # 4. 项目根启发: 沿 __file__ 上溯, 找含 basic_support 子目录的祖先
    try:
        here = Path(__file__).resolve()
        for ancestor in [here] + list(here.parents):
            if (ancestor / "basic_support").is_dir() and (ancestor / ".env").exists():
                _try_load(ancestor / ".env")
                return
    except Exception:
        pass


def build_basic_deps() -> BasicDeps:
    """工厂方法：按默认方式构造一套 BasicDeps。

    本方法是 bootstrap.build_basic_support 的功能等价物，但返回结构化容器
    便于直接注入下游模块；bootstrap 应优先调用本方法。

    新增 (Task #30):
        - 自动加载项目根的 .env 文件 (若存在)
        - 加载 config 后扫描未填 secrets, dev 模式 WARN, 生产抛 StartupError

    注意：import 延迟到函数内，避免模块导入期触发 ConfigManager 加载，
    单测时可只 import BasicDeps 而不触发实例化。
    """
    # 0. 优先加载 .env 让后续 ConfigManager 替换占位符时能拿到值
    _load_dotenv_if_exists()

    from common_utils_module.core.impl import CommonUtils
    from config_module.core.impl import ConfigManager
    from log_module.core.impl import SystemLogger
    from exception_module.core.impl import ExceptionHandler

    config = ConfigManager()
    # ConfigManager 在历史上同时存在 load_config / load 两种方法名,兼容处理
    if hasattr(config, "load_config"):
        config.load_config()
    elif hasattr(config, "load"):
        config.load()

    # 检测未填 secrets (yaml 中 ${XXX} 但环境没设)
    if hasattr(config, "check_required_secrets"):
        unfilled = config.check_required_secrets()
        if unfilled:
            dev = is_dev_mode()
            msg = (
                f"以下 secrets 未在环境变量中提供(yaml 占位符 ${{XXX}} 未被替换): "
                f"{', '.join(unfilled)}"
            )
            if dev:
                # dev 模式: WARN, 让真实功能(LLM/向量库 api_key) 走 fallback
                print(f"[bootstrap][DEV_MODE] {msg}")
            else:
                # 生产模式: fail-fast, 避免 401/部分功能不可用
                raise StartupError(
                    component="secrets",
                    reason=msg,
                    hint="见 docs/secrets-management.md 配置 .env 或 GitHub Secrets",
                )

    # Task PP (#76): 7 cross-cutting registries 通过 get_X() 拿到进程单例引用,
    # 塞进 deps 让上层走 DI. 各 import 用 try/except — 子模块缺失也不阻塞
    # 启动 (deps.<field> 设 None, 上层应做 None 检查 fallback 到 get_X()).
    # 失败先收集, SystemLogger 构造完再统一 WARN (此处 logger 尚不存在), 吞而留痕。
    hook_registry = None
    skill_registry = None
    quota_guard = None
    audit_logger = None
    project_memory = None
    usage_tracker = None
    health_tracker = None
    _inject_failures: list = []

    try:
        from hooks_module import get_hook_registry
        hook_registry = get_hook_registry()
    except Exception as e:
        _inject_failures.append(("hook_registry", e))
    try:
        from skills_module import get_skill_registry
        skill_registry = get_skill_registry()
    except Exception as e:
        _inject_failures.append(("skill_registry", e))
    try:
        from quota_module import get_quota_guard
        quota_guard = get_quota_guard()
    except Exception as e:
        _inject_failures.append(("quota_guard", e))
    try:
        from audit_module import get_audit_logger
        audit_logger = get_audit_logger()
    except Exception as e:
        _inject_failures.append(("audit_logger", e))
    try:
        from project_memory_module import get_project_memory
        project_memory = get_project_memory()
    except Exception as e:
        _inject_failures.append(("project_memory", e))
    try:
        from observability_module import get_usage_tracker
        usage_tracker = get_usage_tracker()
    except Exception as e:
        _inject_failures.append(("usage_tracker", e))
    # health_tracker 属 data_layer (llm_adapter_module)；basic_support 不反向 import
    # 上层模块以保持分层纯净 (AUDIT-2b)。真实消费方 (llm_adapter core/impl 内部、
    # admin 路由) 都直接 get_health_tracker() 拿同一进程单例，不经 deps；过去这里的
    # 反向 import 无任何消费方读取，纯冗余。如需经 deps 共享，由 run 层 (可合法依赖
    # data_layer) 装配后注入 deps.health_tracker。

    logger = SystemLogger()
    for _name, _err in _inject_failures:
        logger.warning(
            f"[deps] 横切组件 {_name} 注入失败, deps.{_name}=None, "
            f"上层将 fallback get_{_name}(): {_err!r}"
        )

    return BasicDeps(
        config=config,
        logger=logger,
        utils=CommonUtils(),
        exception_handler=ExceptionHandler(),
        hook_registry=hook_registry,
        skill_registry=skill_registry,
        quota_guard=quota_guard,
        audit_logger=audit_logger,
        project_memory=project_memory,
        usage_tracker=usage_tracker,
        health_tracker=health_tracker,
    )
