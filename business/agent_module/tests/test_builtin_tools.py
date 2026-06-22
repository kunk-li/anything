# -*- coding: utf-8 -*-
"""
单测 — Task #37 扩展工具集 (calculator / datetime / wikipedia / document_read).
"""

import os

# 避免 build_basic_deps secrets fail-fast
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock, patch

from agent_module.tools import (
    calculator_tool,
    datetime_tool,
    wikipedia_tool,
    make_document_read_tool,
    regex_extract,
    text_stats,
    json_query,
    http_get,
    make_text_summarize_tool,
    code_lint,
    make_email_send_tool,
    make_image_describe_tool,
    weather,
    currency_convert,
    python_sandbox,
    TOOL_DESCRIPTIONS,
)


class TestCalculatorTool(unittest.TestCase):

    def test_basic_arith(self):
        r = calculator_tool({"expression": "1+2*3"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 7)

    def test_powers_and_negatives(self):
        r = calculator_tool({"expression": "-(2**3) + 4"})
        self.assertEqual(r["data"]["result"], -4)

    def test_funcs_and_constants(self):
        r = calculator_tool({"expression": "sqrt(9) + pi"})
        self.assertEqual(r["code"], "SUCCESS")
        # sqrt(9)=3.0 + pi=3.14159... -> ~6.14
        self.assertAlmostEqual(r["data"]["result"], 3.0 + 3.141592653589793, places=4)

    def test_float_div(self):
        r = calculator_tool({"expression": "10/3"})
        self.assertAlmostEqual(r["data"]["result"], 10 / 3)

    def test_max_min_sum(self):
        r = calculator_tool({"expression": "max(1, 2, 3) + min(7, 4)"})
        self.assertEqual(r["data"]["result"], 7)

    def test_reject_attribute_access(self):
        """属性/import 之类的注入应该被拒"""
        r = calculator_tool({"expression": "__import__('os').system('echo hi')"})
        self.assertNotEqual(r["code"], "SUCCESS")

    def test_reject_unknown_name(self):
        r = calculator_tool({"expression": "x + 1"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertIn("未知标识符", r["message"])

    def test_reject_string(self):
        r = calculator_tool({"expression": '"hello" + "world"'})
        self.assertNotEqual(r["code"], "SUCCESS")

    def test_empty(self):
        r = calculator_tool({"expression": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_too_long(self):
        r = calculator_tool({"expression": "1+" * 200})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_syntax_error(self):
        r = calculator_tool({"expression": "1 +"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("语法错误", r["message"])


class TestDatetimeTool(unittest.TestCase):

    def test_now_default_utc(self):
        r = datetime_tool({"op": "now"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("iso", r["data"])
        self.assertIn("timestamp", r["data"])
        self.assertEqual(r["data"]["tz_offset_hours"], 0)

    def test_now_with_tz(self):
        r = datetime_tool({"op": "now", "tz_offset_hours": 8})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["tz_offset_hours"], 8)
        self.assertIn("+08:00", r["data"]["iso"])

    def test_add_days(self):
        r = datetime_tool({"op": "add", "iso": "2026-05-27T10:00:00", "days": 7})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["iso"], "2026-06-03T10:00:00")

    def test_diff(self):
        r = datetime_tool({
            "op": "diff",
            "iso_start": "2026-01-01T00:00:00",
            "iso_end": "2026-01-08T00:00:00",
        })
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["days"], 7)
        self.assertEqual(r["data"]["seconds"], 7 * 86400)

    def test_invalid_op(self):
        r = datetime_tool({"op": "rocket-launch"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_add_missing_iso(self):
        r = datetime_tool({"op": "add"})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestWikipediaTool(unittest.TestCase):
    """离线测试 — 用 patch 替换 urlopen, 模拟成功 / 网络失败两种路径"""

    def test_query_empty(self):
        r = wikipedia_tool({"query": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_offline_returns_failure(self):
        """模拟 urlopen 抛网络异常"""
        with patch("agent_module.tools.tools_impl.wikipedia.urllib.request.urlopen",
                   side_effect=ConnectionError("offline")):
            r = wikipedia_tool({"query": "Anthropic"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertIn("wikipedia", r["message"].lower())

    def test_no_results(self):
        """opensearch 返回空 titles 列表"""
        from io import BytesIO
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: fake_resp
        fake_resp.__exit__ = lambda *a: None
        fake_resp.read.return_value = b'["xyz", [], [], []]'
        with patch("agent_module.tools.tools_impl.wikipedia.urllib.request.urlopen",
                   return_value=fake_resp):
            r = wikipedia_tool({"query": "xyzzzz-no-match"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIsNone(r["data"]["title"])
        self.assertEqual(r["data"]["summary"], "")


class TestDocumentReadTool(unittest.TestCase):

    def test_missing_doc_id(self):
        tool = make_document_read_tool(lambda tid: MagicMock())
        r = tool({})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_doc_not_found(self):
        class _EmptyStore:
            def get_document(self, doc_id): return None
        tool = make_document_read_tool(lambda tid: _EmptyStore())
        r = tool({"doc_id": "missing-id"})
        self.assertEqual(r["code"], "DOCUMENT_NOT_FOUND")

    def test_doc_returned(self):
        class _Store:
            def get_document(self, doc_id):
                return {"content": "x" * 5000, "file_name": "a.md", "file_type": "md"}
        tool = make_document_read_tool(lambda tid: _Store())
        r = tool({"doc_id": "abc", "max_chars": 100})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["total_chars"], 5000)
        self.assertEqual(len(r["data"]["content"]), 100)
        self.assertTrue(r["data"]["truncated"])
        self.assertEqual(r["data"]["file_name"], "a.md")

    def test_invalid_doc_id_raises(self):
        class _Store:
            def get_document(self, doc_id):
                raise ValueError("doc_id 格式非法")
        tool = make_document_read_tool(lambda tid: _Store())
        r = tool({"doc_id": "not-uuid"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_factory_failure(self):
        def _bad_factory(tid):
            raise RuntimeError("不能创建 doc_store")
        tool = make_document_read_tool(_bad_factory)
        r = tool({"doc_id": "any"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")


class TestRegexExtract(unittest.TestCase):

    def test_simple_match(self):
        r = regex_extract({
            "text": "Contact: alice@example.com or bob@test.org",
            "pattern": r"[\w.]+@[\w.]+",
        })
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["match_count"], 2)
        self.assertEqual(r["data"]["matches"][0]["match"], "alice@example.com")

    def test_with_groups(self):
        r = regex_extract({
            "text": "phone: 010-12345678, mobile: 138-0000-1234",
            "pattern": r"(\d{3,4})-(\d{4,8})(?:-(\d{4}))?",
        })
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["matches"][0]["groups"][0], "010")

    def test_named_groups(self):
        r = regex_extract({
            "text": "ver 1.2.3 released",
            "pattern": r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)",
        })
        self.assertEqual(r["data"]["matches"][0]["named"], {
            "major": "1", "minor": "2", "patch": "3",
        })

    def test_flags_ignorecase(self):
        r = regex_extract({"text": "Hello HELLO hello", "pattern": "hello", "flags": "i"})
        self.assertEqual(r["data"]["match_count"], 3)

    def test_max_matches_truncates(self):
        r = regex_extract({"text": "x" * 100, "pattern": "x", "max_matches": 5})
        self.assertEqual(r["data"]["match_count"], 5)
        self.assertTrue(r["data"]["truncated"])

    def test_exact_match_count_not_truncated(self):
        # 命中数恰好等于 max_matches 且没有溢出 -> 不应标记 truncated
        r = regex_extract({"text": "xxxxx", "pattern": "x", "max_matches": 5})
        self.assertEqual(r["data"]["match_count"], 5)
        self.assertFalse(r["data"]["truncated"])

    def test_under_max_not_truncated(self):
        r = regex_extract({"text": "xx", "pattern": "x", "max_matches": 5})
        self.assertEqual(r["data"]["match_count"], 2)
        self.assertFalse(r["data"]["truncated"])

    def test_invalid_pattern(self):
        r = regex_extract({"text": "abc", "pattern": "("})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("正则编译失败", r["message"])

    def test_empty_text(self):
        r = regex_extract({"text": "", "pattern": "x"})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestTextStats(unittest.TestCase):

    def test_basic(self):
        r = text_stats({"text": "Hello 你好\nWorld 世界"})
        self.assertEqual(r["code"], "SUCCESS")
        d = r["data"]
        self.assertEqual(d["line_count"], 2)
        self.assertEqual(d["cjk_chars"], 4)  # 你好世界
        self.assertGreater(d["ascii_chars"], 0)

    def test_digits_and_no_space(self):
        r = text_stats({"text": "abc 123\n456"})
        d = r["data"]
        self.assertEqual(d["digit_chars"], 6)  # 1 2 3 4 5 6
        self.assertEqual(d["char_count_no_space"], 9)  # abc123456

    def test_not_string(self):
        r = text_stats({"text": 123})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_too_long(self):
        r = text_stats({"text": "x" * 2_000_000})
        self.assertEqual(r["code"], "PARAM_INVALID")


class TestJsonQuery(unittest.TestCase):

    def test_simple_path(self):
        r = json_query({"data": {"user": {"name": "Alice", "age": 30}}, "path": "user.name"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], "Alice")

    def test_array_index(self):
        r = json_query({"data": {"items": [{"title": "a"}, {"title": "b"}]}, "path": "items.[1].title"})
        self.assertEqual(r["data"]["result"], "b")

    def test_negative_index(self):
        r = json_query({"data": {"items": [10, 20, 30]}, "path": "items.[-1]"})
        self.assertEqual(r["data"]["result"], 30)

    def test_wildcard(self):
        r = json_query({"data": {"items": [{"v": 1}, {"v": 2}, {"v": 3}]}, "path": "items.*.v"})
        self.assertEqual(r["data"]["result"], [1, 2, 3])

    def test_json_text(self):
        r = json_query({"json_text": '{"a": [1, 2, 3]}', "path": "a.[2]"})
        self.assertEqual(r["data"]["result"], 3)

    def test_key_not_found(self):
        r = json_query({"data": {"a": 1}, "path": "b"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_index_out_of_range(self):
        r = json_query({"data": {"items": [1, 2]}, "path": "items.[10]"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_invalid_json_text(self):
        r = json_query({"json_text": "{not valid", "path": "a"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_neither_data_nor_text(self):
        r = json_query({"path": "x"})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestHttpGet(unittest.TestCase):

    def test_reject_non_http_scheme(self):
        r = http_get({"url": "file:///etc/passwd"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("http / https", r["message"])

    def test_reject_private_ip_literal(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1"):
            r = http_get({"url": f"http://{ip}/x"})
            self.assertEqual(r["code"], "PARAM_INVALID", ip)
            self.assertIn("SSRF", r["message"])

    def test_reject_loopback_ipv6(self):
        r = http_get({"url": "http://[::1]/x"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_dns_resolution_failure(self):
        """不存在的域名 -> PARAM_INVALID (DNS 失败也算 SSRF 防御)"""
        r = http_get({"url": "http://this-domain-definitely-does-not-exist-xxxyyy.invalid/path"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_empty_url(self):
        r = http_get({"url": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_connection_pinned_to_validated_ip(self):
        """DNS-rebinding 防御: 真正拨号的 IP 必须是校验时解析到的那个,
        而不是连接时重新解析的结果 (TOCTOU 窗口)."""
        import importlib
        import socket as _socket
        # 用 importlib 拿子模块本身: tools_impl/__init__ 把同名函数 http_get
        # 重新 export, 直接 import 会拿到函数而非模块.
        _hg = importlib.import_module("agent_module.tools.tools_impl.http_get")

        dialed = {}

        def fake_getaddrinfo(host, port, *a, **k):
            # 校验时解析到公网 IP
            return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        def fake_create_connection(addr, *a, **k):
            dialed["ip"] = addr[0]
            raise OSError("blocked-after-pin")  # 只关心拨到哪个 IP

        with patch.object(_socket, "getaddrinfo", fake_getaddrinfo), \
                patch.object(_socket, "create_connection", fake_create_connection):
            r = _hg.http_get({"url": "http://rebind.example/", "timeout": 2})
        # 拨号 IP == 校验过的 IP (没有发生二次独立解析)
        self.assertEqual(dialed.get("ip"), "93.184.216.34")
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")


class TestTextSummarize(unittest.TestCase):

    def test_calls_llm(self):
        called = {}
        def _fake_llm(prompt):
            called["prompt"] = prompt
            return "这是压缩后的 3 句摘要。第二句。第三句。"
        tool = make_text_summarize_tool(_fake_llm)
        r = tool({"text": "很长很长很长" * 100, "max_sentences": 3})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("压缩成 3 句话", called["prompt"])
        self.assertEqual(r["data"]["max_sentences"], 3)

    def test_lang_english(self):
        called = {}
        def _fake_llm(prompt):
            called["prompt"] = prompt
            return "summary."
        tool = make_text_summarize_tool(_fake_llm)
        tool({"text": "lorem", "lang": "en"})
        self.assertIn("Summarize the following", called["prompt"])

    def test_empty_text(self):
        tool = make_text_summarize_tool(lambda p: "x")
        r = tool({"text": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_llm_failure(self):
        def _bad(p): raise RuntimeError("LLM down")
        tool = make_text_summarize_tool(_bad)
        r = tool({"text": "some content"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertTrue(r["retryable"])


class TestCodeLint(unittest.TestCase):

    def test_python_valid(self):
        r = code_lint({"code": "x = 1\ny = x + 2\nprint(y)", "language": "python"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertTrue(r["data"]["valid"])
        self.assertEqual(len(r["data"]["errors"]), 0)

    def test_python_invalid(self):
        r = code_lint({"code": "def f(:\n    return 1", "language": "python"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertFalse(r["data"]["valid"])
        self.assertGreater(len(r["data"]["errors"]), 0)
        self.assertIn("line", r["data"]["errors"][0])

    def test_json_valid(self):
        r = code_lint({"code": '{"a": 1, "b": [1, 2]}', "language": "json"})
        self.assertTrue(r["data"]["valid"])

    def test_json_invalid(self):
        r = code_lint({"code": '{"a": ,}', "language": "json"})
        self.assertFalse(r["data"]["valid"])

    def test_sql_valid(self):
        r = code_lint({"code": "SELECT 1 + 2", "language": "sql"})
        self.assertTrue(r["data"]["valid"])

    def test_sql_invalid(self):
        r = code_lint({"code": "SELEKT FROM lol", "language": "sql"})
        self.assertFalse(r["data"]["valid"])

    def test_unsupported_language(self):
        r = code_lint({"code": "{}", "language": "klingon"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_yaml_valid_or_skipped(self):
        """PyYAML 不一定装, 装了应过, 没装应优雅降级 TOOL_CALL_FAILED"""
        r = code_lint({"code": "a: 1\nb: [1, 2]", "language": "yaml"})
        # SUCCESS (valid:true) 或 TOOL_CALL_FAILED (PyYAML 缺) 都可接受
        if r["code"] == "SUCCESS":
            self.assertTrue(r["data"]["valid"])
        else:
            self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_path_inside_project_ok(self):
        """cwd 内的相对路径: 应读到真实文件并检查 (沙盒放行)。"""
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=str(Path.cwd()), delete=False, encoding="utf-8"
        ) as f:
            f.write("x = 1\ny = x + 2\n")
            rel = Path(f.name).name
        try:
            r = code_lint({"path": rel})
            self.assertEqual(r["code"], "SUCCESS")
            self.assertTrue(r["data"]["valid"])
            self.assertEqual(r["data"]["language"], "python")  # 按 .py 推断
            self.assertTrue(r["data"]["source"].startswith("path:"))
        finally:
            os.unlink(rel)

    def test_path_absolute_escape_rejected(self):
        """项目树外的绝对路径 (任意文件读取) 必须被拒, 不得读盘。"""
        import sys
        # 选一个肯定在 cwd 外的绝对路径
        outside = os.path.join(sys.prefix, "python.exe") if os.name == "nt" else "/etc/passwd"
        r = code_lint({"path": outside})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIsNone(r["data"])

    def test_path_parent_traversal_rejected(self):
        """.. 穿越出项目根必须被拒。"""
        r = code_lint({"path": "../../../../../../etc/passwd"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIsNone(r["data"])


class TestEmailSendTool(unittest.TestCase):

    def test_no_config_returns_unavailable(self):
        tool = make_email_send_tool({})  # 缺所有 required
        r = tool({"to": "a@b.com", "subject": "x", "body": "y"})
        self.assertEqual(r["code"], "SERVICE_UNAVAILABLE")
        self.assertIn("缺字段", r["message"])

    def test_partial_config(self):
        tool = make_email_send_tool({"host": "smtp", "port": 25})  # 缺 from_addr
        r = tool({"to": "a@b.com", "subject": "x", "body": "y"})
        self.assertEqual(r["code"], "SERVICE_UNAVAILABLE")

    def test_full_config_missing_payload_fields(self):
        cfg = {"host": "smtp.test", "port": 25, "from_addr": "me@x.com"}
        tool = make_email_send_tool(cfg)
        r = tool({"to": "a@b.com", "subject": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_smtp_call_mocked(self):
        """完整 mock smtplib.SMTP, 验证 send_message 调到"""
        from unittest.mock import patch, MagicMock
        cfg = {"host": "smtp.test", "port": 25, "from_addr": "me@x.com",
               "use_tls": False, "user": None, "password": None}
        tool = make_email_send_tool(cfg)
        fake_smtp = MagicMock()
        fake_smtp.__enter__ = lambda s: fake_smtp
        fake_smtp.__exit__ = lambda *a: None
        with patch("smtplib.SMTP", return_value=fake_smtp):
            r = tool({"to": "to@b.com", "subject": "Hi", "body": "test"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["to"], ["to@b.com"])
        fake_smtp.send_message.assert_called_once()


class TestImageDescribeTool(unittest.TestCase):

    def test_no_llm_service(self):
        tool = make_image_describe_tool(None)
        r = tool({"image_path": "/x.png"})
        self.assertEqual(r["code"], "SERVICE_UNAVAILABLE")

    def test_no_input(self):
        tool = make_image_describe_tool(MagicMock(call_llm=lambda req: None))
        r = tool({})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_calls_llm_service(self):
        """mock LLMService.call_llm 返回成功 multimodal_result"""
        from llm_adapter_module.model.data_model import LLMResponse, MultimodalResult
        fake_resp = LLMResponse(
            code="SUCCESS", message="ok",
            multimodal_result=MultimodalResult(text_result="A cat sitting on a sofa", confidence=0.9),
            request_info={"model_name": "gpt-4o-mini"},
        )
        fake_svc = MagicMock()
        fake_svc.call_llm.return_value = fake_resp
        tool = make_image_describe_tool(fake_svc)
        r = tool({"image_path": "/cat.png", "prompt": "what's in the image?"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["description"], "A cat sitting on a sofa")
        # 验证 LLMRequest 真的发出去了
        fake_svc.call_llm.assert_called_once()

    def test_llm_returns_error(self):
        from llm_adapter_module.model.data_model import LLMResponse
        fake_svc = MagicMock()
        fake_svc.call_llm.return_value = LLMResponse(code="VECTOR_QUERY_FAILED", message="kaput")
        tool = make_image_describe_tool(fake_svc)
        r = tool({"image_base64": "abc"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")


class TestWeather(unittest.TestCase):

    def test_no_input(self):
        r = weather({})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_offline(self):
        """无网时应返回 TOOL_CALL_FAILED"""
        from unittest.mock import patch
        with patch("agent_module.tools.tools_impl.weather.urllib.request.urlopen",
                   side_effect=ConnectionError("offline")):
            r = weather({"location": "Beijing"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_with_lat_lon_skips_geocoding(self):
        """显式经纬度时不调 geocoding, 直接调 weather"""
        from io import BytesIO
        from unittest.mock import patch, MagicMock
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: fake_resp
        fake_resp.__exit__ = lambda *a: None
        fake_resp.read.return_value = (
            b'{"current_weather": {"temperature": 22.5, "weathercode": 1, '
            b'"windspeed": 5.0, "winddirection": 90, "is_day": 1, "time": "2026-05-27T10:00"}}'
        )
        with patch("agent_module.tools.tools_impl.weather.urllib.request.urlopen", return_value=fake_resp):
            r = weather({"latitude": 39.9, "longitude": 116.4})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["current"]["temperature_celsius"], 22.5)


class TestCurrencyConvert(unittest.TestCase):

    def test_missing_codes(self):
        r = currency_convert({"amount": 100})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_invalid_codes(self):
        r = currency_convert({"from_currency": "DOLLAR", "to_currency": "EUR"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_same_currency(self):
        """同币种应直接返回, 不调网络"""
        r = currency_convert({"from_currency": "CNY", "to_currency": "CNY", "amount": 100})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["converted"], 100)
        self.assertEqual(r["data"]["rate"], 1.0)

    def test_mocked_conversion(self):
        from unittest.mock import patch, MagicMock
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: fake_resp
        fake_resp.__exit__ = lambda *a: None
        fake_resp.read.return_value = b'{"date": "2026-05-27", "rates": {"CNY": 720.5}}'
        with patch("agent_module.tools.tools_impl.currency_convert.urllib.request.urlopen", return_value=fake_resp):
            r = currency_convert({"from_currency": "USD", "to_currency": "CNY", "amount": 100})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["converted"], 720.5)
        self.assertAlmostEqual(r["data"]["rate"], 7.205)


class TestPythonSandbox(unittest.TestCase):

    def test_simple_assign(self):
        r = python_sandbox({"code": "result = 1 + 2"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 3)

    def test_list_comprehension(self):
        r = python_sandbox({"code": "result = [x*x for x in range(5)]"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], [0, 1, 4, 9, 16])

    def test_dict_and_sorted(self):
        r = python_sandbox({"code": "result = sorted({'a':3,'b':1,'c':2}.items(), key=lambda kv: kv[1])"})
        # lambda 不在白名单, 应当被拒
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_for_loop(self):
        r = python_sandbox({"code": """
total = 0
for i in range(10):
    total = total + i
result = total
""".strip()})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 45)

    def test_reject_import(self):
        r = python_sandbox({"code": "import os"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("禁止", r["message"])

    def test_reject_attribute(self):
        r = python_sandbox({"code": "x = (1).bit_length()"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_reject_open(self):
        r = python_sandbox({"code": "f = open('/etc/passwd')"})
        # open 不在白名单
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_reject_dunder(self):
        r = python_sandbox({"code": "x = __name__"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_reject_def(self):
        r = python_sandbox({"code": "def f(x):\n    return x"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_syntax_error(self):
        r = python_sandbox({"code": "1 +"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("语法错误", r["message"])

    def test_runtime_error(self):
        r = python_sandbox({"code": "result = 1 / 0"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertIn("ZeroDivisionError", r["message"])

    def test_empty(self):
        r = python_sandbox({"code": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_trailing_expression_is_result(self):
        # 末尾裸表达式应作为 result 返回 (此前实现忽略它, 只看 locals)
        r = python_sandbox({"code": "a = 5\na + 100"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 105)

    def test_pure_expression_is_result(self):
        r = python_sandbox({"code": "7 * 6"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 42)

    def test_reassigned_var_no_result_returns_none(self):
        # 重赋值 + 无 result 变量 + 无末尾表达式: 不应按 dict 顺序猜测返回错值, 应为 None
        r = python_sandbox({"code": "x = 1\ny = 2\nx = 99"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIsNone(r["data"]["result"])

    def test_trailing_name_reflects_latest_value(self):
        # 末尾引用被重赋值的变量, 应取其最新值 (99), 而非 dict 中某个先插入的变量
        r = python_sandbox({"code": "x = 1\ny = 2\nx = 99\nx"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 99)


class TestToolDescriptions(unittest.TestCase):
    def test_all_15_tools_have_descriptions(self):
        expected = (
            "calculator", "datetime", "wikipedia", "document_read",
            "regex_extract", "text_stats", "json_query", "http_get", "text_summarize",
            "code_lint", "email_send", "image_describe",
            "weather", "currency_convert", "python_sandbox",
        )
        for name in expected:
            self.assertIn(name, TOOL_DESCRIPTIONS, name)
            self.assertGreater(len(TOOL_DESCRIPTIONS[name]), 20)


if __name__ == "__main__":
    unittest.main()
