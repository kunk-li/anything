# -*- coding: utf-8 -*-
"""
AuditLogger 单元测试 (Task CC #63)
"""

import json
import tempfile
import unittest
from pathlib import Path

from common_utils_module import (
    AuditLogger,
    configure_audit_logger,
    get_audit_logger,
    get_hook_registry,
    install_audit_hooks,
    reset_audit_logger,
    reset_hook_registry,
)


class TestAuditLogger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        reset_audit_logger()
        reset_hook_registry()

    def tearDown(self):
        # 先关单例句柄 (持久 append handle), 再删临时目录 —— Windows 不能删还开着的文件
        reset_audit_logger()
        reset_hook_registry()
        self.tmp.cleanup()

    def test_write_appends_jsonl(self):
        f = self.tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(f))
        self.assertTrue(logger.write({"event": "tool_call", "tool": "x"}))
        self.assertTrue(logger.write({"event": "llm_call", "model": "qwen-turbo"}))
        lines = f.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["tool"], "x")
        self.assertEqual(json.loads(lines[1])["model"], "qwen-turbo")

    def test_write_unicode_safe(self):
        f = self.tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(f))
        logger.write({"event": "x", "msg": "中文测试 🚀"})
        content = f.read_text(encoding="utf-8")
        self.assertIn("中文测试", content)
        self.assertIn("🚀", content)

    def test_write_handles_unserializable(self):
        """非 JSON-serializable 值 (e.g. dataclass) 应被 default=str 兜底, 不抛错"""
        f = self.tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(f))
        class _Foo: pass
        # default=str → 转成 "<class>" 字符串, write 仍 True
        self.assertTrue(logger.write({"event": "x", "obj": _Foo()}))

    def test_write_silent_on_oserror(self):
        """文件无法写时 write 返回 False, 不抛"""
        # 故意指向一个非法路径 — Windows / Linux 都不允许 ASCII 控制字符
        logger = AuditLogger(path="\x00/illegal/path/audit.jsonl")
        self.assertFalse(logger.write({"event": "x"}))

    def test_rotate_when_max_bytes_exceeded(self):
        f = self.tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(f), max_bytes=200, backup_count=2)
        # 写 10 条, 每条约 30 字节 → 触发滚动
        for i in range(10):
            logger.write({"event": "x", "i": i, "padding": "a" * 20})
        # 文件存在
        self.assertTrue(f.exists())
        # 滚动备份也应存在 (.1)
        backup1 = f.with_suffix(f.suffix + ".1")
        self.assertTrue(backup1.exists())

    def test_snapshot(self):
        f = self.tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(f), max_bytes=12345, backup_count=3)
        logger.write({"event": "a"})
        logger.write({"event": "b"})
        snap = logger.snapshot()
        self.assertEqual(snap["path"], str(f))
        self.assertEqual(snap["max_bytes"], 12345)
        self.assertEqual(snap["backup_count"], 3)
        self.assertEqual(snap["writes_this_process"], 2)
        self.assertGreater(snap["current_size_bytes"], 0)

    def test_close_then_write_reopens(self):
        """持久句柄: close() 后再 write 自动重开, 不丢记录 (lifecycle)。"""
        f = self.tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(f))
        self.assertTrue(logger.write({"event": "a"}))
        logger.close()
        self.assertTrue(logger.write({"event": "b"}))  # 句柄已关 → 自动重开
        lines = f.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        logger.close()


class TestInstallAuditHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        reset_audit_logger()
        reset_hook_registry()

    def tearDown(self):
        # 先关单例句柄 (持久 append handle), 再删临时目录 —— Windows 不能删还开着的文件
        reset_audit_logger()
        reset_hook_registry()
        self.tmp.cleanup()

    def test_install_registers_4_hooks(self):
        install_audit_hooks(path=str(self.tmp_path / "audit.jsonl"))
        counts = get_hook_registry().count()
        self.assertEqual(counts["pre_tool_call"], 1)
        self.assertEqual(counts["post_tool_call"], 1)
        self.assertEqual(counts["pre_llm_call"], 1)
        self.assertEqual(counts["post_llm_call"], 1)

    def test_fire_writes_audit_lines(self):
        f = self.tmp_path / "audit.jsonl"
        install_audit_hooks(path=str(f))
        reg = get_hook_registry()
        # 模拟 SimpleAgent 内的 hook fire
        reg.fire("pre_tool_call", "rag_search", {"q": "hi"}, {"trace_id": "t1", "tenant_id": "T1"})
        reg.fire("post_tool_call", "rag_search", {"q": "hi"}, {"code": "SUCCESS"}, {"trace_id": "t1"})
        reg.fire("pre_llm_call", "prompt body", "qwen-turbo", {"trace_id": "t1"})
        reg.fire("post_llm_call", "prompt", "qwen-turbo", "answer", {"trace_id": "t1", "cost_usd": 0.001})
        lines = f.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 4)
        events = [json.loads(ln)["event"] for ln in lines]
        self.assertEqual(events, [
            "tool_call_started", "tool_call_finished",
            "llm_call_started", "llm_call_finished",
        ])
        # 关键字段验证
        rec_llm = json.loads(lines[3])
        self.assertEqual(rec_llm["cost_usd"], 0.001)
        self.assertEqual(rec_llm["response_chars"], len("answer"))

    def test_audit_does_not_log_secrets(self):
        """pre_tool_call 只记 input keys, 不记 values"""
        f = self.tmp_path / "audit.jsonl"
        install_audit_hooks(path=str(f))
        reg = get_hook_registry()
        reg.fire("pre_tool_call", "email_send",
                 {"to": "user@x.com", "password": "secret123", "body": "hi"},
                 {"trace_id": "t1"})
        content = f.read_text(encoding="utf-8")
        self.assertNotIn("secret123", content)
        self.assertNotIn("user@x.com", content)
        self.assertIn("password", content)  # key 名 OK
        self.assertIn("input_keys", content)


if __name__ == "__main__":
    unittest.main()
