"""MQTT 后台线程封装：连接、订阅、发布，回调通过 Qt 信号回到 UI 线程。

线程模型：连接与 paho loop 跑在独立的 daemon 线程里；回调信号由 Qt
队列连接转发到 UI 线程，相关对象始终在主线程创建。
"""
from __future__ import annotations

import json
import threading
from typing import Any

import paho.mqtt.client as mqtt
from PySide6.QtCore import QObject, Signal

from .config import load_code_legend


def describe_code(code: int | None, legend: list[tuple[int, str]] | None = None) -> str:
    if code is None:
        return "no ack"
    if code < 0:
        return f"错误码 {code}"
    if legend is None:
        legend = load_code_legend()
    for value, text in legend:
        if value == code:
            return text
    return f"未知 code={code}"


class SignalBus(QObject):
    """跨线程信号总线（worker 线程 emit，主线程接收）。"""

    connected = Signal(bool, str)          # ok, detail
    disconnected = Signal(str)            # reason
    message_received = Signal(str, str)   # topic, payload
    ack_received = Signal(object, str, int)  # request_id(str/int), method, code
    log = Signal(str)


class MqttSession:
    """WSS(MQTT over WebSocket + TLS) 会话，daemon 线程中循环收发。"""

    def __init__(
        self,
        bus: SignalBus,
        host: str,
        port: int,
        path: str,
        username: str,
        password: str,
        product_id: str,
        device_id: str,
        subscribe_all: bool,
    ) -> None:
        self.bus = bus
        self.host = host
        self.port = port
        self.path = path
        self.username = username
        self.password = password
        self.product_id = product_id
        self.device_id = device_id
        self.subscribe_all = subscribe_all

        self._client: mqtt.Client | None = None
        self._thread: threading.Thread | None = None
        self._connected = threading.Event()
        self._stopped = threading.Event()
        self._use_tls = True

    # ── 供 UI 线程调用 ──────────────────────────────────────────────
    def start(self) -> None:
        use_tls = True
        for scheme in ("wss://", "mqtts://"):
            if self.host.startswith(scheme):
                self.host = self.host[len(scheme):]
                use_tls = True
                break
        else:
            for scheme in ("ws://", "mqtt://"):
                if self.host.startswith(scheme):
                    self.host = self.host[len(scheme):]
                    use_tls = False
                    break
        self._use_tls = use_tls
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        client = self._client
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
        self._connected.clear()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def publish(self, topic: str, payload: bytes, qos: int) -> bool:
        client = self._client
        if client is None or not self._connected.is_set():
            return False
        try:
            info = client.publish(topic, payload, qos=qos, retain=False)
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception:
            return False

    def subscribe(self, topic: str, qos: int) -> None:
        client = self._client
        if client is not None and self._connected.is_set():
            try:
                client.subscribe(topic, qos=qos)
            except Exception:
                pass

    # ── worker 线程 ─────────────────────────────────────────────────
    def _run(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
        client.ws_set_options(path=self.path)
        if self._use_tls:
            try:
                client.tls_set()
            except Exception as exc:
                self.bus.connected.emit(False, f"TLS 初始化失败: {exc}")
                return
        client.username_pw_set(self.username, self.password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        self._client = client
        try:
            client.connect(self.host, self.port, keepalive=60)
        except Exception as exc:
            self.bus.connected.emit(False, f"连接失败: {exc}")
            self.bus.log.emit(f"连接失败: {exc}")
            return
        client.loop_forever()

    def _on_connect(self, client: mqtt.Client, _userdata, _flags, reason_code, _properties) -> None:
        ok = not getattr(reason_code, "is_failure", False)
        if not ok:
            self._connected.clear()
            self.bus.connected.emit(False, f"rc={reason_code}")
            self.bus.log.emit(f"连接被拒: rc={reason_code}，已停止重连，请检查用户名/密码/证书")
            try:
                client.disconnect()
            except Exception:
                pass
            return
        if self._stopped.is_set():
            try:
                client.disconnect()
            except Exception:
                pass
            return
        self._connected.set()
        up_topic = f"up/{self.product_id}/{self.device_id}"
        client.subscribe(up_topic, qos=1)
        if self.subscribe_all:
            client.subscribe("up/+/+", qos=1)
        client.subscribe(f"{up_topic}/log", qos=0)
        self.bus.connected.emit(True, up_topic)
        self.bus.log.emit(f"已订阅: {up_topic} / {up_topic}/log(调试流)")
        self.bus.log.emit(f"已连接: {self.host}:{self.port}{self.path}")

    def _on_subscribe(self, _client, _userdata, _mid, reason_codes, _properties) -> None:
        failed = [
            rc
            for rc in reason_codes
            if (isinstance(rc, int) and rc >= 0x80)
            or (not isinstance(rc, int) and getattr(rc, "is_failure", False))
        ]
        if failed:
            self.bus.log.emit(f"MQTT 订阅被拒绝: {failed}")

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if self._stopped.is_set():
            return
        self._connected.clear()
        self.bus.disconnected.emit(f"rc={reason_code}")
        self.bus.log.emit(f"MQTT 断开: rc={reason_code}")

    def _on_message(self, _client, _userdata, message) -> None:
        if self._stopped.is_set():
            return
        try:
            text = message.payload.decode("utf-8", errors="replace")
        except Exception:
            return
        self.bus.message_received.emit(message.topic, text)
        try:
            data: Any = json.loads(text)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        method = data.get("method")
        req_id = data.get("id")
        raw_code = data.get("code")
        if not (isinstance(method, str) and isinstance(req_id, (int, str))):
            return
        if isinstance(raw_code, bool) or not isinstance(raw_code, (int, float, str)):
            return
        try:
            code = int(raw_code)
        except (TypeError, ValueError):
            return
        self.bus.ack_received.emit(req_id, method, code)