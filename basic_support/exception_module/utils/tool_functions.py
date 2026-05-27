from __future__ import annotations

import traceback
from typing import Optional, Dict

# 注意：
# - 映射表需与系统全局错误码表严格对应（文档第10章）
# - 若错误码表更新，请同步更新此映射
EXCEPTION_CODE_MAP: Dict[str, str] = {
    # 通用
    "UNKNOWN_ERROR": "未知异常，请查看日志排查具体原因",
    "SYSTEM_ERROR": "系统内部错误，无法正常处理请求",

    # 配置模块
    "CONFIG_NOT_FOUND": "配置文件不存在，请检查配置路径是否正确",
    "CONFIG_KEY_MISSING": "配置缺失：{key}，请补充配置信息",
    "CONFIG_INVALID_FORMAT": "配置文件格式无效，请检查配置语法",

    # 向量数据库模块
    "VECTOR_DB_CONNECT_FAILED": "向量数据库连接失败，请检查连接配置",
    "VECTOR_QUERY_FAILED": "向量检索失败，请检查检索参数或数据库状态",
    "VECTOR_INSERT_FAILED": "向量插入失败，请检查插入数据格式或数据库权限",

    # RAG模块
    "RAG_DOC_LOAD_FAILED": "RAG文档加载失败，请检查文档路径或格式",
    "RAG_EMBED_FAILED": "RAG文档嵌入失败，请检查嵌入模型或数据",
    "RAG_RUN_FAILED": "RAG执行失败，请检查检索策略或模型配置",

    # Agent模块
    "AGENT_TASK_PARSE_FAILED": "Agent任务解析失败，请检查任务描述格式",
    "TOOL_NOT_FOUND": "工具不存在：{tool_name}，请检查工具配置",
    "TOOL_CALL_FAILED": "工具调用失败：{tool_name}，请检查工具状态或参数",

    # 异常处理模块自身
    "EXCEPTION_HANDLER_ERROR": "异常处理器执行失败，请检查日志模块或异常配置",

    # 多租户隔离 (Task #33 PR4b, 见 docs/multi-tenancy-design.md §8)
    "TENANT_REQUIRED": "请求未携带合法 tenant_id 或已被停用",
    "TENANT_NOT_FOUND": "tenant_id 格式合法但不在配置中",
    "QUOTA_DOC_EXCEEDED": "文档数超过租户配额 {max_documents}",
    "QUOTA_STORAGE_EXCEEDED": "存储空间超过租户配额 {max_vector_store_mb} MB",
}


def get_exception_message(exception: Exception) -> str:
    """获取异常的详细信息（包含链式异常/嵌套异常）。

    规则：
    - 优先使用 str(exception)
    - 若存在 __cause__/__context__，拼接链路信息
    - 若 str(exception) 为空，退化为异常类名
    """
    parts = []
    visited = set()
    cur: Optional[BaseException] = exception  # type: ignore

    while cur is not None and id(cur) not in visited:
        visited.add(id(cur))
        msg = str(cur).strip()
        if not msg:
            msg = cur.__class__.__name__
        parts.append(msg)

        # PEP 409/415：__cause__ 优先于 __context__
        next_exc = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        cur = next_exc

    # 用 " <- " 表示异常链，便于阅读
    return " <- ".join(parts)


def exception_code_to_message(code: str, **kwargs) -> str:
    """根据错误码返回默认异常信息。

    Args:
        code: 错误码（如 CONFIG_KEY_MISSING）
        **kwargs: 用于格式化默认消息中的占位符（如 {key} / {tool_name}）

    Returns:
        默认异常信息；若 code 未配置，返回 UNKNOWN_ERROR 的默认信息。
    """
    template = EXCEPTION_CODE_MAP.get(code) or EXCEPTION_CODE_MAP.get("UNKNOWN_ERROR", "未知异常")
    try:
        return template.format(**kwargs) if kwargs else template
    except Exception:
        # 防御：占位符不匹配时，返回模板原文，避免异常处理再次抛错
        return template


def format_traceback(exception: Exception) -> str:
    """将异常堆栈格式化为字符串（用于日志记录）。"""
    return "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
