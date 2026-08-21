"""time / sync_weather 下发的自动时间字段生成（纯函数，可单测）。

固件校验约束（见 hp-ctrl-box-gd32 weather_snapshot.c 与 hp-ctrl-box-rust time_sync.rs）：
  - sync_weather: iana==timezone_id、tz=="+HH:MM"==timezone_offset_seconds、
    timezone==offset//3600、timeStr==observed_at+offset 的本地时间；
    observed_at 比设备已存快照旧 -> STALE 拒绝。
  - time: timestamp 比上次成功小 -> Stale 忽略；== -> Duplicate 忽略；
    timeStr 若存在须与 timestamp+offset 计算一致。
两者都不回 MQTT ack，见 NO_ACK_METHODS。
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta
from typing import Any

# 下发的常用时区：(显示名, timezone_id, 偏移秒)
COMMON_TIMEZONES: list[tuple[str, str, int]] = [
    ("UTC (+00:00)", "UTC", 0),
    ("Asia/Shanghai (+08:00)", "Asia/Shanghai", 8 * 3600),
    ("Asia/Hong_Kong (+08:00)", "Asia/Hong_Kong", 8 * 3600),
    ("Asia/Singapore (+08:00)", "Asia/Singapore", 8 * 3600),
    ("Asia/Tokyo (+09:00)", "Asia/Tokyo", 9 * 3600),
    ("Asia/Seoul (+09:00)", "Asia/Seoul", 9 * 3600),
    ("Asia/Kolkata (+05:30)", "Asia/Kolkata", 5 * 3600 + 1800),
    ("Asia/Kathmandu (+05:45)", "Asia/Kathmandu", 5 * 3600 + 2700),
    ("Europe/London (+00:00)", "Europe/London", 0),
    ("Europe/Paris (+01:00)", "Europe/Paris", 3600),
    ("America/New_York (-05:00)", "America/New_York", -5 * 3600),
    ("America/Chicago (-06:00)", "America/Chicago", -6 * 3600),
    ("America/Denver (-07:00)", "America/Denver", -7 * 3600),
    ("America/Los_Angeles (-08:00)", "America/Los_Angeles", -8 * 3600),
    ("America/Sao_Paulo (-03:00)", "America/Sao_Paulo", -3 * 3600),
    ("Australia/Sydney (+10:00)", "Australia/Sydney", 10 * 3600),
    ("Australia/Eucla (+08:45)", "Australia/Eucla", 8 * 3600 + 2700),
]

DEFAULT_TIMEZONE_ID = "Asia/Shanghai"
DEFAULT_OFFSET_SECONDS = 8 * 3600

MAX_OFFSET_SECONDS = 14 * 3600
MIN_OFFSET_SECONDS = -14 * 3600

# 这两个方法 ESP32/GD32 不发布 MQTT ack（fire-and-forget）。
NO_ACK_METHODS: frozenset[str] = frozenset({"time", "sync_weather"})

# 需要自动同步时间的方法。
TIME_SYNC_METHODS: frozenset[str] = frozenset({"time", "sync_weather"})


def now_unix() -> int:
    """当前 Unix 时间（秒）。"""
    return int(_time.time())


def local_time_str(timestamp: int, offset_seconds: int) -> str:
    """timestamp+offset 的本地时间字符串，格式 YYYY-MM-DD HH:MM:SS（固件精确比对）。

    用 UTC 墙钟解释 ts+offset，避免在 UTC+8 等本机时区上重复加偏移。
    """
    return datetime.utcfromtimestamp(timestamp + offset_seconds).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def tz_hhmm(offset_seconds: int) -> str:
    """偏移秒 -> 固件要求的 ±HH:MM 字符串（如 28800 -> "+08:00"）。"""
    sign = "+" if offset_seconds >= 0 else "-"
    absolute = abs(offset_seconds)
    hours = absolute // 3600
    minutes = (absolute % 3600) // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def offset_hours(offset_seconds: int) -> int:
    """偏移秒 -> 时数（整除，固件用 offset/3600 整数比较）。"""
    return offset_seconds // 3600


def valid_offset(offset_seconds: int) -> bool:
    return MIN_OFFSET_SECONDS <= offset_seconds <= MAX_OFFSET_SECONDS


def normalize_offset(offset_seconds: int) -> int:
    """越界偏移夹回合法范围。"""
    return max(MIN_OFFSET_SECONDS, min(MAX_OFFSET_SECONDS, offset_seconds))


def default_timezone_label() -> str:
    for label, tz_id, offset in COMMON_TIMEZONES:
        if tz_id == DEFAULT_TIMEZONE_ID and offset == DEFAULT_OFFSET_SECONDS:
            return label
    return f"{DEFAULT_TIMEZONE_ID} ({tz_hhmm(DEFAULT_OFFSET_SECONDS)})"


def time_params(
    *,
    offset_seconds: int = DEFAULT_OFFSET_SECONDS,
    timezone_id: str = DEFAULT_TIMEZONE_ID,
    is_dst: bool = False,
    timestamp: int | None = None,
) -> dict[str, Any]:
    """构建 `time` 方法完整 params（时间戳默认取当前时间）。"""
    offset_seconds = normalize_offset(offset_seconds)
    ts = now_unix() if timestamp is None else timestamp
    return {
        "timestamp": ts,
        "timezone_offset_seconds": offset_seconds,
        "timezone_id": timezone_id or DEFAULT_TIMEZONE_ID,
        "isDaylightSavingTime": bool(is_dst),
        "timeStr": local_time_str(ts, offset_seconds),
    }


def weather_params(
    *,
    home_id: str,
    temperature: float | None = None,
    humidity: float | None = None,
    pm25: float | None = None,
    offset_seconds: int = DEFAULT_OFFSET_SECONDS,
    timezone_id: str = DEFAULT_TIMEZONE_ID,
    is_dst: bool = False,
    observed_at: int | None = None,
) -> dict[str, Any]:
    """构建 `sync_weather` 方法完整 params（字段自洽，观测时间默认取当前时间）。"""
    offset_seconds = normalize_offset(offset_seconds)
    ts = now_unix() if observed_at is None else observed_at
    tz_id = timezone_id or DEFAULT_TIMEZONE_ID
    params: dict[str, Any] = {
        "home_id": home_id,
        "observed_at": ts,
        "iana": tz_id,
        "tz": tz_hhmm(offset_seconds),
        "timezone": offset_hours(offset_seconds),
        "timezone_id": tz_id,
        "timezone_offset_seconds": offset_seconds,
        "timeStr": local_time_str(ts, offset_seconds),
        "isDaylightSavingTime": bool(is_dst),
    }
    for key, value in (("temperature", temperature), ("humidity", humidity), ("pm25", pm25)):
        params[key] = value
    return params


def _stale(timestamp: int | None) -> bool:
    """缺失或落后于当前时间视为需刷新（落后 1 秒以上）。"""
    if timestamp is None:
        return True
    return timestamp < now_unix() - 1


def refresh_time_fields(method: str, params: Any) -> Any:
    """发送前刷新时间字段：缺失或过期的时间戳更新为当前时间并重算派生字段。

    `time`/`sync_weather` 会补全 timezone 相关字段（含默认时区 Asia/Shanghai）。
    其它方法原样返回。
    """
    if method not in TIME_SYNC_METHODS or not isinstance(params, dict):
        return params
    if method == "time":
        timestamp = params.get("timestamp")
        if isinstance(timestamp, int) and not _stale(timestamp):
            return params
        offset = params.get("timezone_offset_seconds")
        if not isinstance(offset, int) or not valid_offset(offset):
            offset = DEFAULT_OFFSET_SECONDS
        tz_id = params.get("timezone_id")
        if not isinstance(tz_id, str) or not tz_id:
            tz_id = DEFAULT_TIMEZONE_ID
        is_dst = params.get("isDaylightSavingTime")
        fresh = time_params(
            offset_seconds=offset,
            timezone_id=tz_id,
            is_dst=bool(is_dst) if isinstance(is_dst, bool) else False,
        )
        merged = dict(params)
        merged.update(fresh)
        return merged
    if method == "sync_weather":
        observed_at = params.get("observed_at")
        if isinstance(observed_at, int) and not _stale(observed_at):
            return params
        offset = params.get("timezone_offset_seconds")
        if not isinstance(offset, int) or not valid_offset(offset):
            offset = DEFAULT_OFFSET_SECONDS
        tz_id = params.get("timezone_id")
        if not isinstance(tz_id, str) or not tz_id:
            tz_id = DEFAULT_TIMEZONE_ID
        is_dst = params.get("isDaylightSavingTime")
        fresh = weather_params(
            home_id=str(params.get("home_id", "")),
            temperature=params.get("temperature"),
            humidity=params.get("humidity"),
            pm25=params.get("pm25"),
            offset_seconds=offset,
            timezone_id=tz_id,
            is_dst=bool(is_dst) if isinstance(is_dst, bool) else False,
        )
        merged = dict(params)
        merged.update(fresh)
        return merged
    return params
