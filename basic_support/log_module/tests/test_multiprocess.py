import logging
import os
import sys
import time
import unittest
import multiprocessing

from log_module.core.impl import SystemLogger
from log_module.utils import get_log_file_name


class TestSystemLoggerMultiprocess(unittest.TestCase):
    def setUp(self):
        self.logger = SystemLogger()
        self.log_file = os.path.join(self.logger.log_dir, get_log_file_name())
        # Windows 下 logging.FileHandler 持有文件句柄, 直接 remove 会撞 WinError 32.
        # 先关闭现有 handler 再尝试 remove + 短重试 (其他进程可能还没 release).
        self._close_root_handlers()
        for _ in range(5):
            try:
                if os.path.exists(self.log_file):
                    os.remove(self.log_file)
                break
            except PermissionError:
                time.sleep(0.2)
        else:
            self.skipTest(f"log file held by another process: {self.log_file}")

    @staticmethod
    def _close_root_handlers():
        """关闭所有 FileHandler 让 Windows 释放文件锁"""
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler):
                try:
                    h.close()
                except Exception:
                    pass
                root.removeHandler(h)
        # 同时关闭具名 logger (rag_agent_system_*) 上的 handler
        for name in list(logging.Logger.manager.loggerDict):
            lg = logging.getLogger(name)
            for h in list(getattr(lg, "handlers", [])):
                if isinstance(h, logging.FileHandler):
                    try:
                        h.close()
                    except Exception:
                        pass
                    lg.removeHandler(h)

    def tearDown(self):
        # 主动释放, 让后续测试或 cleanup 不再撞文件锁
        self._close_root_handlers()

    @staticmethod
    def _task(proc_idx: int):
        lg = SystemLogger()
        for i in range(3):
            lg.info(f"proc={proc_idx} line={i}", logger_name=f"task_{proc_idx}")
            time.sleep(0.05)

    def test_multiprocess_log_integrity(self):
        task_count = 5
        procs = []
        for i in range(task_count):
            p = multiprocessing.Process(target=self._task, args=(i,), name=f"TestProcess-{i}")
            p.start()
            procs.append(p)

        for p in procs:
            p.join()

        self.assertTrue(os.path.exists(self.log_file))

        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]

        # 5 * 3 = 15 行
        self.assertEqual(len(lines), task_count * 3)

        # 校验包含 processName（TestProcess-）和 pid（数字）
        for ln in lines:
            self.assertIn("TestProcess-", ln)
            # 粗略判断：时间 - pid - processName - ...
            parts = ln.split(" - ")
            self.assertGreaterEqual(len(parts), 6)
            self.assertTrue(parts[1].isdigit())

    def tearDown(self):
        # 测试完成后不强制删除，便于排查；可按需开启
        pass


if __name__ == "__main__":
    unittest.main()
