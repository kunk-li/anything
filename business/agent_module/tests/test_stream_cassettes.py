# -*- coding: utf-8 -*-
"""VCR 流式回放 (执行计划⑤): 用录制的真实 LLM 输出确定性回放 run_stream, 抓流式路径漂移。

cassettes/*.json 由 scripts/record_cassette.py 拿真 LLM 录制 (responses=每次 LLM 原始输出)。
本测试在 CI (无 LLM) 每次 push 跑: 把 responses 当 llm_planner 依次回放 → run_stream → 断言
末事件/答案/工具与录制一致。相同 LLM 输出却产出不同答案 = 流式逻辑漂移 (本会话大半 bug 这类),
DummyLLM 抓不到。修改流式逻辑致 cassette 失败时: 确认是预期改进则重录 (record_cassette.py)。
"""
import glob
import json
import os
import unittest

from agent_module.core.impl import SimpleAgent

CASSETTE_DIR = os.path.join(os.path.dirname(__file__), "cassettes")


class _ReplayLLM:
    """按录制顺序回放 LLM 输出 (耗尽返 '', 同 _ScriptedLLM)。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.i = 0

    def __call__(self, prompt: str) -> str:
        if self.i < len(self.responses):
            r = self.responses[self.i]
            self.i += 1
            return r
        return ""


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


def _stub_tool(payload):
    return {"code": "SUCCESS", "message": "ok", "data": {"text": "stub_result"}}


def _load_cassettes():
    out = []
    for p in sorted(glob.glob(os.path.join(CASSETTE_DIR, "*.json"))):
        with open(p, encoding="utf-8") as f:
            out.append(json.load(f))
    return out


class TestStreamCassettes(unittest.TestCase):
    def _agent(self, cassette):
        reg = _Reg()
        reg.register("llm_generate", _stub_tool)
        for t in cassette.get("tools_seen", []):
            reg.register(t, _stub_tool)
        agent = SimpleAgent(tool_registry=reg, llm_planner=_ReplayLLM(cassette["responses"]))
        agent.execution_strategy = "react"   # 走流式 ReAct (纯 planner 路径, 不碰 chat_stream)
        agent.enable_self_verify = False
        agent.use_llm_planner = False
        agent.tool_approval_required = set()
        return agent

    def test_cassettes_present(self):
        self.assertTrue(_load_cassettes(), "无 cassette; 先跑 scripts/record_cassette.py 录制")

    def test_replay_matches_recording(self):
        for c in _load_cassettes():
            with self.subTest(cassette=c["name"]):
                agent = self._agent(c)
                events = list(agent.run_stream({
                    "task": c["task"], "session_id": f"replay_{c['name']}",
                    "trace_id": f"replay_{c['name']}",
                    "extra_params": c.get("extra_params", {}),
                }))
                answer = "".join(e.get("text", "") for e in events if e.get("type") == "chunk")
                # 1. 末事件类型与录制一致 (没回退到 error / 没提前中断)
                self.assertTrue(events, f"{c['name']}: 无事件产出")
                self.assertEqual(events[-1].get("type"), c["event_types"][-1],
                                 f"{c['name']}: 末事件漂移 (录={c['event_types'][-1]})")
                # 2. 答案非空
                self.assertTrue(answer.strip(), f"{c['name']}: 空答案")
                # 3. 回归核心: 相同 LLM 输出 → 相同流式答案 (前缀对齐录制, 漂移即失败)
                preview = c.get("_recorded_answer_preview", "")
                if preview:
                    self.assertEqual(answer[:len(preview)], preview,
                                     f"{c['name']}: 流式答案漂移 (相同 LLM 输出却产出不同答案)")
                # 4. 工具路径与录制一致
                replay_tools = sorted({e.get("tool_name") for e in events
                                       if e.get("type") in ("action", "observation") and e.get("tool_name")})
                self.assertEqual(replay_tools, c.get("tools_seen", []),
                                 f"{c['name']}: 工具调用漂移")


if __name__ == "__main__":
    unittest.main()
