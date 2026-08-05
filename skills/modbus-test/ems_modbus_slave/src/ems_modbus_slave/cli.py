"""无 GUI 命令行启动 Modbus RTU 从站（供 CSV 联调 / CI 使用）。"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from serial.tools import list_ports

from .device_profile import DeviceProfile
from .paths import app_root
from .preset_loader import SimulatorPreset, load_startup_preset
from .profile_repository import discover_profiles
from .register_model import RegisterBank
from .serial_slave import SerialSlaveServer
from .stdio_control import StdioControlServer, emit_json, log_event


ROOT = app_root()
PROFILE_DIR = ROOT / "profiles"


def _resolve_profile_path(profile_arg: str) -> Path:
    candidate = Path(profile_arg)
    if candidate.is_file():
        return candidate.resolve()
    if not candidate.suffix:
        by_id = PROFILE_DIR / f"{profile_arg}.json"
        if by_id.is_file():
            return by_id.resolve()
    for _label, path in discover_profiles(PROFILE_DIR):
        if path.stem == profile_arg or path.name == profile_arg:
            return path.resolve()
    raise FileNotFoundError(f"Profile not found: {profile_arg}")


def _resolve_preset_path(preset_arg: str | None, profile_path: Path, profile: DeviceProfile) -> Path | None:
    if preset_arg:
        candidate = Path(preset_arg)
        if candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(f"Preset not found: {preset_arg}")
    if profile.startup_preset:
        preset = load_startup_preset(ROOT, profile_path, profile.startup_preset)
        if preset is not None:
            # load_startup_preset already validated; re-resolve path for logging only
            from .preset_loader import resolve_preset_path

            return resolve_preset_path(ROOT, profile_path, profile.startup_preset)
    return None


def _log_line(message: str) -> None:
    print(message, flush=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EMS Modbus Slave headless CLI (no Qt GUI)",
    )
    parser.add_argument("--port", help="Serial port, e.g. COM5")
    parser.add_argument(
        "--profile",
        default="dm_hp3_rs48_v2",
        help="Profile path or profile_id (default: dm_hp3_rs48_v2)",
    )
    parser.add_argument("--preset", help="Preset JSON path (optional)")
    parser.add_argument("--baudrate", type=int, help="Override profile baudrate")
    parser.add_argument("--slave-id", type=int, default=1, help="Default slave ID (default: 1)")
    parser.add_argument(
        "--respond-1-40",
        action="store_true",
        help="Respond to Modbus slave ID 1..40 (group control bench)",
    )
    parser.add_argument(
        "--stdio-control",
        action="store_true",
        help="Enable stdio JSON-RPC control channel on stdin/stdout (CSV automation)",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List serial ports and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_ports:
        for port in list_ports.comports():
            print(f"{port.device}\t{port.description}")
        return 0

    port = args.port
    if not port:
        ports = [p.device for p in list_ports.comports()]
        if len(ports) == 1:
            port = ports[0]
            _log_line(f"Auto-selected port: {port}")
        else:
            parser.error("--port is required (multiple COM ports detected)")

    profile_path = _resolve_profile_path(args.profile)
    profile = DeviceProfile.from_json(profile_path)
    preset_path = _resolve_preset_path(args.preset, profile_path, profile)

    preset: SimulatorPreset | None = None
    if preset_path is not None:
        preset = SimulatorPreset.from_json(preset_path)
        if preset.profile_id != profile.profile_id:
            raise ValueError(
                f"Preset profile_id={preset.profile_id!r} != profile {profile.profile_id!r}"
            )
    elif profile.startup_preset:
        preset = load_startup_preset(ROOT, profile_path, profile.startup_preset)

    bank = RegisterBank(profile, preset)
    baudrate = args.baudrate if args.baudrate is not None else profile.baudrate

    if args.stdio_control:
        def log_line(message: str) -> None:
            log_event("info", message)

        def message_line(message: str, level: str = "info") -> None:
            mapped = "error" if level == "error" else "info"
            log_event(mapped, message)

        def startup_log(message: str) -> None:
            log_event("info", message)
    else:
        def log_line(message: str) -> None:
            _log_line(message)

        def message_line(message: str, level: str = "info") -> None:
            _log_line(message)

        def startup_log(message: str) -> None:
            _log_line(message)

    server = SerialSlaveServer(
        profile=profile,
        bank=bank,
        log_fn=log_line,
        refresh_fn=lambda: None,
        message_fn=message_line,
    )
    server.configure(
        port=port,
        baudrate=baudrate,
        slave_id=args.slave_id,
        respond_id_min=1 if args.respond_1_40 else None,
        respond_id_max=40 if args.respond_1_40 else None,
    )

    startup_log(f"Profile: {profile.name} ({profile.profile_id})")
    if preset is not None:
        startup_log(f"Preset: {preset.name} ({preset.preset_id})")
    id_mode = "respond 1-40" if args.respond_1_40 else f"slave_id={args.slave_id}"
    startup_log(f"Starting: {port} @ {baudrate}, {id_mode}")

    stop_requested = False

    def _handle_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    server.start()
    if not server.running:
        if args.stdio_control:
            emit_json({"type": "error", "message": f"Failed to open serial port: {port}"})
        else:
            _log_line(f"Failed to open serial port: {port}")
        return 1

    if args.stdio_control:
        emit_json(
            {
                "type": "ready",
                "pid": os.getpid(),
                "port": port,
                "profile": profile.profile_id,
                "preset": preset.preset_id if preset is not None else None,
            }
        )
    else:
        _log_line("Slave running. Press Ctrl+C to stop.")

    def _request_stop() -> None:
        nonlocal stop_requested
        stop_requested = True

    if args.stdio_control:
        control = StdioControlServer(bank, on_shutdown=_request_stop)
        control.start()

    try:
        while not stop_requested:
            time.sleep(0.2)
    finally:
        server.stop()
        if args.stdio_control:
            emit_json({"type": "log", "level": "info", "message": "Slave stopped."})
        else:
            _log_line("Slave stopped.")

    return 0
