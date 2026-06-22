# -*- coding: utf-8 -*-
"""
Tool: sql_query (Task TTTT-3 #140)

执行 SQL 查询返回结果. 支持 sqlite (本地) / postgres / mysql.

安全:
  - 只允许 SELECT (拒绝 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE)
  - 行数上限 500 防爆 prompt
  - 没配 connection_string 时默认开个临时 in-memory sqlite, 让 LLM 能玩 demo 数据
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple


# 允许的语句前缀 (大小写不敏感)
_ALLOWED_PREFIX = re.compile(r"^\s*(select|with|explain|pragma\s+table_info)\b", re.I)
# 检测危险 keyword (双保险)
_DANGEROUS_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|attach|grant|revoke)\b",
    re.I,
)
_MAX_ROWS = 500


def sql_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Input:
      query: str                必填, SQL SELECT 语句
      connection_string: str    可选, sqlalchemy URL (postgres://... / mysql://... / sqlite:///path)
                                没填 → 用内存 sqlite (带 demo 表 users/orders)
      params: dict|list         可选, 参数化查询绑定值
      max_rows: int             默 100, 上限 500
    Output:
      data: { columns, rows, row_count, truncated, description }
    """
    trace_id = payload.get("trace_id")
    query = (payload.get("query") or "").strip()
    if not query:
        return _err("PARAM_MISSING", "query 必填", trace_id)

    # 安全检查 1: 只能 SELECT/WITH/EXPLAIN/PRAGMA
    if not _ALLOWED_PREFIX.match(query):
        return _err(
            "FORBIDDEN_STATEMENT",
            f"只允许 SELECT/WITH/EXPLAIN. 收到: {query[:50]!r}",
            trace_id,
        )
    # 安全检查 2: 显式 ban 写入关键字
    if _DANGEROUS_KEYWORDS.search(query):
        return _err(
            "FORBIDDEN_STATEMENT",
            "query 含写入/DDL 关键字 (INSERT/UPDATE/DELETE/DROP/...), 拒绝执行",
            trace_id,
        )

    max_rows = min(_MAX_ROWS, max(1, int(payload.get("max_rows") or 100)))
    params = payload.get("params")
    conn_str = payload.get("connection_string") or os.environ.get(
        "ANYTHING_SQL_CONNECTION", ""
    )

    try:
        rows, cols, demo_note = _run_query(query, conn_str, params)
    except Exception as e:
        return _err("QUERY_FAILED", f"执行失败: {e}", trace_id)

    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]

    # Task PM-7-8: description 含 markdown 表格, 前端 (普通 markdown 渲染) 直接显成表
    md_table = _rows_to_markdown_table(cols, rows, max_rows=20)
    return {
        "code": "SUCCESS",
        "message": "ok",
        "data": {
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "description": (
                f"返回 {len(cols)} 列 {len(rows)} 行"
                + (" (已截断 max_rows)" if truncated else "")
                + (f". {demo_note}" if demo_note else "")
                + (f"\n\n{md_table}" if md_table else "")
            ),
        },
        "trace_id": trace_id,
        "retryable": False,
        "details": None,
    }


def _rows_to_markdown_table(cols: List[str], rows: List[List[Any]], max_rows: int = 20) -> str:
    """Task PM-7-8: SQL 结果 → markdown 表 (前端 markdown 渲染直接显)."""
    if not cols:
        return ""
    if not rows:
        return f"| {' | '.join(cols)} |\n| {' | '.join('---' for _ in cols)} |\n| {' | '.join('(空)' for _ in cols)} |"
    visible = rows[:max_rows]
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for r in visible:
        cells = [str(c) if c is not None else "" for c in r]
        # 转义 markdown 表格中 | 字符
        cells = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
        body_lines.append("| " + " | ".join(cells) + " |")
    note = ""
    if len(rows) > max_rows:
        note = f"\n\n_(共 {len(rows)} 行, 表中展示前 {max_rows} 行)_"
    return "\n".join([header, sep] + body_lines) + note


def _sqlite_url_to_path(conn_str: str) -> str:
    """sqlite sqlalchemy URL → 文件路径 (保留绝对/相对语义).

    sqlalchemy 约定: 'sqlite://' authority + 路径, 其中第一个 '/' 是 URL 路径分隔符:
      sqlite:///rel.db      → 'rel.db'        (相对)
      sqlite:////abs/x.db   → '/abs/x.db'     (绝对, POSIX)
      sqlite:///C:/x.db     → 'C:/x.db'       (绝对, Windows)
    旧实现 lstrip('/') 会把 sqlite:////abs 的前导 '/' 全削掉, 绝对路径退化成相对 → 连错库.
    优先用 sqlalchemy 自带 URL 解析器 (与工具宣传的 sqlalchemy URL 语义完全一致),
    没装 sqlalchemy 时手工只剥 'sqlite://' authority + 恰好一个路径分隔符 '/'.
    """
    try:
        from sqlalchemy.engine.url import make_url

        db = make_url(conn_str).database
        return db or ""
    except ImportError:
        # 'sqlite:' + '//' authority = 'sqlite://' (8 chars), 余下首个 '/' 是路径分隔符, 只剥它一个
        rest = conn_str[len("sqlite://"):] if conn_str.startswith("sqlite://") else conn_str[len("sqlite:"):]
        return rest[1:] if rest.startswith("/") else rest


def _run_query(
    query: str, conn_str: str, params: Any
) -> Tuple[List[List[Any]], List[str], Optional[str]]:
    """跑 SQL. 返回 (rows, columns, demo_note).

    没 conn_str → 开内存 sqlite + demo 数据, demo_note 提示用户.
    """
    if not conn_str or conn_str.lower().startswith("sqlite::memory") or conn_str == ":memory:":
        return _run_demo_sqlite(query, params)

    if conn_str.startswith("sqlite:"):
        import sqlite3

        path = _sqlite_url_to_path(conn_str)
        conn = sqlite3.connect(path)
        try:
            return _run_sqlite_cursor(conn, query, params) + (None,)
        finally:
            conn.close()

    # postgres / mysql / 其他 → 走 sqlalchemy (需要装)
    try:
        import sqlalchemy  # noqa: F401
        from sqlalchemy import create_engine, text
    except ImportError as e:
        raise RuntimeError(
            "执行 postgres/mysql 需要 sqlalchemy, pip install sqlalchemy"
        ) from e

    engine = create_engine(conn_str, future=True)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            cols = list(result.keys())
            rows = [list(r) for r in result.fetchall()]
        return rows, cols, None
    finally:
        engine.dispose()  # 释放该 engine 的连接池, 防反复调用累积悬挂物理连接


def _run_demo_sqlite(
    query: str, params: Any
) -> Tuple[List[List[Any]], List[str], Optional[str]]:
    """没配 conn 时, 开 in-memory sqlite + demo users/orders 表."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, age INTEGER, country TEXT);
        INSERT INTO users (name, email, age, country) VALUES
            ('Alice', 'alice@example.com', 28, 'CN'),
            ('Bob', 'bob@example.com', 35, 'US'),
            ('Charlie', 'charlie@example.com', 42, 'JP'),
            ('Dora', 'dora@example.com', 24, 'CN'),
            ('Eric', 'eric@example.com', 31, 'US');

        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT, created_at TEXT);
        INSERT INTO orders (user_id, amount, status, created_at) VALUES
            (1, 199.99, 'paid', '2025-12-01'),
            (1, 49.50, 'pending', '2025-12-15'),
            (2, 999.00, 'paid', '2025-11-20'),
            (3, 1200.00, 'refunded', '2025-10-05'),
            (4, 88.88, 'paid', '2025-12-20'),
            (5, 350.00, 'paid', '2026-01-08');
        """
    )
    conn.commit()
    try:
        rows, cols = _run_sqlite_cursor(conn, query, params)
        note = (
            "DEMO 数据 — 没配 connection_string, 用了内存 sqlite + 示例 users/orders 表. "
            "生产用真 DB 在 invoke extra_params.connection_string 或 ANYTHING_SQL_CONNECTION 配."
        )
        return rows, cols, note
    finally:
        conn.close()


def _run_sqlite_cursor(conn, query: str, params: Any) -> Tuple[List[List[Any]], List[str]]:
    cur = conn.cursor()
    if params is None:
        cur.execute(query)
    elif isinstance(params, dict):
        cur.execute(query, params)
    elif isinstance(params, (list, tuple)):
        cur.execute(query, params)
    else:
        cur.execute(query)
    cols = [d[0] for d in (cur.description or [])]
    rows = [list(r) for r in cur.fetchall()]
    return rows, cols


def _err(code: str, message: str, trace_id: Optional[str]) -> Dict[str, Any]:
    return {
        "code": code, "message": message, "data": None,
        "trace_id": trace_id, "retryable": False, "details": None,
    }


SQL_QUERY_DESCRIPTION = (
    '执行只读 SQL 查询. input: {"query": str (只能 SELECT/WITH/EXPLAIN), '
    '"connection_string": str? (sqlalchemy URL, 如 "postgresql://u:p@h/db" / '
    '"sqlite:///path.db"; 没填用内存 sqlite + demo 表 users/orders), '
    '"params": dict|list? (参数绑定), "max_rows": int? (默 100, 上限 500)}. '
    "返回 columns + rows. 适合: 数据库查询/统计/报表. "
    "禁: INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE."
)
