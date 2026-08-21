#!/usr/bin/env python3
"""CSV-driven Modbus serial test runner."""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import logging
import math
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_TIME_ADDR = 4399
DEFAULT_CLIENT_TIMEOUT_S = 3.0
WRITE_RETRY_COUNT = 3
WRITE_RETRY_DELAY_S = 1.0
FUNC_WRITE = "write"
FUNC_WRITE_MULTI = "write_multi"
FUNC_READ = "read"
FUNC_DELAY = "delay"
FUNC_WAIT = "wait"
FUNC_READ_START_TIME = "read_start_time"
FUNC_LOGIC_DELAY = "logic_delay"
FUNC_SIM_CONTROL = "sim_control"
FUNC_SIM_POWER = "sim_power"
FUNC_SIM_READ = "sim_read"
FUNC_SIM_WAIT = "sim_wait"
FUNC_SET_SLAVE = "set_slave"
FUNC_SLAVE_START = "slave_start"
FUNC_SLAVE_STOP = "slave_stop"
FUNC_SLAVE_WRITE = "slave_write"
FUNC_SLAVE_READ = "slave_read"
FUNC_SLAVE_WAIT = "slave_wait"
FUNC_MQTT_START = "mqtt_start"
FUNC_MQTT_STOP = "mqtt_stop"
FUNC_MQTT_SEND = "mqtt_send"
FUNC_MQTT_WAIT = "mqtt_wait"
FUNC_MQTT_WATCH = "mqtt_watch"
FUNC_MQTT_SET = "mqtt_set"
SIM_FUNCS = {FUNC_SIM_CONTROL, FUNC_SIM_POWER, FUNC_SIM_READ, FUNC_SIM_WAIT}
SLAVE_FUNCS = {
    FUNC_SLAVE_START,
    FUNC_SLAVE_STOP,
    FUNC_SLAVE_WRITE,
    FUNC_SLAVE_READ,
    FUNC_SLAVE_WAIT,
}
MQTT_FUNCS = {
    FUNC_MQTT_START,
    FUNC_MQTT_STOP,
    FUNC_MQTT_SEND,
    FUNC_MQTT_WAIT,
    FUNC_MQTT_WATCH,
    FUNC_MQTT_SET,
}
VALID_FUNCS = {
    FUNC_WRITE,
    FUNC_WRITE_MULTI,
    FUNC_READ,
    FUNC_DELAY,
    FUNC_WAIT,
    FUNC_READ_START_TIME,
    FUNC_LOGIC_DELAY,
    FUNC_SIM_CONTROL,
    FUNC_SIM_POWER,
    FUNC_SIM_READ,
    FUNC_SIM_WAIT,
    FUNC_SET_SLAVE,
    *SLAVE_FUNCS,
    *MQTT_FUNCS,
}

SIM_PROP_MAP: dict[str, str] = {
    "power": "2_1",
    "mode": "2_3",
    "fan_level": "2_4",
    "target_temp": "2_5",
    "indoor_temp": "3_1",
    "indoor_humi": "3_2",
    "fault_status": "2_11",
    "cur_fan_speed": "3_4",
    "comp_status": "3_5",
    "fan_supply_demand": "3_7",
    "floor_supply_demand": "3_8",
}
SIM_BOOL_PROPS = {"power"}
SIM_SUPPLY_DEMAND_PROPS = {"fan_supply_demand", "floor_supply_demand"}
SIM_CONTROL_WHITELIST = {
    "power",
    "mode",
    "fan_level",
    "target_temp",
    *SIM_SUPPLY_DEMAND_PROPS,
}
SIM_POWER_ON_VALUES = {"on", "true", "1"}
SIM_POWER_OFF_VALUES = {"off", "false", "0"}

MQTT_SET_KEYS: dict[str, str] = {
    "host": "str",
    "port": "int",
    "path": "str",
    "username": "str",
    "password": "str",
    "product_id": "str",
    "device_id": "str",
    "qos": "int",
    "ack_timeout": "int",
    "timeout": "int",
    "connect_timeout": "int",
    "subscribe_all": "bool",
    "expect_ack_code": "int",
}
MQTT_SET_INT_KEYS = {
    "port",
    "qos",
    "ack_timeout",
    "timeout",
    "connect_timeout",
    "expect_ack_code",
}
MQTT_SET_BOOL_KEYS = {"subscribe_all"}


class CsvParseError(Exception):
    """Raised when CSV content is invalid."""


class ConnectionSetupError(Exception):
    """Raised when the serial connection cannot be prepared."""


class SessionTimeoutError(Exception):
    """Raised when the run exceeds the configured session timeout."""


class SimApiError(Exception):
    """Raised on DeviceSimulator API call failure."""


class SimUnavailableError(SimApiError):
    """Raised when DeviceSimulator cannot be reached."""


class SlaveControlError(Exception):
    """Raised on EMS Modbus Slave control channel failure."""


class MqttControlError(Exception):
    """Raised on EMS MQTT master daemon control channel failure."""


@dataclass(frozen=True)
class Step:
    func: str
    addr: int
    value: str
    desc: str
    row_num: int


@dataclass
class StepResult:
    index: int
    func: str
    status: str
    summary: str
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class FileResult:
    name: str
    path: str
    status: str
    passed: int
    total: int
    duration_s: float
    step_results: list[StepResult]
    error: str = ""


@dataclass(frozen=True)
class InputFile:
    path: Path
    display_name: str


@dataclass
class PreparedFile:
    input_file: InputFile
    steps: list[Step] | None = None
    error: str = ""


@dataclass
class ExecutionContext:
    client: Any
    slave_id: int
    initial_slave_id: int
    wait_timeout: int
    wait_interval: float
    session_deadline: float | None
    session_timeout: int = 60
    dry_run: bool = False
    start_time_value: int | None = None
    time_addr: int = DEFAULT_TIME_ADDR
    sim: SimContext | None = None
    slave: "SlaveContext | None" = None
    mqtt: "MqttContext | None" = None


@dataclass
class SimContext:
    api_base: str
    http_timeout: float
    _index_to_sn: dict[int, str] = field(default_factory=dict)


@dataclass
class SlaveContext:
    """Child process state for the EMS Modbus Slave (stdio JSON-RPC control)."""

    app_path: str
    port: str | None = None
    profile: str = "dm_hp3_rs48_v2"
    preset: str | None = None
    baudrate: int | None = None
    slave_id: int = 1
    respond_1_40: bool = False
    ready_timeout: float = 10.0
    stop_timeout: float = 5.0
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    lines: list[str] = field(default_factory=list)
    next_id: int = 1
    started: bool = False


@dataclass
class MqttContext:
    """Child process state for the EMS MQTT master daemon (stdio JSON-RPC control).

    ``config_overrides`` accumulates ``mqtt_set`` steps; it is sent to the daemon
    with the ``connect`` op at ``mqtt_start`` and persists for the session.
    """

    app_path: str
    config_path: str | None = None
    connect_timeout: float = 15.0
    stop_timeout: float = 5.0
    config_overrides: dict[str, Any] = field(default_factory=dict)
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    lines: list[str] = field(default_factory=list)
    next_id: int = 1
    started: bool = False
    connected: bool = False


@dataclass(frozen=True)
class WaitSpec:
    kind: str
    expected: Any
    timeout_s: float | None = None
    interval_s: float | None = None
    logic_timeout_s: float | None = None


LOG_LOGGER_NAME = "modbus_test"


def setup_logging(log_dir: str, no_log: bool) -> Path | None:
    logger = logging.getLogger(LOG_LOGGER_NAME)
    for h in logger.handlers[:]:
        h.close()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if no_log:
        logger.addHandler(logging.NullHandler())
        return None
    log_path = Path(log_dir)
    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.addHandler(logging.NullHandler())
        return None
    filename = f"modbus_test_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_path = log_path / filename
    try:
        handler = logging.FileHandler(file_path, encoding="utf-8")
    except OSError:
        logger.addHandler(logging.NullHandler())
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return file_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Modbus serial tests from a CSV directory or an ordered list of CSV files."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="one CSV directory or one or more CSV file paths",
    )
    parser.add_argument("--port", default="auto", help="Serial port path or auto")
    parser.add_argument("--baudrate", type=int, default=115200, help="Serial baudrate")
    parser.add_argument("--slave-id", type=int, default=1, help="Modbus device_id")
    parser.add_argument(
        "--time-addr",
        type=int,
        default=DEFAULT_TIME_ADDR,
        help=f"Device logic time register address (default: {DEFAULT_TIME_ADDR})",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=50,
        help="Maximum wait poll attempts (Modbus wait) or seconds (sim_wait) before FAIL",
    )
    parser.add_argument(
        "--wait-interval",
        type=float,
        default=1.0,
        help="Wait polling interval in seconds",
    )
    parser.add_argument(
        "--session-timeout",
        type=int,
        default=60,
        help="Maximum idle time in seconds since last PASS; resets on each PASS (default: 60)",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="CSV text encoding",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse only, do not connect or execute Modbus requests",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively search subdirectories for CSV files",
    )
    parser.add_argument(
        "--sim-api",
        default="http://127.0.0.1:9090",
        help="DeviceSimulator API base URL (default: http://127.0.0.1:9090)",
    )
    parser.add_argument(
        "--sim-http-timeout",
        type=float,
        default=5.0,
        help="HTTP request timeout for DeviceSimulator API (default: 5.0s)",
    )
    parser.add_argument(
        "--slave-app",
        default=None,
        help="Path to the EMS Modbus Slave app.py (default: bundled ems_modbus_slave/app.py "
        "next to the skill, overrides auto-detection)",
    )
    parser.add_argument(
        "--slave-port",
        default=None,
        help="Serial port for the EMS Modbus Slave child process (default: auto-detect)",
    )
    parser.add_argument(
        "--slave-profile",
        default="dm_hp3_rs48_v2",
        help="Profile path or profile_id for the slave child process "
        "(default: dm_hp3_rs48_v2)",
    )
    parser.add_argument(
        "--slave-preset",
        default=None,
        help="Preset JSON path to apply to the slave child process (optional)",
    )
    parser.add_argument(
        "--slave-baudrate",
        type=int,
        default=None,
        help="Override the slave profile baudrate",
    )
    parser.add_argument(
        "--slave-slave-id",
        type=int,
        default=1,
        help="Default slave ID for the child process (default: 1)",
    )
    parser.add_argument(
        "--slave-respond-1-40",
        action="store_true",
        help="Make the child process respond to Modbus slave ID 1..40",
    )
    parser.add_argument(
        "--slave-ready-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the slave child process ready event (default: 10.0)",
    )
    parser.add_argument(
        "--slave-stop-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for graceful slave shutdown before kill (default: 5.0)",
    )
    parser.add_argument(
        "--mqtt-app",
        default=None,
        help="Path to the EMS MQTT master app_cli.py (default: bundled "
        "ems_mqtt_master/app_cli.py next to the skill)",
    )
    parser.add_argument(
        "--mqtt-config",
        default=None,
        help="Path to the MQTT config.json used by the master daemon "
        "(default: bundled ems_mqtt_master/config/config.json)",
    )
    parser.add_argument(
        "--mqtt-connect-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for the mqtt daemon ready/connect (default: 15.0)",
    )
    parser.add_argument(
        "--mqtt-stop-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for graceful mqtt daemon shutdown before kill (default: 5.0)",
    )
    parser.add_argument(
        "--log-dir",
        default="./logs",
        help="Directory for log files (default: ./logs)",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Disable file logging",
    )
    args = parser.parse_args(argv)
    if args.wait_timeout < 1:
        parser.error("--wait-timeout must be >= 1")
    if args.wait_interval <= 0:
        parser.error("--wait-interval must be > 0")
    if args.session_timeout < 1:
        parser.error("--session-timeout must be >= 1")
    if args.baudrate < 1:
        parser.error("--baudrate must be >= 1")
    if args.slave_id < 0:
        parser.error("--slave-id must be >= 0")
    if args.time_addr < 0:
        parser.error("--time-addr must be >= 0")
    if args.sim_http_timeout <= 0:
        parser.error("--sim-http-timeout must be > 0")
    if args.slave_ready_timeout <= 0:
        parser.error("--slave-ready-timeout must be > 0")
    if args.slave_stop_timeout <= 0:
        parser.error("--slave-stop-timeout must be > 0")
    if args.slave_slave_id < 1 or args.slave_slave_id > 247:
        parser.error("--slave-slave-id must be 1-247")
    if args.mqtt_connect_timeout <= 0:
        parser.error("--mqtt-connect-timeout must be > 0")
    if args.mqtt_stop_timeout <= 0:
        parser.error("--mqtt-stop-timeout must be > 0")
    return args


def resolve_input_files(raw_paths: list[str], recursive: bool = False) -> list[InputFile]:
    paths = [Path(raw_path).expanduser() for raw_path in raw_paths]
    directories = [path for path in paths if path.is_dir()]

    if directories:
        if len(paths) != 1:
            raise CsvParseError("directory mode accepts exactly one directory")
        input_path = directories[0]
        pattern = "**/*.csv" if recursive else "*.csv"
        files = sorted(
            [path for path in input_path.glob(pattern) if path.is_file()],
            key=lambda path: extract_number(path.relative_to(input_path)),
        )
        if not files:
            raise CsvParseError(f"no CSV files found in directory: {input_path}")
        return [
            InputFile(path=path, display_name=str(path.relative_to(input_path)))
            for path in files
        ]

    if recursive:
        raise CsvParseError("--recursive can only be used with directory mode")

    return [
        InputFile(path=path, display_name=raw_path)
        for raw_path, path in zip(raw_paths, paths)
    ]


def prepare_input_file(input_file: InputFile, encoding: str) -> PreparedFile:
    path = input_file.path
    try:
        if not path.exists():
            raise CsvParseError(f"path does not exist: {path}")
        if not path.is_file():
            raise CsvParseError(f"path is not a file: {path}")
        if path.suffix.lower() != ".csv":
            raise CsvParseError(f"path is not a CSV file: {path}")
        return PreparedFile(input_file=input_file, steps=parse_csv(path, encoding))
    except (CsvParseError, OSError, UnicodeError, csv.Error) as exc:
        return PreparedFile(input_file=input_file, error=str(exc))


def extract_number(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", path.name.lower())
    key: list[Any] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return key


def normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def find_column(fieldnames: list[str], aliases: list[str], label: str) -> str:
    normalized = {normalize_header(name): name for name in fieldnames}
    for alias in aliases:
        if normalize_header(alias) in normalized:
            return normalized[normalize_header(alias)]
    raise CsvParseError(f"missing required CSV column: {label}")


def parse_int(raw: str, field_name: str, row_num: int) -> int:
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing {field_name}")
    try:
        return int(float(text))
    except (ValueError, OverflowError) as exc:
        raise CsvParseError(f"row {row_num}: invalid {field_name}: {raw!r}") from exc


FC16_MAX_COUNT = 123
UINT16_MAX = 65535


def parse_int_list(raw: str, field_name: str, row_num: int) -> list[int]:
    """Parse comma-separated integers, e.g. '1,2,3' -> [1, 2, 3]."""
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing {field_name}")
    parts = [p.strip() for p in text.split(",")]
    values: list[int] = []
    for part in parts:
        if not part:
            raise CsvParseError(f"row {row_num}: empty element in {field_name}")
        try:
            values.append(int(float(part)))
        except (ValueError, OverflowError) as exc:
            raise CsvParseError(
                f"row {row_num}: invalid element in {field_name}: {part!r}"
            ) from exc
    if not values:
        raise CsvParseError(f"row {row_num}: {field_name} must contain at least one integer")
    if len(values) > FC16_MAX_COUNT:
        raise CsvParseError(
            f"row {row_num}: {field_name} count {len(values)} exceeds FC16 limit {FC16_MAX_COUNT}"
        )
    for v in values:
        if not 0 <= v <= UINT16_MAX:
            raise CsvParseError(
                f"row {row_num}: {field_name} element {v} outside uint16 range 0-{UINT16_MAX}"
            )
    return values


def parse_float(raw: str, field_name: str, row_num: int) -> float:
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing {field_name}")
    try:
        return float(text)
    except ValueError as exc:
        raise CsvParseError(f"row {row_num}: invalid {field_name}: {raw!r}") from exc


def parse_positive_float(raw: str, field_name: str, row_num: int) -> float:
    value = parse_float(raw, field_name, row_num)
    if value <= 0:
        raise CsvParseError(f"row {row_num}: {field_name} must be > 0")
    return value


def parse_csv(csv_path: Path, encoding: str) -> list[Step]:
    with csv_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CsvParseError(f"{csv_path}: empty CSV or missing header row")

        func_col = find_column(reader.fieldnames, ["功能", "function", "func"], "function")
        addr_col = find_column(
            reader.fieldnames,
            ["目标地址", "target_addr", "address", "addr"],
            "target address",
        )
        value_col = find_column(
            reader.fieldnames,
            ["目标值", "target_value", "value"],
            "target value",
        )
        desc_col = find_column(
            reader.fieldnames,
            ["说明", "description", "desc", "note"],
            "description",
        )

        steps: list[Step] = []
        for row_num, row in enumerate(reader, start=2):
            values = [str(value).strip() for value in row.values() if value is not None]
            if not any(values):
                continue

            if None in row:
                extra = row[None]
                if not isinstance(extra, list):
                    extra = [extra]
                extra_str = ",".join(str(v).strip() for v in extra if v is not None and str(v).strip())
                if extra_str:
                    current_desc = str(row.get(desc_col, "") or "").strip()
                    row[desc_col] = current_desc + "," + extra_str
                del row[None]
                logging.getLogger(LOG_LOGGER_NAME).warning(
                    "%s: row %d: merged %d extra field(s) into description",
                    csv_path, row_num, len(extra))

            func = str(row.get(func_col, "")).strip().lower()
            if func not in VALID_FUNCS:
                raise CsvParseError(
                    f"row {row_num}: unsupported function {func!r}; expected one of "
                    f"{', '.join(sorted(VALID_FUNCS))}"
                )

            addr_text = str(row.get(addr_col, "")).strip()
            value_text = str(row.get(value_col, "")).strip()
            desc_text = str(row.get(desc_col, "")).strip()

            addr = 0
            if func != FUNC_READ_START_TIME or addr_text:
                addr = parse_int(addr_text or "0", "target address", row_num)

            if func in SIM_FUNCS and addr <= 0:
                raise CsvParseError(
                    f"row {row_num}: DeviceIndex must be > 0 for {func}"
                )

            if not value_text and func not in (
                FUNC_READ_START_TIME,
                FUNC_SLAVE_START,
                FUNC_SLAVE_STOP,
                FUNC_MQTT_START,
                FUNC_MQTT_STOP,
            ):
                raise CsvParseError(f"row {row_num}: missing target value")

            if func == FUNC_WRITE:
                parse_int(value_text, "write value", row_num)
            elif func == FUNC_WRITE_MULTI:
                parse_int_list(value_text, "write_multi value", row_num)
            elif func == FUNC_DELAY:
                parse_float(value_text, "delay seconds", row_num)
            elif func == FUNC_LOGIC_DELAY:
                parse_positive_float(value_text, "logic delay seconds", row_num)
            elif func == FUNC_READ:
                parse_expected(value_text, row_num)
            elif func == FUNC_WAIT:
                parse_wait_value(value_text, row_num)
            elif func == FUNC_SIM_CONTROL:
                _validate_sim_control_value(value_text, row_num)
            elif func == FUNC_SIM_POWER:
                _validate_sim_power_value(value_text, row_num)
            elif func == FUNC_SIM_READ:
                _validate_sim_read_value(value_text, row_num)
            elif func == FUNC_SIM_WAIT:
                _validate_sim_wait_value(value_text, row_num)
            elif func == FUNC_SET_SLAVE:
                sid = parse_int(value_text, "slave id", row_num)
                if sid < 1 or sid > 247:
                    raise CsvParseError(
                        f"row {row_num}: slave id must be 1-247, got {sid}"
                    )
            elif func in (FUNC_SLAVE_START, FUNC_SLAVE_STOP):
                if addr != 0:
                    raise CsvParseError(f"row {row_num}: {func} requires address 0")
            elif func == FUNC_SLAVE_WRITE:
                _parse_slave_write_spec(value_text, row_num)
            elif func == FUNC_SLAVE_READ:
                _parse_slave_read_spec(value_text, row_num)
            elif func == FUNC_SLAVE_WAIT:
                _parse_slave_wait_spec(value_text, row_num)
            elif func in MQTT_FUNCS:
                if addr != 0:
                    raise CsvParseError(
                        f"row {row_num}: {func} requires address 0, got {addr}"
                    )
                if func == FUNC_MQTT_SEND:
                    _parse_mqtt_send_value(value_text, row_num)
                elif func == FUNC_MQTT_WAIT:
                    _parse_mqtt_wait_value(value_text, row_num)
                elif func == FUNC_MQTT_WATCH:
                    _parse_mqtt_watch_value(value_text, row_num)
                elif func == FUNC_MQTT_SET:
                    _parse_mqtt_set_value(value_text, row_num)

            steps.append(
                Step(
                    func=func,
                    addr=addr,
                    value=value_text,
                    desc=desc_text,
                    row_num=row_num,
                )
            )

    if not steps:
        raise CsvParseError(f"{csv_path}: no test steps found")
    return steps


def detect_port(port_arg: str) -> str:
    if port_arg != "auto":
        return port_arg

    system = platform.system()
    candidates: list[str]
    if system == "Linux":
        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]
        candidates = sorted(
            {item for pattern in patterns for item in glob.glob(pattern)},
            key=str.lower,
        )
    elif system == "Darwin":
        patterns = ["/dev/cu.usbserial*", "/dev/cu.usbmodem*"]
        candidates = sorted(
            {item for pattern in patterns for item in glob.glob(pattern)},
            key=str.lower,
        )
    elif system == "Windows":
        try:
            from serial.tools import list_ports
        except ImportError as exc:
            raise ConnectionSetupError(
                "pyserial is required for Windows auto port detection"
            ) from exc
        candidates = sorted(
            [port.device for port in list_ports.comports() if port.device.upper().startswith("COM")],
            key=str.lower,
        )
    else:
        raise ConnectionSetupError(f"unsupported platform for auto port detection: {system}")

    if not candidates:
        raise ConnectionSetupError("no serial port candidates found for --port auto")
    if len(candidates) > 1:
        joined = ", ".join(candidates)
        raise ConnectionSetupError(
            f"multiple serial ports found for --port auto: {joined}; specify --port explicitly"
        )
    return candidates[0]


def create_client(port: str, baudrate: int) -> Any:
    try:
        from pymodbus.client import ModbusSerialClient
    except ImportError as exc:
        raise ConnectionSetupError(
            "pymodbus is not installed in this interpreter; use uv run --with pymodbus --with pyserial"
        ) from exc

    return ModbusSerialClient(
        port=port,
        baudrate=baudrate,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=DEFAULT_CLIENT_TIMEOUT_S,
    )


def ensure_session_time(ctx: ExecutionContext) -> None:
    if ctx.session_deadline is not None and time.monotonic() > ctx.session_deadline:
        logging.getLogger(LOG_LOGGER_NAME).warning("Session timeout exceeded")
        raise SessionTimeoutError("session timeout exceeded")


def sleep_with_session_check(ctx: ExecutionContext, duration_s: float) -> None:
    end_time = time.monotonic() + max(duration_s, 0.0)
    while time.monotonic() < end_time:
        ensure_session_time(ctx)
        time.sleep(min(0.2, end_time - time.monotonic()))


def logic_elapsed(start: int, now: int) -> int:
    return (now - start) & 0xFFFF


def read_register(client: Any, slave_id: int, addr: int) -> tuple[bool, int | None, str]:
    try:
        result = client.read_holding_registers(address=addr, count=1, device_id=slave_id)
    except Exception as exc:  # pragma: no cover - hardware dependent
        return False, None, str(exc)

    if result is None:
        return False, None, "empty Modbus response"
    if result.isError():
        return False, None, str(result)
    registers = getattr(result, "registers", None)
    if not registers:
        return False, None, "response has no registers"
    return True, int(registers[0]), ""


def write_register(client: Any, slave_id: int, addr: int, value: int) -> tuple[bool, str]:
    try:
        result = client.write_register(address=addr, value=value, device_id=slave_id)
    except Exception as exc:  # pragma: no cover - hardware dependent
        return False, str(exc)

    if result is None:
        return False, "empty Modbus response"
    if result.isError():
        return False, str(result)
    return True, ""


def write_registers(client: Any, slave_id: int, addr: int, values: list[int]) -> tuple[bool, str]:
    try:
        result = client.write_registers(address=addr, values=values, device_id=slave_id)
    except Exception as exc:  # pragma: no cover - hardware dependent
        return False, str(exc)

    if result is None:
        return False, "empty Modbus response"
    if result.isError():
        return False, str(result)
    return True, ""


def parse_expected(raw_value: str, row_num: int) -> tuple[str, Any]:
    text = raw_value.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing expected value")

    if re.fullmatch(r"[bB]\d+", text):
        return "bit", int(text[1:])

    if "," in text:
        parts = [part.strip() for part in text.split(",", 1)]
        if len(parts) != 2:
            raise CsvParseError(f"row {row_num}: invalid range value {raw_value!r}")
        min_val = parse_int(parts[0], "range minimum", row_num)
        max_val = parse_int(parts[1], "range maximum", row_num)
        if min_val > max_val:
            raise CsvParseError(f"row {row_num}: range minimum is greater than maximum")
        return "range", (min_val, max_val)

    return "exact", parse_int(text, "expected value", row_num)


def parse_wait_value(raw_value: str, row_num: int) -> WaitSpec:
    text = raw_value.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing expected value")

    if ";" not in text:
        kind, expected = parse_expected(text, row_num)
        return WaitSpec(kind=kind, expected=expected)

    parts = [part.strip() for part in text.split(";")]
    expected_text = parts[0]
    if not expected_text:
        raise CsvParseError(f"row {row_num}: missing expected value")

    kind, expected = parse_expected(expected_text, row_num)
    timeout_s: float | None = None
    interval_s: float | None = None
    logic_timeout_s: float | None = None
    seen_keys: set[str] = set()

    for option in parts[1:]:
        if not option:
            raise CsvParseError(f"row {row_num}: invalid empty wait option")
        if "=" not in option:
            raise CsvParseError(
                f"row {row_num}: invalid wait option {option!r}; expected key=value"
            )

        raw_key, raw_option_value = option.split("=", 1)
        key = raw_key.strip().lower().replace("-", "_")
        if not key:
            raise CsvParseError(f"row {row_num}: invalid wait option {option!r}")
        if key in seen_keys:
            raise CsvParseError(f"row {row_num}: duplicate wait option {key!r}")
        seen_keys.add(key)

        option_value = raw_option_value.strip()
        if key == "timeout":
            timeout_s = parse_positive_float(option_value, "wait timeout", row_num)
            continue
        if key == "interval":
            interval_s = parse_positive_float(option_value, "wait interval", row_num)
            continue
        if key == "logic_timeout":
            logic_timeout_s = parse_positive_float(option_value, "logic timeout", row_num)
            continue
        raise CsvParseError(f"row {row_num}: unsupported wait option {key!r}")

    if timeout_s is not None and logic_timeout_s is not None:
        raise CsvParseError(
            f"row {row_num}: 'timeout' and 'logic_timeout' cannot both be specified"
        )
    if timeout_s is None and logic_timeout_s is None:
        raise CsvParseError(
            f"row {row_num}: 'timeout' or 'logic_timeout' is required "
            f"when using inline wait options"
        )

    return WaitSpec(
        kind=kind,
        expected=expected,
        timeout_s=timeout_s,
        interval_s=interval_s,
        logic_timeout_s=logic_timeout_s,
    )


def expected_label(kind: str, expected: Any) -> str:
    if kind == "exact":
        return str(expected)
    if kind == "range":
        return f"{expected[0]},{expected[1]}"
    if kind == "bit":
        return f"b{expected}"
    return str(expected)


def matches_expected(actual: int, kind: str, expected: Any) -> bool:
    if kind == "exact":
        return actual == expected
    if kind == "range":
        return expected[0] <= actual <= expected[1]
    if kind == "bit":
        return (actual & (1 << expected)) != 0
    return False


def _resolve_sim_prop_key(name_or_key: str) -> str:
    return SIM_PROP_MAP.get(name_or_key, name_or_key)


def _validate_sim_control_value(raw: str, row_num: int) -> None:
    if ":" not in raw:
        raise CsvParseError(f"row {row_num}: sim_control value must be property:value")
    prop, val = raw.split(":", 1)
    if not prop:
        raise CsvParseError(f"row {row_num}: sim_control missing property name")
    if prop not in SIM_CONTROL_WHITELIST:
        raise CsvParseError(
            f"row {row_num}: unknown sim_control property {prop!r}; "
            f"expected one of {', '.join(sorted(SIM_CONTROL_WHITELIST))}"
        )
    if prop == "power":
        if val.lower() not in SIM_POWER_ON_VALUES | SIM_POWER_OFF_VALUES:
            raise CsvParseError(
                f"row {row_num}: sim_control power value must be true/false"
            )
    elif prop == "target_temp":
        try:
            v = int(val)
        except ValueError:
            raise CsvParseError(
                f"row {row_num}: sim_control target_temp must be an integer"
            )
        if not 16 <= v <= 32:
            raise CsvParseError(
                f"row {row_num}: sim_control target_temp must be 16..32, got {v}"
            )
    elif prop in SIM_SUPPLY_DEMAND_PROPS:
        try:
            v = int(val)
        except ValueError:
            raise CsvParseError(
                f"row {row_num}: sim_control {prop} must be an integer"
            )
        if not 0 <= v <= 3:
            raise CsvParseError(
                f"row {row_num}: sim_control {prop} must be 0..3, got {v}"
            )
    else:
        try:
            int(val)
        except ValueError:
            raise CsvParseError(
                f"row {row_num}: sim_control {prop} value must be an integer"
            )


def _validate_sim_power_value(raw: str, row_num: int) -> None:
    if raw.lower() not in SIM_POWER_ON_VALUES | SIM_POWER_OFF_VALUES:
        raise CsvParseError(
            f"row {row_num}: sim_power value must be on/off/true/false/1/0"
        )


def _validate_sim_read_value(raw: str, row_num: int) -> None:
    if ":" not in raw:
        raise CsvParseError(f"row {row_num}: sim_read value must be property:expected")
    prop, expected_text = raw.split(":", 1)
    if not prop:
        raise CsvParseError(f"row {row_num}: sim_read missing property name")
    if prop not in SIM_PROP_MAP:
        raise CsvParseError(
            f"row {row_num}: unknown sim_read property {prop!r}; "
            f"expected one of {', '.join(sorted(SIM_PROP_MAP))}"
        )
    if prop in SIM_BOOL_PROPS:
        if expected_text.strip().lower() not in SIM_POWER_ON_VALUES | SIM_POWER_OFF_VALUES:
            raise CsvParseError(
                f"row {row_num}: sim_read {prop} expected must be true/false/on/off/1/0, "
                f"got {expected_text!r}"
            )
    else:
        parse_expected(expected_text, row_num)


def _validate_sim_wait_value(raw: str, row_num: int) -> None:
    if ";" in raw:
        main_part, *options = raw.split(";")
    else:
        main_part = raw
        options = []
    if ":" not in main_part:
        raise CsvParseError(f"row {row_num}: sim_wait value must be property:expected")
    prop, expected_text = main_part.split(":", 1)
    if not prop:
        raise CsvParseError(f"row {row_num}: sim_wait missing property name")
    if prop not in SIM_PROP_MAP:
        raise CsvParseError(
            f"row {row_num}: unknown sim_wait property {prop!r}; "
            f"expected one of {', '.join(sorted(SIM_PROP_MAP))}"
        )
    if prop in SIM_BOOL_PROPS:
        if expected_text.strip().lower() not in SIM_POWER_ON_VALUES | SIM_POWER_OFF_VALUES:
            raise CsvParseError(
                f"row {row_num}: sim_wait {prop} expected must be true/false/on/off/1/0, "
                f"got {expected_text!r}"
            )
    else:
        parse_expected(expected_text, row_num)
    seen_keys: set[str] = set()
    for opt in options:
        if not opt.strip():
            raise CsvParseError(f"row {row_num}: empty sim_wait option")
        if "=" not in opt:
            raise CsvParseError(
                f"row {row_num}: invalid sim_wait option {opt!r}; expected key=value"
            )
        raw_key, raw_val = opt.split("=", 1)
        key = raw_key.strip().lower().replace("-", "_")
        if not key:
            raise CsvParseError(f"row {row_num}: invalid sim_wait option {opt!r}")
        if key in seen_keys:
            raise CsvParseError(f"row {row_num}: duplicate sim_wait option {key!r}")
        seen_keys.add(key)
        if key not in ("timeout", "interval"):
            raise CsvParseError(f"row {row_num}: unsupported sim_wait option {key!r}")
        try:
            v = float(raw_val.strip())
        except ValueError:
            raise CsvParseError(
                f"row {row_num}: sim_wait {key} must be a number, got {raw_val!r}"
            )
        if v <= 0:
            raise CsvParseError(f"row {row_num}: sim_wait {key} must be > 0")


def _parse_slave_slave_id_option(raw: str, row_num: int) -> int | None:
    """Parse an optional 'slave_id=N' option; None when absent."""
    parts = [part.strip() for part in raw.split(";")]
    slave_id: int | None = None
    for part in parts:
        if not part:
            continue
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() == "slave_id":
            sid = parse_int(value.strip(), "slave_id", row_num)
            if sid < 1 or sid > 247:
                raise CsvParseError(f"row {row_num}: slave_id must be 1-247, got {sid}")
            if slave_id is not None:
                raise CsvParseError(f"row {row_num}: duplicate slave_id option")
            slave_id = sid
    return slave_id


def default_slave_app_path() -> Path:
    """Return the bundled EMS Modbus Slave app.py shipped with this skill.

    The slave ships in ``ems_modbus_slave/`` next to the skill directory
    (i.e. one level above ``scripts/``), which works both for the installed
    copy (~/.agents/skills/yzc-modbus-test) and the source repo
    (skills/modbus-test).
    """
    return Path(__file__).resolve().parents[1] / "ems_modbus_slave" / "app.py"


def _parse_slave_write_spec(raw: str, row_num: int) -> tuple[list[tuple[int, int]], int | None]:
    """Parse 'addr:value[;addr:value][;slave_id=N]' into (pairs, slave_id)."""
    parts = [part.strip() for part in raw.split(";")]
    pairs: list[tuple[int, int]] = []
    slave_id: int | None = None
    for part in parts:
        if not part:
            raise CsvParseError(f"row {row_num}: empty slave_write element")
        if "=" in part:
            key, value_text = part.split("=", 1)
            if key.strip().lower() != "slave_id":
                raise CsvParseError(
                    f"row {row_num}: invalid slave_write element {part!r}; expected addr:value"
                )
            sid = parse_int(value_text.strip(), "slave_id", row_num)
            if sid < 1 or sid > 247:
                raise CsvParseError(f"row {row_num}: slave_id must be 1-247, got {sid}")
            if slave_id is not None:
                raise CsvParseError(f"row {row_num}: duplicate slave_id option")
            slave_id = sid
            continue
        if ":" not in part:
            raise CsvParseError(
                f"row {row_num}: invalid slave_write element {part!r}; expected addr:value"
            )
        addr_text, value_text = part.split(":", 1)
        addr = parse_int(addr_text.strip(), "slave address", row_num)
        if addr < 0:
            raise CsvParseError(f"row {row_num}: slave address must be >= 0, got {addr}")
        value = parse_int(value_text.strip(), "slave value", row_num)
        pairs.append((addr, value))
    if not pairs:
        raise CsvParseError(
            f"row {row_num}: slave_write requires at least one addr:value pair"
        )
    return pairs, slave_id


def _parse_slave_read_spec(raw: str, row_num: int) -> tuple[int, str, Any, str, int | None]:
    """Parse 'addr:expected[;slave_id=N]' into (addr, kind, expected, label, slave_id)."""
    main_part, options = _split_slave_main_option(raw, row_num, "slave_read")
    if ":" not in main_part:
        raise CsvParseError(
            f"row {row_num}: slave_read value must be addr:expected[;slave_id=N]"
        )
    addr_text, expected_text = main_part.split(":", 1)
    addr = parse_int(addr_text.strip(), "slave address", row_num)
    if addr < 0:
        raise CsvParseError(f"row {row_num}: slave address must be >= 0, got {addr}")
    kind, expected = parse_expected(expected_text, row_num)
    label = expected_label(kind, expected)
    slave_id = _parse_slave_options(options, row_num, "slave_read")
    return addr, kind, expected, label, slave_id


def _parse_slave_wait_spec(
    raw: str, row_num: int
) -> tuple[int, str, Any, str, float, float, int | None]:
    """Parse 'addr:expected[;timeout=N][;interval=M][;slave_id=N]'."""
    main_part, options = _split_slave_main_option(raw, row_num, "slave_wait")
    if ":" not in main_part:
        raise CsvParseError(
            f"row {row_num}: slave_wait value must be addr:expected[;timeout=N][;interval=M][;slave_id=N]"
        )
    addr_text, expected_text = main_part.split(":", 1)
    addr = parse_int(addr_text.strip(), "slave address", row_num)
    if addr < 0:
        raise CsvParseError(f"row {row_num}: slave address must be >= 0, got {addr}")
    kind, expected = parse_expected(expected_text, row_num)
    label = expected_label(kind, expected)
    timeout_s = 0.0
    interval_s = 1.0
    slave_id: int | None = None
    seen_keys: set[str] = set()
    for option in options:
        if not option:
            raise CsvParseError(f"row {row_num}: empty slave_wait option")
        if "=" not in option:
            raise CsvParseError(
                f"row {row_num}: invalid slave_wait option {option!r}; expected key=value"
            )
        key, value_text = option.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if not key:
            raise CsvParseError(f"row {row_num}: invalid slave_wait option {option!r}")
        if key in seen_keys:
            raise CsvParseError(f"row {row_num}: duplicate slave_wait option {key!r}")
        seen_keys.add(key)
        if key == "timeout":
            timeout_s = parse_positive_float(value_text, "slave_wait timeout", row_num)
        elif key == "interval":
            interval_s = parse_positive_float(value_text, "slave_wait interval", row_num)
        elif key == "slave_id":
            sid = parse_int(value_text, "slave_id", row_num)
            if sid < 1 or sid > 247:
                raise CsvParseError(f"row {row_num}: slave_id must be 1-247, got {sid}")
            slave_id = sid
        else:
            raise CsvParseError(f"row {row_num}: unsupported slave_wait option {key!r}")
    return addr, kind, expected, label, timeout_s, interval_s, slave_id


def _split_slave_main_option(raw: str, row_num: int, func: str) -> tuple[str, list[str]]:
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing value")
    parts = [part.strip() for part in text.split(";")]
    if not parts or not parts[0]:
        raise CsvParseError(f"row {row_num}: missing value")
    return parts[0], parts[1:]


def _parse_slave_options(options: list[str], row_num: int, func: str) -> int | None:
    seen_keys: set[str] = set()
    slave_id: int | None = None
    for option in options:
        if not option:
            raise CsvParseError(f"row {row_num}: empty {func} option")
        if "=" not in option:
            raise CsvParseError(
                f"row {row_num}: invalid {func} option {option!r}; expected key=value"
            )
        key, value_text = option.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if not key:
            raise CsvParseError(f"row {row_num}: invalid {func} option {option!r}")
        if key in seen_keys:
            raise CsvParseError(f"row {row_num}: duplicate {func} option {key!r}")
        seen_keys.add(key)
        if key != "slave_id":
            raise CsvParseError(f"row {row_num}: unsupported {func} option {key!r}")
        sid = parse_int(value_text, "slave_id", row_num)
        if sid < 1 or sid > 247:
            raise CsvParseError(f"row {row_num}: slave_id must be 1-247, got {sid}")
        slave_id = sid
    return slave_id


# ── mqtt_* value parsing ────────────────────────────────────────────

def _parse_mqtt_send_value(
    raw: str, row_num: int
) -> tuple[dict[str, Any], int | None, float | None]:
    """Parse '<json-envelope>[;expect=N][;timeout=N]' into (envelope, expect, timeout).

    Options are split off from the right until the remainder parses as JSON, so a
    JSON string containing ';' (e.g. inside a description field) is tolerated.
    """
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing mqtt_send envelope")
    expect: int | None = None
    timeout: float | None = None
    seen: set[str] = set()
    while True:
        try:
            envelope = json.loads(text)
            break
        except json.JSONDecodeError:
            if ";" not in text:
                raise CsvParseError(
                    f"row {row_num}: mqtt_send value must be a JSON envelope with "
                    f"optional ;expect=N / ;timeout=N options"
                )
            head, _, tail = text.rpartition(";")
            if not head or "=" not in tail:
                raise CsvParseError(
                    f"row {row_num}: mqtt_send invalid trailing option {tail!r}"
                )
            key, value_text = tail.split("=", 1)
            key = key.strip().lower().replace("-", "_")
            if not key or key in seen:
                raise CsvParseError(
                    f"row {row_num}: mqtt_send duplicate/invalid option {key!r}"
                )
            seen.add(key)
            if key == "expect":
                expect = parse_int(value_text, "mqtt_send expect", row_num)
            elif key == "timeout":
                timeout = parse_positive_float(value_text, "mqtt_send timeout", row_num)
            else:
                raise CsvParseError(
                    f"row {row_num}: unsupported mqtt_send option {key!r}"
                )
            text = head
    if not isinstance(envelope, dict):
        raise CsvParseError(f"row {row_num}: mqtt_send envelope must be a JSON object")
    if not isinstance(envelope.get("method"), str):
        raise CsvParseError(
            f"row {row_num}: mqtt_send envelope must contain a string 'method'"
        )
    return envelope, expect, timeout


def _parse_mqtt_wait_value(
    raw: str, row_num: int
) -> tuple[str | None, str | None, int | None, float]:
    """Parse 'method=X;id=Y;code=Z;timeout=N' (at least one match criterion)."""
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing mqtt_wait criteria")
    method: str | None = None
    rid: str | None = None
    code: int | None = None
    timeout: float | None = None
    seen: set[str] = set()
    for part in text.split(";"):
        if not part:
            raise CsvParseError(f"row {row_num}: empty mqtt_wait option")
        if "=" not in part:
            raise CsvParseError(f"row {row_num}: invalid mqtt_wait option {part!r}")
        key, value_text = part.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if not key or key in seen:
            raise CsvParseError(f"row {row_num}: duplicate/invalid mqtt_wait option {key!r}")
        seen.add(key)
        if key == "method":
            method = value_text.strip()
        elif key == "id":
            rid = value_text.strip()
        elif key == "code":
            code = parse_int(value_text, "mqtt_wait code", row_num)
        elif key == "timeout":
            timeout = parse_positive_float(value_text, "mqtt_wait timeout", row_num)
        else:
            raise CsvParseError(f"row {row_num}: unsupported mqtt_wait option {key!r}")
    if method is None and rid is None and code is None:
        raise CsvParseError(
            f"row {row_num}: mqtt_wait requires at least one of method/id/code"
        )
    return method, rid, code, timeout if timeout is not None else 0.0


def _parse_mqtt_watch_value(raw: str, row_num: int) -> float:
    """Parse watch seconds; empty value uses the daemon default."""
    text = raw.strip()
    if not text:
        return 0.0
    return parse_positive_float(text, "mqtt_watch seconds", row_num)


def _parse_mqtt_set_value(raw: str, row_num: int) -> dict[str, Any]:
    """Parse 'key=value[;key=value]' into a validated config override dict."""
    text = raw.strip()
    if not text:
        raise CsvParseError(f"row {row_num}: missing mqtt_set options")
    overrides: dict[str, Any] = {}
    seen: set[str] = set()
    for part in text.split(";"):
        if not part:
            raise CsvParseError(f"row {row_num}: empty mqtt_set option")
        if "=" not in part:
            raise CsvParseError(f"row {row_num}: invalid mqtt_set option {part!r}")
        key, value_text = part.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if key == "ack_timeout":
            key = "timeout"
        if key not in MQTT_SET_KEYS:
            raise CsvParseError(
                f"row {row_num}: unsupported mqtt_set key {key!r}; "
                f"expected one of {', '.join(sorted(MQTT_SET_KEYS))}"
            )
        if key in seen:
            raise CsvParseError(f"row {row_num}: duplicate mqtt_set option {key!r}")
        seen.add(key)
        value_text = value_text.strip()
        if key in MQTT_SET_INT_KEYS:
            value = parse_int(value_text, f"mqtt_set {key}", row_num)
            if key == "qos" and not 0 <= value <= 2:
                raise CsvParseError(f"row {row_num}: mqtt_set qos must be 0-2, got {value}")
            if value < 0:
                raise CsvParseError(f"row {row_num}: mqtt_set {key} must be >= 0, got {value}")
        elif key in MQTT_SET_BOOL_KEYS:
            lowered = value_text.lower()
            if lowered in SIM_POWER_ON_VALUES:
                value = True
            elif lowered in SIM_POWER_OFF_VALUES:
                value = False
            else:
                raise CsvParseError(
                    f"row {row_num}: mqtt_set {key} must be true/false/1/0, got {value_text!r}"
                )
        else:
            if not value_text:
                raise CsvParseError(f"row {row_num}: mqtt_set {key} value is empty")
            value = value_text
        overrides[key] = value
    return overrides


def default_mqtt_app_path() -> Path:
    """Return the bundled EMS MQTT master app_cli.py shipped with this skill."""
    return Path(__file__).resolve().parents[1] / "ems_mqtt_master" / "app_cli.py"


def has_mqtt_steps(steps: list[Step]) -> bool:
    return any(step.func in MQTT_FUNCS for step in steps)


def slave_spawn(slave: SlaveContext) -> None:
    """Spawn the EMS Modbus Slave child process in --cli --stdio-control mode."""
    app_path = Path(slave.app_path)
    if not app_path.is_file():
        raise SlaveControlError(f"slave app not found: {app_path}")

    command = [sys.executable, str(app_path), "--cli", "--stdio-control"]
    if slave.port:
        command += ["--port", slave.port]
    if slave.profile:
        command += ["--profile", slave.profile]
    if slave.preset:
        command += ["--preset", slave.preset]
    if slave.baudrate:
        command += ["--baudrate", str(slave.baudrate)]
    if slave.slave_id:
        command += ["--slave-id", str(slave.slave_id)]
    if slave.respond_1_40:
        command += ["--respond-1-40"]

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    logging.getLogger(LOG_LOGGER_NAME).info("Spawning slave: %s", " ".join(command))
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise SlaveControlError(f"failed to launch slave app: {exc}") from exc

    slave.proc = proc

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            with slave.lock:
                slave.lines.append(line.rstrip("\r\n"))

    threading.Thread(target=_reader, daemon=True, name="slave-stdout").start()


def slave_collect_logs(slave: SlaveContext, limit: int = 12) -> str:
    with slave.lock:
        snapshot = list(slave.lines)
    if not snapshot:
        return "no slave output yet"
    return "; ".join(snapshot[-limit:])


def slave_wait_ready(slave: SlaveContext) -> dict[str, Any]:
    """Wait for the slave ready (or error) event within the ready timeout."""
    deadline = time.monotonic() + slave.ready_timeout
    while time.monotonic() < deadline:
        proc = slave.proc
        if proc is None or proc.poll() is not None:
            exit_code = proc.poll() if proc is not None else None
            raise SlaveControlError(
                f"slave process exited before ready (code={exit_code}): {slave_collect_logs(slave)}"
            )
        with slave.lock:
            for line in slave.lines:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                msg_type = message.get("type")
                if msg_type == "ready":
                    return message
                if msg_type == "error":
                    raise SlaveControlError(
                        f"slave startup failed: {message.get('message', line)}"
                    )
        time.sleep(0.05)
    raise SlaveControlError(
        f"slave not ready within {format_seconds(slave.ready_timeout)}s: "
        f"{slave_collect_logs(slave)}"
    )


def slave_command(slave: SlaveContext, op: str, timeout_s: float = 10.0, **params: Any) -> dict[str, Any]:
    """Send one JSON-RPC request and wait for the matching response."""
    proc = slave.proc
    if proc is None or proc.poll() is not None:
        raise SlaveControlError(
            f"slave process not running (op={op}): {slave_collect_logs(slave)}"
        )

    with slave.lock:
        request_id = slave.next_id
        slave.next_id += 1
    payload = {"type": "request", "id": request_id, "op": op, **params}
    command_line = json.dumps(payload)
    try:
        assert proc.stdin is not None
        proc.stdin.write(command_line + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise SlaveControlError(f"failed to send command {op}: {exc}") from exc

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SlaveControlError(
                f"slave process exited during {op} (code={proc.poll()}): "
                f"{slave_collect_logs(slave)}"
            )
        with slave.lock:
            for line in slave.lines:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "response" and message.get("id") == request_id:
                    if not message.get("ok", False):
                        raise SlaveControlError(
                            f"slave op {op} failed: {message.get('error', 'unknown error')}"
                        )
                    return message
        time.sleep(0.02)
    raise SlaveControlError(
        f"slave op {op} timed out after {format_seconds(timeout_s)}s: "
        f"{slave_collect_logs(slave)}"
    )


def slave_read_register(slave: SlaveContext, addr: int, slave_id: int | None) -> int:
    params: dict[str, Any] = {"address": addr}
    if slave_id is not None:
        params["slave_id"] = slave_id
    response = slave_command(slave, "get_register", **params)
    value = response.get("value")
    if not isinstance(value, int):
        raise SlaveControlError(f"slave get_register returned unexpected value: {value!r}")
    return value


def slave_write_register(slave: SlaveContext, addr: int, value: int, slave_id: int | None) -> None:
    params: dict[str, Any] = {"address": addr, "value": value}
    if slave_id is not None:
        params["slave_id"] = slave_id
    slave_command(slave, "set_register", **params)


def slave_shutdown(slave: SlaveContext) -> None:
    """Gracefully stop the slave child process; kill as a fallback."""
    proc = slave.proc
    if proc is None:
        return
    if proc.poll() is None:
        try:
            slave_command(slave, "shutdown", timeout_s=min(slave.stop_timeout, 10.0))
        except SlaveControlError as exc:
            logging.getLogger(LOG_LOGGER_NAME).warning("Graceful slave shutdown failed: %s", exc)
        try:
            proc.wait(timeout=slave.stop_timeout)
        except subprocess.TimeoutExpired:
            slave_kill(slave)
    slave.proc = None
    slave.started = False


def slave_kill(slave: SlaveContext) -> None:
    proc = slave.proc
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    slave.proc = None
    slave.started = False


def has_slave_steps(steps: list[Step]) -> bool:
    return any(step.func in SLAVE_FUNCS for step in steps)


def mqtt_spawn(mqtt: MqttContext) -> None:
    """Spawn the EMS MQTT master daemon in stdio JSON-RPC mode."""
    app_path = Path(mqtt.app_path)
    if not app_path.is_file():
        raise MqttControlError(f"mqtt app not found: {app_path}")

    command = [sys.executable, str(app_path), "session", "--stdio"]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    logging.getLogger(LOG_LOGGER_NAME).info(
        "Spawning mqtt daemon: %s", " ".join(command)
    )
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise MqttControlError(f"failed to launch mqtt app: {exc}") from exc

    mqtt.proc = proc

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            with mqtt.lock:
                mqtt.lines.append(line.rstrip("\r\n"))

    threading.Thread(target=_reader, daemon=True, name="mqtt-stdout").start()


def mqtt_collect_logs(mqtt: MqttContext, limit: int = 12) -> str:
    with mqtt.lock:
        snapshot = list(mqtt.lines)
    if not snapshot:
        return "no mqtt output yet"
    return "; ".join(snapshot[-limit:])


def mqtt_wait_ready(mqtt: MqttContext) -> None:
    """Wait for the daemon ready event within the connect timeout."""
    deadline = time.monotonic() + mqtt.connect_timeout
    while time.monotonic() < deadline:
        proc = mqtt.proc
        if proc is None or proc.poll() is not None:
            exit_code = proc.poll() if proc is not None else None
            raise MqttControlError(
                f"mqtt daemon exited before ready (code={exit_code}): {mqtt_collect_logs(mqtt)}"
            )
        with mqtt.lock:
            for line in mqtt.lines:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                msg_type = message.get("type")
                if msg_type == "ready":
                    return
                if msg_type == "error":
                    raise MqttControlError(
                        f"mqtt daemon startup failed: {message.get('message', line)}"
                    )
        time.sleep(0.05)
    raise MqttControlError(
        f"mqtt daemon not ready within {format_seconds(mqtt.connect_timeout)}s: "
        f"{mqtt_collect_logs(mqtt)}"
    )


def mqtt_command(
    mqtt: MqttContext, op: str, timeout_s: float = 10.0, **params: Any
) -> dict[str, Any]:
    """Send one JSON-RPC request and wait for the matching response."""
    proc = mqtt.proc
    if proc is None or proc.poll() is not None:
        raise MqttControlError(
            f"mqtt daemon not running (op={op}): {mqtt_collect_logs(mqtt)}"
        )

    with mqtt.lock:
        request_id = mqtt.next_id
        mqtt.next_id += 1
    payload = {"type": "request", "id": request_id, "op": op, **params}
    command_line = json.dumps(payload)
    try:
        assert proc.stdin is not None
        proc.stdin.write(command_line + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise MqttControlError(f"failed to send command {op}: {exc}") from exc

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise MqttControlError(
                f"mqtt daemon exited during {op} (code={proc.poll()}): "
                f"{mqtt_collect_logs(mqtt)}"
            )
        with mqtt.lock:
            for line in mqtt.lines:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "response" and message.get("id") == request_id:
                    if not message.get("ok", False):
                        raise MqttControlError(
                            f"mqtt op {op} failed: {message.get('error', 'unknown error')}"
                        )
                    return message
        time.sleep(0.02)
    raise MqttControlError(
        f"mqtt op {op} timed out after {format_seconds(timeout_s)}s: "
        f"{mqtt_collect_logs(mqtt)}"
    )


def mqtt_connect(mqtt: MqttContext) -> dict[str, Any]:
    """Establish the MQTT connection with the accumulated config overrides."""
    params: dict[str, Any] = {"config": dict(mqtt.config_overrides)}
    if mqtt.config_path:
        params["config_path"] = mqtt.config_path
    return mqtt_command(mqtt, "connect", **params)


def mqtt_effective_config(mqtt: MqttContext) -> dict[str, Any]:
    """Resolve the effective MQTT config in the parent process.

    Same precedence as the daemon ``connect`` op: bundled ``config/config.json``
    (when no ``--mqtt-config``) -> explicit config file -> ``mqtt_set`` overrides.
    Used to expand ``${key}`` placeholders in ``mqtt_send`` envelopes.
    """
    cfg: dict[str, Any] = {}
    if mqtt.config_path:
        config_file = Path(mqtt.config_path)
        if not config_file.is_file():
            raise MqttControlError(f"mqtt config not found: {config_file}")
        try:
            cfg = json.loads(config_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise MqttControlError(
                f"mqtt config invalid JSON: {config_file} ({exc})"
            ) from exc
        if not isinstance(cfg, dict):
            raise MqttControlError(f"mqtt config must be a JSON object: {config_file}")
    else:
        fallback = Path(mqtt.app_path).resolve().parent / "config" / "config.json"
        if fallback.is_file():
            try:
                loaded = json.loads(fallback.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    cfg = loaded
            except json.JSONDecodeError:
                pass
    cfg = {**cfg, **mqtt.config_overrides}
    return cfg


_MQTT_VAR_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def mqtt_expand_vars(text: str, cfg: dict[str, Any], row_num: int) -> str:
    """Expand ``${key}`` placeholders in a mqtt_send value from the effective config."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in cfg:
            raise CsvParseError(
                f"row {row_num}: mqtt placeholder ${{{key}}} has no value in config"
            )
        value = cfg[key]
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return _MQTT_VAR_RE.sub(_replace, text)


def mqtt_shutdown(mqtt: MqttContext) -> None:
    """Gracefully stop the mqtt daemon; kill as a fallback."""
    proc = mqtt.proc
    if proc is None:
        return
    if proc.poll() is None:
        try:
            mqtt_command(mqtt, "shutdown", timeout_s=min(mqtt.stop_timeout, 10.0))
        except MqttControlError as exc:
            logging.getLogger(LOG_LOGGER_NAME).warning(
                "Graceful mqtt shutdown failed: %s", exc
            )
        try:
            proc.wait(timeout=mqtt.stop_timeout)
        except subprocess.TimeoutExpired:
            mqtt_kill(mqtt)
    mqtt.proc = None
    mqtt.started = False
    mqtt.connected = False


def mqtt_kill(mqtt: MqttContext) -> None:
    proc = mqtt.proc
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    mqtt.proc = None
    mqtt.started = False
    mqtt.connected = False


def _sim_http_json(sim: SimContext, method: str, path: str, body: dict | None = None) -> Any:
    url = sim.api_base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=sim.http_timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SimApiError(f"HTTP request failed: {exc}") from exc
    except (urllib.error.URLError, ConnectionError) as exc:
        raise SimUnavailableError(f"HTTP request failed: {exc}") from exc
    except TimeoutError as exc:
        raise SimUnavailableError(f"HTTP request timed out: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SimApiError(f"invalid JSON response: {exc}") from exc
    if not isinstance(payload, dict):
        raise SimApiError(f"unexpected response shape: {type(payload).__name__}")
    if not payload.get("ok", False):
        raise SimApiError(payload.get("error", "unknown API error"))
    return payload.get("data")


def update_sim_device_map(sim: SimContext, devices: Any) -> None:
    if not isinstance(devices, list):
        raise SimApiError("unexpected /api/devices response shape")

    new_map: dict[int, str] = {}
    for dev in devices:
        if not isinstance(dev, dict):
            raise SimApiError("unexpected device entry in /api/devices response")
        sn = dev.get("sn", "")
        idx = dev.get("device_index", 0)
        if idx and isinstance(idx, int) and idx > 0 and sn:
            if idx in new_map:
                raise SimApiError(
                    f"duplicate device_index {idx} (sn={new_map[idx]} and sn={sn})"
                )
            new_map[idx] = sn
    sim._index_to_sn.update(new_map)


def sim_resolve_sn(sim: SimContext, device_index: int) -> str:
    if device_index in sim._index_to_sn:
        return sim._index_to_sn[device_index]
    devices = _sim_http_json(sim, "GET", "/api/devices")
    update_sim_device_map(sim, devices)
    if device_index in sim._index_to_sn:
        return sim._index_to_sn[device_index]
    available = sorted(sim._index_to_sn.keys())
    raise SimApiError(
        f"DeviceIndex {device_index} not found; available: {available}"
    )


def sim_read_property(sim: SimContext, sn: str, prop_key: str) -> Any:
    path = f"/api/devices/{urllib.parse.quote(sn, safe='')}"
    data = _sim_http_json(sim, "GET", path)
    if not isinstance(data, dict):
        raise SimApiError("device response has no data")
    hw = data.get("hardware")
    if not isinstance(hw, dict):
        raise SimApiError("device response has no hardware snapshot")
    if prop_key not in hw:
        raise SimApiError(f"property {prop_key!r} not found in hardware snapshot")
    return hw[prop_key]


def sim_control_property(sim: SimContext, sn: str, prop: str, value: Any) -> None:
    path = f"/api/devices/{urllib.parse.quote(sn, safe='')}/control"
    _sim_http_json(sim, "POST", path, {"property": prop, "value": value})


def sim_power_device(sim: SimContext, sn: str, on: bool) -> None:
    path = f"/api/devices/{urllib.parse.quote(sn, safe='')}/power"
    _sim_http_json(sim, "POST", path, {"on": on})


def _parse_sim_control_value(raw: str) -> tuple[str, Any]:
    prop, val_str = raw.split(":", 1)
    if prop == "power":
        return prop, val_str.lower() in SIM_POWER_ON_VALUES
    return prop, int(val_str)


def _parse_sim_power_value(raw: str) -> bool:
    return raw.strip().lower() in SIM_POWER_ON_VALUES


def _parse_sim_read_value(raw: str) -> tuple[str, str, Any, str]:
    prop, expected_text = raw.split(":", 1)
    prop_key = _resolve_sim_prop_key(prop)
    if prop in SIM_BOOL_PROPS:
        on = expected_text.strip().lower() in SIM_POWER_ON_VALUES
        return prop_key, "exact", 1 if on else 0, str(1 if on else 0)
    kind, expected = parse_expected(expected_text, 0)
    label = expected_label(kind, expected)
    return prop_key, kind, expected, label


def _parse_sim_wait_value(raw: str) -> tuple[str, str, Any, str, float, float]:
    prop = ""
    expected_text = ""
    timeout_s = 0.0
    interval_s = 1.0
    if ";" in raw:
        main_part, *options = raw.split(";")
    else:
        main_part = raw
        options = []
    prop, expected_text = main_part.split(":", 1)
    prop_key = _resolve_sim_prop_key(prop)
    for opt in options:
        k, v = opt.split("=", 1)
        if k.strip().lower() == "timeout":
            timeout_s = float(v.strip())
        elif k.strip().lower() == "interval":
            interval_s = float(v.strip())
    if prop in SIM_BOOL_PROPS:
        on = expected_text.strip().lower() in SIM_POWER_ON_VALUES
        return prop_key, "exact", 1 if on else 0, str(1 if on else 0), timeout_s, interval_s
    kind, expected = parse_expected(expected_text, 0)
    label = expected_label(kind, expected)
    return prop_key, kind, expected, label, timeout_s, interval_s


def _normalize_sim_actual(prop_key: str, raw_value: Any) -> int:
    try:
        if prop_key in ("2_1",):
            return 1 if raw_value else 0
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise SimApiError(
            f"non-numeric hardware value for {prop_key}: {raw_value!r}"
        ) from exc


def requires_serial(step: Step) -> bool:
    if step.func in SIM_FUNCS:
        return False
    if step.func in SLAVE_FUNCS:
        return False
    if step.func in MQTT_FUNCS:
        return False
    if step.func == FUNC_DELAY:
        return step.addr != 0
    if step.func == FUNC_SET_SLAVE:
        return False
    return True


def format_seconds(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_wait_summary(addr: int, wait_spec: WaitSpec) -> str:
    summary = f"wait {addr} expected={expected_label(wait_spec.kind, wait_spec.expected)}"
    if wait_spec.timeout_s is not None:
        summary += f" timeout={format_seconds(wait_spec.timeout_s)}s"
    if wait_spec.logic_timeout_s is not None:
        summary += f" logic_timeout={format_seconds(wait_spec.logic_timeout_s)}s"
    if wait_spec.interval_s is not None:
        summary += f" interval={format_seconds(wait_spec.interval_s)}s"
    return summary


def build_wait_pass_detail(actual: int, ctx: ExecutionContext, label: str) -> str:
    detail = f"expected={label} actual={actual}"
    if ctx.start_time_value is not None:
        ok_now, now_value, _ = read_register(ctx.client, ctx.slave_id, ctx.time_addr)
        if ok_now and now_value is not None:
            detail += f" elapsed={logic_elapsed(ctx.start_time_value, now_value)}"
    return detail


def build_wait_timeout_detail(
    *,
    label: str,
    last_actual: int | None,
    last_error: str,
    elapsed_s: float,
    timeout_s: float,
) -> str:
    detail = f"expected={label}"
    if last_actual is not None:
        detail += f" actual={last_actual}"
    elif last_error:
        detail += f" error={last_error}"
    else:
        detail += " actual=none"
    detail += f" elapsed={format_seconds(elapsed_s)}s timeout={format_seconds(timeout_s)}s"
    return detail


def execute_step(step: Step, ctx: ExecutionContext) -> StepResult:
    ensure_session_time(ctx)
    index = step.row_num

    if step.func == FUNC_SET_SLAVE:
        new_id = parse_int(step.value, "slave id", step.row_num)
        if new_id < 1 or new_id > 247:
            return StepResult(
                index, step.func, "FAIL",
                f"set_slave {step.value}",
                f"slave id must be 1-247, got {new_id}",
            )
        old_id = ctx.slave_id
        ctx.slave_id = new_id
        ctx.start_time_value = None
        return StepResult(
            index, step.func, "PASS",
            f"set_slave {old_id} -> {new_id}",
        )

    if step.func == FUNC_WRITE:
        value = parse_int(step.value, "write value", step.row_num)
        summary = f"write {step.addr}={value}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        max_attempts = WRITE_RETRY_COUNT + 1
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            ensure_session_time(ctx)
            ok, error = write_register(ctx.client, ctx.slave_id, step.addr, value)
            if ok:
                detail = "" if attempt == 1 else f"attempts={attempt}"
                return StepResult(index, step.func, "PASS", summary, detail)
            last_error = error or "write failed"
            if attempt < max_attempts:
                sleep_with_session_check(ctx, WRITE_RETRY_DELAY_S)
        return StepResult(
            index,
            step.func,
            "FAIL",
            summary,
            f"{last_error} attempts={max_attempts}",
        )

    if step.func == FUNC_WRITE_MULTI:
        values = parse_int_list(step.value, "write_multi value", step.row_num)
        value_label = ",".join(str(v) for v in values)
        summary = f"write_multi {step.addr}={value_label}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        max_attempts = WRITE_RETRY_COUNT + 1
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            ensure_session_time(ctx)
            ok, error = write_registers(ctx.client, ctx.slave_id, step.addr, values)
            if ok:
                detail = "" if attempt == 1 else f"attempts={attempt}"
                return StepResult(index, step.func, "PASS", summary, detail)
            last_error = error or "write_multi failed"
            if attempt < max_attempts:
                sleep_with_session_check(ctx, WRITE_RETRY_DELAY_S)
        return StepResult(
            index,
            step.func,
            "FAIL",
            summary,
            f"{last_error} attempts={max_attempts}",
        )

    if step.func == FUNC_DELAY:
        base_delay = float(step.value)
        total_delay = base_delay
        if ctx.dry_run:
            return StepResult(
                index,
                step.func,
                "PASS",
                f"delay {format_seconds(base_delay)}s",
            )

        if step.addr != 0:
            ok, extra_delay, error = read_register(ctx.client, ctx.slave_id, step.addr)
            if not ok or extra_delay is None:
                return StepResult(
                    index,
                    step.func,
                    "FAIL",
                    f"delay {format_seconds(base_delay)}s + addr {step.addr}",
                    error,
                )
            total_delay += float(extra_delay)

        summary = f"delay {format_seconds(total_delay)}s"
        sleep_with_session_check(ctx, total_delay)
        return StepResult(index, step.func, "PASS", summary)

    if step.func == FUNC_LOGIC_DELAY:
        target_logic_s = float(step.value)
        logic_addr = step.addr if step.addr != 0 else ctx.time_addr
        logic_limit = math.ceil(target_logic_s)
        summary = f"logic_delay {format_seconds(target_logic_s)}s"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)

        ok_start, logic_start, err_start = read_register(
            ctx.client, ctx.slave_id, logic_addr
        )
        if not ok_start or logic_start is None:
            return StepResult(
                index, step.func, "FAIL", summary,
                f"logic time read failed: {err_start}",
            )

        while True:
            ensure_session_time(ctx)
            ok_t, logic_now, err_t = read_register(
                ctx.client, ctx.slave_id, logic_addr
            )
            if not ok_t or logic_now is None:
                return StepResult(
                    index, step.func, "FAIL", summary,
                    f"logic time read failed: {err_t}",
                )
            le = logic_elapsed(logic_start, logic_now)
            if le >= logic_limit:
                return StepResult(
                    index, step.func, "PASS", summary,
                    f"logic_elapsed={le}s",
                )
            sleep_with_session_check(ctx, ctx.wait_interval)

    if step.func == FUNC_READ:
        kind, expected = parse_expected(step.value, step.row_num)
        label = expected_label(kind, expected)
        summary = f"read {step.addr} expected={label}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        ok, actual, error = read_register(ctx.client, ctx.slave_id, step.addr)
        if not ok or actual is None:
            return StepResult(index, step.func, "FAIL", summary, error)
        detail = f"expected={label} actual={actual}"
        if matches_expected(actual, kind, expected):
            return StepResult(index, step.func, "PASS", summary, detail)
        return StepResult(index, step.func, "FAIL", summary, detail)

    if step.func == FUNC_WAIT:
        wait_spec = parse_wait_value(step.value, step.row_num)
        label = expected_label(wait_spec.kind, wait_spec.expected)
        summary = format_wait_summary(step.addr, wait_spec)
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)

        last_actual: int | None = None
        last_error = ""
        if wait_spec.timeout_s is None and wait_spec.logic_timeout_s is None:
            for attempt in range(1, ctx.wait_timeout + 1):
                ensure_session_time(ctx)
                ok, actual, error = read_register(ctx.client, ctx.slave_id, step.addr)
                if ok and actual is not None:
                    last_actual = actual
                    if matches_expected(actual, wait_spec.kind, wait_spec.expected):
                        detail = build_wait_pass_detail(actual, ctx, label)
                        return StepResult(index, step.func, "PASS", summary, detail)
                    last_error = f"expected={label} actual={actual}"
                else:
                    last_error = error or "read failed"

                if attempt < ctx.wait_timeout:
                    end_sleep = time.monotonic() + ctx.wait_interval
                    while time.monotonic() < end_sleep:
                        ensure_session_time(ctx)
                        time.sleep(min(0.2, end_sleep - time.monotonic()))

            detail = last_error or f"expected={label} actual={last_actual}"
            return StepResult(index, step.func, "FAIL", summary, detail)

        if wait_spec.timeout_s is not None:
            wait_started = time.monotonic()
            deadline = wait_started + wait_spec.timeout_s
            poll_interval = wait_spec.interval_s
            if poll_interval is None:
                poll_interval = ctx.wait_interval

            while True:
                ensure_session_time(ctx)
                ok, actual, error = read_register(ctx.client, ctx.slave_id, step.addr)
                if ok and actual is not None:
                    last_actual = actual
                    if matches_expected(actual, wait_spec.kind, wait_spec.expected):
                        detail = build_wait_pass_detail(actual, ctx, label)
                        return StepResult(index, step.func, "PASS", summary, detail)
                    last_error = f"expected={label} actual={actual}"
                else:
                    last_error = error or "read failed"

                now = time.monotonic()
                if now >= deadline:
                    break

                end_sleep = min(now + poll_interval, deadline)
                while time.monotonic() < end_sleep:
                    ensure_session_time(ctx)
                    time.sleep(min(0.2, end_sleep - time.monotonic()))

            detail = build_wait_timeout_detail(
                label=label,
                last_actual=last_actual,
                last_error=last_error,
                elapsed_s=time.monotonic() - wait_started,
                timeout_s=wait_spec.timeout_s,
            )
            return StepResult(index, step.func, "FAIL", summary, detail)

        if wait_spec.logic_timeout_s is not None:
            ok_start, logic_start, err_start = read_register(
                ctx.client, ctx.slave_id, ctx.time_addr
            )
            if not ok_start or logic_start is None:
                return StepResult(
                    index, step.func, "FAIL", summary,
                    f"logic time read failed: {err_start}",
                )

            poll_interval = wait_spec.interval_s
            if poll_interval is None:
                poll_interval = ctx.wait_interval
            logic_limit = math.ceil(wait_spec.logic_timeout_s)

            while True:
                ensure_session_time(ctx)
                ok_t, logic_now, err_t = read_register(
                    ctx.client, ctx.slave_id, ctx.time_addr
                )
                if not ok_t or logic_now is None:
                    return StepResult(
                        index, step.func, "FAIL", summary,
                        f"expected={label} actual={last_actual} logic time read failed: {err_t}",
                    )

                le = logic_elapsed(logic_start, logic_now)
                if le >= logic_limit:
                    ok, actual, _ = read_register(
                        ctx.client, ctx.slave_id, step.addr
                    )
                    if ok and actual is not None:
                        last_actual = actual
                    return StepResult(
                        index, step.func, "FAIL", summary,
                        f"expected={label} actual={last_actual} "
                        f"logic_elapsed={le}s logic_timeout={logic_limit}s",
                    )

                ok, actual, error = read_register(
                    ctx.client, ctx.slave_id, step.addr
                )
                if ok and actual is not None:
                    last_actual = actual
                    if matches_expected(actual, wait_spec.kind, wait_spec.expected):
                        return StepResult(
                            index, step.func, "PASS", summary,
                            f"expected={label} actual={actual} logic_elapsed={le}s",
                        )
                    last_error = f"expected={label} actual={actual}"
                else:
                    last_error = error or "read failed"

                sleep_with_session_check(ctx, poll_interval)

    if step.func == FUNC_SIM_CONTROL:
        prop, value = _parse_sim_control_value(step.value)
        summary = f"sim_control dev={step.addr} {prop}={value}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        try:
            sim = ctx.sim
            if sim is None:
                return StepResult(index, step.func, "FAIL", summary, "sim not initialized")
            sn = sim_resolve_sn(sim, step.addr)
            sim_control_property(sim, sn, prop, value)
            return StepResult(index, step.func, "PASS", summary)
        except SimApiError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_SIM_POWER:
        on = _parse_sim_power_value(step.value)
        summary = f"sim_power dev={step.addr} {'on' if on else 'off'}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        try:
            sim = ctx.sim
            if sim is None:
                return StepResult(index, step.func, "FAIL", summary, "sim not initialized")
            sn = sim_resolve_sn(sim, step.addr)
            sim_power_device(sim, sn, on)
            return StepResult(index, step.func, "PASS", summary)
        except SimApiError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_SIM_READ:
        prop_key, kind, expected, label = _parse_sim_read_value(step.value)
        summary = f"sim_read dev={step.addr} {label}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        try:
            sim = ctx.sim
            if sim is None:
                return StepResult(index, step.func, "FAIL", summary, "sim not initialized")
            sn = sim_resolve_sn(sim, step.addr)
            raw_val = sim_read_property(sim, sn, prop_key)
            actual = _normalize_sim_actual(prop_key, raw_val)
            detail = f"expected={label} actual={actual}"
            if matches_expected(actual, kind, expected):
                return StepResult(index, step.func, "PASS", summary, detail)
            return StepResult(index, step.func, "FAIL", summary, detail)
        except SimApiError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_SIM_WAIT:
        prop_key, kind, expected, label, inline_timeout, interval_s = _parse_sim_wait_value(step.value)
        timeout_s = inline_timeout if inline_timeout > 0 else float(ctx.wait_timeout)
        summary = f"sim_wait dev={step.addr} {label} timeout={format_seconds(timeout_s)}s"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        try:
            sim = ctx.sim
            if sim is None:
                return StepResult(index, step.func, "FAIL", summary, "sim not initialized")
            sn = sim_resolve_sn(sim, step.addr)
        except SimApiError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

        wait_started = time.monotonic()
        deadline = wait_started + timeout_s
        last_actual: int | None = None
        last_error = ""
        while True:
            ensure_session_time(ctx)
            try:
                raw_val = sim_read_property(sim, sn, prop_key)
                actual = _normalize_sim_actual(prop_key, raw_val)
                last_actual = actual
                if matches_expected(actual, kind, expected):
                    return StepResult(
                        index, step.func, "PASS", summary,
                        f"expected={label} actual={actual}",
                    )
                last_error = f"expected={label} actual={actual}"
            except SimApiError as exc:
                last_error = str(exc)

            if time.monotonic() >= deadline:
                break
            sleep_with_session_check(ctx, min(interval_s, deadline - time.monotonic()))

        detail = build_wait_timeout_detail(
            label=label,
            last_actual=last_actual,
            last_error=last_error,
            elapsed_s=time.monotonic() - wait_started,
            timeout_s=timeout_s,
        )
        return StepResult(index, step.func, "FAIL", summary, detail)

    if step.func == FUNC_SLAVE_START:
        summary = "slave_start"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.slave is None:
            return StepResult(
                index, step.func, "FAIL", summary, "slave not configured (--slave-app)"
            )
        if ctx.slave.started:
            return StepResult(
                index, step.func, "FAIL", summary, "slave already started"
            )
        try:
            slave_spawn(ctx.slave)
            ready = slave_wait_ready(ctx.slave)
            ctx.slave.started = True
            detail = f"pid={ready.get('pid')} port={ready.get('port')} profile={ready.get('profile')}"
            return StepResult(index, step.func, "PASS", summary, detail)
        except SlaveControlError as exc:
            slave_kill(ctx.slave)
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_SLAVE_STOP:
        summary = "slave_stop"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.slave is None or not ctx.slave.started:
            return StepResult(
                index, step.func, "FAIL", summary, "slave not started"
            )
        proc = ctx.slave.proc
        try:
            slave_shutdown(ctx.slave)
            exit_code = proc.poll() if proc is not None else None
            return StepResult(
                index, step.func, "PASS", summary,
                f"exit={exit_code}",
            )
        except SlaveControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_SLAVE_WRITE:
        pairs, slave_id = _parse_slave_write_spec(step.value, step.row_num)
        summary = "slave_write dev={} {}".format(
            step.addr,
            ";".join(f"{addr}:{value}" for addr, value in pairs),
        )
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.slave is None or not ctx.slave.started:
            return StepResult(
                index, step.func, "FAIL", summary, "slave not started"
            )
        try:
            for addr, value in pairs:
                slave_write_register(ctx.slave, addr, value, slave_id)
            return StepResult(index, step.func, "PASS", summary)
        except SlaveControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_SLAVE_READ:
        addr, kind, expected, label, slave_id = _parse_slave_read_spec(
            step.value, step.row_num
        )
        summary = f"slave_read dev={step.addr} addr={addr} expected={label}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.slave is None or not ctx.slave.started:
            return StepResult(
                index, step.func, "FAIL", summary, "slave not started"
            )
        try:
            actual = slave_read_register(ctx.slave, addr, slave_id)
        except SlaveControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))
        detail = f"expected={label} actual={actual}"
        if matches_expected(actual, kind, expected):
            return StepResult(index, step.func, "PASS", summary, detail)
        return StepResult(index, step.func, "FAIL", summary, detail)

    if step.func == FUNC_SLAVE_WAIT:
        addr, kind, expected, label, inline_timeout, interval_s, slave_id = (
            _parse_slave_wait_spec(step.value, step.row_num)
        )
        timeout_s = inline_timeout if inline_timeout > 0 else float(ctx.wait_timeout)
        summary = (
            f"slave_wait dev={step.addr} addr={addr} expected={label} "
            f"timeout={format_seconds(timeout_s)}s"
        )
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.slave is None or not ctx.slave.started:
            return StepResult(
                index, step.func, "FAIL", summary, "slave not started"
            )
        wait_started = time.monotonic()
        deadline = wait_started + timeout_s
        last_actual: int | None = None
        last_error = ""
        while True:
            ensure_session_time(ctx)
            try:
                actual = slave_read_register(ctx.slave, addr, slave_id)
                last_actual = actual
                if matches_expected(actual, kind, expected):
                    return StepResult(
                        index, step.func, "PASS", summary,
                        f"expected={label} actual={actual}",
                    )
                last_error = f"expected={label} actual={actual}"
            except SlaveControlError as exc:
                last_error = str(exc)

            if time.monotonic() >= deadline:
                break
            sleep_with_session_check(ctx, min(interval_s, deadline - time.monotonic()))

        detail = build_wait_timeout_detail(
            label=label,
            last_actual=last_actual,
            last_error=last_error,
            elapsed_s=time.monotonic() - wait_started,
            timeout_s=timeout_s,
        )
        return StepResult(index, step.func, "FAIL", summary, detail)

    if step.func == FUNC_MQTT_SET:
        overrides = _parse_mqtt_set_value(step.value, step.row_num)
        summary = "mqtt_set " + ";".join(f"{k}={v}" for k, v in overrides.items())
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.mqtt is None:
            return StepResult(
                index, step.func, "FAIL", summary, "mqtt not configured (--mqtt-app)"
            )
        if ctx.mqtt.connected:
            return StepResult(
                index,
                step.func,
                "FAIL",
                summary,
                "mqtt_set must precede mqtt_start or follow mqtt_stop",
            )
        ctx.mqtt.config_overrides.update(overrides)
        return StepResult(index, step.func, "PASS", summary)

    if step.func == FUNC_MQTT_START:
        summary = "mqtt_start"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.mqtt is None:
            return StepResult(
                index, step.func, "FAIL", summary, "mqtt not configured (--mqtt-app)"
            )
        if ctx.mqtt.started:
            return StepResult(index, step.func, "FAIL", summary, "mqtt already started")
        try:
            mqtt_spawn(ctx.mqtt)
            mqtt_wait_ready(ctx.mqtt)
            ctx.mqtt.started = True
            response = mqtt_connect(ctx.mqtt)
            ctx.mqtt.connected = True
            detail = str((response.get("data") or {}).get("detail", ""))
            return StepResult(index, step.func, "PASS", summary, detail)
        except MqttControlError as exc:
            mqtt_kill(ctx.mqtt)
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_MQTT_STOP:
        summary = "mqtt_stop"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.mqtt is None or not ctx.mqtt.started:
            return StepResult(index, step.func, "FAIL", summary, "mqtt not started")
        proc = ctx.mqtt.proc
        try:
            mqtt_shutdown(ctx.mqtt)
            exit_code = proc.poll() if proc is not None else None
            return StepResult(index, step.func, "PASS", summary, f"exit={exit_code}")
        except MqttControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_MQTT_SEND:
        value_text = step.value
        if not ctx.dry_run and ctx.mqtt is not None:
            try:
                cfg = mqtt_effective_config(ctx.mqtt)
                value_text = mqtt_expand_vars(step.value, cfg, step.row_num)
            except (MqttControlError, CsvParseError) as exc:
                return StepResult(index, step.func, "FAIL", "mqtt_send", str(exc))
        envelope, expect, timeout = _parse_mqtt_send_value(value_text, step.row_num)
        method = envelope.get("method", "")
        req_id = envelope.get("id")
        summary = f"mqtt_send {method} id={req_id}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.mqtt is None or not ctx.mqtt.connected:
            return StepResult(
                index, step.func, "FAIL", summary, "mqtt not connected (run mqtt_start first)"
            )
        params: dict[str, Any] = {"envelope": envelope}
        if expect is not None:
            params["expect"] = expect
        if timeout is not None:
            params["timeout"] = timeout
        try:
            send_timeout = float(params.get("timeout", 10.0)) if "timeout" in params else 10.0
            response = mqtt_command(
                ctx.mqtt, "send", timeout_s=send_timeout + 5.0, **params
            )
            data = response.get("data") or {}
            if data.get("no_ack"):
                return StepResult(
                    index, step.func, "PASS", summary, "no ack (fire-and-forget)"
                )
            return StepResult(
                index, step.func, "PASS", summary, f"code={data.get('code')}"
            )
        except MqttControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_MQTT_WAIT:
        method, rid, code, timeout = _parse_mqtt_wait_value(step.value, step.row_num)
        criteria = []
        if method is not None:
            criteria.append(f"method={method}")
        if rid is not None:
            criteria.append(f"id={rid}")
        if code is not None:
            criteria.append(f"code={code}")
        summary = "mqtt_wait " + " ".join(criteria)
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.mqtt is None or not ctx.mqtt.connected:
            return StepResult(
                index, step.func, "FAIL", summary, "mqtt not connected (run mqtt_start first)"
            )
        params: dict[str, Any] = {}
        if method is not None:
            params["method"] = method
        if rid is not None:
            params["filter_id"] = rid
        if code is not None:
            params["code"] = code
        if timeout > 0:
            params["timeout"] = timeout
        try:
            wait_timeout = float(params.get("timeout", 10.0)) if "timeout" in params else 10.0
            response = mqtt_command(
                ctx.mqtt, "wait", timeout_s=wait_timeout + 5.0, **params
            )
            data = response.get("data") or {}
            detail = (
                f"{data.get('topic', '')} method={data.get('method')} "
                f"id={data.get('id')} code={data.get('code')}"
            )
            return StepResult(index, step.func, "PASS", summary, detail)
        except MqttControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_MQTT_WATCH:
        seconds = _parse_mqtt_watch_value(step.value, step.row_num)
        if seconds <= 0:
            seconds = 30.0
        summary = f"mqtt_watch {format_seconds(seconds)}s"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)
        if ctx.mqtt is None or not ctx.mqtt.connected:
            return StepResult(
                index, step.func, "FAIL", summary, "mqtt not connected (run mqtt_start first)"
            )
        try:
            response = mqtt_command(
                ctx.mqtt, "watch", timeout_s=seconds + 5.0, seconds=seconds
            )
            data = response.get("data") or {}
            return StepResult(
                index, step.func, "PASS", summary, f"received={data.get('count')}"
            )
        except MqttControlError as exc:
            return StepResult(index, step.func, "FAIL", summary, str(exc))

    if step.func == FUNC_READ_START_TIME:
        summary = f"read_start_time {ctx.time_addr}"
        if ctx.dry_run:
            return StepResult(index, step.func, "PASS", summary)

        ok, actual, error = read_register(ctx.client, ctx.slave_id, ctx.time_addr)
        if not ok or actual is None:
            return StepResult(index, step.func, "FAIL", summary, error)
        ctx.start_time_value = actual
        return StepResult(index, step.func, "PASS", summary, f"baseline={actual}")

    return StepResult(index, step.func, "FAIL", step.func, "unsupported function")


def run_file(
    csv_path: Path,
    steps: list[Step],
    ctx: ExecutionContext,
    *,
    display_name: str | None = None,
) -> FileResult:
    name = display_name or csv_path.name
    logger = logging.getLogger(LOG_LOGGER_NAME)
    ctx.slave_id = ctx.initial_slave_id
    ctx.start_time_value = None
    logger.info("Running file: %s (%d steps)", name, len(steps))
    started = time.monotonic()
    step_results: list[StepResult] = []
    passed = 0
    status = "pass"

    for step_number, step in enumerate(steps, start=1):
        step_started = time.monotonic()
        try:
            result = execute_step(step, ctx)
        except (SessionTimeoutError, SimApiError) as exc:
            result = StepResult(step.row_num, step.func, "FAIL", step.func, str(exc))
        result.duration_s = time.monotonic() - step_started

        step_results.append(result)
        logger.info("Step %d/%d %s %s [%s] %.3fs",
                    step_number, len(steps),
                    result.summary, result.detail, result.status,
                    result.duration_s)
        if result.status == "PASS":
            passed += 1
            # 有 PASS 则重计时：滑动窗口 60s
            if ctx.session_deadline is not None:
                ctx.session_deadline = time.monotonic() + ctx.session_timeout
            continue

        status = "fail"
        break

    total = len(steps)
    duration_s = time.monotonic() - started

    logger.info("File result: %s %s (%d/%d passed) %.3fs",
                name, status.upper(), passed, total, duration_s)
    return FileResult(
        name=name,
        path=str(csv_path),
        status=status,
        passed=passed,
        total=total,
        duration_s=duration_s,
        step_results=step_results,
    )


def make_file_result(prepared: PreparedFile, status: str, reason: str) -> FileResult:
    steps = prepared.steps or []
    return FileResult(
        name=prepared.input_file.display_name,
        path=str(prepared.input_file.path),
        status=status,
        passed=0,
        total=len(steps),
        duration_s=0.0,
        step_results=[],
        error=reason,
    )


def has_sim_steps(steps: list[Step]) -> bool:
    return any(step.func in SIM_FUNCS for step in steps)


def print_results_header(log_path: Path | None, no_log: bool) -> None:
    print("=== RESULTS ===")
    if log_path is not None:
        print(f"Log: {log_path}")
    elif no_log:
        print("Log: disabled")
    else:
        print("Log: unavailable")


def print_file_result(index: int, total: int, result: FileResult) -> None:
    suffix = ""
    if result.status == "skip":
        suffix = f" ({result.error})"
    elif not result.error or result.step_results:
        suffix = f" ({result.passed}/{result.total})"
    print(f"[{index}/{total}] {result.name} ... {result.status.upper()}{suffix}")


def print_errors(results: list[FileResult]) -> None:
    print()
    print("=== ERRORS ===")
    failed = [result.name for result in results if result.status == "fail"]
    if failed:
        for name in failed:
            print(name)
    else:
        print("None")


def main() -> int:
    args = parse_args()
    log_path = setup_logging(args.log_dir, args.no_log)
    logger = logging.getLogger(LOG_LOGGER_NAME)

    logger.info(
        "modbus_test started: paths=%s baudrate=%d slave_id=%d dry_run=%s session_timeout=%d",
        args.paths,
        args.baudrate,
        args.slave_id,
        args.dry_run,
        args.session_timeout,
    )

    try:
        input_files = resolve_input_files(args.paths, args.recursive)
    except CsvParseError as exc:
        logger.error("Input error: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    prepared_files = [
        prepare_input_file(input_file, args.encoding) for input_file in input_files
    ]
    logger.info("Resolved %d CSV file(s)", len(prepared_files))
    for prepared in prepared_files:
        if prepared.error:
            logger.error(
                "Input file failed: %s: %s",
                prepared.input_file.display_name,
                prepared.error,
            )
        else:
            logger.debug(
                "Parsed %s: %d steps",
                prepared.input_file.path,
                len(prepared.steps or []),
            )

    valid_files = [prepared for prepared in prepared_files if prepared.steps is not None]
    session_has_sim_steps = any(
        has_sim_steps(prepared.steps or []) for prepared in valid_files
    )
    session_has_slave_steps = any(
        has_slave_steps(prepared.steps or []) for prepared in valid_files
    )
    session_has_mqtt_steps = any(
        has_mqtt_steps(prepared.steps or []) for prepared in valid_files
    )

    if session_has_sim_steps and not args.sim_api and not args.dry_run:
        logger.error("CSV contains sim_* operations but --sim-api is not set")
        print("ERROR: CSV contains sim_* operations but --sim-api is not set", file=sys.stderr)
        return 2

    if session_has_slave_steps and not args.slave_app and not args.dry_run:
        bundled = default_slave_app_path()
        if bundled.is_file():
            args.slave_app = str(bundled)
            logger.info("Using bundled EMS Modbus Slave: %s", args.slave_app)
        else:
            logger.error(
                "CSV contains slave_* operations but --slave-app is not set "
                "and bundled slave not found at %s",
                bundled,
            )
            print(
                f"ERROR: CSV contains slave_* operations but --slave-app is not set; "
                f"no bundled slave at {bundled}",
                file=sys.stderr,
            )
            return 2

    if args.slave_app and session_has_slave_steps and not args.dry_run:
        if not Path(args.slave_app).is_file():
            logger.error("Slave app not found: %s", args.slave_app)
            print(f"ERROR: slave app not found: {args.slave_app}", file=sys.stderr)
            return 2

    if session_has_mqtt_steps and not args.mqtt_app and not args.dry_run:
        bundled = default_mqtt_app_path()
        if bundled.is_file():
            args.mqtt_app = str(bundled)
            logger.info("Using bundled EMS MQTT master: %s", args.mqtt_app)
        else:
            logger.error(
                "CSV contains mqtt_* operations but --mqtt-app is not set "
                "and bundled mqtt app not found at %s",
                bundled,
            )
            print(
                f"ERROR: CSV contains mqtt_* operations but --mqtt-app is not set; "
                f"no bundled mqtt app at {bundled}",
                file=sys.stderr,
            )
            return 2

    if args.mqtt_app and session_has_mqtt_steps and not args.dry_run:
        if not Path(args.mqtt_app).is_file():
            logger.error("MQTT app not found: %s", args.mqtt_app)
            print(f"ERROR: mqtt app not found: {args.mqtt_app}", file=sys.stderr)
            return 2
    if args.mqtt_config and not Path(args.mqtt_config).is_file():
        logger.error("MQTT config not found: %s", args.mqtt_config)
        print(f"ERROR: mqtt config not found: {args.mqtt_config}", file=sys.stderr)
        return 2

    sim_ctx: SimContext | None = None
    sim_unavailable = False
    if args.sim_api and session_has_sim_steps and not args.dry_run:
        sim_ctx = SimContext(api_base=args.sim_api, http_timeout=args.sim_http_timeout)
        try:
            devices = _sim_http_json(sim_ctx, "GET", "/api/devices")
            update_sim_device_map(sim_ctx, devices)
            logger.info(
                "DeviceSimulator initialized: %d devices mapped",
                len(sim_ctx._index_to_sn),
            )
        except SimUnavailableError as exc:
            sim_unavailable = True
            sim_ctx = None
            logger.warning("DeviceSimulator unavailable: %s", exc)
        except SimApiError as exc:
            logger.error("DeviceSimulator API error: %s", exc)
            print(f"ERROR: DeviceSimulator API error: {exc}", file=sys.stderr)
            return 2

    runnable_files = [
        prepared
        for prepared in valid_files
        if not (sim_unavailable and has_sim_steps(prepared.steps or []))
    ]
    needs_serial = any(
        requires_serial(step)
        for prepared in runnable_files
        for step in prepared.steps or []
    )

    client = None
    port = args.port
    if needs_serial and not args.dry_run:
        try:
            port = detect_port(args.port)
            client = create_client(port, args.baudrate)
            if not client.connect():
                raise ConnectionSetupError(f"failed to connect to serial port: {port}")
            logger.info("Serial connected: port=%s baudrate=%d", port, args.baudrate)
        except ConnectionSetupError as exc:
            logger.error("Serial connection failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    session_deadline = time.monotonic() + args.session_timeout

    slave_ctx: SlaveContext | None = None
    if session_has_slave_steps and not args.dry_run and args.slave_app:
        slave_ctx = SlaveContext(
            app_path=args.slave_app,
            port=args.slave_port,
            profile=args.slave_profile,
            preset=args.slave_preset,
            baudrate=args.slave_baudrate,
            slave_id=args.slave_slave_id,
            respond_1_40=args.slave_respond_1_40,
            ready_timeout=args.slave_ready_timeout,
            stop_timeout=args.slave_stop_timeout,
        )

    mqtt_ctx: MqttContext | None = None
    if session_has_mqtt_steps and not args.dry_run and args.mqtt_app:
        mqtt_ctx = MqttContext(
            app_path=args.mqtt_app,
            config_path=args.mqtt_config,
            connect_timeout=args.mqtt_connect_timeout,
            stop_timeout=args.mqtt_stop_timeout,
        )

    ctx = ExecutionContext(
        client=client,
        slave_id=args.slave_id,
        initial_slave_id=args.slave_id,
        wait_timeout=args.wait_timeout,
        wait_interval=args.wait_interval,
        session_deadline=session_deadline,
        session_timeout=args.session_timeout,
        dry_run=args.dry_run,
        time_addr=args.time_addr,
        sim=sim_ctx,
        slave=slave_ctx,
        mqtt=mqtt_ctx,
    )

    results: list[FileResult] = []
    print_results_header(log_path, args.no_log)
    try:
        for index, prepared in enumerate(prepared_files, start=1):
            steps = prepared.steps
            if prepared.error:
                result = make_file_result(prepared, "fail", prepared.error)
            elif sim_unavailable and has_sim_steps(steps or []):
                result = make_file_result(
                    prepared,
                    "skip",
                    "DeviceSimulator unavailable",
                )
            elif ctx.session_deadline is not None and time.monotonic() >= ctx.session_deadline:
                result = make_file_result(prepared, "skip", "session timeout")
            else:
                result = run_file(
                    prepared.input_file.path,
                    steps or [],
                    ctx,
                    display_name=prepared.input_file.display_name,
                )
            results.append(result)
            print_file_result(index, len(prepared_files), result)
    finally:
        if client is not None:
            client.close()
        if slave_ctx is not None and slave_ctx.started and slave_ctx.proc is not None:
            logger.warning("Slave still running at session end; force stopping")
            slave_kill(slave_ctx)
        if mqtt_ctx is not None and mqtt_ctx.started and mqtt_ctx.proc is not None:
            logger.warning("MQTT daemon still running at session end; force stopping")
            mqtt_kill(mqtt_ctx)

    print_errors(results)

    overall = "FAIL" if any(result.status == "fail" for result in results) else "PASS"
    logger.info("Session finished: %s (%d files)", overall, len(results))

    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
