"""工作流 schema v2 数据模型辅助（trigger / nodes 构建与解析）。"""
from __future__ import annotations

from typing import Any

TRIGGER_TYPES = (
    "periodic",
    "time_of_day",
    "property_change",
    "property_sustain",
    "env_change",
    "manual",
)
NODE_TYPES = ("service", "exclusive_gw")
ACTION_OPS = ("write", "copy", "add", "add_reg", "min", "max", "clamp")
CMP_OPS = ("eq", "ne", "gt", "ge", "lt", "le", "bit_on", "bit_off")
VALUE_TYPES = ("bool", "int", "float")
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
ENV_PARAMS = ("temperature", "humidity", "pm25")
ENV_TEMP_CMP = ("gt", "lt")
ENV_RANGE_HUMIDITY = ("dry", "comfort", "humid")
ENV_RANGE_PM25 = ("good", "moderate", "exceed")
ENV_PARAM_LABELS = {
    "temperature": "温度（℃）",
    "humidity": "湿度（%RH）",
    "pm25": "PM2.5（µg/m³）",
}
ENV_RANGE_HUMIDITY_LABELS = {
    "dry": "干燥 [0, 40)",
    "comfort": "舒适 [40, 70)",
    "humid": "潮湿 [70, 100]",
}
ENV_RANGE_PM25_LABELS = {
    "good": "优 [0, 35)",
    "moderate": "良 [35, 75]",
    "exceed": "超标 > 75",
}


def prop_ref(
    device_id: str,
    product_id: str,
    siid: int,
    piid: int,
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "product_id": product_id,
        "siid": siid,
        "piid": piid,
    }


def write_target(
    device_ids: list[str],
    product_id: str,
    siid: int,
    piid: int,
) -> dict[str, Any]:
    return {
        "device_ids": device_ids,
        "product_id": product_id,
        "siid": siid,
        "piid": piid,
    }


def typed_value(type_name: str, value: object) -> dict[str, Any]:
    return {"type": type_name, "value": value}


GUARD_CONN = ("and", "or")


def default_guard(device_id: str, product_id: str) -> dict[str, Any]:
    return {
        "cmp": "gt",
        "left": prop_ref(device_id, product_id, 2, 1),
        "right_value": typed_value("int", 0),
    }


def default_flow(device_id: str, product_id: str, target: object = "end") -> dict[str, Any]:
    return {
        "guard_conn": "and",
        "guards": [default_guard(device_id, product_id)],
        "target": target,
    }


def default_exclusive_gw_node(device_id: str, product_id: str) -> dict[str, Any]:
    return {
        "type": "exclusive_gw",
        "flows": [default_flow(device_id, product_id, 1)],
        "default_target": "end",
    }


def default_trigger(device_id: str, product_id: str) -> dict[str, Any]:
    """默认：周期触发，1 秒。"""
    return {"type": "periodic", "period_ticks": 10}


def default_nodes(device_id: str, product_id: str) -> list[dict[str, Any]]:
    """默认：单 SERVICE 节点，写入 bool true。"""
    return [
        {
            "type": "service",
            "actions": [
                {
                    "op": "write",
                    "target": write_target([device_id], product_id, 2, 1),
                    "value": typed_value("bool", True),
                }
            ],
            "next": "end",
        }
    ]


def parse_target_field(target: object) -> tuple[list[str], str, int, int]:
    if not isinstance(target, dict):
        return [], "", 2, 1
    raw_ids = target.get("device_ids", [])
    device_ids: list[str] = []
    if isinstance(raw_ids, list):
        device_ids = [str(x) for x in raw_ids]
    product_id = str(target.get("product_id", ""))
    siid = int(target.get("siid", 2)) if isinstance(target.get("siid"), (int, float)) else 2
    piid = int(target.get("piid", 1)) if isinstance(target.get("piid"), (int, float)) else 1
    return device_ids, product_id, siid, piid


def parse_prop_ref(ref: object) -> tuple[str, str, int, int]:
    if not isinstance(ref, dict):
        return "", "", 2, 1
    device_id = str(ref.get("device_id", ""))
    product_id = str(ref.get("product_id", ""))
    siid = int(ref.get("siid", 2)) if isinstance(ref.get("siid"), (int, float)) else 2
    piid = int(ref.get("piid", 1)) if isinstance(ref.get("piid"), (int, float)) else 1
    return device_id, product_id, siid, piid


def parse_typed_value(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "int", "0"
    type_name = str(value.get("type", "int"))
    raw = value.get("value")
    if isinstance(raw, bool):
        return type_name, "true" if raw else "false"
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return type_name, str(raw)
    if raw is None:
        return type_name, ""
    return type_name, str(raw)


def coerce_typed_value(type_name: str, text: str) -> object:
    stripped = text.strip()
    if type_name == "bool":
        return stripped.lower() in ("1", "true", "yes", "on")
    if type_name == "float":
        return float(stripped) if stripped else 0.0
    return int(stripped) if stripped else 0


def parse_next_target(value: object) -> str:
    if value == "end":
        return "end"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    return str(value) if value is not None else "end"


def format_next_target(text: str) -> object:
    stripped = text.strip()
    if stripped == "end":
        return "end"
    try:
        return int(stripped)
    except ValueError:
        return stripped


def parse_days_of_week(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    days: list[str] = []
    for item in value:
        day = str(item).strip().lower()
        if day in WEEKDAYS:
            days.append(day)
    return days


def format_days_of_week(text: str) -> list[str]:
    days: list[str] = []
    for part in text.replace(";", ",").split(","):
        day = part.strip().lower()
        if day in WEEKDAYS:
            days.append(day)
    return days
