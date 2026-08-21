"""stdio JSON-RPC 守护模式：CSV 测试脚本通过子进程 stdin/stdout 控制一条 MQTT 连接。

协议（每行一个 JSON 对象，UTF-8，stdout 立即 flush）：

- ready 事件（启动后发出）：  {"type": "ready", "pid": ...}
- 请求（stdin 读入）：        {"type": "request", "id": N, "op": "...", ...op参数}
- 响应（stdout 输出）：       {"type": "response", "id": N, "ok": true, "data": {...}}
                              或 {"type": "response", "id": N, "ok": false, "error": "..."}

支持操作：
- connect     {config?: {...}}  -> {"detail": ...}，按覆盖参数建立 MQTT 连接并订阅 up 消息
- disconnect  {}                -> {"ok": true}，断开连接
- send        {envelope, expect?, timeout?} -> {"id","method","code"}（time/sync_weather 返回 no_ack）
- wait        {method?, id?, code?, timeout} -> {"topic","method","id","code"}，匹配上行消息
- watch       {seconds}         -> {"count": N}，观察期间收到的新消息数
- shutdown    {}                -> {"ok": true, "shutdown": true}，随后优雅退出

线程安全：接收消息 / ack 列表由 _LOCK 保护；stdout 写入由 _OUT_LOCK 串行化。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import Qt

from .config import load_config
from .mqtt_worker import MqttSession, SignalBus
from .time_fields import NO_ACK_METHODS, TIME_SYNC_METHODS, refresh_time_fields

_OUT_LOCK = threading.Lock()
_LOCK = threading.Lock()


def emit_json(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    with _OUT_LOCK:
        print(line, flush=True)


def _load_config_quiet() -> dict[str, Any]:
    """加载 config.json，但把 config 模块的提示打印改道到 stderr，保持 stdout 协议纯净。"""
    old = sys.stdout
    try:
        sys.stdout = sys.stderr
        return load_config()
    finally:
        sys.stdout = old


class _MqttBridge:
    """封装 MqttSession + SignalBus，把接收消息与 ack 收集到线程安全列表。"""

    def __init__(self) -> None:
        self.session: MqttSession | None = None
        self.received: list[dict[str, Any]] = []
        self.acks: list[tuple[Any, str, int]] = []
        self._connected = threading.Event()
        self._connect_result: dict[str, Any] = {}

    def connect(self, cfg: dict[str, Any], connect_timeout: int) -> tuple[bool, str]:
        bus = SignalBus()

        def on_connected(ok: bool, detail: str) -> None:
            self._connect_result["ok"] = ok
            self._connect_result["detail"] = detail
            self._connected.set()

        def on_message(topic: str, text: str) -> None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return
            if not isinstance(data, dict):
                return
            with _LOCK:
                self.received.append({"topic": topic, "data": data})

        def on_ack(rid: Any, method: str, code: int) -> None:
            with _LOCK:
                self.acks.append((rid, method, code))

        bus.connected.connect(on_connected, Qt.DirectConnection)
        bus.message_received.connect(on_message, Qt.DirectConnection)
        bus.ack_received.connect(on_ack, Qt.DirectConnection)

        session = MqttSession(
            bus=bus,
            host=str(cfg["host"]),
            port=int(cfg["port"]),
            path=str(cfg["path"]),
            username=str(cfg["username"]),
            password=str(cfg["password"]),
            product_id=str(cfg["product_id"]),
            device_id=str(cfg["device_id"]),
            subscribe_all=bool(cfg["subscribe_all"]),
        )
        session.start()
        if not self._connected.wait(connect_timeout):
            session.stop()
            return False, "connect timed out"
        if not bool(self._connect_result.get("ok")):
            session.stop()
            return False, str(self._connect_result.get("detail", "connect failed"))
        self.session = session
        return True, str(self._connect_result.get("detail", "connected"))

    def stop(self) -> None:
        session = self.session
        if session is not None:
            session.stop()
        self.session = None
        self._connected.clear()
        self._connect_result.clear()
        with _LOCK:
            self.received.clear()
            self.acks.clear()

    @property
    def connected(self) -> bool:
        session = self.session
        return session is not None and session.is_connected

    def publish(self, topic: str, payload: bytes, qos: int) -> bool:
        session = self.session
        if session is None or not session.is_connected:
            return False
        return session.publish(topic, payload, qos)

    def received_since(self, start: int) -> list[dict[str, Any]]:
        with _LOCK:
            return list(self.received[start:])

    def acks_since(self, start: int) -> list[tuple[Any, str, int]]:
        with _LOCK:
            return list(self.acks[start:])


class StdioServer:
    """stdin 读取循环：逐行解析 JSON-RPC 请求，响应写回 stdout。"""

    def __init__(self, bridge: _MqttBridge) -> None:
        self.bridge = bridge
        self.cfg: dict[str, Any] = {}

    def _run_op(self, command: dict[str, Any]) -> dict[str, Any]:
        op = command.get("op")
        try:
            if op == "connect":
                config_path = command.get("config_path")
                if config_path:
                    from pathlib import Path

                    self.cfg = json.loads(
                        Path(str(config_path)).read_text(encoding="utf-8")
                    )
                else:
                    self.cfg = _load_config_quiet()
                overrides = command.get("config")
                if isinstance(overrides, dict):
                    self.cfg = {**self.cfg, **overrides}
                ok, detail = self.bridge.connect(
                    self.cfg, int(self.cfg.get("connect_timeout", 15))
                )
                if not ok:
                    return {"ok": False, "error": f"connect failed: {detail}"}
                return {"ok": True, "data": {"detail": detail}}
            if op == "disconnect":
                self.bridge.stop()
                return {"ok": True, "data": {}}
            if op == "send":
                return self._op_send(command)
            if op == "wait":
                return self._op_wait(command)
            if op == "watch":
                return self._op_watch(command)
            if op == "shutdown":
                return {"ok": True, "data": {"shutdown": True}}
            return {"ok": False, "error": f"unknown op: {op!r}"}
        except Exception as exc:  # pragma: no cover - 防御性兜底
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _op_send(self, command: dict[str, Any]) -> dict[str, Any]:
        envelope = command.get("envelope")
        if not isinstance(envelope, dict):
            return {"ok": False, "error": "send requires envelope (JSON object)"}
        method = envelope.get("method")
        if not isinstance(method, str):
            return {"ok": False, "error": "envelope.method must be a string"}
        expect = command.get("expect")
        if expect is None:
            expect = int(self.cfg.get("expect_ack_code", 0))
        timeout = command.get("timeout")
        if timeout is None:
            timeout = float(self.cfg.get("timeout", 25))

        if method in TIME_SYNC_METHODS and isinstance(envelope.get("params"), dict):
            fresh = refresh_time_fields(method, envelope["params"])
            if fresh is not envelope["params"]:
                envelope = dict(envelope)
                envelope["params"] = fresh

        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        topic = f"down/{self.cfg['product_id']}/{self.cfg['device_id']}"
        if not self.bridge.publish(topic, payload, int(self.cfg.get("qos", 1))):
            return {"ok": False, "error": "publish failed"}
        if method in NO_ACK_METHODS:
            return {"ok": True, "data": {"method": method, "no_ack": True}}

        want_id = str(envelope["id"]) if "id" in envelope else None
        start = len(self.bridge.acks)
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            for rid, ack_method, code in self.bridge.acks_since(start):
                if (want_id is None or str(rid) == want_id) and ack_method == method:
                    if code == expect:
                        return {
                            "ok": True,
                            "data": {"id": rid, "method": ack_method, "code": code},
                        }
                    return {
                        "ok": False,
                        "error": f"ack code={code} != expect={expect}",
                        "data": {"id": rid, "method": ack_method, "code": code},
                    }
            time.sleep(0.05)
        return {
            "ok": False,
            "error": f"timeout waiting ack (id={want_id} method={method} {timeout}s)",
        }

    def _op_wait(self, command: dict[str, Any]) -> dict[str, Any]:
        method = command.get("method")
        # filter_id avoids collision with JSON-RPC 'id'; legacy fallbacks kept for compat
        rid = command.get("filter_id")
        if rid is None:
            rid = command.get("msg_id")
        if rid is None:
            rid = command.get("wait_id")
        # do not fallback to command.get("id") (RPC id) to avoid the 25s/35s timeout bug
        code = command.get("code")
        timeout = command.get("timeout")
        if timeout is None:
            timeout = float(self.cfg.get("timeout", 25))
        if method is None and rid is None and code is None:
            return {"ok": False, "error": "wait requires at least one of method/id/code"}

        # start slightly before current tail to catch log that arrived during preceding Modbus steps
        start = max(0, len(self.bridge.received) - 2)
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            for item in self.bridge.received_since(start):
                data = item["data"]
                if method is not None and data.get("method") != method:
                    continue
                if rid is not None and str(data.get("id")) != str(rid):
                    continue
                if code is not None and data.get("code") != code:
                    continue
                return {
                    "ok": True,
                    "data": {
                        "topic": item["topic"],
                        "method": data.get("method"),
                        "id": data.get("id"),
                        "code": data.get("code"),
                    },
                }
            time.sleep(0.05)
        return {"ok": False, "error": f"timeout waiting message ({timeout}s)"}

    def _op_watch(self, command: dict[str, Any]) -> dict[str, Any]:
        seconds = float(command.get("seconds") or self.cfg.get("watch_seconds", 30))
        start = len(self.bridge.received)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(0.1)
        return {"ok": True, "data": {"count": len(self.bridge.received) - start}}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EMS Workflow MQTT stdio daemon (CSV automation)",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Enable stdio JSON-RPC control channel on stdin/stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.stdio:
        print("session mode requires --stdio", file=sys.stderr, flush=True)
        return 4

    bridge = _MqttBridge()
    server = StdioServer(bridge)
    emit_json({"type": "ready", "pid": os.getpid()})

    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                command = json.loads(raw)
            except json.JSONDecodeError:
                emit_json(
                    {"type": "response", "id": None, "ok": False, "error": "invalid JSON command"}
                )
                continue
            if not isinstance(command, dict):
                emit_json(
                    {"type": "response", "id": None, "ok": False, "error": "command must be a JSON object"}
                )
                continue
            req_id = command.get("id")
            payload = server._run_op(command)
            emit_json({"type": "response", "id": req_id, **payload})
            if payload.get("ok") and payload.get("data", {}).get("shutdown"):
                bridge.stop()
                return 0
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())