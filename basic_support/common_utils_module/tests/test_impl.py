import unittest
from datetime import datetime

from common_utils_module import CommonUtils


class TestCommonUtils(unittest.TestCase):
    def setUp(self):
        self.utils = CommonUtils()

    def test_text_tool(self):
        t = self.utils.get_text_tool()
        self.assertEqual(t.text_clean("  测试\n\t文本  "), "测试 文本")
        self.assertTrue(t.text_validate("test@example.com", "email"))
        self.assertFalse(t.text_validate("not-an-email", "email"))
        self.assertEqual(t.text_desensitize("13812345678", "phone"), "138****5678")

    def test_data_tool(self):
        d = self.utils.get_data_tool()
        self.assertEqual(d.dict_to_json({"a": 1}), '{"a": 1}')
        self.assertEqual(d.data_convert("123", "int"), 123)
        self.assertEqual(d.data_convert("true", "bool"), True)
        self.assertEqual(d.list_deduplicate([1, 2, 2, 3, 1]), [1, 2, 3])

    def test_param_validate(self):
        p = self.utils.get_param_validate()
        params = {"user": {"name": "kunsheng"}, "age": 20}
        self.assertTrue(p.required_validate(params, ["user.name", "age"]))
        self.assertFalse(p.required_validate(params, ["user.phone"]))
        self.assertTrue(p.range_validate(20, 18, 30))
        self.assertTrue(p.range_validate("abcd", 1, 10))
        self.assertFalse(p.range_validate("abcd", 5, 10))

    def test_assist_time(self):
        a = self.utils.get_assist_tool()
        time_str = "2026-02-28 10:30:00"
        dt = a.time_convert(time_str, "datetime")
        self.assertIsInstance(dt, datetime)
        ts = a.time_convert(dt, "timestamp")
        self.assertIsInstance(ts, int)
        diff_h = a.time_diff_calculate("2026-02-27 10:00:00", "2026-02-28 12:30:00", unit="hour")
        self.assertAlmostEqual(diff_h, 26.5, places=2)
        offset = a.time_offset(time_str, 3, unit="hour")
        self.assertEqual(offset, "2026-02-28 13:30:00")
        self.assertTrue(a.is_in_time_range(time_str, "2026-02-28 00:00:00", "2026-02-28 23:59:59"))
        self.assertEqual(a.get_time_segment("2026-02-28 01:00:00"), "凌晨")
        day_start, day_end = a.get_day_start_end(time_str)
        self.assertEqual(day_start, "2026-02-28 00:00:00")
        self.assertEqual(day_end, "2026-02-28 23:59:59")


if __name__ == "__main__":
    unittest.main()
