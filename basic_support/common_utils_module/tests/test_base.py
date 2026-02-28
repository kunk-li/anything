import unittest
from common_utils_module.core.base import BaseUtils


class TestBase(unittest.TestCase):
    def test_base_is_abc(self):
        self.assertTrue(hasattr(BaseUtils, "__abstractmethods__"))


if __name__ == "__main__":
    unittest.main()
