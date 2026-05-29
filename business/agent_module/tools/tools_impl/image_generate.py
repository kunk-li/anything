# -*- coding: utf-8 -*-
"""
Tool: image_generate (Task TTTT-6 #143)

调 DashScope 通义万相 text2image API 生成图片. 返回 image URL 给前端展示.

Env: DASHSCOPE_API_KEY 必须设, 复用 LLM key.

API 文档:
  https://help.aliyun.com/zh/dashscope/developer-reference/api-details
  POST https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis
  X-DashScope-Async: enable   异步任务, 需轮询 task_id
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    requests = None


_DASHSCOPE_SYN_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
)
_DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
_DEFAULT_MODEL = "wanx-v1"  # 通义万相基础, 免费 tier 可用


def image_generate_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Input:
      prompt: str            (必填) 中文/英文描述
      negative_prompt: str   (可选) 负面描述
      size: str              "1024*1024" / "720*1280" / "1280*720" 等, 默 1024*1024
      n: int                 张数, 默 1, max 4
      model: str             默 wanx-v1
      timeout_seconds: int   总轮询超时, 默 60
    Output (success):
      { code: SUCCESS,
        data: { images: [url1, ...], image_url: url1, prompt: ..., model: ..., task_id: ...} }
    """
    trace_id = payload.get("trace_id")
    if requests is None:
        return _err("MISSING_DEPS", "requests 未安装, 跑 pip install requests", trace_id)

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return _err("PARAM_MISSING", "prompt 必填", trace_id)

    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return _err(
            "SERVICE_UNAVAILABLE",
            "DASHSCOPE_API_KEY 没设. 请在 .env 加 DASHSCOPE_API_KEY=sk-xxx 后重启.",
            trace_id,
        )

    model = payload.get("model") or _DEFAULT_MODEL
    size = payload.get("size") or "1024*1024"
    try:
        n = int(payload.get("n") or 1)
    except Exception:
        n = 1
    n = max(1, min(4, n))
    timeout = int(payload.get("timeout_seconds") or 60)
    negative = payload.get("negative_prompt") or ""

    body: Dict[str, Any] = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": n},
    }
    if negative:
        body["input"]["negative_prompt"] = negative

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",  # 万相只支持异步
    }

    # 1. 提交任务
    try:
        r = requests.post(_DASHSCOPE_SYN_URL, json=body, headers=headers, timeout=15)
    except Exception as e:
        return _err("NETWORK_ERROR", f"提交任务失败: {e}", trace_id)
    if r.status_code != 200:
        return _err(
            f"HTTP_{r.status_code}",
            f"DashScope 返回 {r.status_code}: {r.text[:200]}",
            trace_id,
        )
    try:
        j = r.json()
    except Exception:
        return _err("BAD_RESPONSE", "DashScope 返回非 JSON", trace_id)
    task_id = (j.get("output") or {}).get("task_id")
    if not task_id:
        return _err(
            "BAD_RESPONSE",
            f"DashScope 响应缺 task_id: {j}",
            trace_id,
        )

    # 2. 轮询任务
    poll_url = _DASHSCOPE_TASK_URL.format(task_id=task_id)
    poll_headers = {"Authorization": f"Bearer {api_key}"}
    start = time.time()
    last_payload: Optional[Dict[str, Any]] = None
    while time.time() - start < timeout:
        try:
            r2 = requests.get(poll_url, headers=poll_headers, timeout=10)
        except Exception as e:
            return _err("NETWORK_ERROR", f"轮询任务失败: {e}", trace_id)
        if r2.status_code != 200:
            return _err(
                f"HTTP_{r2.status_code}",
                f"DashScope 轮询返回 {r2.status_code}: {r2.text[:200]}",
                trace_id,
            )
        try:
            jj = r2.json()
        except Exception:
            return _err("BAD_RESPONSE", "DashScope 轮询返回非 JSON", trace_id)
        last_payload = jj
        out = jj.get("output") or {}
        status = (out.get("task_status") or "").upper()
        if status == "SUCCEEDED":
            results = out.get("results") or []
            urls = [r.get("url") for r in results if r.get("url")]
            if not urls:
                return _err(
                    "BAD_RESPONSE",
                    f"任务完成但没拿到 url: {jj}",
                    trace_id,
                )
            # description 含 URL 让前端 _collectGeneratedImages 能扫到
            url_block = "\n".join(urls)
            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": {
                    "images": urls,
                    "image_url": urls[0],  # 单图便捷字段
                    "prompt": prompt,
                    "model": model,
                    "size": size,
                    "n": n,
                    "task_id": task_id,
                    "description": f"为你生成了 {len(urls)} 张图: {prompt}\n{url_block}",
                },
                "trace_id": trace_id,
                "retryable": False,
                "details": None,
            }
        if status in ("FAILED", "CANCELED"):
            return _err(
                "GENERATION_FAILED",
                f"任务失败: {out.get('message') or status} (task_id={task_id})",
                trace_id,
            )
        # 还在跑, 等 2s
        time.sleep(2)

    return _err(
        "TIMEOUT",
        f"任务 {task_id} 在 {timeout}s 内未完成 (last status: "
        f"{((last_payload or {}).get('output') or {}).get('task_status')})",
        trace_id,
    )


def _err(code: str, message: str, trace_id: Optional[str]) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": None,
        "trace_id": trace_id,
        "retryable": code in ("NETWORK_ERROR", "TIMEOUT", "HTTP_500", "HTTP_502", "HTTP_503"),
        "details": None,
    }


TOOL_DESCRIPTION = (
    "文本生成图片 (text-to-image), 调阿里通义万相 API. "
    'input: {"prompt": str, "negative_prompt": str?, "size": "1024*1024"|"720*1280"|"1280*720"?, '
    '"n": int? (1-4), "model": "wanx-v1"?, "timeout_seconds": int? (默 60)}. '
    "需要 DASHSCOPE_API_KEY 环境变量. 异步任务自动轮询. "
    "返回 data.image_url + data.images (多张时). "
    "适合: 创作插画 / 概念图 / 设计草图."
)
