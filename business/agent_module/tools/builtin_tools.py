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
# 10. code_lint — 多语言语法检查
# ============================================================

def code_lint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """代码语法检查 (静态, 不执行).

    payload:
        code: 待检查代码 (必填)
        language: "python"|"json"|"yaml"|"sql" (默认 python)

    返回 data: {"language", "valid": bool, "errors": [{line, col, message}], "summary"}
    """
    code = payload.get("code")
    if not isinstance(code, str):
        return {"code": "PARAM_MISSING", "message": "code 必须是字符串", "data": None, "retryable": False}
    if len(code) > 200_000:
        return {"code": "PARAM_INVALID", "message": "code 过长 (>200K)", "data": None, "retryable": False}

    lang = str(payload.get("language", "python") or "python").lower()
    errors: List[Dict[str, Any]] = []

    if lang in ("python", "py"):
        try:
            ast.parse(code, mode="exec")
        except SyntaxError as e:
            errors.append({
                "line": e.lineno or 0,
                "col": e.offset or 0,
                "message": e.msg or "syntax error",
            })

    elif lang == "json":
        try:
            json.loads(code)
        except json.JSONDecodeError as e:
            errors.append({
                "line": e.lineno,
                "col": e.colno,
                "message": e.msg,
            })

    elif lang in ("yaml", "yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            return {
                "code": "TOOL_CALL_FAILED",
                "message": "yaml language 需要 PyYAML (pip install pyyaml)",
                "data": None, "retryable": False,
            }
        try:
            yaml.safe_load(code)
        except yaml.YAMLError as e:  # type: ignore[attr-defined]
            mark = getattr(e, "problem_mark", None)
            errors.append({
                "line": (mark.line + 1) if mark else 0,
                "col": (mark.column + 1) if mark else 0,
                "message": str(getattr(e, "problem", e)),
            })

    elif lang == "sql":
        # sqlite3 EXPLAIN — 不真正建表, 只编译 SQL 语句, 抓语法错
        import sqlite3
        try:
            with sqlite3.connect(":memory:") as conn:
                conn.execute(f"EXPLAIN {code}")
        except sqlite3.Error as e:
            errors.append({"line": 0, "col": 0, "message": str(e)})

    else:
        return {
            "code": "PARAM_INVALID",
            "message": f"不支持的 language: {lang} (允许: python/json/yaml/sql)",
            "data": None, "retryable": False,
        }

    valid = len(errors) == 0
    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "language": lang,
            "valid": valid,
            "errors": errors,
            "summary": "代码语法合法" if valid else f"{len(errors)} 处语法错误",
        },
        "retryable": False,
    }


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


# ============================================================
# 12. image_describe — 多模态 LLM 工厂
# ============================================================

def make_image_describe_tool(
    llm_service: Any,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 闭包 llm_service (LLMService 实例) 返回图片理解工具.

    payload:
        image_path: 本地路径 (二选一)
        image_base64: base64 字符串 (二选一)
        prompt: 提问文本, 默认 "请描述这张图片"
        model_name: 可选, 走 LLMService.cfg 默认多模态模型

    返回 data: {"description", "model_name", "image_source"}

    依赖: llm_service 必须有 call_llm 方法 (LLMService 而非 DummyLLMClient)。
    """
    def _describe(payload: Dict[str, Any]) -> Dict[str, Any]:
        if llm_service is None or not hasattr(llm_service, "call_llm"):
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": "llm_service 未注入或不支持 call_llm",
                "data": None, "retryable": False,
            }

        image_path = payload.get("image_path")
        image_base64 = payload.get("image_base64")
        if not image_path and not image_base64:
            return {
                "code": "PARAM_MISSING",
                "message": "image_path 或 image_base64 二选一必填",
                "data": None, "retryable": False,
            }

        prompt = str(payload.get("prompt") or "请描述这张图片")
        model_name = str(payload.get("model_name") or "default")

        # 构造 MediaContent
        try:
            from llm_adapter_module.model.data_model import (
                LLMRequest, LLMParam, MediaContent,
            )
        except Exception as e:
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": f"llm_adapter 模块不可用: {e}",
                "data": None, "retryable": False,
            }

        media = MediaContent(
            media_type="image",
            media_path=str(image_path or ""),
            media_base64=str(image_base64 or "") or None,
        )

        req = LLMRequest(
            request_type="MULTIMODAL",
            input_text=prompt,
            media_input=[media],
            model_name=model_name,
            model_param=LLMParam(temperature=0.3, max_tokens=512),
        )

        try:
            resp = llm_service.call_llm(req)
        except Exception as e:
            return {
                "code": "TOOL_CALL_FAILED",
                "message": f"多模态 LLM 调用异常: {e}",
                "data": None, "retryable": True,
            }

        code = getattr(resp, "code", "UNKNOWN_ERROR")
        if code != "SUCCESS":
            return {
                "code": "TOOL_CALL_FAILED",
                "message": f"多模态 LLM 返回 {code}: {getattr(resp, 'message', '')}",
                "data": None, "retryable": True,
            }

        result = getattr(resp, "multimodal_result", None)
        text = getattr(result, "text_result", "") if result else ""
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "description": text,
                "model_name": getattr(resp, "request_info", {}).get("model_name") if getattr(resp, "request_info", None) else model_name,
                "image_source": "path" if image_path else "base64",
            },
            "retryable": False,
        }

    return _describe


# ============================================================
# 13. weather — Open-Meteo 免 key 天气查询
# ============================================================

def weather(payload: Dict[str, Any]) -> Dict[str, Any]:
    """天气查询. 用 Open-Meteo (https://open-meteo.com), 无需 API key, 公网开放。

    payload:
        location: 城市名 (中英都行, 通过 geocoding API resolve)
        OR latitude + longitude: 经纬度 (跳过 geocoding)

    返回 data: {
        "location": {"name", "country", "latitude", "longitude"},
        "current": {"temperature", "weathercode", "windspeed", "winddirection", "is_day", "time"},
    }
    """
    location = payload.get("location")
    lat = payload.get("latitude")
    lon = payload.get("longitude")

    if not location and (lat is None or lon is None):
        return {
            "code": "PARAM_MISSING",
            "message": "需要 location 字符串 或 latitude+longitude",
            "data": None, "retryable": False,
        }

    location_info = {"name": None, "country": None, "latitude": lat, "longitude": lon}

    try:
        # 1. 没经纬度时, 用 geocoding 解析
        if lat is None or lon is None:
            geo_url = (
                "https://geocoding-api.open-meteo.com/v1/search"
                f"?name={urllib.parse.quote(str(location))}&count=1&language=zh"
            )
            req = urllib.request.Request(geo_url, headers={"User-Agent": "anything-agent/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                geo_data = json.loads(resp.read().decode("utf-8"))
            results = geo_data.get("results") or []
            if not results:
                return {
                    "code": "SUCCESS", "message": "location not found",
                    "data": {"location": {"name": location}, "current": None},
                    "retryable": False,
                }
            top = results[0]
            location_info = {
                "name": top.get("name"),
                "country": top.get("country"),
                "latitude": top.get("latitude"),
                "longitude": top.get("longitude"),
            }
            lat = top.get("latitude")
            lon = top.get("longitude")

        # 2. 拉当前天气
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current_weather=true"
        )
        req2 = urllib.request.Request(weather_url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req2, timeout=8) as resp2:
            wdata = json.loads(resp2.read().decode("utf-8"))

        current = wdata.get("current_weather") or {}
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "location": location_info,
                "current": {
                    "temperature_celsius": current.get("temperature"),
                    "weathercode": current.get("weathercode"),
                    "windspeed_kmh": current.get("windspeed"),
                    "winddirection_deg": current.get("winddirection"),
                    "is_day": bool(current.get("is_day", 1)),
                    "time": current.get("time"),
                },
            },
            "retryable": False,
        }
    except Exception as e:
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"天气查询失败 (可能无网): {e}",
            "data": None, "retryable": True,
        }


# ============================================================
# 14. currency_convert — ECB Frankfurter 免 key 汇率换算
# ============================================================

def currency_convert(payload: Dict[str, Any]) -> Dict[str, Any]:
    """汇率换算. 用 https://api.frankfurter.app (ECB 数据, 免 key, 公网开放)。

    payload:
        from_currency: 3 字母代码 (USD/EUR/CNY/JPY/GBP/...)
        to_currency: 同上
        amount: 数字 (默认 1.0)
        date: "YYYY-MM-DD" 或 "latest" (默认 latest)

    返回 data: {"from", "to", "amount", "converted", "rate", "date"}
    """
    from_cur = str(payload.get("from_currency") or "").strip().upper()
    to_cur = str(payload.get("to_currency") or "").strip().upper()
    if not from_cur or not to_cur:
        return {
            "code": "PARAM_MISSING",
            "message": "from_currency / to_currency 必填",
            "data": None, "retryable": False,
        }
    if len(from_cur) != 3 or len(to_cur) != 3:
        return {
            "code": "PARAM_INVALID",
            "message": "货币代码必须是 3 字母 ISO 4217",
            "data": None, "retryable": False,
        }
    amount = float(payload.get("amount", 1.0) or 1.0)
    date = str(payload.get("date") or "latest")

    if from_cur == to_cur:
        return {
            "code": "SUCCESS", "message": "ok (same currency)",
            "data": {
                "from": from_cur, "to": to_cur, "amount": amount,
                "converted": amount, "rate": 1.0, "date": date,
            },
            "retryable": False,
        }

    try:
        url = (
            f"https://api.frankfurter.app/{urllib.parse.quote(date)}"
            f"?from={from_cur}&to={to_cur}&amount={amount}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"汇率查询失败: {e}",
            "data": None, "retryable": True,
        }

    rates = data.get("rates") or {}
    converted = rates.get(to_cur)
    if converted is None:
        return {
            "code": "PARAM_INVALID",
            "message": f"不支持的货币对: {from_cur} -> {to_cur}",
            "data": None, "retryable": False,
        }
    rate = converted / amount if amount else None
    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "from": from_cur,
            "to": to_cur,
            "amount": amount,
            "converted": converted,
            "rate": rate,
            "date": data.get("date") or date,
        },
        "retryable": False,
    }


# ============================================================
# 15. python_sandbox — AST 严格白名单 + 超时
# ============================================================
# ⚠️ 关键约束 (Python sandbox 是公认难题, 这是教学/受限版本, 不能跑用户脚本):
#   - 仅允许下面列出的 AST 节点类型 + 内置函数白名单
#   - 禁止: import, attribute access, exec, eval, open, __ 双下划线, 函数定义, 类定义
#   - 超时通过 threading.Timer + 协作式停止 (Python 没法强杀线程, 极端死循环可能 hang)

_SANDBOX_ALLOWED_AST_NODES = {
    ast.Module, ast.Expression, ast.Expr,
    ast.Constant, ast.Name, ast.Load, ast.Store,
    ast.BinOp, ast.UnaryOp, ast.BoolOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not,
    ast.And, ast.Or,
    ast.Compare,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.IfExp,                                 # 三元
    ast.Call,
    ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension,
    ast.Subscript, ast.Slice, ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.Assign, ast.AugAssign,
    ast.If, ast.For, ast.While, ast.Pass, ast.Break, ast.Continue,
    ast.Return,
    ast.JoinedStr, ast.FormattedValue,        # f-string
    ast.Starred,
    ast.keyword,
}

_SANDBOX_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool,
    "dict": dict, "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "len": len, "list": list, "map": map,
    "max": max, "min": min, "range": range, "round": round,
    "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip, "reversed": reversed,
    "True": True, "False": False, "None": None,
    # 数学
    "math_pi": math.pi, "math_e": math.e,
}


def _sandbox_validate_ast(tree: ast.AST) -> Optional[str]:
    """遍历 AST, 任一节点不在白名单 -> 返回错误描述; 通过返回 None."""
    for node in ast.walk(tree):
        if type(node) not in _SANDBOX_ALLOWED_AST_NODES:
            return f"禁止的 AST 节点: {type(node).__name__}"
        if isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id.endswith("__"):
                return f"禁止访问 __ 双下划线名称: {node.id}"
        if isinstance(node, ast.Attribute):
            return "禁止属性访问 (xxx.yyy)"
        if isinstance(node, ast.Call):
            # Call 的 func 必须是 Name 且在白名单
            if not isinstance(node.func, ast.Name):
                return "禁止调用非简单函数名"
            if node.func.id not in _SANDBOX_BUILTINS:
                return f"禁止调用未在白名单的函数: {node.func.id}"
    return None


def python_sandbox(payload: Dict[str, Any]) -> Dict[str, Any]:
    """超受限 Python 执行. 比 calculator 更宽 (有 for / if / 列表推导 / dict / 切片),
    但仍严禁 import / attribute / 双下划线 / 函数定义 / 类定义。

    payload:
        code: 待执行代码 (必填)
        timeout_seconds: 默认 2.0, 上限 10.0

    返回 data: {"stdout": str, "result": Any, "elapsed_seconds": float}
        - "result": 最后一个表达式的值 (如果是赋值语句序列, 取 `result` 变量, 没有则 None)
        - "stdout": 暂不支持 (sandbox 不允许 print)

    ⚠️ 教学/受限实现, 不能用来跑不可信代码 — Python sandbox 是公认难题,
       生产部署建议跑在隔离进程 / Docker / gVisor 里。
    """
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"code": "PARAM_MISSING", "message": "code 不能为空", "data": None, "retryable": False}
    if len(code) > 5000:
        return {"code": "PARAM_INVALID", "message": "code 过长 (>5000)", "data": None, "retryable": False}

    timeout_s = max(0.1, min(float(payload.get("timeout_seconds", 2.0) or 2.0), 10.0))

    # 1. 解析 + AST 白名单校验
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return {"code": "PARAM_INVALID", "message": f"语法错误: {e}", "data": None, "retryable": False}
    err = _sandbox_validate_ast(tree)
    if err:
        return {"code": "PARAM_INVALID", "message": err, "data": None, "retryable": False}

    # 2. 在受限命名空间执行, 用线程 + 标志位做协作式超时
    import threading
    import time as _time
    result_holder: Dict[str, Any] = {"value": None, "error": None}
    safe_globals = {"__builtins__": {}}
    safe_globals.update(_SANDBOX_BUILTINS)
    safe_locals: Dict[str, Any] = {}

    def _run():
        try:
            exec(compile(tree, "<sandbox>", "exec"), safe_globals, safe_locals)
        except Exception as e:
            result_holder["error"] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=_run, daemon=True)
    t0 = _time.time()
    thread.start()
    thread.join(timeout=timeout_s)
    elapsed = _time.time() - t0

    if thread.is_alive():
        # 没法强杀, 但标记 timeout (线程可能继续跑直到自然结束)
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"超时 (>{timeout_s}s)",
            "data": {"elapsed_seconds": round(elapsed, 3)},
            "retryable": False,
        }
    if result_holder["error"]:
        return {
            "code": "TOOL_CALL_FAILED",
            "message": result_holder["error"],
            "data": {"elapsed_seconds": round(elapsed, 3)},
            "retryable": False,
        }

    # 提取 `result` 变量 (如果用户定义了), 否则取最后一个赋值变量, 都没就 None
    final = safe_locals.get("result")
    if final is None and safe_locals:
        # 取最后赋值的非内部变量
        for k in list(safe_locals.keys())[::-1]:
            if not k.startswith("_"):
                final = safe_locals[k]
                break

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "result": final,
            "locals_keys": [k for k in safe_locals.keys() if not k.startswith("_")],
            "elapsed_seconds": round(elapsed, 3),
        },
        "retryable": False,
    }


# ============================================================
# 工具描述表 — 给 Agent LLM 看, 决定何时调用何工具
# ============================================================

# ============================================================
# Task EE (#65): spawn_subagent — 派生受限子 Agent 处理子任务
#
# Claude Code subagent / Task tool 模式. 主 Agent 调 spawn_subagent 把子任务
# 委托给一个新 SimpleAgent 实例, 该子 agent 只能用 allowed_tools 子集的工具,
# 跑完 ReAct 后把 final answer 回报给父 agent.
#
# 用途:
#   - 任务分解: 复杂任务拆成多个子任务, 每个子 agent 专注一件事
#   - 工具隔离: 子 agent 只看到必要工具子集, 减少 prompt 噪音 + 提升判断准确度
#   - 上下文隔离: 子 agent 的 history 不污染父 agent 的 react_history
# ============================================================

def make_spawn_subagent_tool(parent_agent: Any) -> Callable[..., Dict[str, Any]]:
    """生成 spawn_subagent 工具闭包. parent_agent 是宿主 SimpleAgent 实例.

    子 agent 复用宿主的 tool_registry / llm_planner / state_store / deps,
    但通过 allowed_tools 限制可见工具集. ReAct 跑完后把 final_answer 包装成
    标准 tool 返回结构 (code/data/success).
    """
    def spawn_subagent(
        role: Optional[str] = None,
        task: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        max_iterations: Optional[int] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        **_ignored,
    ) -> Dict[str, Any]:
        if not task:
            return {
                "code": "PARAM_MISSING",
                "message": "task 不能为空",
                "data": None,
                "success": False,
            }
        if parent_agent is None or parent_agent.tool_registry is None:
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": "spawn_subagent 需要 parent_agent 注入",
                "data": None,
                "success": False,
            }

        # 1. 解析子 agent 工具白名单 (取父 registry 的子集)
        parent_tools = _list_parent_tools(parent_agent.tool_registry)
        if allowed_tools:
            allowed = [t for t in allowed_tools if t in parent_tools]
        else:
            # 默认 inherit 父全部工具, 但去掉 spawn_subagent 自身防递归
            allowed = [t for t in parent_tools if t != "spawn_subagent"]
        if not allowed:
            return {
                "code": "PARAM_INVALID",
                "message": f"allowed_tools 跟父 registry 无交集. 父可见: {parent_tools[:10]}",
                "data": None,
                "success": False,
            }

        # 2. new 一个子 SimpleAgent (复用父的关键字段)
        try:
            # 延迟 import 避免循环
            from agent_module.core.impl import SimpleAgent as _SA
        except Exception:
            return {
                "code": "UNKNOWN_ERROR",
                "message": "无法导入 SimpleAgent",
                "data": None,
                "success": False,
            }

        child_registry = _RestrictedRegistry(parent_agent.tool_registry, allow=set(allowed))
        child_agent = _SA(
            state_store=parent_agent.state_store,
            tool_registry=child_registry,
            timeout=parent_agent.timeout,
            max_retries=parent_agent.max_retries,
            llm_planner=getattr(parent_agent, "llm_planner", None),
        )
        child_agent.execution_strategy = "react"
        child_agent.use_llm_planner = False  # role 是父决定的, 不让子重新规划
        # 不继承父的 approval 白名单 — 子 agent 只能用 allowed 里的工具,
        # 那些工具父已经审批过, 子运行期需要再过一遍是过度防御.
        child_agent.tool_approval_required = set()
        if max_iterations is not None:
            try:
                child_agent.max_react_iterations = max(1, min(10, int(max_iterations)))
            except (ValueError, TypeError):
                pass

        # 3. 跑子 task
        role_hint = f"[role={role}] " if role else ""
        sub_task = role_hint + str(task)
        # 子 trace_id 衍生自父, session_id 复用 (历史共享便于调试)
        child_trace = f"{trace_id or 'subagent'}#sub"
        child_session = session_id or "subagent_session"
        try:
            result = child_agent.execute({
                "task": sub_task,
                "trace_id": child_trace,
                "session_id": child_session,
                "extra_params": (extra_params or {}),
            })
        except Exception as e:
            return {
                "code": "UNKNOWN_ERROR",
                "message": f"子 agent 执行异常: {e}",
                "data": None,
                "success": False,
            }

        # 4. 整理回报给父 agent (保留 answer + steps 概要)
        data = result.get("data") or {}
        return {
            "code": result.get("code", "UNKNOWN_ERROR"),
            "message": result.get("message", ""),
            "success": result.get("code") == "SUCCESS",
            "data": {
                "answer": data.get("answer") or "",
                "iterations_used": data.get("iterations_used"),
                "tool_results_summary": (data.get("tool_results_summary") or [])[:5],
                "allowed_tools": allowed,
                "role": role,
            },
        }

    return spawn_subagent


def _list_parent_tools(registry: Any) -> List[str]:
    """从父 tool_registry 拿可见工具名列表. 兼容 DictToolRegistry / dict / 任意 list_tools()."""
    if registry is None:
        return []
    if hasattr(registry, "list_tools"):
        try:
            return list(registry.list_tools())
        except Exception:
            pass
    if isinstance(registry, dict):
        return list(registry.keys())
    return []


class _RestrictedRegistry:
    """子 agent 用的工具注册表代理: 把父 registry 包一层, 只暴露 allow 集合内的工具.

    支持 register / get / list_tools / describe / describe_all 4 个常用接口,
    跟 DictToolRegistry 协议兼容.
    """
    def __init__(self, parent: Any, allow: set):
        self._parent = parent
        self._allow = set(allow)

    def register(self, name: str, tool: Any, description: str = "") -> None:
        # 子 agent 想注册新工具? 拒绝 — 它的工具集是父定的
        return None

    def get(self, name: str) -> Any:
        if name not in self._allow:
            return None
        if hasattr(self._parent, "get"):
            return self._parent.get(name)
        if isinstance(self._parent, dict):
            return self._parent.get(name)
        return None

    def list_tools(self) -> List[str]:
        if hasattr(self._parent, "list_tools"):
            try:
                avail = set(self._parent.list_tools())
            except Exception:
                avail = set()
        elif isinstance(self._parent, dict):
            avail = set(self._parent.keys())
        else:
            avail = set()
        return sorted(self._allow & avail)

    def describe(self, name: str) -> str:
        if name not in self._allow:
            return ""
        if hasattr(self._parent, "describe"):
            try:
                return self._parent.describe(name)
            except Exception:
                return ""
        return ""

    def describe_all(self) -> Dict[str, str]:
        if hasattr(self._parent, "describe_all"):
            try:
                all_desc = self._parent.describe_all()
            except Exception:
                all_desc = {}
        else:
            all_desc = {}
        return {n: d for n, d in all_desc.items() if n in self._allow}


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
    "code_lint": (
        "代码语法检查 (静态, 不执行), 不联网。"
        ' input: {"code": str, "language": "python"|"json"|"yaml"|"sql"}.'
        " 返回 valid + 行列号 + 错误描述。"
        " 适合: 用户/LLM 输出代码前做合法性校验。"
    ),
    "email_send": (
        "通过 SMTP 发邮件。需要后端注入 smtp 配置 (host/port/user/password/from_addr)。"
        ' input: {"to": str|list, "subject": str, "body": str, "cc": list, "is_html": bool}.'
        " 缺配置时返回 SERVICE_UNAVAILABLE。适合: 通知 / 报告分发场景。"
    ),
    "image_describe": (
        "多模态 LLM 描述图片。"
        ' input: {"image_path": 本地路径 或 "image_base64": str, "prompt": "请描述", "model_name": "default"}.'
        " 需要 OpenAIMultimodalAdapter 或类似配置的多模态模型。"
        " 适合: 看图回答 / 图像内容理解。"
    ),
    "weather": (
        "天气查询, 用 Open-Meteo (公网免 key)。"
        ' input: {"location": "北京" 或 "latitude": 39.9, "longitude": 116.4}.'
        " 返回 location 元信息 + 当前温度/风速/天气码。无网时失败。"
    ),
    "currency_convert": (
        "汇率换算, 用 ECB Frankfurter API (公网免 key)。"
        ' input: {"from_currency": "USD", "to_currency": "CNY", "amount": 100, "date": "latest"}.'
        " 货币代码 ISO 4217 3 字母。返回 converted + rate + date。"
    ),
    "python_sandbox": (
        "Python 受限执行, AST 严格白名单 + 协作式超时。"
        ' input: {"code": str, "timeout_seconds": 2.0}.'
        " 禁: import / attribute / __ 双下划线 / def / class / open / exec / eval。"
        " 允许: 数字 + 字符串 + 列表/字典 + for/if/while + 列表推导 + 内置 abs/len/sum 等。"
        " 适合: 比 calculator 更复杂的小段算法验证 (排序、统计聚合、列表变换)。"
        " ⚠️ 不能跑不可信代码, sandbox 是公认难题, 生产请隔离进程 / Docker。"
    ),
    "spawn_subagent": (
        "任务分解: 把一个独立子任务交给受限子 Agent 处理 (Claude Code Task tool 风格)。"
        ' input: {"role": str 可选, "task": str, "allowed_tools": [str] 可选, "max_iterations": int 可选}.'
        " 子 agent 只能用 allowed_tools 中的工具 (默认继承父全部工具但去掉 spawn_subagent 防递归);"
        " 跑完 ReAct 后只回报 final answer + 概要, 子 history 不污染父 react_history。"
        " 适合: 多步骤任务先分解再合并 (例: 子1 查资料 / 子2 算指标 / 主 agent 汇总)。"
    ),
}
