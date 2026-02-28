import os
import time
import unittest
import multiprocessing

from log_module.core.impl import SystemLogger
from log_module.utils import get_log_file_name


class TestSystemLoggerMultiprocess(unittest.TestCase):
    def setUp(self):
        self.logger = SystemLogger()
        self.log_file = os.path.join(self.logger.log_dir, get_log_file_name())
        if os.path.exists(self.log_file):
            os.remove(self.log_file)

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
