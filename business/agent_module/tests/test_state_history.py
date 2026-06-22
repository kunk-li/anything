# -*- coding: utf-8 -*-
"""StateHistoryMixin 回归测试 (state_history.py 的 review fix):

findings:
  #1 _load_history 先 events[-N:] 截断再按 role 过滤 → ReAct 状态事件挤掉对话轮,
     多轮记忆静默丢失。修后应先按 role 过滤再截断。
  #2/#3 _save_state_safe 用 get_state + 全量 save_state 做读-改-写, 与并发
     append_event lost-update。修后走 append_event (事件链) + merge_state (标量),
     两条路径同把 per-session 锁下不互相覆盖。
"""

import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import tempfile
import threading
import unittest
from unittest.mock import MagicMock

from agent_module.core.impl import SimpleAgent
from state_store_module.core.impl import LocalStateStore


class TestStateHistoryMixin(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agent_state_hist_")
        self.store = LocalStateStore(store_dir=self.temp_dir)
        self.agent = SimpleAgent(
            state_store=self.store,
            tool_registry=MagicMock(),
            timeout=10,
            max_retries=1,
            session_prefix="test",
        )

    def tearDown(self):
        for root, _, files in os.walk(self.temp_dir):
            for fn in files:
                try:
                    os.remove(os.path.join(root, fn))
                except Exception:
                    pass
        try:
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def test_load_history_not_crowded_out_by_state_events(self):
        """finding #1: 对话 role 事件被大量 ReAct 状态事件包围时, _load_history
        仍应返回这些对话轮 (先过滤再截断), 而不是被状态事件挤出截断窗口。"""
        sid = "sess_hist_filter"
        # 一对真实对话 role 事件
        self.store.append_event(sid, {"role": "user", "content": "第一个问题"})
        self.store.append_event(sid, {"role": "assistant", "content": "第一个回答"})
        # 之后 ReAct 跑了一堆状态事件 (无 role 字段), 远超 max_turns*2 窗口
        for i in range(50):
            self.store.append_event(sid, {"event_type": "tool_call", "i": i})

        history = self.agent._load_history(sid, max_turns=6)
        # 修前: events[-12:] 全是 tool_call, 过滤后 history 为空 (金鱼记忆)
        # 修后: 先过滤出 2 条 role 消息再截断 → 应拿到这 2 条
        self.assertEqual(
            [m["content"] for m in history],
            ["第一个问题", "第一个回答"],
        )

    def test_load_history_truncates_role_messages_to_max(self):
        """过滤后再截断: role 消息超过 max_turns*2 时取最近的。"""
        sid = "sess_hist_trunc"
        for i in range(10):
            self.store.append_event(sid, {"role": "user", "content": f"u{i}"})
            self.store.append_event(sid, {"role": "assistant", "content": f"a{i}"})
        history = self.agent._load_history(sid, max_turns=2)  # max_msgs=4
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["content"], "u8")
        self.assertEqual(history[-1]["content"], "a9")

    def test_save_state_safe_appends_role_events_and_merges_scalars(self):
        """finding #2/#3: 多轮 _save_state_safe 应累积 events (不覆盖前轮),
        顶层标量被 merge_state 更新 (保留 events)。"""
        sid = "sess_save_merge"
        self.agent._save_state_safe(
            sid, {"status": "completed", "task": "Q1", "answer": "A1",
                  "execution_mode": "agent"}, trace_id="t1")
        self.agent._save_state_safe(
            sid, {"status": "completed", "task": "Q2", "answer": "A2",
                  "execution_mode": "agent"}, trace_id="t2")

        state = self.store.get_state(sid)
        role_msgs = [(e["role"], e["content"]) for e in state["events"]
                     if isinstance(e, dict) and e.get("role")]
        # 两轮 = 4 条 role 事件, 前轮未被覆盖
        self.assertEqual(
            role_msgs,
            [("user", "Q1"), ("assistant", "A1"), ("user", "Q2"), ("assistant", "A2")],
        )
        # 顶层标量是最后一轮的值
        self.assertEqual(state["task"], "Q2")
        self.assertEqual(state["answer"], "A2")
        self.assertEqual(state["status"], "completed")

    def test_save_state_safe_concurrent_no_lost_events(self):
        """finding #2/#3 回归: 并发 _save_state_safe (走 append_event) 不丢事件。
        旧 get_state+save_state 读-改-写在此处会丢。"""
        sid = "sess_save_concurrent"
        n_threads = 8

        def worker(tid):
            self.agent._save_state_safe(
                sid, {"status": "completed", "task": f"Q{tid}",
                      "answer": f"A{tid}", "execution_mode": "agent"},
                trace_id=f"t{tid}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        state = self.store.get_state(sid)
        role_events = [e for e in state["events"]
                       if isinstance(e, dict) and e.get("role")]
        # 每个线程 append 2 条 (user+assistant), 一条都不能丢
        self.assertEqual(len(role_events), n_threads * 2)


if __name__ == "__main__":
    unittest.main()
