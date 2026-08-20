from __future__ import annotations

from queue import Empty, Queue
import threading
import time
from typing import Callable, Optional

import serial
from serial import SerialException

from .capture import CaptureRecord, CaptureTracker
from .device_profile import DeviceProfile
from .modbus_rtu import (
    FC_WRITE_MULTIPLE,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_SINGLE,
    FC_WRITE_SINGLE_COIL,
    parse_request,
)
from .protocol_messages import describe_request, describe_response
from .register_model import RegisterBank


LogFn = Callable[[str], None]
RefreshFn = Callable[[], None]
MessageFn = Callable[[str, str], None]
CaptureFn = Callable[[CaptureRecord], None]
WRITE_FUNCTION_CODES = {
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE,
}


class SerialSlaveServer:
    def __init__(
        self,
        profile: DeviceProfile,
        bank: RegisterBank,
        log_fn: LogFn,
        refresh_fn: RefreshFn,
        message_fn: MessageFn,
        capture_tracker: CaptureTracker | None = None,
        capture_fn: CaptureFn | None = None,
    ) -> None:
        self.profile = profile
        self.bank = bank
        self.log_fn = log_fn
        self.refresh_fn = refresh_fn
        self.message_fn = message_fn
        self.capture_tracker = capture_tracker
        self.capture_fn = capture_fn
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._serial: Optional[serial.Serial] = None
        self._port = ""
        self._baudrate = profile.baudrate
        self._slave_id = profile.slave_id
        self._respond_id_min: int | None = None
        self._respond_id_max: int | None = None
        self._tx_queue: "Queue[bytes]" = Queue()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def configure(
        self,
        port: str,
        baudrate: int,
        slave_id: int,
        respond_id_min: int | None = None,
        respond_id_max: int | None = None,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._slave_id = slave_id
        self._respond_id_min = respond_id_min
        self._respond_id_max = respond_id_max

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._serial is not None:
            try:
                self._serial.close()
            except SerialException:
                pass
        self._serial = None

    def _run(self) -> None:
        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
            )
            if self._respond_id_min is None or self._respond_id_max is None:
                id_text = f"slave_id={self._slave_id}"
            else:
                id_text = f"slave_id_range={self._respond_id_min}-{self._respond_id_max}"
            self.log_fn(f"Serial started: port={self._port}, baudrate={self._baudrate}, {id_text}")
        except SerialException as exc:
            self.log_fn(f"Failed to open serial port: {exc}")
            self.message_fn(f"串口打开失败：{exc}", "error")
            return

        rx = bytearray()
        last_byte_ts = 0.0
        while not self._stop_event.is_set():
            try:
                incoming = self._serial.read(256)
            except SerialException as exc:
                self.log_fn(f"Serial read error: {exc}")
                self.message_fn(f"串口读取错误：{exc}", "error")
                break

            if incoming:
                rx.extend(incoming)
                last_byte_ts = time.monotonic()

            if rx and (time.monotonic() - last_byte_ts) > 0.02:
                frame = bytes(rx)
                rx.clear()
                self._handle_frame(frame)

            try:
                tx = self._tx_queue.get_nowait()
            except Empty:
                tx = None
            if tx:
                try:
                    self._serial.write(tx)
                except SerialException as exc:
                    self.log_fn(f"Serial write error: {exc}")
                    self.message_fn(f"串口发送错误：{exc}", "error")
                    break

        self.log_fn("Serial slave stopped")

    def _handle_frame(self, frame: bytes) -> None:
        self.log_fn(f"RX: {frame.hex(' ')}")
        parsed = parse_request(frame)
        if parsed is None:
            self.log_fn("Ignored invalid frame")
            self.message_fn("收到非法 Modbus 帧，已忽略", "error")
            return
        response_slave_id = parsed.slave_id
        if self._respond_id_min is None or self._respond_id_max is None:
            accepts_frame = parsed.slave_id == self._slave_id
        else:
            accepts_frame = self._respond_id_min <= parsed.slave_id <= self._respond_id_max

        if not accepts_frame:
            self.log_fn(f"Ignored frame for slave_id={parsed.slave_id}")
            self.message_fn(f"收到 slave_id={parsed.slave_id} 请求，当前未响应", "error")
            return

        request_message = describe_request(self.profile, parsed)
        self.message_fn(request_message, "received")
        response = self.bank.handle_request(frame, response_slave_id)
        if response is None:
            return
        if parsed.function_code in WRITE_FUNCTION_CODES and not response[1] & 0x80:
            self.refresh_fn()
        self.log_fn(f"TX: {response.hex(' ')}")
        response_message, is_error = describe_response(parsed, response)
        self.message_fn(response_message, "error" if is_error else "sent")
        if self.capture_tracker is not None and self.capture_fn is not None:
            match = self.capture_tracker.matching_capture(self.profile, parsed)
            if match is not None:
                self.capture_fn(
                    CaptureRecord.create(
                        self.profile,
                        parsed,
                        response,
                        match,
                        request_message,
                        response_message,
                        is_error,
                    )
                )
        self._tx_queue.put(response)
