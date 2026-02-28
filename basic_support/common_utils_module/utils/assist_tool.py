"""通用辅助工具类实现（重点：时间相关方法）。

仅使用标准库实现，避免引入额外依赖：
- 时区：优先使用 zoneinfo（Python 3.9+），若时区名无效则抛出 ValueError
- 加密：MD5、Base64
- 时间：格式转换、时间戳、差值、偏移、区间判断、日/月/年起止
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Union, Tuple, Dict

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from ..core.base import BaseAssistTool


_FORMAT_MAP: Dict[str, str] = {
    "YYYY": "%Y",
    "MM": "%m",
    "DD": "%d",
    "HH": "%H",
    "mm": "%M",  # 兼容小写写法
    "MMIN": "%M",  # 内部占位，避免和月冲突
    "SS": "%S",
}


def _normalize_format(fmt: str) -> str:
    """将文档约定的时间格式转换为 Python strftime/strptime 格式。"""
    if fmt is None or not isinstance(fmt, str) or not fmt.strip():
        raise ValueError("format_ 不能为空")
    f = fmt.strip()

    # 为避免 MM(月) 与 MM(分钟)冲突：约定分钟使用 HH:MM:SS 中的 MM，
    # 实现上先把 ':MM' 替换成 ':MMIN' 再统一替换
    f = f.replace(":MM:", ":MMIN:").replace(":MM", ":MMIN")
    # 统一替换
    f = f.replace("YYYY", _FORMAT_MAP["YYYY"])
    f = f.replace("DD", _FORMAT_MAP["DD"])
    # 月份替换（剩余的 MM）
    f = f.replace("MM", _FORMAT_MAP["MM"])
    # 小时
    f = f.replace("HH", _FORMAT_MAP["HH"])
    # 分钟（MMIN）
    f = f.replace("MMIN", _FORMAT_MAP["MMIN"])
    # 秒
    f = f.replace("SS", _FORMAT_MAP["SS"])
    return f


def _get_tz(time_zone: Optional[str]):
    if time_zone is None:
        return None
    if not isinstance(time_zone, str) or not time_zone.strip():
        raise ValueError("time_zone 必须为有效时区字符串或 None")
    if ZoneInfo is None:
        raise RuntimeError("当前Python环境不支持 zoneinfo，请升级到 Python 3.9+")
    try:
        return ZoneInfo(time_zone.strip())
    except Exception as e:
        raise ValueError(f"无效时区：{time_zone}") from e


def _ensure_datetime(
    time_data: Optional[Union[str, datetime, int]],
    format_: str,
    time_zone: Optional[str],
) -> datetime:
    """将 time_data 转为 datetime（带时区则生成 aware datetime）。"""
    tz = _get_tz(time_zone)
    if time_data is None:
        now = datetime.now(tz=tz) if tz else datetime.now()
        return now

    if isinstance(time_data, datetime):
        if tz:
            # 若传入 naive datetime，则认为其是 tz 时区的本地时间
            if time_data.tzinfo is None:
                return time_data.replace(tzinfo=tz)
            return time_data.astimezone(tz)
        return time_data

    if isinstance(time_data, int):
        # 默认认为秒级；若是毫秒级，建议上层先除以 1000
        if tz:
            return datetime.fromtimestamp(time_data, tz=tz)
        return datetime.fromtimestamp(time_data)

    if isinstance(time_data, str):
        return _parse_time_str(time_data, format_, time_zone)

    raise TypeError("time_data 类型不支持，需为 str/datetime/int 或 None")


def _parse_time_str(time_str: str, format_: str, time_zone: Optional[str]) -> datetime:
    if not isinstance(time_str, str) or not time_str.strip():
        raise ValueError("time_str 不能为空")
    pyfmt = _normalize_format(format_)
    tz = _get_tz(time_zone)
    try:
        dt = datetime.strptime(time_str.strip(), pyfmt)
    except Exception as e:
        raise ValueError(f"时间字符串格式错误，需符合 {format_}") from e

    if tz:
        return dt.replace(tzinfo=tz)
    return dt


def _dt_to_str(dt: datetime, format_: str) -> str:
    pyfmt = _normalize_format(format_)
    return dt.strftime(pyfmt)


def _unit_to_seconds(unit: str) -> float:
    u = (unit or "").strip().lower()
    if u == "day":
        return 86400.0
    if u == "hour":
        return 3600.0
    if u == "minute":
        return 60.0
    if u == "second":
        return 1.0
    raise ValueError('unit 不支持，仅支持 "day"/"hour"/"minute"/"second"')


class AssistTool(BaseAssistTool):
    """通用辅助工具类。"""

    def md5_encrypt(self, text: str) -> str:
        if text is None:
            raise ValueError("text 不能为空")
        if not isinstance(text, str):
            raise TypeError("text 必须为 str 类型")
        m = hashlib.md5()
        m.update(text.encode("utf-8"))
        return m.hexdigest()

    # 可选扩展：Base64（非接口必需）
    def base64_encode(self, text: str) -> str:
        if text is None:
            raise ValueError("text 不能为空")
        if not isinstance(text, str):
            raise TypeError("text 必须为 str 类型")
        return base64.b64encode(text.encode("utf-8")).decode("utf-8")

    def base64_decode(self, encoded_text: str) -> str:
        if encoded_text is None:
            raise ValueError("encoded_text 不能为空")
        if not isinstance(encoded_text, str):
            raise TypeError("encoded_text 必须为 str 类型")
        try:
            return base64.b64decode(encoded_text.encode("utf-8")).decode("utf-8")
        except Exception as e:
            raise ValueError("Base64 解码失败，请检查输入") from e

    # 时间相关方法
    def get_current_time(self, format_: str = "YYYY-MM-DD HH:MM:SS", time_zone: Optional[str] = None) -> str:
        dt = _ensure_datetime(None, format_, time_zone)
        return _dt_to_str(dt, format_)

    def get_timestamp(
        self,
        time_str: Optional[str] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        millisecond: bool = False,
        time_zone: Optional[str] = None,
    ) -> int:
        dt = _ensure_datetime(time_str, format_, time_zone)
        ts = dt.timestamp()
        if millisecond:
            return int(round(ts * 1000))
        return int(round(ts))

    def time_convert(
        self,
        time_data: Union[str, datetime, int],
        target_type: str,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Union[str, datetime, int]:
        if target_type is None or not isinstance(target_type, str) or not target_type.strip():
            raise ValueError("target_type 不能为空")
        t = target_type.strip().lower()
        dt = _ensure_datetime(time_data, format_, time_zone)

        if t == "datetime":
            return dt
        if t in {"timestamp", "ts"}:
            return int(round(dt.timestamp()))
        if t == "str":
            return _dt_to_str(dt, format_)
        raise ValueError('target_type 不支持，仅支持 "str"/"datetime"/"timestamp"')

    def time_diff_calculate(
        self,
        start_time: Union[str, datetime, int],
        end_time: Union[str, datetime, int],
        unit: str = "day",
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> float:
        start_dt = _ensure_datetime(start_time, format_, time_zone)
        end_dt = _ensure_datetime(end_time, format_, time_zone)
        seconds = (end_dt - start_dt).total_seconds()
        unit_seconds = _unit_to_seconds(unit)
        val = seconds / unit_seconds
        return round(val, 2)

    def time_offset(
        self,
        time_data: Union[str, datetime, int],
        offset: int,
        unit: str = "day",
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> str:
        base_dt = _ensure_datetime(time_data, format_, time_zone)
        u = (unit or "").strip().lower()
        if u == "day":
            dt = base_dt + timedelta(days=offset)
        elif u == "hour":
            dt = base_dt + timedelta(hours=offset)
        elif u == "minute":
            dt = base_dt + timedelta(minutes=offset)
        elif u == "second":
            dt = base_dt + timedelta(seconds=offset)
        else:
            raise ValueError('unit 不支持，仅支持 "day"/"hour"/"minute"/"second"')
        return _dt_to_str(dt, format_)

    def is_in_time_range(
        self,
        time_data: Union[str, datetime, int],
        start_range: Union[str, datetime, int],
        end_range: Union[str, datetime, int],
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> bool:
        t = _ensure_datetime(time_data, format_, time_zone)
        s = _ensure_datetime(start_range, format_, time_zone)
        e = _ensure_datetime(end_range, format_, time_zone)
        if s > e:
            s, e = e, s
        return s <= t <= e

    def get_time_segment(
        self,
        time_data: Union[str, datetime, int],
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> str:
        dt = _ensure_datetime(time_data, format_, time_zone)
        hour = dt.hour
        if 0 <= hour < 6:
            return "凌晨"
        if 6 <= hour < 12:
            return "上午"
        if 12 <= hour < 18:
            return "下午"
        return "晚上"

    def get_day_start_end(
        self,
        time_data: Optional[Union[str, datetime, int]] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Tuple[str, str]:
        dt = _ensure_datetime(time_data, format_, time_zone)
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = dt.replace(hour=23, minute=59, second=59, microsecond=0)
        return _dt_to_str(start, format_), _dt_to_str(end, format_)

    def get_month_start_end(
        self,
        time_data: Optional[Union[str, datetime, int]] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Tuple[str, str]:
        dt = _ensure_datetime(time_data, format_, time_zone)
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # 下月第一天 - 1 秒
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - timedelta(seconds=1)
        return _dt_to_str(start, format_), _dt_to_str(end, format_)

    def get_year_start_end(
        self,
        time_data: Optional[Union[str, datetime, int]] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Tuple[str, str]:
        dt = _ensure_datetime(time_data, format_, time_zone)
        start = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        next_year = start.replace(year=start.year + 1)
        end = next_year - timedelta(seconds=1)
        return _dt_to_str(start, format_), _dt_to_str(end, format_)
