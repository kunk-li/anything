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


class DummyLLMClient:
    """最小可运行占位 LLM，仅供没有真实 LLM 时的本地联调使用"""

    def generate(self, prompt: str, trace_id: Optional[str] = None) -> str:
        prompt = prompt or ""
        return "【占位回答】\n" + prompt[:400]


def call_llm_compat(llm_client: Any, prompt: str, trace_id: Optional[str] = None) -> str:
    """统一通过 generate 协议调用 LLM。

    契约依赖：llm_client 必须实现 BaseLLMService.generate(prompt, trace_id) -> str。
    不符合契约或调用失败时回退到 DummyLLMClient，并把异常细节记录在返回文本之外
    （由上层日志侧负责记录）。
    """
    if llm_client is None or not hasattr(llm_client, "generate"):
        return DummyLLMClient().generate(prompt, trace_id=trace_id)

    try:
        result = llm_client.generate(prompt, trace_id=trace_id)
    except TypeError:
        # 兼容仅接受单参数 prompt 的旧实现（如 DummyLLMClient 早期版本）
        try:
            result = llm_client.generate(prompt)
        except Exception:
            return DummyLLMClient().generate(prompt, trace_id=trace_id)
    except Exception:
        return DummyLLMClient().generate(prompt, trace_id=trace_id)

    # 契约要求返回 str；为兼容个别实现返回 dict/对象的情况做最小提取
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    return str(result)
