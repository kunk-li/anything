# -*- coding: utf-8 -*-
"""
Task XXXX-19 (#164): _sanitize_api_base 防御性测试.

只覆盖纯函数, 不依赖网络 / 真实 adapter 构造.
"""
import os
import unittest
from unittest.mock import patch

from llm_adapter_module.core.adapters._http_mixin import _sanitize_api_base


class TestSanitizeApiBase(unittest.TestCase):

    def test_valid_url_passes_through(self):
        url = "https://api.openai.com/v1"
        self.assertEqual(_sanitize_api_base(url), url)

    def test_valid_url_trailing_slash_stripped(self):
        self.assertEqual(_sanitize_api_base("https://api.openai.com/v1/"), "https://api.openai.com/v1")

    def test_empty_falls_back_to_openai_default(self):
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("")
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_none_falls_back(self):
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base(None)
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_undefined_literal_string_falls_back(self):
        # 这就是 user 报的 bug — JS undefined 被字符串化成 "undefined" 串到 backend
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("undefined")
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_https_undefined_falls_back(self):
        # 拼出来的 "https://undefined" 也得拦
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("https://undefined")
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_null_literal_falls_back(self):
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("null")
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_no_scheme_falls_back(self):
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("api.openai.com/v1")
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_qwen_model_uses_dashscope_default(self):
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("undefined", model_name="qwen-max")
            self.assertEqual(r, "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_qwen_model_with_dashscope_env(self):
        with patch.dict(os.environ, {
            "OPENAI_API_BASE": "",
            "DASHSCOPE_API_BASE": "https://my-dashscope-proxy.example.com/v1",
        }, clear=False):
            r = _sanitize_api_base("", model_name="qwen-turbo")
            self.assertEqual(r, "https://my-dashscope-proxy.example.com/v1")

    def test_env_openai_takes_precedence(self):
        with patch.dict(os.environ, {
            "OPENAI_API_BASE": "https://my-openai-proxy.example.com/v1",
        }, clear=False):
            r = _sanitize_api_base("undefined")
            self.assertEqual(r, "https://my-openai-proxy.example.com/v1")

    def test_whitespace_only_falls_back(self):
        with patch.dict(os.environ, {"OPENAI_API_BASE": "", "DASHSCOPE_API_BASE": ""}, clear=False):
            r = _sanitize_api_base("   \t  ")
            self.assertEqual(r, "https://api.openai.com/v1")

    def test_http_localhost_preserved(self):
        # localhost 本地开发用 (Ollama 等), 不该被当垃圾干掉
        url = "http://localhost:11434/v1"
        self.assertEqual(_sanitize_api_base(url), url)


if __name__ == "__main__":
    unittest.main()
