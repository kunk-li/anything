# -*- coding: utf-8 -*-
"""raw 结构化答案 → 自然语言合成 (Task SSSS) 的 dict-repr 回归。

修复点: _looks_like_raw_json 此前只认严格 JSON (双引号)。当 final_answer 来自
str(dict) 时是 Python repr (单引号), json.loads 解析不了 → 检测漏判 → 合成路径被
跳过 → 原始字典串直喷给用户。现加 ast.literal_eval 兜底 (只解析字面量, 不执行代码)。
"""

import unittest

from agent_module.core.impl import SimpleAgent


class TestLooksLikeRawJson(unittest.TestCase):
    """检测器: JSON 与 Python 字面量两种形态都要识别为 raw。"""

    def test_strict_json_dict(self):
        self.assertTrue(SimpleAgent._looks_like_raw_json('{"a": 1, "b": 2}'))

    def test_strict_json_list(self):
        self.assertTrue(SimpleAgent._looks_like_raw_json("[1, 2, 3]"))

    def test_python_dict_repr_single_quotes(self):
        # 核心回归: str(dict) 出来的单引号字面量, json.loads 解析不了
        self.assertTrue(
            SimpleAgent._looks_like_raw_json(
                "{'1. 稳定复现': '先固定输入', '2. 二分定位': '砍一半'}"
            )
        )

    def test_python_list_repr_single_quotes(self):
        self.assertTrue(SimpleAgent._looks_like_raw_json("['步骤一', '步骤二']"))

    def test_plain_prose_is_not_raw(self):
        self.assertFalse(
            SimpleAgent._looks_like_raw_json("调试建议: 先稳定复现, 再二分定位。")
        )

    def test_brace_prefixed_non_literal_is_not_raw(self):
        # 以 { 开头但不是合法字面量 (普通中文) → 不算 raw, 不应触发合成
        self.assertFalse(
            SimpleAgent._looks_like_raw_json("{这是一段以花括号开头的中文说明}")
        )

    def test_non_string_inputs(self):
        self.assertFalse(SimpleAgent._looks_like_raw_json(None))
        self.assertFalse(SimpleAgent._looks_like_raw_json({"a": 1}))

    def test_empty_string(self):
        self.assertFalse(SimpleAgent._looks_like_raw_json(""))


class TestSynthesizeNaturalAnswer(unittest.TestCase):
    """合成: 有 planner → 返回自然语言; 无 planner → 空串 (调用方保留 raw, fail-open)。"""

    def test_dict_repr_gets_synthesized_via_planner(self):
        captured = {}

        def fake_planner(prompt: str) -> str:
            captured["prompt"] = prompt
            return "调试时先稳定复现问题, 然后二分定位根因。"

        agent = SimpleAgent(llm_planner=fake_planner)
        raw = "{'1. 稳定复现': '先固定输入', '2. 二分定位': '砍一半'}"
        out = agent._synthesize_natural_answer(task="怎么调试?", raw=raw, trace_id="t1")

        self.assertEqual(out, "调试时先稳定复现问题, 然后二分定位根因。")
        # 原始字典内容应进 prompt 供 LLM 参考, 但不作为最终答案直接返回
        self.assertIn("稳定复现", captured["prompt"])

    def test_no_planner_returns_empty(self):
        agent = SimpleAgent()  # 无 llm_planner、无 tool_registry → 无 LLM 通道
        out = agent._synthesize_natural_answer(task="x", raw="{'a': 1}", trace_id=None)
        self.assertEqual(out, "")


class TestAuthoritativeAnswer(unittest.TestCase):
    """authoritative 工具结果 (如 software_info list) 直接作为最终答案, 不经 LLM 复述/合成。"""

    def test_helper_matches_only_authoritative_with_answer(self):
        self.assertIsNone(SimpleAgent._authoritative_answer(None))
        self.assertIsNone(SimpleAgent._authoritative_answer([]))
        # 有 answer 但未标 authoritative → 不命中
        self.assertIsNone(SimpleAgent._authoritative_answer(
            [{"output": {"data": {"answer": "x"}}}]))
        # authoritative + answer → 命中
        self.assertEqual(SimpleAgent._authoritative_answer(
            [{"output": {"data": {"authoritative": True, "answer": "命中"}}}]), "命中")
        # 多个命中取最后一个
        self.assertEqual(SimpleAgent._authoritative_answer([
            {"output": {"data": {"authoritative": True, "answer": "first"}}},
            {"output": {"data": {"authoritative": True, "answer": "last"}}},
        ]), "last")

    def test_aggregate_uses_authoritative_full_text(self):
        # 关键回归: 长清单(>2000 字)经 authoritative 完整直达, 绕过 _summarize 的 _LIMIT 与 LLM 复述
        agent = SimpleAgent()  # 无 llm_planner: 若误走 LLM 合成只会拿到空
        long_answer = "\n".join(f"{i}. 某软件{i:04d} 版本 {i}.0.0" for i in range(200))
        self.assertGreater(len(long_answer), 2000)
        tool_results = [{
            "tool_name": "software_info", "success": True,
            "output": {"code": "SUCCESS", "data": {
                "authoritative": True, "answer": long_answer,
                "software": [{"name": f"某软件{i:04d}"} for i in range(200)],
            }},
        }]
        out = agent.aggregate_results(
            task="列出已安装软件", session_id="s1", trace_id="t1",
            tool_results=tool_results, execution_mode="agent",
        )
        self.assertEqual(out["answer"], long_answer)   # 完整, 未被截断


if __name__ == "__main__":
    unittest.main()
