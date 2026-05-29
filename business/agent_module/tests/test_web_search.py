# -*- coding: utf-8 -*-
"""
web_search 工具测试 (Task HHH #94).

不依赖真实网络 — 全部用 patch 替 urlopen, 验证:
    - 参数校验 (空 query 返 PARAM_MISSING)
    - DuckDuckGo HTML 解析 (title / url / snippet 抽取)
    - DDG URL unwrap (/l/?uddg=<encoded>)
    - 失败时 fallback 到 wikipedia
    - 全部失败时返 TOOL_CALL_FAILED
"""
import io
import unittest
from unittest.mock import patch, MagicMock

from agent_module.tools.tools_impl.web_search import (
    web_search, _duckduckgo_search, _unwrap_ddg_url, _strip_html,
)


def _ddg_html(*results):
    """生成 DDG HTML 片段, 每条 results = (href, title, snippet)"""
    blocks = []
    for href, title, snippet in results:
        blocks.append(
            f'<div class="result__body">'
            f'<a class="result__a" href="{href}">{title}</a>'
            f'<a class="result__snippet">{snippet}</a>'
            f'</div>'
        )
    return "<html><body>" + "\n".join(blocks) + "</body></html>"


class TestWebSearchParams(unittest.TestCase):

    def test_empty_query(self):
        r = web_search({"query": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_query_whitespace_only(self):
        r = web_search({"query": "   "})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestUnwrapDdgUrl(unittest.TestCase):

    def test_unwrap_l_redirect(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpath&rut=xxx"
        self.assertEqual(_unwrap_ddg_url(href), "https://example.com/path")

    def test_unwrap_direct_url(self):
        href = "https://example.com/page"
        self.assertEqual(_unwrap_ddg_url(href), "https://example.com/page")

    def test_unwrap_empty(self):
        self.assertEqual(_unwrap_ddg_url(""), "")

    def test_unwrap_with_uddg_short(self):
        href = "//duckduckgo.com/l/?u=https%3A%2F%2Ffoo.com&rut=xx"
        # 'u' 参数也能 unwrap (fallback)
        self.assertEqual(_unwrap_ddg_url(href), "https://foo.com")


class TestStripHtml(unittest.TestCase):

    def test_strip_tags(self):
        self.assertEqual(_strip_html("<b>hello</b> <i>world</i>"), "hello world")

    def test_unescape_entities(self):
        self.assertEqual(_strip_html("Tom &amp; Jerry"), "Tom & Jerry")
        self.assertEqual(_strip_html("&quot;hi&quot;"), '"hi"')

    def test_collapse_whitespace(self):
        self.assertEqual(_strip_html("a  \n\t b"), "a b")

    def test_empty(self):
        self.assertEqual(_strip_html(""), "")


class TestDuckDuckGoSearchParsing(unittest.TestCase):

    def _mock_resp(self, body: str):
        fake = MagicMock()
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda *a: None
        fake.read.return_value = body.encode("utf-8")
        return fake

    def test_parses_three_results(self):
        html = _ddg_html(
            ("https://example.com/a", "Title A", "Snippet A"),
            ("https://example.com/b", "Title B", "Snippet B"),
            ("https://example.com/c", "Title C", "Snippet C"),
        )
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            return_value=self._mock_resp(html),
        ):
            results = _duckduckgo_search("test", top_k=10, lang="en")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["title"], "Title A")
        self.assertEqual(results[0]["url"], "https://example.com/a")
        self.assertEqual(results[0]["snippet"], "Snippet A")

    def test_top_k_caps_results(self):
        html = _ddg_html(
            *[(f"https://e.com/{i}", f"T{i}", f"S{i}") for i in range(10)]
        )
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            return_value=self._mock_resp(html),
        ):
            results = _duckduckgo_search("x", top_k=3)
        self.assertEqual(len(results), 3)

    def test_unwraps_ddg_redirect_in_results(self):
        wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Freal.com%2Fpage&xxx"
        html = _ddg_html((wrapped, "Real Title", "snippet"))
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            return_value=self._mock_resp(html),
        ):
            results = _duckduckgo_search("test")
        self.assertEqual(results[0]["url"], "https://real.com/page")

    def test_snippet_truncated_300(self):
        long_snip = "x" * 500
        html = _ddg_html(("https://e.com", "T", long_snip))
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            return_value=self._mock_resp(html),
        ):
            results = _duckduckgo_search("x")
        self.assertEqual(len(results[0]["snippet"]), 300)

    def test_no_results_in_html(self):
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            return_value=self._mock_resp("<html><body>No results.</body></html>"),
        ):
            results = _duckduckgo_search("x")
        self.assertEqual(results, [])


class TestWebSearchEndToEnd(unittest.TestCase):

    def _mock_resp(self, body: str):
        fake = MagicMock()
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda *a: None
        fake.read.return_value = body.encode("utf-8")
        return fake

    def test_success_returns_results(self):
        html = _ddg_html(
            ("https://example.com/page", "Example", "An example snippet"),
        )
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            return_value=self._mock_resp(html),
        ):
            r = web_search({"query": "example", "top_k": 5})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["source"], "duckduckgo")
        self.assertEqual(r["data"]["count"], 1)
        self.assertEqual(r["data"]["results"][0]["title"], "Example")

    def test_ddg_failure_falls_back_to_wikipedia(self):
        """DDG 抛异常 → fallback wikipedia_tool."""
        # 1) mock DDG urlopen 抛
        # 2) mock wikipedia urlopen 给一个成功的 search + summary
        wiki_search_resp = b'["x", ["Cat"], [], []]'  # opensearch returns [query, titles, ...]
        wiki_sum_resp = b'{"extract": "A cat is a small mammal", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Cat"}}}'

        wiki_responses = iter([
            self._mock_resp_bytes(wiki_search_resp),
            self._mock_resp_bytes(wiki_sum_resp),
        ])

        def _urlopen_side(*args, **kwargs):
            # 第一次调 (DDG) 抛, 后面调 (wikipedia) 走 wiki_responses
            req = args[0] if args else kwargs.get("url")
            url = getattr(req, "full_url", "") if hasattr(req, "full_url") else str(req)
            if "duckduckgo" in url:
                raise ConnectionError("DDG offline")
            return next(wiki_responses)

        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            side_effect=_urlopen_side,
        ), patch(
            "agent_module.tools.tools_impl.wikipedia.urllib.request.urlopen",
            side_effect=_urlopen_side,
        ):
            r = web_search({"query": "Cat", "lang": "en"})

        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["source"], "wikipedia_fallback")
        self.assertEqual(r["data"]["count"], 1)
        self.assertEqual(r["data"]["results"][0]["title"], "Cat")
        # fallback_reason 应该提到 DDG 的错
        self.assertIn("duckduckgo", r["data"]["fallback_reason"])

    def _mock_resp_bytes(self, body_bytes: bytes):
        fake = MagicMock()
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda *a: None
        fake.read.return_value = body_bytes
        return fake

    def test_both_fail_returns_tool_call_failed(self):
        """DDG + wikipedia 都失败 → TOOL_CALL_FAILED."""
        with patch(
            "agent_module.tools.tools_impl.web_search.urllib.request.urlopen",
            side_effect=ConnectionError("DDG down"),
        ), patch(
            "agent_module.tools.tools_impl.wikipedia.urllib.request.urlopen",
            side_effect=ConnectionError("wiki down"),
        ):
            r = web_search({"query": "test"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertTrue(r["retryable"])


if __name__ == "__main__":
    unittest.main()
