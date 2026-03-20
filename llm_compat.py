# -*- coding: utf-8 -*-
"""
LLM 兼容适配层
统一兼容不同 llm client 的调用方式：
- generate(prompt)
- call(payload)
- invoke(payload)
- chat(messages=...)
- run(payload)
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class DummyLLMClient:
    """最小可运行占位 LLM"""

    def generate(self, prompt: str) -> str:
        prompt = prompt or ""
        return "【占位回答】\n" + prompt[:400]


def extract_text_from_result(result: Any) -> str:
    """从不同返回结构里提取文本"""
    if result is None:
        return ""

    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        # 一级常见字段
        for key in ("content", "answer", "text", "output", "result", "message"):
            if key in result and result[key]:
                return str(result[key])

        # data 层
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("content", "answer", "text", "output", "result", "message"):
                if key in data and data[key]:
                    return str(data[key])

            # OpenAI/兼容格式
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message")
                    if isinstance(msg, dict) and msg.get("content"):
                        return str(msg["content"])
                    if first.get("text"):
                        return str(first["text"])

        # 顶层 choices
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message")
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg["content"])
                if first.get("text"):
                    return str(first["text"])

        return ""

    # 某些 SDK 返回对象
    if hasattr(result, "content") and result.content:
        return str(result.content)

    if hasattr(result, "text") and result.text:
        return str(result.text)

    return str(result)


def call_llm_compat(llm_client: Any, prompt: str, trace_id: Optional[str] = None) -> str:
    """
    用统一方式调用 LLM，兼容多种接口风格
    """
    if llm_client is None:
        return DummyLLMClient().generate(prompt)

    payload = {
        "mode": "chat",
        "input_text": prompt,
        "prompt": prompt,
        "query": prompt,
        "trace_id": trace_id,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    # 1. generate(prompt)
    if hasattr(llm_client, "generate"):
        try:
            result = llm_client.generate(prompt)
            text = extract_text_from_result(result)
            if text:
                return text
        except TypeError:
            try:
                result = llm_client.generate(prompt=prompt, trace_id=trace_id)
                text = extract_text_from_result(result)
                if text:
                    return text
            except Exception:
                pass
        except Exception:
            pass

    # 2. call(payload)
    if hasattr(llm_client, "call"):
        try:
            result = llm_client.call(payload)
            text = extract_text_from_result(result)
            if text:
                return text
        except TypeError:
            try:
                result = llm_client.call(prompt)
                text = extract_text_from_result(result)
                if text:
                    return text
            except Exception:
                pass
        except Exception:
            pass

    # 3. invoke(payload)
    if hasattr(llm_client, "invoke"):
        try:
            result = llm_client.invoke(payload)
            text = extract_text_from_result(result)
            if text:
                return text
        except TypeError:
            try:
                result = llm_client.invoke(prompt)
                text = extract_text_from_result(result)
                if text:
                    return text
            except Exception:
                pass
        except Exception:
            pass

    # 4. run(payload)
    if hasattr(llm_client, "run"):
        try:
            result = llm_client.run(payload)
            text = extract_text_from_result(result)
            if text:
                return text
        except TypeError:
            try:
                result = llm_client.run(prompt)
                text = extract_text_from_result(result)
                if text:
                    return text
            except Exception:
                pass
        except Exception:
            pass

    # 5. chat(messages=...)
    if hasattr(llm_client, "chat"):
        try:
            result = llm_client.chat(messages=payload["messages"], trace_id=trace_id)
            text = extract_text_from_result(result)
            if text:
                return text
        except TypeError:
            try:
                result = llm_client.chat(payload["messages"])
                text = extract_text_from_result(result)
                if text:
                    return text
            except Exception:
                pass
        except Exception:
            pass

    # 6. completion / completions / create
    if hasattr(llm_client, "create"):
        try:
            result = llm_client.create(**payload)
            text = extract_text_from_result(result)
            if text:
                return text
        except Exception:
            pass

    return DummyLLMClient().generate(prompt)