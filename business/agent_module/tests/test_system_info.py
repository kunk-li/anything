# -*- coding: utf-8 -*-
"""system_info 工具 (只读看本机状态): 注入 fake backend, 不碰真机。

验证: overview 综合 / 各 action 分发 / 默认 action / 坏 action / psutil 未装降级 / 后端异常。
只读 → 不进审批白名单 (该断言在工厂/agent 侧, 这里只测工具本身)。
"""
import unittest

from agent_module.tools.tools_impl.system_info import make_system_info_tool


class _FakeBackend:
    def cpu(self):
        return {"percent": 12.5, "per_cpu_percent": [10.0, 15.0],
                "logical_cores": 8, "physical_cores": 4}

    def memory(self):
        return {"total_gb": 16.0, "used_gb": 8.0, "available_gb": 8.0,
                "percent": 50.0, "swap_total_gb": 4.0, "swap_percent": 0.0}

    def disk(self):
        return [{"device": "C:", "mountpoint": "C:\\", "fstype": "NTFS",
                 "total_gb": 500.0, "used_gb": 200.0, "percent": 40.0}]

    def network(self):
        return {"bytes_sent_mb": 100.0, "bytes_recv_mb": 200.0,
                "packets_sent": 1000, "packets_recv": 2000}

    def processes(self, top=8):
        rows = [{"pid": 1, "name": "proc", "cpu_percent": 5.0, "memory_percent": 3.0}]
        return rows[:top]

    def boot_time(self):
        return 0.0


class TestSystemInfo(unittest.TestCase):
    def _tool(self, be=None):
        return make_system_info_tool(backend=be or _FakeBackend())

    def test_overview_has_all_sections(self):
        r = self._tool()({"action": "overview"})
        self.assertEqual(r["code"], "SUCCESS")
        for k in ("platform", "cpu", "memory", "disk", "network", "top_processes"):
            self.assertIn(k, r["data"])
        self.assertEqual(r["data"]["memory"]["percent"], 50.0)

    def test_default_action_is_overview(self):
        r = self._tool()({})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("cpu", r["data"])

    def test_each_action(self):
        t = self._tool()
        self.assertEqual(t({"action": "cpu"})["data"]["logical_cores"], 8)
        self.assertEqual(t({"action": "memory"})["data"]["percent"], 50.0)
        self.assertEqual(t({"action": "disk"})["data"]["partitions"][0]["device"], "C:")
        self.assertEqual(t({"action": "network"})["data"]["bytes_recv_mb"], 200.0)
        self.assertEqual(t({"action": "processes", "top": 1})["data"]["top_processes"][0]["pid"], 1)

    def test_invalid_action(self):
        self.assertEqual(self._tool()({"action": "nuke"})["code"], "PARAM_INVALID")

    def test_missing_psutil_degrades(self):
        class _NoDep:
            def cpu(self):
                raise ImportError("no psutil")
        r = make_system_info_tool(backend=_NoDep())({"action": "cpu"})
        self.assertEqual(r["code"], "MISSING_DEPS")

    def test_backend_error_toolfailed(self):
        class _Boom:
            def cpu(self):
                raise RuntimeError("boom")
        r = make_system_info_tool(backend=_Boom())({"action": "cpu"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertTrue(r["retryable"])


if __name__ == "__main__":
    unittest.main()
