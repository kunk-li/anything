# -*- coding: utf-8 -*-
"""
Agent 内置扩展工具集合 (Task #37)

每个工具签名: Callable[[Dict[str, Any]], Dict[str, Any]]
统一返回信封: {"code": "SUCCESS"|"<ERR>", "message": str, "data": dict|null, ...}

工具列表:
    calculator_tool       — AST 安全数学求值, 无外部依赖
    datetime_tool         — 当前时间 / 时区算术
    wikipedia_tool        — Wikipedia REST API (urllib, 离线时降级 stub)
    make_document_read_tool(doc_store_factory)
                          — 工厂模式生成 callable, 按 doc_id 拉文档全文

TOOL_DESCRIPTIONS 是给 SimpleAgent._build_planner_prompt 用的 docstring 表;
bootstrap 在 register 时也会把对应 description 传给 DictToolRegistry。
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
# 1. calculator_tool — AST 安全数学求值
# ============================================================

_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "pow": math.pow, "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "pi": lambda: math.pi, "e": lambda: math.e,
    "max": max, "min": min, "sum": sum,
}
_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e, "inf": math.inf}


def _safe_eval(node: ast.AST) -> Any:
    """递归求值 AST 节点, 只允许预设运算符 + 数字 + 调用白名单函数."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):  # numbers / bool
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ValueError(f"不支持的常量类型: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_cls = type(node.op)
        if op_cls not in _ALLOWED_BIN_OPS:
            raise ValueError(f"不支持的二元运算符: {op_cls.__name__}")
        return _ALLOWED_BIN_OPS[op_cls](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_cls = type(node.op)
        if op_cls not in _ALLOWED_UNARY_OPS:
            raise ValueError(f"不支持的一元运算符: {op_cls.__name__}")
        return _ALLOWED_UNARY_OPS[op_cls](_safe_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_CONSTS:
            return _ALLOWED_CONSTS[node.id]
        raise ValueError(f"未知标识符: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError(f"不允许调用: {getattr(node.func, 'id', node.func)!r}")
        fn = _ALLOWED_FUNCS[node.func.id]
        args = [_safe_eval(a) for a in node.args]
        return fn(*args) if args else fn()
    if isinstance(node, ast.List) or isinstance(node, ast.Tuple):
        return [_safe_eval(e) for e in node.elts]
    raise ValueError(f"不支持的 AST 节点: {type(node).__name__}")


def calculator_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """安全数学计算. payload: {"expression": "1+2*sqrt(9)+pi"}

    支持: + - * / // % ** 一元正负, 函数 sqrt/pow/abs/round/floor/ceil/log*/exp/三角/max/min/sum,
          常量 pi/e/inf。
    禁止: 任何变量名解引用, 属性访问, 字符串, 切片, lambda, import, ...
    """
    expr = str(payload.get("expression") or "").strip()
    if not expr:
        return {
            "code": "PARAM_MISSING", "message": "expression 不能为空", "data": None,
            "retryable": False,
        }
    if len(expr) > 200:
        return {
            "code": "PARAM_INVALID", "message": "expression 过长 (>200)", "data": None,
            "retryable": False,
        }
    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree)
    except SyntaxError as e:
        return {
            "code": "PARAM_INVALID", "message": f"语法错误: {e}", "data": None,
            "retryable": False,
        }
    except Exception as e:
        return {
            "code": "TOOL_CALL_FAILED", "message": str(e), "data": None,
            "retryable": False,
        }
    return {
        "code": "SUCCESS",
        "message": "ok",
        "data": {"expression": expr, "result": result},
        "retryable": False,
    }


# ============================================================
# 2. datetime_tool — 当前时间 / 时区算术
# ============================================================

def datetime_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """时间工具.

    payload:
        {"op": "now"}                                  当前 UTC + 本地
        {"op": "now", "tz_offset_hours": 8}            指定时区
        {"op": "add", "iso": "2026-05-27T10:00", "days": 7, "hours": -3}
                                                       日期算术
        {"op": "diff", "iso_start": "...", "iso_end": "..."}
                                                       天/秒差

    返回 data: {iso, timestamp, weekday, op_specific...}
    """
    op = str(payload.get("op") or "now").lower()

    try:
        if op == "now":
            tz_offset = payload.get("tz_offset_hours")
            if tz_offset is not None:
                tz = timezone(timedelta(hours=float(tz_offset)))
            else:
                tz = timezone.utc
            dt = datetime.now(tz)
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "iso": dt.isoformat(),
                    "timestamp": dt.timestamp(),
                    "weekday": dt.strftime("%A"),
                    "tz_offset_hours": (tz.utcoffset(dt) or timedelta()).total_seconds() / 3600,
                },
                "retryable": False,
            }

        if op == "add":
            iso = str(payload.get("iso") or "").strip()
            if not iso:
                return {"code": "PARAM_MISSING", "message": "add 需要 iso", "data": None, "retryable": False}
            base = datetime.fromisoformat(iso)
            delta = timedelta(
                days=float(payload.get("days", 0)),
                hours=float(payload.get("hours", 0)),
                minutes=float(payload.get("minutes", 0)),
                seconds=float(payload.get("seconds", 0)),
            )
            new_dt = base + delta
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "iso": new_dt.isoformat(),
                    "weekday": new_dt.strftime("%A"),
                    "delta_seconds": delta.total_seconds(),
                },
                "retryable": False,
            }

        if op == "diff":
            s = str(payload.get("iso_start") or "").strip()
            e = str(payload.get("iso_end") or "").strip()
            if not s or not e:
                return {"code": "PARAM_MISSING", "message": "diff 需要 iso_start + iso_end", "data": None, "retryable": False}
            d1 = datetime.fromisoformat(s)
            d2 = datetime.fromisoformat(e)
            diff = d2 - d1
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "seconds": diff.total_seconds(),
                    "days": diff.days,
                    "hours": diff.total_seconds() / 3600,
                },
                "retryable": False,
            }

        return {"code": "PARAM_INVALID", "message": f"未知 op: {op}", "data": None, "retryable": False}

    except ValueError as e:
        return {"code": "PARAM_INVALID", "message": str(e), "data": None, "retryable": False}
    except Exception as e:
        return {"code": "TOOL_CALL_FAILED", "message": str(e), "data": None, "retryable": False}


# ============================================================
# 3. wikipedia_tool — REST API, 无外部依赖 (urllib only)
# ============================================================

def wikipedia_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Wikipedia 摘要查询. payload:
        {"query": str, "lang": "zh"|"en", "max_chars": 800}

    成功返回 data: {"query", "title", "summary", "url", "lang"}
    无网时返回 TOOL_CALL_FAILED + message 告知 (Agent 应降级到 llm_generate)。
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"code": "PARAM_MISSING", "message": "query 不能为空", "data": None, "retryable": False}
    lang = str(payload.get("lang") or "zh").lower()
    if lang not in ("zh", "en", "ja", "ko", "fr", "de", "es"):
        lang = "zh"
    max_chars = max(50, min(int(payload.get("max_chars", 800) or 800), 4000))

    # Wikipedia REST: /api/rest_v1/page/summary/<title>
    # 但 user 查询是关键词不一定是 title, 用 search 接口先 resolve
    try:
        # 1. 搜索 -> 拿到第一条 title
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            f"?action=opensearch&search={urllib.parse.quote(query)}"
            f"&limit=1&namespace=0&format=json"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            search_data = json.loads(resp.read().decode("utf-8"))
        titles = search_data[1] if isinstance(search_data, list) and len(search_data) > 1 else []
        if not titles:
            return {
                "code": "SUCCESS", "message": "no results",
                "data": {"query": query, "title": None, "summary": "", "url": None, "lang": lang},
                "retryable": False,
            }
        title = titles[0]

        # 2. 拉摘要
        sum_url = (
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
            f"{urllib.parse.quote(title.replace(' ', '_'))}"
        )
        req2 = urllib.request.Request(sum_url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req2, timeout=8) as resp2:
            sum_data = json.loads(resp2.read().decode("utf-8"))

        extract = str(sum_data.get("extract", "") or "")[:max_chars]
        url = (
            sum_data.get("content_urls", {}).get("desktop", {}).get("page")
            or f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}"
        )
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "query": query,
                "title": title,
                "summary": extract,
                "url": url,
                "lang": lang,
            },
            "retryable": False,
        }
    except Exception as e:
        # 离线 / 超时 / DNS 失败 — 返回 TOOL_CALL_FAILED, agent 应该走 llm_generate 兜底
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"wikipedia 查询失败 (可能无网): {e}",
            "data": None,
            "retryable": True,
        }


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


# ============================================================
# 5. regex_extract — 正则抽取
# ============================================================

_REGEX_FLAGS = {
    "i": re.IGNORECASE, "ignorecase": re.IGNORECASE,
    "m": re.MULTILINE,  "multiline": re.MULTILINE,
    "s": re.DOTALL,     "dotall": re.DOTALL,
    "u": re.UNICODE,    "unicode": re.UNICODE,
    "x": re.VERBOSE,    "verbose": re.VERBOSE,
}


def regex_extract(payload: Dict[str, Any]) -> Dict[str, Any]:
    """正则抽取工具.

    payload:
        text: 待搜索文本 (必填)
        pattern: 正则表达式 (必填)
        flags: list[str] 或 str, 可选 ["i","m","s",...] 或 "ims"
        max_matches: int 默认 50, 上限 500
        return_groups: bool 默认 True, True 返回 group dict, False 返回完整 match

    返回 data: {"matches": [...], "match_count": N, "pattern": str}
    """
    text = payload.get("text")
    pattern = payload.get("pattern")
    if not isinstance(text, str) or not text:
        return {"code": "PARAM_MISSING", "message": "text 不能为空", "data": None, "retryable": False}
    if not isinstance(pattern, str) or not pattern:
        return {"code": "PARAM_MISSING", "message": "pattern 不能为空", "data": None, "retryable": False}
    if len(text) > 100_000:
        return {"code": "PARAM_INVALID", "message": "text 过长 (>100K)", "data": None, "retryable": False}
    if len(pattern) > 500:
        return {"code": "PARAM_INVALID", "message": "pattern 过长", "data": None, "retryable": False}

    raw_flags = payload.get("flags") or []
    flag_value = 0
    flag_items: List[str] = []
    if isinstance(raw_flags, str):
        flag_items = list(raw_flags)
    elif isinstance(raw_flags, list):
        flag_items = [str(x) for x in raw_flags]
    for f in flag_items:
        v = _REGEX_FLAGS.get(f.lower())
        if v:
            flag_value |= v

    max_matches = max(1, min(int(payload.get("max_matches", 50) or 50), 500))
    return_groups = bool(payload.get("return_groups", True))

    try:
        compiled = re.compile(pattern, flag_value)
    except re.error as e:
        return {"code": "PARAM_INVALID", "message": f"正则编译失败: {e}", "data": None, "retryable": False}

    matches: List[Any] = []
    for i, m in enumerate(compiled.finditer(text)):
        if i >= max_matches:
            break
        if return_groups and m.groups():
            try:
                matches.append({
                    "match": m.group(0),
                    "groups": list(m.groups()),
                    "named": m.groupdict(),
                    "span": [m.start(), m.end()],
                })
            except IndexError:
                matches.append({"match": m.group(0), "span": [m.start(), m.end()]})
        else:
            matches.append({"match": m.group(0), "span": [m.start(), m.end()]})

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "pattern": pattern,
            "match_count": len(matches),
            "matches": matches,
            "truncated": len(matches) >= max_matches,
        },
        "retryable": False,
    }


# ============================================================
# 6. text_stats — 文本统计
# ============================================================

def text_stats(payload: Dict[str, Any]) -> Dict[str, Any]:
    """文本统计 — 字数 / 词数 / 行数 / Unicode 块占比.

    payload: {"text": str}
    返回:
        char_count            总字符数 (含空格)
        char_count_no_space   不含空白
        word_count            按空白分割的"词"数 (中文 / 日韩按字算)
        line_count            行数
        cjk_chars             中日韩字符数
        ascii_chars           ASCII 字符数 (含字母数字标点)
        digit_chars           数字字符数
    """
    text = payload.get("text")
    if not isinstance(text, str):
        return {"code": "PARAM_MISSING", "message": "text 不是字符串", "data": None, "retryable": False}
    if len(text) > 1_000_000:
        return {"code": "PARAM_INVALID", "message": "text 过长 (>1M)", "data": None, "retryable": False}

    cjk_chars = 0
    ascii_chars = 0
    digit_chars = 0
    no_space_chars = 0
    for ch in text:
        cp = ord(ch)
        if not ch.isspace():
            no_space_chars += 1
        if ch.isdigit():
            digit_chars += 1
        if cp < 128:
            ascii_chars += 1
        # CJK 主块: U+4E00-9FFF 中日韩统一表意, +3000-303F 中日韩符号
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x3000 <= cp <= 0x303F:
            cjk_chars += 1

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "char_count": len(text),
            "char_count_no_space": no_space_chars,
            "word_count": len(text.split()),
            "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "cjk_chars": cjk_chars,
            "ascii_chars": ascii_chars,
            "digit_chars": digit_chars,
        },
        "retryable": False,
    }


# ============================================================
# 7. json_query — 简化 JSONPath 查询
# ============================================================

def _json_query_walk(data: Any, parts: List[str]) -> Any:
    """递归用 parts 逐级取值. parts 元素支持:
        - "key"      字典键
        - "[0]"      数组下标 (整数)
        - "*"        遍历当前层所有子项, 返回 list (扁平化)
    """
    if not parts:
        return data
    head, *rest = parts

    # 数组下标 [n]
    if re.fullmatch(r"\[-?\d+\]", head):
        idx = int(head[1:-1])
        if not isinstance(data, list):
            raise ValueError(f"路径段 {head!r} 期望 list, 实际 {type(data).__name__}")
        if not (-len(data) <= idx < len(data)):
            raise ValueError(f"数组下标越界: {idx}")
        return _json_query_walk(data[idx], rest)

    # 通配符 *
    if head == "*":
        if isinstance(data, list):
            return [_json_query_walk(item, rest) for item in data]
        if isinstance(data, dict):
            return [_json_query_walk(v, rest) for v in data.values()]
        raise ValueError(f"路径段 * 期望 list/dict, 实际 {type(data).__name__}")

    # 字典键
    if not isinstance(data, dict):
        raise ValueError(f"路径段 {head!r} 期望 dict, 实际 {type(data).__name__}")
    if head not in data:
        raise KeyError(f"找不到键 {head!r}")
    return _json_query_walk(data[head], rest)


def json_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """简化 JSONPath. payload:
        data: dict / list / 任意 JSON 值 (二选一)
        json_text: str — 当 data 没传时, 这里解析为 JSON 后用
        path: str, 点分键名 + [n] 数组下标 + * 通配符
              例 "user.name", "items.[0].title", "results.*.score"

    返回 data: {"path": str, "result": Any}
    """
    path = payload.get("path", "")
    if not isinstance(path, str):
        return {"code": "PARAM_INVALID", "message": "path 必须是字符串", "data": None, "retryable": False}

    if "data" in payload:
        data = payload["data"]
    elif "json_text" in payload:
        try:
            data = json.loads(str(payload["json_text"]))
        except Exception as e:
            return {"code": "PARAM_INVALID", "message": f"json_text 解析失败: {e}",
                    "data": None, "retryable": False}
    else:
        return {"code": "PARAM_MISSING", "message": "需要 data 或 json_text", "data": None, "retryable": False}

    # 切分路径, 例 "items.[0].title" -> ["items", "[0]", "title"]
    parts: List[str] = []
    for token in path.split("."):
        token = token.strip()
        if not token:
            continue
        # 处理 "key[0]" 这种, 拆出来
        m = re.match(r"^([^\[\]]*?)(\[-?\d+\])+$", token)
        if m:
            base = m.group(1)
            if base:
                parts.append(base)
            # 把所有 [n] 段都抽出来
            for ix in re.findall(r"\[-?\d+\]", token):
                parts.append(ix)
        else:
            parts.append(token)

    try:
        result = _json_query_walk(data, parts) if parts else data
    except (KeyError, ValueError) as e:
        return {"code": "TOOL_CALL_FAILED", "message": str(e), "data": None, "retryable": False}

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {"path": path, "result": result},
        "retryable": False,
    }


# ============================================================
# 8. http_get — 受控 GET (SSRF 防御)
# ============================================================

def _is_private_ip(ip_str: str) -> bool:
    """判断 IP 是否私网 / 环回 / 链路本地 / 多播等不该被 SSRF 出去的地址."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _resolve_safe(host: str) -> Optional[str]:
    """DNS 解析 host, 检查所有 A/AAAA 记录是否私网. 任一私网 -> 拒绝 (返回 None)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    ips = {ai[4][0] for ai in infos}
    for ip in ips:
        if _is_private_ip(ip):
            return None
    return ", ".join(sorted(ips))


def http_get(payload: Dict[str, Any]) -> Dict[str, Any]:
    """受控 HTTP GET. SSRF 防御:
        - 只允许 http/https
        - DNS 解析后任一 IP 落入私网/环回/链路本地 -> 拒
        - max_bytes 默认 1MB, 上限 10MB
        - max_redirects = 0 (不跟跳转, 避开间接跳到内网的攻击)
        - timeout 默认 8s, 上限 30s

    payload: {"url": str, "max_bytes": int = 1048576, "timeout": int = 8}
    返回 data: {"url", "status", "content_type", "text", "truncated"}
    """
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"code": "PARAM_MISSING", "message": "url 不能为空", "data": None, "retryable": False}

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return {"code": "PARAM_INVALID", "message": f"url 解析失败: {e}", "data": None, "retryable": False}

    if parsed.scheme not in ("http", "https"):
        return {"code": "PARAM_INVALID", "message": "仅允许 http / https 协议",
                "data": None, "retryable": False}
    host = parsed.hostname or ""
    if not host:
        return {"code": "PARAM_INVALID", "message": "url 缺 host", "data": None, "retryable": False}

    # 拒绝直接写 IP 私网
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_ip(str(ip)):
            return {"code": "PARAM_INVALID",
                    "message": f"拒绝私网 IP (SSRF 防御): {host}",
                    "data": None, "retryable": False}
        resolved = str(ip)
    except ValueError:
        # 是 hostname, 走 DNS
        resolved = _resolve_safe(host)
        if resolved is None:
            return {"code": "PARAM_INVALID",
                    "message": f"DNS 解析失败或指向私网 (SSRF 防御): {host}",
                    "data": None, "retryable": False}

    max_bytes = max(1024, min(int(payload.get("max_bytes", 1048576) or 1048576), 10 * 1024 * 1024))
    timeout = max(1, min(int(payload.get("timeout", 8) or 8), 30))

    # 禁用 redirect: 用 build_opener + 自定义 handler
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # 不跟跳转

    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "anything-agent/1.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]
            # 尝试用 content-type 里的 charset 解码, 失败回退 utf-8 (errors=replace)
            charset = "utf-8"
            for piece in content_type.split(";"):
                piece = piece.strip()
                if piece.lower().startswith("charset="):
                    charset = piece.split("=", 1)[1].strip().lower()
                    break
            try:
                text = raw.decode(charset, errors="replace")
            except Exception:
                text = raw.decode("utf-8", errors="replace")
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "url": url,
                    "resolved_ip": resolved,
                    "status": resp.status,
                    "content_type": content_type,
                    "text": text,
                    "byte_count": len(raw),
                    "truncated": truncated,
                },
                "retryable": False,
            }
    except urllib.request.HTTPError as e:
        return {"code": "TOOL_CALL_FAILED",
                "message": f"HTTP {e.code} {e.reason}",
                "data": {"url": url, "status": e.code}, "retryable": True}
    except Exception as e:
        return {"code": "TOOL_CALL_FAILED", "message": f"请求异常: {e}",
                "data": None, "retryable": True}


# ============================================================
# 9. text_summarize — LLM 压缩 (工厂模式闭包 llm_call)
# ============================================================

def make_text_summarize_tool(
    llm_call: Callable[[str], str],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 闭包 llm_call (str -> str) 返回 summarize 工具.

    payload:
        text: 待压缩文本 (必填)
        max_sentences: 目标句数 (默认 3, 上限 10)
        lang: "zh"|"en" (影响 prompt 模板, 默认 zh)

    返回 data: {"summary", "original_length", "summary_length"}
    """
    def _summarize(payload: Dict[str, Any]) -> Dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"code": "PARAM_MISSING", "message": "text 不能为空", "data": None, "retryable": False}
        if len(text) > 50_000:
            text = text[:50_000]  # 防 LLM context 爆掉
        max_sentences = max(1, min(int(payload.get("max_sentences", 3) or 3), 10))
        lang = str(payload.get("lang", "zh")).lower()

        if lang == "en":
            prompt = (
                f"Summarize the following text into {max_sentences} concise sentences. "
                "Preserve key facts and proper nouns. Reply with summary only, no preamble.\n\n"
                f"---\n{text}\n---"
            )
        else:
            prompt = (
                f"请把以下文本压缩成 {max_sentences} 句话, 保留关键事实和专有名词。"
                "只输出压缩后的内容, 不要任何解释或前言。\n\n"
                f"---\n{text}\n---"
            )

        try:
            summary = str(llm_call(prompt) or "").strip()
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"LLM 调用失败: {e}",
                    "data": None, "retryable": True}

        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "summary": summary,
                "original_length": len(text),
                "summary_length": len(summary),
                "max_sentences": max_sentences,
                "lang": lang,
            },
            "retryable": False,
        }
    return _summarize


# ============================================================
# 工具描述表 — 给 Agent LLM 看, 决定何时调用何工具
# ============================================================

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "calculator": (
        "安全数学计算, 不联网。"
        ' input: {"expression": str}. 支持 + - * / // % ** sqrt pow log exp 三角函数 + 常量 pi/e。'
        " 适合: 算术、统计、单位换算。"
    ),
    "datetime": (
        "时间工具, 不联网。"
        ' input: {"op": "now"|"add"|"diff", ...}.'
        ' op=now (可选 tz_offset_hours), op=add (iso + days/hours/...), op=diff (iso_start + iso_end).'
        " 适合: 当前时间、日期算术、距某天还有几天。"
    ),
    "wikipedia": (
        "Wikipedia 摘要查询, 需联网。"
        ' input: {"query": str, "lang": "zh"|"en", "max_chars": int}.'
        " 适合: 查询人物/概念/历史事件的权威信息。无网时会失败, 应降级到 llm_generate。"
    ),
    "document_read": (
        "按 doc_id 拉本地文档的全文 (从 document_store)。"
        ' input: {"doc_id": str, "tenant_id": str = "default", "max_chars": int = 2000}.'
        " 适合: rag_search 命中后想看完整原文, 或 citations 钻取。"
    ),
    "regex_extract": (
        "正则抽取, 不联网。"
        ' input: {"text": str, "pattern": str, "flags": "ims" 或 ["i","m","s"], "max_matches": int, "return_groups": bool}.'
        " 返回所有匹配的 match / groups / span。"
        " 适合: 从文本里提取邮箱、URL、电话、数字、日期等结构化片段。"
    ),
    "text_stats": (
        "文本统计, 不联网。"
        ' input: {"text": str}.'
        " 返回字符数 / 词数 / 行数 / 中日韩字符数 / ASCII / 数字字符数。"
        " 适合: 写作分析、内容审计、判断文档复杂度。"
    ),
    "json_query": (
        "简化 JSONPath 查询, 不联网。"
        ' input: {"data": Any 或 "json_text": str, "path": "user.name" / "items.[0].title" / "results.*.score"}.'
        " path 支持点分键、[n] 数组下标、* 通配符。"
        " 适合: 从 http_get 等工具返回的 JSON 里提取特定字段。"
    ),
    "http_get": (
        "受控 HTTP GET, 联网。带 SSRF 防御 (拒绝私网/环回 IP, 禁跟跳转, 上限 10MB)。"
        ' input: {"url": str, "max_bytes": int = 1MB, "timeout": int = 8s}.'
        " 仅支持 http/https。"
        " 适合: 抓取公开网页 API / 静态资源。私网/内部服务严禁,失败请走 llm_generate 兜底。"
    ),
    "text_summarize": (
        "调 LLM 把长文本压缩为 N 句摘要。"
        ' input: {"text": str, "max_sentences": int = 3, "lang": "zh"|"en"}.'
        " 适合: 把 rag_search / document_read / http_get 拿到的大段文本浓缩进上下文。"
    ),
}
