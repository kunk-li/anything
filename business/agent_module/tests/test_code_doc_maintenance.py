# -*- coding: utf-8 -*-
"""方向4 扩域·代码文档自维护: 只读扫描 → advisory 维护清单 (不自动改代码/文档)。"""
import os
import tempfile
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.core.components.self_reflection import (
    scan_code_doc_health, propose_from_code_doc_signals,
)
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


def _make_tree(root):
    os.makedirs(os.path.join(root, "business", "foo_module"))      # 缺 README
    os.makedirs(os.path.join(root, "business", "bar_module"))      # 有 README
    with open(os.path.join(root, "business", "bar_module", "README.md"), "w", encoding="utf-8") as f:
        f.write("# bar")
    with open(os.path.join(root, "business", "foo_module", "impl.py"), "w", encoding="utf-8") as f:
        f.write("# TODO: x\nx = 1  # FIXME later\n")
    os.makedirs(os.path.join(root, "run"))                         # _SKIP_DIRS 应被跳过
    with open(os.path.join(root, "run", "junk.py"), "w", encoding="utf-8") as f:
        f.write("# TODO ignored\n")


class TestScanCodeDoc(unittest.TestCase):
    def test_scan_missing_readme_and_todos(self):
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            s = scan_code_doc_health(root)
            self.assertIn("business/foo_module", s["modules_missing_readme"])
            self.assertNotIn("business/bar_module", s["modules_missing_readme"])
            self.assertEqual(s["todo_total"], 2)        # foo_module/impl.py 2 处; run/ 跳过
            self.assertTrue(any("foo_module/impl.py" in t["file"] for t in s["todo_top_files"]))


class TestProposeCodeDoc(unittest.TestCase):
    def test_proposals_advisory(self):
        props = propose_from_code_doc_signals(
            {"modules_missing_readme": ["a/x_module", "b/y_module"], "todo_total": 7,
             "todo_top_files": [{"file": "f.py", "count": 7}]})
        self.assertEqual(len(props), 2)
        self.assertTrue(all(p.action_type == "investigate" for p in props))  # 全 advisory
        self.assertIn("缺 README", props[0].problem)

    def test_clean_no_proposals(self):
        props = propose_from_code_doc_signals(
            {"modules_missing_readme": [], "todo_total": 0, "todo_top_files": []})
        self.assertEqual(props, [])


class TestAgentCodeDocMaintenance(unittest.TestCase):
    def _agent(self, enable=True):
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=None,
                        long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()))
        a.enable_self_reflection = enable
        return a

    def test_disabled_gate(self):
        self.assertFalse(self._agent(enable=False).propose_code_doc_maintenance()["enabled"])

    def test_scan_temp_root(self):
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            r = self._agent().propose_code_doc_maintenance(root=root)
            self.assertTrue(r["enabled"])
            self.assertGreaterEqual(len(r["proposals"]), 1)
            self.assertIn("advisory", r["note"])           # advisory-only, 不自动执行

    def test_resolve_project_root_finds_repo(self):
        # 真实仓库根应能定位到 (含 business 层目录)
        root = self._agent()._resolve_project_root()
        self.assertTrue(root and os.path.isdir(os.path.join(root, "business")))


if __name__ == "__main__":
    unittest.main()
