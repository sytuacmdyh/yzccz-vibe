"""stdio JSON-RPC 控制通道：CSV 联调 / 自动化通过子进程 stdin/stdout 操作从站寄存器。

协议（每行一个 JSON 对象，UTF-8，stdout 立即 flush）：

- ready 事件（cli 启动后发出）：{"type": "ready", "pid": ..., "port": ..., "profile": ...}
- log 事件：                    {"type": "log", "level": ..., "message": ...}
- 请求（stdin 读入）：          {"type": "request", "id": N, "op": "...", ...op参数}
- 响应（stdout 输出）：         {"type": "response", "id": N, "ok": true, ...}
                                或 {"type": "response", "id": N, "ok": false, "error": "..."}

支持操作：
- get_register   {address, slave_id?}         -> {"value": int}
- get_registers  {address, count, slave_id?}  -> {"values": [int, ...]}
- set_register   {address, value, slave_id?}  -> {"ok": true, "value": int}（经 set_direct 注入，不受 writable 限制）
- get_coil       {address}                    -> {"value": 0|1}
- set_coil       {address, value}             -> {"ok": true}（value 接受 true/false/1/0）
- snapshot       {slave_id?}                  -> {"rows": [...]}（复用 RegisterBank.snapshot）
- get_profile    {}                           -> {"profile_id", "name", "baudrate", "slave_id"}
- shutdown       {}                           -> {"ok": true, "shutdown": true}，随后优雅退出

线程安全：RegisterBank 自带 RLock；stdout 写入由 _OUT_LOCK 串行化，避免与串口日志线程交错。
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, Callable, Optional

from .register_model import RegisterBank

_OUT_LOCK = threading.Lock()


def emit_json(payload: dict[str, Any]) -> None:
    """写一行 JSON 到 stdout 并立即 flush（多线程安全）。"""
    line = json.dumps(payload, ensure_ascii=False)
    with _OUT_LOCK:
        print(line, flush=True)


def log_event(level: str, message: str) -> None:
    emit_json({"type": "log", "level": level, "message": message})


def _int(command: dict[str, Any], key: str, required: bool = False) -> Optional[int]:
    value = command.get(key)
    if value is None:
        if required:
            raise ValueError(f"missing field: {key}")
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"field {key} must be an integer, got {value!r}") from exc


def _bool(command: dict[str, Any], key: str, required: bool = False) -> Optional[bool]:
    value = command.get(key)
    if value is None:
        if required:
            raise ValueError(f"missing field: {key}")
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "on"):
            return True
        if lowered in ("false", "0", "off"):
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"field {key} must be boolean, got {value!r}")


def handle_command(bank: RegisterBank, command: dict[str, Any]) -> dict[str, Any]:
    """执行单个控制命令，返回响应负载（不含 type/id 包装）。"""
    op = command.get("op")
    try:
        if op == "get_register":
            address = _int(command, "address", required=True)
            return {"ok": True, "address": address, "value": bank.get(address, _int(command, "slave_id"))}
        if op == "get_registers":
            start = _int(command, "address", required=True)
            count = _int(command, "count", required=True)
            if count <= 0:
                raise ValueError("count must be > 0")
            slave_id = _int(command, "slave_id")
            values = [bank.get(start + index, slave_id) for index in range(count)]
            return {"ok": True, "address": start, "values": values}
        if op == "set_register":
            address = _int(command, "address", required=True)
            value = _int(command, "value", required=True)
            bank.set_direct(address, value, _int(command, "slave_id"))
            return {"ok": True, "address": address, "value": value}
        if op == "get_coil":
            address = _int(command, "address", required=True)
            return {"ok": True, "address": address, "value": 1 if bank.get_coil(address) else 0}
        if op == "set_coil":
            address = _int(command, "address", required=True)
            value = _bool(command, "value", required=True)
            bank.set_coil_direct(address, value)
            return {"ok": True, "address": address, "value": 1 if value else 0}
        if op == "snapshot":
            rows = bank.snapshot(_int(command, "slave_id"))
            return {"ok": True, "rows": rows}
        if op == "get_profile":
            profile = bank.profile
            return {
                "ok": True,
                "profile_id": profile.profile_id,
                "name": profile.name,
                "baudrate": profile.baudrate,
                "slave_id": profile.slave_id,
            }
        if op == "shutdown":
            return {"ok": True, "shutdown": True}
        return {"ok": False, "error": f"unknown op: {op!r}"}
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - 防御性兜底
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


class StdioControlServer:
    """stdin 读取线程：逐行解析 JSON-RPC 请求，响应写回 stdout。"""

    def __init__(self, bank: RegisterBank, on_shutdown: Callable[[], None]) -> None:
        self._bank = bank
        self._on_shutdown = on_shutdown
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="stdio-control", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                command = json.loads(raw)
            except json.JSONDecodeError:
                emit_json({"type": "response", "id": None, "ok": False, "error": "invalid JSON command"})
                continue
            if not isinstance(command, dict):
                emit_json({"type": "response", "id": None, "ok": False, "error": "command must be a JSON object"})
                continue
            req_id = command.get("id")
            payload = handle_command(self._bank, command)
            emit_json({"type": "response", "id": req_id, **payload})
            if payload.get("ok") and payload.get("shutdown"):
                self._on_shutdown()
                return
