# -*- coding: utf-8 -*-
"""
Tool: document_read (Task MM #73)
"""

from __future__ import annotations

import ast
import ipaddress
import json
import math
import operator
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

# ============================================================
# 4. document_read_tool — 工厂模式, 闭包 doc_store
# ============================================================

def make_document_read_tool(
    doc_store_factory: Callable[[str], Any],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 把 (按 tenant_id 构造 doc_store 的 callable) 闭包成一个工具.

    payload:
        {"doc_id": str, "tenant_id": str = "default", "max_chars": int = 2000}

    返回 data: {"doc_id", "file_name", "file_type", "total_chars", "content"}
    跨租户 / doc 不存在 -> DOCUMENT_NOT_FOUND (跟 §9.3 一致, 不区分)。
    """

    def _read(payload: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = str(payload.get("doc_id") or "").strip()
        if not doc_id:
            return {"code": "PARAM_MISSING", "message": "doc_id 不能为空", "data": None, "retryable": False}
        tenant_id = str(payload.get("tenant_id") or "default")
        max_chars = max(100, min(int(payload.get("max_chars", 2000) or 2000), 20000))

        try:
            store = doc_store_factory(tenant_id)
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"doc_store_factory 失败: {e}",
                    "data": None, "retryable": True}
        try:
            doc = store.get_document(doc_id)
        except ValueError as e:
            return {"code": "PARAM_INVALID", "message": str(e), "data": None, "retryable": False}
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": str(e), "data": None, "retryable": True}

        if not doc:
            return {"code": "DOCUMENT_NOT_FOUND", "message": "文档不存在",
                    "data": {"doc_id": doc_id}, "retryable": False}

        content = str(doc.get("content") or "")
        truncated = content[:max_chars]
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "doc_id": doc_id,
                "file_name": doc.get("file_name"),
                "file_type": doc.get("file_type"),
                "total_chars": len(content),
                "content": truncated,
                "truncated": len(content) > max_chars,
            },
            "retryable": False,
        }

    return _read


