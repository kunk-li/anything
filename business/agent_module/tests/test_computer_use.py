# -*- coding: utf-8 -*-
"""computer_use 工具 (计算机操作): 注入 fake backend, 不碰真桌面。

验证: 各动作分发(screen_size/screenshot/move/click/double/right/type/key/scroll) /
截图 base64 往返 / 坏 action / 缺 x,y / 缺 keys / pyautogui 未装降级 / 后端异常。
"""
import base64
import unittest

from agent_module.tools.tools_impl.computer_use import make_computer_use_tool


class _FakeBackend:
    def __init__(self):
        self.calls = []
    def screen_size(self):
        self.calls.append(("size",)); return {"width": 1920, "height": 1080}
    def screenshot_png(self):
        self.calls.append(("shot",)); return b"\x89PNG\r\nFAKE"
    def move(self, x, y):
        self.calls.append(("move", x, y))
    def click(self, x, y, button="left", clicks=1):
        self.calls.append(("click", x, y, button, clicks))
    def type_text(self, text):
        self.calls.append(("type", text))
    def press_keys(self, keys):
        self.calls.append(("key", keys))
    def scroll(self, amount):
        self.calls.append(("scroll", amount))


class TestComputerUse(unittest.TestCase):
    def _tool(self, be=None):
        return make_computer_use_tool(backend=be or _FakeBackend())

    def test_screen_size(self):
        r = self._tool()({"action": "screen_size"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["width"], 1920)

    def test_screenshot_base64_roundtrip(self):
        r = self._tool()({"action": "screenshot"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(base64.b64decode(r["data"]["image_base64"]), b"\x89PNG\r\nFAKE")
        self.assertEqual(r["data"]["format"], "png")

    def test_click_records_coords(self):
        be = _FakeBackend()
        r = make_computer_use_tool(backend=be)({"action": "click", "x": 10, "y": 20})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn(("click", 10, 20, "left", 1), be.calls)

    def test_double_and_right_click(self):
        be = _FakeBackend()
        t = make_computer_use_tool(backend=be)
        t({"action": "double_click", "x": 1, "y": 2})
        t({"action": "right_click", "x": 3, "y": 4})
        self.assertIn(("click", 1, 2, "left", 2), be.calls)
        self.assertIn(("click", 3, 4, "right", 1), be.calls)

    def test_type_key_scroll(self):
        be = _FakeBackend()
        t = make_computer_use_tool(backend=be)
        self.assertEqual(t({"action": "type", "text": "hi"})["code"], "SUCCESS")
        self.assertEqual(t({"action": "key", "keys": "ctrl+c"})["code"], "SUCCESS")
        self.assertEqual(t({"action": "scroll", "amount": -5})["code"], "SUCCESS")
        self.assertIn(("type", "hi"), be.calls)
        self.assertIn(("key", "ctrl+c"), be.calls)
        self.assertIn(("scroll", -5), be.calls)

    def test_invalid_action(self):
        self.assertEqual(self._tool()({"action": "nuke"})["code"], "PARAM_INVALID")

    def test_missing_xy(self):
        self.assertEqual(self._tool()({"action": "click"})["code"], "PARAM_MISSING")

    def test_key_missing_keys(self):
        self.assertEqual(self._tool()({"action": "key"})["code"], "PARAM_MISSING")

    def test_missing_deps_degrades(self):
        class _NoDep:
            def screen_size(self):
                raise ImportError("no pyautogui")
        r = make_computer_use_tool(backend=_NoDep())({"action": "screen_size"})
        self.assertEqual(r["code"], "MISSING_DEPS")

    def test_backend_error_toolfailed(self):
        class _Boom:
            def screen_size(self):
                raise RuntimeError("display fail")
        r = make_computer_use_tool(backend=_Boom())({"action": "screen_size"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertTrue(r["retryable"])


if __name__ == "__main__":
    unittest.main()
