"""文本处理工具类实现。

功能覆盖：
- 基础清洗：去除多余空白、控制字符
- 脱敏：phone / id_card / email
- 格式校验：email / phone / id_card / url
"""

from __future__ import annotations

import re
from typing import Pattern

from ..core.base import BaseTextTool


_RE_MULTI_SPACE: Pattern[str] = re.compile(r"\s+")
_RE_CONTROL: Pattern[str] = re.compile(r"[\x00-\x1f\x7f]")
_RE_PHONE: Pattern[str] = re.compile(r"^1\d{10}$")
_RE_ID18: Pattern[str] = re.compile(r"^\d{17}[\dXx]$")
_RE_EMAIL: Pattern[str] = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_RE_URL: Pattern[str] = re.compile(
    r"^(https?://)"
    r"(([A-Za-z0-9-]+\.)+[A-Za-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})"
    r"(?::\d+)?"
    r"(?:/[^\s]*)?$"
)


class TextTool(BaseTextTool):
    """文本处理工具类。"""

    def text_clean(self, text: str) -> str:
        if text is None:
            raise ValueError("text 不能为空")
        if not isinstance(text, str):
            raise TypeError("text 必须为 str 类型")

        # 去除控制字符，统一空白
        cleaned = _RE_CONTROL.sub("", text)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = cleaned.replace("\t", " ").replace("\n", " ")
        cleaned = _RE_MULTI_SPACE.sub(" ", cleaned).strip()
        return cleaned

    def text_desensitize(self, text: str, type_: str) -> str:
        if text is None:
            raise ValueError("text 不能为空")
        if not isinstance(text, str):
            raise TypeError("text 必须为 str 类型")
        if not isinstance(type_, str) or not type_.strip():
            raise ValueError("type_ 不能为空")

        type_ = type_.strip().lower()
        if type_ == "phone":
            if not _RE_PHONE.match(text):
                raise ValueError("手机号格式错误，需为11位数字且以1开头")
            return f"{text[:3]}****{text[-4:]}"
        if type_ == "id_card":
            # 仅做 18 位简单脱敏
            if not _RE_ID18.match(text):
                raise ValueError("身份证号格式错误，需为18位（末位可为X）")
            return f"{text[:6]}********{text[-4:]}"
        if type_ == "email":
            if not _RE_EMAIL.match(text):
                raise ValueError("邮箱格式错误")
            name, domain = text.split("@", 1)
            if len(name) <= 1:
                masked = "*"
            elif len(name) == 2:
                masked = name[0] + "*"
            else:
                masked = name[0] + "*" * (len(name) - 2) + name[-1]
            return f"{masked}@{domain}"
        raise ValueError('脱敏类型不支持，仅支持 "phone"、"id_card"、"email"')

    def text_validate(self, text: str, type_: str) -> bool:
        if text is None or type_ is None:
            return False
        if not isinstance(text, str) or not isinstance(type_, str):
            return False
        type_ = type_.strip().lower()

        if type_ == "email":
            return bool(_RE_EMAIL.match(text))
        if type_ == "phone":
            return bool(_RE_PHONE.match(text))
        if type_ == "id_card":
            return bool(_RE_ID18.match(text))
        if type_ == "url":
            return bool(_RE_URL.match(text))
        return False
