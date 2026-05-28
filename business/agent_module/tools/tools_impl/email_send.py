# -*- coding: utf-8 -*-
"""
Tool: email_send (Task MM #73)
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
# 11. email_send — SMTP 工厂模式
# ============================================================

def make_email_send_tool(
    smtp_config: Dict[str, Any],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 闭包 smtp 配置 (host/port/user/password/from_addr/use_tls) 返回邮件工具.

    payload:
        to: str | list[str]  收件人
        subject: str
        body: str
        cc: list[str] = None
        is_html: bool = False

    返回 data: {"to", "subject", "sent_at"}
    """
    required_cfg = {"host", "port", "from_addr"}
    missing = required_cfg - set(smtp_config or {})

    def _send(payload: Dict[str, Any]) -> Dict[str, Any]:
        if missing:
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": f"SMTP 未配置, 缺字段: {sorted(missing)}",
                "data": None, "retryable": False,
            }
        to = payload.get("to")
        subject = str(payload.get("subject") or "").strip()
        body = str(payload.get("body") or "")
        if not to or not subject or not body:
            return {
                "code": "PARAM_MISSING",
                "message": "to / subject / body 都必填",
                "data": None, "retryable": False,
            }
        recipients = [to] if isinstance(to, str) else list(to)
        cc = payload.get("cc") or []
        if isinstance(cc, str):
            cc = [cc]
        is_html = bool(payload.get("is_html", False))

        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["From"] = smtp_config["from_addr"]
        msg["To"] = ", ".join(recipients)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html" if is_html else "plain", "utf-8"))

        try:
            with smtplib.SMTP(
                smtp_config["host"],
                int(smtp_config.get("port", 587)),
                timeout=int(smtp_config.get("timeout", 15)),
            ) as smtp:
                if smtp_config.get("use_tls", True):
                    smtp.starttls()
                if smtp_config.get("user") and smtp_config.get("password"):
                    smtp.login(smtp_config["user"], smtp_config["password"])
                smtp.send_message(msg, to_addrs=recipients + cc)
        except Exception as e:
            return {
                "code": "TOOL_CALL_FAILED",
                "message": f"SMTP 发送失败: {e}",
                "data": None, "retryable": True,
            }

        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "to": recipients,
                "cc": cc,
                "subject": subject,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
            "retryable": False,
        }

    return _send


