import unittest
import multiprocessing

from log_module.core.impl import SystemLogger


class TestSystemLogger(unittest.TestCase):
    def setUp(self):
        self.logger = SystemLogger()

    def test_singleton_per_process(self):
        a = SystemLogger()
        b = SystemLogger()
        self.assertIs(a, b)

    def test_get_logger_cache(self):
        l1 = self.logger.get_logger("test_logger")
        l2 = self.logger.get_logger("test_logger")
        self.assertIs(l1, l2)
        self.assertIn(str(multiprocessing.current_process().pid), l1.name)

    def test_log_levels_no_exception(self):
        self.logger.debug("测试 DEBUG")
        self.logger.info("测试 INFO")
        self.logger.warning("测试 WARNING")
        self.logger.error("测试 ERROR")
        self.logger.critical("测试 CRITICAL")


if __name__ == "__main__":
    unittest.main()
