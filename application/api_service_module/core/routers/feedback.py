# -*- coding: utf-8 -*-
"""
FeedbackRoutesMixin (Task PM-2)

用户对 assistant 回答的 👍/👎 + 可选评语. 数据写本地 sqlite, 之后产
品迭代用 — 哪个 task 高频被否, 哪个 tool 总翻车, 都从这里读.

端点:
    POST /feedback         提交一条反馈
    GET  /feedback/stats   汇总统计 (按时间段 + 按 tool / model)
    GET  /feedback/recent  最近 N 条 (含 comment, 用于产品复盘)

设计:
    - 反馈以 trace_id 为锚 (每个 LLM 调用都有), 不依赖前端 message id
      的临时性, 切会话 / 刷新页都还能映射回原请求
    - sqlite WAL 模式, 不需要复杂依赖
    - 表很简单, 之后加字段 alter table 加 column 即可
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


# 表 schema. 加字段时只加 column, 不破坏老行.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT,
    session_id TEXT,
    tenant_id TEXT DEFAULT 'default',
    rating INTEGER NOT NULL,         -- +1 = thumbs up, -1 = thumbs down
    comment TEXT,                    -- 可选评语
    user_msg_preview TEXT,           -- user 那条 prompt 前 200 字 (用于复盘)
    assistant_msg_preview TEXT,      -- assistant 回答前 200 字
    mode TEXT,                       -- rag / agent / hybrid
    model_name TEXT,                 -- 反馈时的 default model (可选)
    tools_used TEXT,                 -- agent 模式用了哪些 tool, 逗号分隔
    created_at INTEGER NOT NULL,
    created_at_iso TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_trace ON feedback(trace_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);
CREATE INDEX IF NOT EXISTS idx_feedback_tenant ON feedback(tenant_id, created_at);
"""


def _get_db_path() -> Path:
    # 跟 state_store 共住一个 run/ 目录, 避免散文件
    root = Path(os.environ.get("ANYTHING_DATA_ROOT", "run")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / "feedback.sqlite3"


def _get_conn() -> sqlite3.Connection:
    p = _get_db_path()
    conn = sqlite3.connect(str(p), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


class FeedbackRoutesMixin:
    """Feedback router mixin."""

    def _register_feedback_routes(self) -> None:
        @self.app.post("/feedback")
        async def submit_feedback(request: Request):
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"code": "BAD_REQUEST", "message": "请求体非合法 JSON",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=400,
                )

            rating = body.get("rating")
            if rating not in (1, -1, "1", "-1"):
                return JSONResponse(
                    {"code": "PARAM_INVALID", "message": "rating 必须是 +1 或 -1",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=400,
                )

            target_trace = (body.get("trace_id") or "").strip() or trace_id
            session_id = (body.get("session_id") or "").strip() or None
            tenant_id = (body.get("tenant_id") or "default").strip() or "default"
            comment = body.get("comment")
            user_preview = (body.get("user_msg_preview") or "")[:200]
            assistant_preview = (body.get("assistant_msg_preview") or "")[:200]
            mode = (body.get("mode") or "").lower() or None
            model_name = body.get("model_name") or None
            tools_used = body.get("tools_used")
            if isinstance(tools_used, list):
                tools_used = ",".join(str(t) for t in tools_used)

            try:
                conn = _get_conn()
                now = int(time.time())
                iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
                conn.execute(
                    "INSERT INTO feedback (trace_id, session_id, tenant_id, rating, "
                    "comment, user_msg_preview, assistant_msg_preview, mode, model_name, "
                    "tools_used, created_at, created_at_iso) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        target_trace, session_id, tenant_id, int(rating),
                        (comment or "")[:2000] or None,
                        user_preview, assistant_preview,
                        mode, model_name, tools_used,
                        now, iso,
                    ),
                )
                conn.close()
                return JSONResponse({
                    "code": "SUCCESS", "message": "thanks",
                    "data": {"trace_id": target_trace, "rating": int(rating)},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "FEEDBACK_WRITE_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": True, "details": None},
                    status_code=500,
                )

        @self.app.get("/feedback/stats")
        async def feedback_stats(request: Request):
            trace_id = request.state.trace_id
            try:
                conn = _get_conn()
                cur = conn.execute(
                    "SELECT rating, COUNT(*) FROM feedback GROUP BY rating"
                )
                rows = dict(cur.fetchall())
                up = int(rows.get(1, 0))
                down = int(rows.get(-1, 0))
                total = up + down
                ratio = (up / total) if total else 0.0
                # 按 tool 拆分 down: 哪个 tool 最常被否
                cur = conn.execute(
                    "SELECT tools_used, COUNT(*) FROM feedback WHERE rating=-1 "
                    "AND tools_used IS NOT NULL GROUP BY tools_used "
                    "ORDER BY 2 DESC LIMIT 10"
                )
                down_by_tools = [{"tools": r[0], "count": r[1]} for r in cur.fetchall()]
                # 按 mode
                cur = conn.execute(
                    "SELECT mode, rating, COUNT(*) FROM feedback "
                    "WHERE mode IS NOT NULL GROUP BY mode, rating"
                )
                by_mode_raw = cur.fetchall()
                by_mode: Dict[str, Dict[str, int]] = {}
                for mode, rating, cnt in by_mode_raw:
                    by_mode.setdefault(mode, {"up": 0, "down": 0})
                    key = "up" if rating == 1 else "down"
                    by_mode[mode][key] = cnt
                conn.close()
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {
                        "total": total, "up": up, "down": down,
                        "up_ratio": round(ratio, 3),
                        "down_by_tools": down_by_tools,
                        "by_mode": by_mode,
                    },
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "FEEDBACK_STATS_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": True, "details": None},
                    status_code=500,
                )

        @self.app.get("/feedback/recent")
        async def feedback_recent(request: Request):
            trace_id = request.state.trace_id
            try:
                limit = int(request.query_params.get("limit", "50"))
            except ValueError:
                limit = 50
            limit = min(500, max(1, limit))
            rating_filter = request.query_params.get("rating")  # +1 / -1 / 都返
            try:
                conn = _get_conn()
                if rating_filter in ("1", "-1"):
                    cur = conn.execute(
                        "SELECT trace_id, session_id, tenant_id, rating, comment, "
                        "user_msg_preview, assistant_msg_preview, mode, model_name, "
                        "tools_used, created_at_iso FROM feedback WHERE rating=? "
                        "ORDER BY created_at DESC LIMIT ?",
                        (int(rating_filter), limit),
                    )
                else:
                    cur = conn.execute(
                        "SELECT trace_id, session_id, tenant_id, rating, comment, "
                        "user_msg_preview, assistant_msg_preview, mode, model_name, "
                        "tools_used, created_at_iso FROM feedback "
                        "ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    )
                items = []
                for row in cur.fetchall():
                    items.append({
                        "trace_id": row[0], "session_id": row[1], "tenant_id": row[2],
                        "rating": row[3], "comment": row[4],
                        "user_msg_preview": row[5], "assistant_msg_preview": row[6],
                        "mode": row[7], "model_name": row[8], "tools_used": row[9],
                        "created_at_iso": row[10],
                    })
                conn.close()
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"count": len(items), "items": items},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "FEEDBACK_RECENT_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": True, "details": None},
                    status_code=500,
                )
