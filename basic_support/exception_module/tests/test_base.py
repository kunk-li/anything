import unittest
from exception_module.core.base import BaseExceptionHandler
from exception_module.core.impl import ExceptionHandler


class TestBaseContract(unittest.TestCase):
    def test_is_subclass(self):
        self.assertTrue(issubclass(ExceptionHandler, BaseExceptionHandler))


if __name__ == "__main__":
    unittest.main()
