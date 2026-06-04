# -*- coding: utf-8 -*-
"""
Tool: computer_use — 计算机操作 (截屏 + 鼠标/键盘), 对标 Claude computer use / OpenAI Operator.

**危险能力**: 控制真实桌面 (点击/输入/按键) → 默认并入 agent 审批白名单 (human-in-loop,
须 extra_params.approve_tools 才执行)。可选依赖 pyautogui (lazy import; 未装 → MISSING_DEPS
优雅降级, 不拖垮启动)。backend 可注入 (测试不碰真桌面)。

动作: screenshot / screen_size / move / click / double_click / right_click / type / key / scroll。
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_MAX_TEXT_LEN = 5000
_VALID_ACTIONS = ("screenshot", "screen_size", "move", "click",
                  "double_click", "right_click", "type", "key", "scroll")
_XY_ACTIONS = ("move", "click", "double_click", "right_click")


class _PyAutoGuiBackend:
    """默认后端: lazy 包 pyautogui。首次用时 import; 未装 → ImportError (被工具捕获降级)。"""

    def __init__(self):
        self._pg = None

    def _pg_(self):
        if self._pg is None:
            import pyautogui  # lazy; 未装抛 ImportError
            pyautogui.FAILSAFE = True   # 鼠标甩到左上角可紧急中断
            self._pg = pyautogui
        return self._pg

    def screen_size(self) -> Dict[str, int]:
        w, h = self._pg_().size()
        return {"width": int(w), "height": int(h)}

    def screenshot_png(self) -> bytes:
        import io
        buf = io.BytesIO()
        self._pg_().screenshot().save(buf, format="PNG")
        return buf.getvalue()

    def move(self, x: int, y: int) -> None:
        self._pg_().moveTo(x, y, duration=0.1)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self._pg_().click(x=x, y=y, button=button, clicks=clicks)

    def type_text(self, text: str) -> None:
        self._pg_().typewrite(text, interval=0.01)

    def press_keys(self, keys: Any) -> None:
        pg = self._pg_()
        if isinstance(keys, list):
            pg.hotkey(*[str(k) for k in keys])
        elif isinstance(keys, str) and "+" in keys:
            pg.hotkey(*[k.strip() for k in keys.split("+") if k.strip()])
        else:
            pg.press(str(keys))

    def scroll(self, amount: int) -> None:
        self._pg_().scroll(int(amount))


def _ok(data: Dict[str, Any], trace_id) -> Dict[str, Any]:
    return {"code": "SUCCESS", "message": "ok", "data": data,
            "trace_id": trace_id, "retryable": False, "details": None}


def _err(code: str, message: str, trace_id, retryable: bool = False) -> Dict[str, Any]:
    return {"code": code, "message": message, "data": None,
            "trace_id": trace_id, "retryable": retryable, "details": None}


def _save_png(png: bytes) -> Optional[str]:
    """截图存 uploads/screenshots/ 便于回溯。失败返 None (不影响主返回)。"""
    try:
        out_dir = Path.cwd() / "uploads" / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        fpath = out_dir / f"screen_{int(time.time())}_{os.urandom(3).hex()}.png"
        fpath.write_bytes(png)
        return str(fpath.relative_to(Path.cwd()))
    except Exception:
        return None


def make_computer_use_tool(backend: Optional[Any] = None) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """构造 computer_use 工具。backend 缺省用 pyautogui (lazy); 测试可注入 fake backend。"""

    def computer_use(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        trace_id = payload.get("trace_id")
        action = str(payload.get("action") or "").strip().lower()
        if action not in _VALID_ACTIONS:
            return _err("PARAM_INVALID",
                        f"action 必须是 {'/'.join(_VALID_ACTIONS)}; 收到 {action!r}", trace_id)
        # 需坐标的动作先校验 x/y
        if action in _XY_ACTIONS and (payload.get("x") is None or payload.get("y") is None):
            return _err("PARAM_MISSING", f"{action} 需 x 与 y 坐标", trace_id)

        be = backend if backend is not None else _PyAutoGuiBackend()
        try:
            x = int(payload["x"]) if action in _XY_ACTIONS else None
            y = int(payload["y"]) if action in _XY_ACTIONS else None

            if action == "screen_size":
                return _ok(be.screen_size(), trace_id)
            if action == "screenshot":
                png = be.screenshot_png()
                return _ok({"image_base64": base64.b64encode(png).decode("ascii"),
                            "bytes": len(png), "format": "png",
                            "screenshot_path": _save_png(png)}, trace_id)
            if action == "move":
                be.move(x, y)
                return _ok({"moved_to": [x, y]}, trace_id)
            if action == "click":
                be.click(x, y, button=str(payload.get("button") or "left"))
                return _ok({"clicked": [x, y], "button": payload.get("button") or "left"}, trace_id)
            if action == "double_click":
                be.click(x, y, button="left", clicks=2)
                return _ok({"double_clicked": [x, y]}, trace_id)
            if action == "right_click":
                be.click(x, y, button="right")
                return _ok({"right_clicked": [x, y]}, trace_id)
            if action == "type":
                text = str(payload.get("text") or "")[:_MAX_TEXT_LEN]
                be.type_text(text)
                return _ok({"typed_chars": len(text)}, trace_id)
            if action == "key":
                keys = payload.get("keys")
                if not keys:
                    return _err("PARAM_MISSING", "key 需 keys (如 'enter' / 'ctrl+c' / ['ctrl','c'])", trace_id)
                be.press_keys(keys)
                return _ok({"keys": keys}, trace_id)
            if action == "scroll":
                amount = int(payload.get("amount", -3))
                be.scroll(amount)
                return _ok({"scrolled": amount}, trace_id)
        except ImportError:
            return _err("MISSING_DEPS",
                        "pyautogui 未装. 跑: pip install pyautogui (Linux 另需 scrot + python3-tk; 须有桌面/显示)",
                        trace_id)
        except Exception as e:
            return _err("TOOL_CALL_FAILED", f"计算机操作失败: {str(e)[:200]}", trace_id, retryable=True)
        return _err("PARAM_INVALID", f"未处理的 action: {action}", trace_id)   # 理论不可达

    return computer_use


COMPUTER_USE_DESCRIPTION = (
    '计算机操作(对标 Claude computer use / Operator): 截屏 + 鼠标/键盘控制真实桌面。'
    'input: {"action": "screenshot"|"screen_size"|"move"|"click"|"double_click"|"right_click"|"type"|"key"|"scroll", '
    '"x": int?, "y": int? (move/click 系需要), "text": str? (type), '
    '"keys": str|list? (key, 如 "enter"/"ctrl+c"/["ctrl","c"]), "button": "left"|"right"? (click), "amount": int? (scroll)}. '
    'screenshot 返回 image_base64(PNG) + screenshot_path。**危险: 控制真实桌面, 默认需审批**。'
    '需 pip install pyautogui (须有桌面环境)。建议先 screenshot 看屏, 再按坐标操作。'
)
