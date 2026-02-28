import unittest

from exception_module.core.impl import (
    ExceptionHandler,
    ConfigException,
    AgentException,
    SystemBaseException,
)


class TestExceptionHandler(unittest.TestCase):
    def setUp(self):
        self.exception_handler = ExceptionHandler()

    def test_custom_exception_handle(self):
        config_exc = ConfigException("CONFIG_KEY_MISSING", "配置缺失：vector_db.host")
        self.assertEqual(self.exception_handler.get_exception_code(config_exc), "CONFIG_KEY_MISSING")
        error_info = self.exception_handler.handle_exception(config_exc)
        self.assertEqual(error_info["code"], "CONFIG_KEY_MISSING")
        self.assertEqual(error_info["message"], "配置缺失：vector_db.host")

        agent_exc = AgentException("TOOL_NOT_FOUND", "工具不存在：rag_search")
        self.assertEqual(self.exception_handler.get_exception_code(agent_exc), "TOOL_NOT_FOUND")
        error_info = self.exception_handler.handle_exception(agent_exc)
        self.assertEqual(error_info["code"], "TOOL_NOT_FOUND")
        self.assertEqual(error_info["message"], "工具不存在：rag_search")

    def test_unknown_exception_handle(self):
        unknown_exc = Exception("未知错误：数据库连接超时")
        self.assertEqual(self.exception_handler.get_exception_code(unknown_exc), "UNKNOWN_ERROR")
        error_info = self.exception_handler.handle_exception(unknown_exc)
        self.assertEqual(error_info["code"], "UNKNOWN_ERROR")
        # 文档示例：应包含“未知异常”
        self.assertIn("未知异常", error_info["message"])

    def test_handler_internal_error_fallback(self):
        # 构造一个会让 get_exception_code 出错的 handler：通过 monkey patch
        h = ExceptionHandler()
        h.get_exception_code = lambda e: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore
        res = h.handle_exception(Exception("x"))
        self.assertEqual(res["code"], "EXCEPTION_HANDLER_ERROR")
        self.assertIn("异常处理器内部错误", res["message"])

    def test_system_base_exception_details_fallback_to_default_message(self):
        # message 为空时，回退到错误码默认模板
        exc = SystemBaseException("CONFIG_NOT_FOUND", "")
        res = self.exception_handler.handle_exception(exc)
        self.assertEqual(res["code"], "CONFIG_NOT_FOUND")
        self.assertTrue(res["message"])  # 有默认文案


if __name__ == "__main__":
    unittest.main()
