#!/usr/bin/env python3
"""CSV-driven Modbus serial test runner."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import platform
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TIME_ADDR = 4399
DEFAULT_CLIENT_TIMEOUT_S = 3.0
WRITE_RETRY_COUNT = 3
WRITE_RETRY_DELAY_S = 1.0
FUNC_WRITE = "write"
FUNC_READ = "read"
FUNC_DELAY = "delay"
FUNC_WAIT = "wait"
FUNC_READ_START_TIME = "read_start_time"
FUNC_LOGIC_DELAY = "logic_delay"
VALID_FUNCS = {
    FUNC_WRITE,
    FUNC_READ,
    FUNC_DELAY,
    FUNC_WAIT,
    FUNC_READ_START_TIME,
    FUNC_LOGIC_DELAY,
}


class CsvParseError(Exception):
    """Raised when CSV content is invalid."""


class ConnectionSetupError(Exception):
    """Raised when the serial connection cannot be prepared."""


class SessionTimeoutError(Exception):
    """Raised when the run exceeds the configured session timeout."""


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
class FuncStats:
    func: str
    count: int
    total_s: float
    min_s: float
    max_s: float
    avg_s: float


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


@dataclass
class ExecutionContext:
    client: Any
    slave_id: int
    wait_timeout: int
    wait_interval: float
    session_deadline: float | None
    dry_run: bool
    start_time_value: int | None = None
    time_addr: int = DEFAULT_TIME_ADDR


@dataclass(frozen=True)
class WaitSpec:
    kind: str
    expected: Any
    timeout_s: float | None = None
    interval_s: float | None = None
    logic_timeout_s: float | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Modbus serial tests from a CSV file or a folder of CSV files."
    )
    parser.add_argument("path", help="CSV file or folder path")
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
        help="Maximum wait poll attempts before FAIL",
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
        default=120,
        help="Maximum run time in seconds for the whole session",
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
        "--continue-on-fail",
        action="store_true",
        help="On file failure, continue with the next CSV instead of stopping the batch",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print per-function-type timing statistics (default: disabled)",
    )
    args = parser.parse_args()
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
    return args


def resolve_input_files(raw_path: str, recursive: bool = False) -> tuple[Path, list[Path], bool]:
    input_path = Path(raw_path).expanduser()
    if not input_path.exists():
        raise CsvParseError(f"path does not exist: {input_path}")
    if input_path.is_file():
        return input_path, [input_path], False
    if not input_path.is_dir():
        raise CsvParseError(f"path is not a file or directory: {input_path}")

    pattern = "**/*.csv" if recursive else "*.csv"
    files = sorted(
        [path for path in input_path.glob(pattern) if path.is_file()],
        key=lambda p: extract_number(p.relative_to(input_path)),
    )
    if not files:
        raise CsvParseError(f"no CSV files found in directory: {input_path}")
    return input_path, files, True


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
    except ValueError as exc:
        raise CsvParseError(f"row {row_num}: invalid {field_name}: {raw!r}") from exc


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

            if not value_text and func != FUNC_READ_START_TIME:
                raise CsvParseError(f"row {row_num}: missing target value")

            if func == FUNC_WRITE:
                parse_int(value_text, "write value", row_num)
            elif func == FUNC_DELAY:
                parse_float(value_text, "delay seconds", row_num)
            elif func == FUNC_LOGIC_DELAY:
                parse_positive_float(value_text, "logic delay seconds", row_num)
            elif func == FUNC_READ:
                parse_expected(value_text, row_num)
            elif func == FUNC_WAIT:
                parse_wait_value(value_text, row_num)

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


def print_step(step_number: int, result: StepResult) -> None:
    summary = result.summary
    if result.detail:
        summary = f"{summary}  {result.detail}"
    print(f"Step {step_number:2d}  {summary:<42} {result.status}")


def run_file(
    csv_path: Path,
    steps: list[Step],
    ctx: ExecutionContext,
    *,
    verbose: bool,
    display_name: str | None = None,
) -> FileResult:
    name = display_name or csv_path.name
    if verbose:
        print(f"=== TEST: {name} ===")
    started = time.monotonic()
    step_results: list[StepResult] = []
    passed = 0
    status = "pass"
    error = ""

    for step_number, step in enumerate(steps, start=1):
        step_started = time.monotonic()
        try:
            result = execute_step(step, ctx)
        except SessionTimeoutError as exc:
            result = StepResult(step.row_num, step.func, "FAIL", step.func, str(exc))
        result.duration_s = time.monotonic() - step_started

        step_results.append(result)
        if verbose:
            print_step(step_number, result)
        if result.status == "PASS":
            passed += 1
            continue

        status = "fail"
        break

    total = len(steps)
    duration_s = time.monotonic() - started
    if error:
        status = "error"

    if verbose:
        verdict = status.upper()
        print(f"=== RESULT: {verdict} ({passed}/{total} passed) ===")
        print()
    return FileResult(
        name=name,
        path=str(csv_path),
        status=status,
        passed=passed,
        total=total,
        duration_s=duration_s,
        step_results=step_results,
        error=error,
    )


def print_batch_header(base_path: Path) -> None:
    print(f"=== BATCH: {base_path} ===")


def print_batch_progress(index: int, total: int, result: FileResult) -> None:
    print(
        f"[{index}/{total}] {result.name} ... {result.status.upper()} "
        f"({result.passed}/{result.total})"
    )


def print_summary(results: list[FileResult]) -> None:
    print()
    print("=== SUMMARY ===")
    max_name = max(len(r.name) for r in results) if results else 4
    col = max(max_name, 4)
    print(f"| {'File':<{col}} | Result | Passed/Total |")
    print(f"|{'-' * (col + 2)}|--------|--------------|")
    for result in results:
        ratio = f"{result.passed}/{result.total}"
        print(
            f"| {result.name:<{col}} | {result.status.upper():<6} | "
            f"{ratio:<12} |"
        )
    passed_files = sum(1 for result in results if result.status == "pass")
    failed_files = len(results) - passed_files
    print(
        f"=== {len(results)} files: {passed_files} passed, {failed_files} failed ==="
    )


def collect_time_stats(results: list[FileResult]) -> list[FuncStats]:
    buckets: dict[str, list[float]] = {}
    order: list[str] = []
    for result in results:
        for step in result.step_results:
            if step.func not in buckets:
                buckets[step.func] = []
                order.append(step.func)
            buckets[step.func].append(step.duration_s)

    stats: list[FuncStats] = []
    for func in order:
        durations = buckets[func]
        total = sum(durations)
        stats.append(FuncStats(
            func=func,
            count=len(durations),
            total_s=total,
            min_s=min(durations),
            max_s=max(durations),
            avg_s=total / len(durations),
        ))
    return stats


def print_time_stats(stats: list[FuncStats], results: list[FileResult]) -> None:
    print()
    print("=== TIME STATISTICS ===")
    print(f"| {'Function':<12} | {'Count':>5} | {'Total':>8} | {'Avg':>8} | {'Min':>8} | {'Max':>8} |")
    print(f"|{'-' * 14}|{'-' * 7}|{'-' * 10}|{'-' * 10}|{'-' * 10}|{'-' * 10}|")
    for s in stats:
        print(
            f"| {s.func:<12} | {s.count:>5} | {format_seconds(s.total_s):>7}s "
            f"| {format_seconds(s.avg_s):>7}s | {format_seconds(s.min_s):>7}s "
            f"| {format_seconds(s.max_s):>7}s |"
        )
    total_count = sum(s.count for s in stats)
    total_dur = sum(s.total_s for s in stats)
    print(f"| {'TOTAL':<12} | {total_count:>5} | {format_seconds(total_dur):>7}s |")


def build_json_summary(
    results: list[FileResult],
    *,
    include_stats: bool = False,
) -> dict[str, Any]:
    overall_status = "pass" if all(result.status == "pass" for result in results) else "fail"
    passed_files = sum(1 for result in results if result.status == "pass")
    summary: dict[str, Any] = {
        "status": overall_status,
        "total_files": len(results),
        "passed_files": passed_files,
        "files": [
            {
                "name": result.name,
                "status": result.status,
                "passed": result.passed,
                "total": result.total,
                "duration_s": round(result.duration_s, 3),
                "steps": [
                    {
                        "row": step.index,
                        "function": step.func,
                        "status": step.status.lower(),
                        "duration_s": round(step.duration_s, 3),
                        "summary": step.summary,
                        "detail": step.detail,
                    }
                    for step in result.step_results
                ],
            }
            for result in results
        ],
    }
    if include_stats:
        stats = collect_time_stats(results)
        summary["stats"] = [
            {
                "func": s.func,
                "count": s.count,
                "total_s": round(s.total_s, 3),
                "min_s": round(s.min_s, 3),
                "max_s": round(s.max_s, 3),
                "avg_s": round(s.avg_s, 3),
            }
            for s in stats
        ]
    return summary


def main() -> int:
    args = parse_args()

    try:
        input_path, csv_files, is_batch = resolve_input_files(args.path, args.recursive)
        parsed_files = [(csv_path, parse_csv(csv_path, args.encoding)) for csv_path in csv_files]
    except CsvParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    use_relative = args.recursive and is_batch

    client = None
    port = args.port
    if not args.dry_run:
        try:
            port = detect_port(args.port)
            client = create_client(port, args.baudrate)
            if not client.connect():
                raise ConnectionSetupError(f"failed to connect to serial port: {port}")
        except ConnectionSetupError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    session_deadline = time.monotonic() + args.session_timeout
    ctx = ExecutionContext(
        client=client,
        slave_id=args.slave_id,
        wait_timeout=args.wait_timeout,
        wait_interval=args.wait_interval,
        session_deadline=session_deadline,
        dry_run=args.dry_run,
        time_addr=args.time_addr,
    )

    results: list[FileResult] = []
    try:
        if is_batch:
            print_batch_header(input_path)
            for index, (csv_path, steps) in enumerate(parsed_files, start=1):
                display = str(csv_path.relative_to(input_path)) if use_relative else csv_path.name
                result = run_file(
                    csv_path,
                    steps,
                    ctx,
                    verbose=False,
                    display_name=display,
                )
                results.append(result)
                print_batch_progress(index, len(parsed_files), result)
                if result.status != "pass" and not args.continue_on_fail:
                    break
        else:
            csv_path, steps = parsed_files[0]
            results.append(
                run_file(
                    csv_path,
                    steps,
                    ctx,
                    verbose=True,
                )
            )
    finally:
        if client is not None:
            client.close()

    if is_batch:
        print_summary(results)

    if args.stats:
        stats = collect_time_stats(results)
        print_time_stats(stats, results)

    print(json.dumps(
        build_json_summary(results, include_stats=args.stats),
        ensure_ascii=True,
        separators=(",", ":"),
    ))

    if all(result.status == "pass" for result in results):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
