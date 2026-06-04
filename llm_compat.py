# -*- coding: utf-8 -*-
"""
LLM 兼容适配层

依据 BaseLLMService.generate(prompt, trace_id) -> str 抽象契约，
上层（RAG/Agent）只通过 generate 一种入口拿文本。

历史上曾尝试 generate/call/invoke/run/chat/create 等 6 种调法做兜底，
现已收敛为：
- 主路径：调用 llm_client.generate(prompt, trace_id=...)
- 兜底：llm_client 为 None / generate 不存在 / 抛异常 → 走 DummyLLMClient

注意：本文件不应再做"猜方法名"的探测式适配；新增 LLM 实现请遵守
BaseLLMService 抽象签名。
"""

from __future__ import annotations

from typing import Any, Optional


_STUB_PREFIX_NO_LLM = "⚠️ **[未配置可用的 LLM]**"
_STUB_PREFIX_CALL_FAIL = "⚠️ **[LLM 调用失败]**"

_STUB_HINT = """
> 系统当前没有可用的 LLM 服务, 所以返回的是 stub. 不是真回答.

**怎么修**:
1. 把有效 API key 写到环境变量, 例如:
   - `DASHSCOPE_API_KEY=xxx` (阿里通义千问)
   - `OPENAI_API_KEY=sk-xxx` (OpenAI)
   - `ANTHROPIC_API_KEY=sk-xxx` (Claude)
2. 或者本地起 Ollama, 然后在 config.yaml 把 default_chat_model 改成 ollama 适配器
3. 重启服务 (`run/start_dev.bat`), 再到 **管理 → 监控 → 模型健康** 看模型是否变 healthy

你刚问的内容:
"""


class DummyLLMClient:
    """最小可运行占位 LLM, 用于没有真实 LLM 时清晰告诉用户"系统是 stub 不是真答案"."""

    def generate(self, prompt: str, trace_id: Optional[str] = None) -> str:
        prompt = prompt or ""
        return _STUB_PREFIX_NO_LLM + _STUB_HINT + "\n```\n" + prompt[:300] + "\n```"


def call_llm_compat(llm_client: Any, prompt: str, trace_id: Optional[str] = None) -> str:
    """统一通过 generate 协议调用 LLM.

    契约依赖: llm_client 必须实现 BaseLLMService.generate(prompt, trace_id) -> str.
    Task AAAA (#118): 出错回退时返回明确的错误提示, 不再静默返"占位回答".
    """
    if llm_client is None or not hasattr(llm_client, "generate"):
        return DummyLLMClient().generate(prompt, trace_id=trace_id)

    try:
        result = llm_client.generate(prompt, trace_id=trace_id)
    except TypeError:
        # 兼容仅接受单参数 prompt 的旧实现
        try:
            result = llm_client.generate(prompt)
        except Exception as e:
            return _stub_with_err(prompt, e)
    except Exception as e:
        return _stub_with_err(prompt, e)

    # 契约要求返回 str
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    return str(result)


def _stub_with_err(prompt: str, err: Exception) -> str:
    """LLM 调用真的出了异常 (key 失效 / 网络挂 / 503 等), 把错误明确告诉用户."""
    err_msg = str(err).strip()
    if len(err_msg) > 300:
        err_msg = err_msg[:300] + "..."
    return (
        f"{_STUB_PREFIX_CALL_FAIL}\n\n"
        f"> 后端调用 LLM 时抛了异常, 这是 stub 不是真回答.\n\n"
        f"**异常**:\n```\n{err_msg}\n```\n\n"
        f"**最常见的原因**:\n"
        f"- API key 失效 / 没配 (401 Unauthorized)\n"
        f"- 余额不足 (402 Payment Required)\n"
        f"- 限流 (429 Too Many Requests)\n"
        f"- 网络不通 / SSL 错误\n\n"
        f"去 **管理 → 监控 → 模型健康** 看具体哪个模型挂了. "
        f"网络/限流/余额恢复后, 熔断器会在冷却结束自动重试恢复, 无需重启 (仅改 key/配置才需重启).\n\n"
        f"---\n你刚问的:\n```\n{(prompt or '')[:300]}\n```"
    )
