# -*- coding: utf-8 -*-
"""VCR 录制器 (执行计划⑤): 拿真实 LLM 跑代表任务, 录下每次 LLM 输出成"磁带"(cassette),
供主 CI 用 _ReplayLLM 确定性回放 run_stream (不花钱、抓流式漂移; 见 test_stream_cassettes.py)。

用法 (需 .env 里 DASHSCOPE_API_KEY 真 key):
    PYTHONPATH=... python scripts/record_cassette.py
录到 business/agent_module/tests/cassettes/*.json。每条 cassette:
    {name, task, extra_params, responses:[LLM 原始输出...], events:[流式事件类型摘要], asserts:{...}}
录制原理: 包 llm_client.generate (run_stream 的 react 规划/最终答案都走它), 顺序记录输出;
回放时把 responses 当 llm_planner 依次返回即可复现 (LLM 不看 prompt → 工具用 stub 也一致)。
"""
from __future__ import annotations

import io
import json
import os
import sys

# 录哪些代表任务 (覆盖流式 react 关键形态)
TASKS = [
    {"name": "calc_multitool", "task": "现在几点了? 顺便算一下 100 乘以 200 等于几"},
    {"name": "direct_answer", "task": "用一句话解释什么是 RAG"},
    {"name": "debug_methodology", "task": "我的程序老报一个错, 给我一套系统的排查思路"},
]
OUT_DIR = os.path.join("business", "agent_module", "tests", "cassettes")


def main() -> int:
    from bootstrap import build_business_layer
    b = build_business_layer()
    agent = b["agent"]
    llm_client = getattr(agent, "llm_client", None)
    if llm_client is None or not hasattr(llm_client, "generate"):
        print("FAIL: 无真实 llm_client.generate, 无法录制")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    real_generate = llm_client.generate

    for spec in TASKS:
        recorded: list = []

        def _rec(prompt, trace_id=None, *a, **k):
            out = real_generate(prompt, trace_id, *a, **k)
            recorded.append(out if isinstance(out, str) else str(out))
            return out

        llm_client.generate = _rec
        try:
            events = list(agent.run_stream({
                "task": spec["task"], "session_id": f"rec_{spec['name']}",
                "trace_id": f"rec_{spec['name']}",
                "extra_params": {"execution_strategy": "react"},
            }))
        finally:
            llm_client.generate = real_generate

        answer = "".join(e.get("text", "") for e in events if e.get("type") == "chunk")
        tools = [e.get("tool_name") for e in events if e.get("type") in ("action", "observation")]
        cassette = {
            "name": spec["name"], "task": spec["task"],
            "extra_params": {"execution_strategy": "react"},
            "responses": recorded,
            "event_types": [e.get("type") for e in events],
            "tools_seen": sorted({t for t in tools if t}),
            "asserts": {"no_error": True, "nonempty": True, "not_raw_json": True},
            "_recorded_answer_preview": answer[:160],
        }
        path = os.path.join(OUT_DIR, f"{spec['name']}.json")
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(cassette, f, ensure_ascii=False, indent=2)
        print(f"[rec] {spec['name']}: {len(recorded)} LLM 调用, {len(events)} 事件 -> {path}")
        print(f"      answer: {answer[:80]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
