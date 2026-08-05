from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


FC_READ_HOLDING = 0x03
FC_READ_COILS = 0x01
FC_READ_INPUT = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTIPLE_COILS = 0x0F
FC_WRITE_MULTIPLE = 0x10

EX_ILLEGAL_FUNCTION = 0x01
EX_ILLEGAL_DATA_ADDRESS = 0x02
EX_ILLEGAL_DATA_VALUE = 0x03


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def validate_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    received = frame[-2] | (frame[-1] << 8)
    return crc16_modbus(frame[:-2]) == received


@dataclass
class RequestFrame:
    slave_id: int
    function_code: int
    raw: bytes


def parse_request(frame: bytes) -> Optional[RequestFrame]:
    if len(frame) < 4 or not validate_crc(frame):
        return None
    return RequestFrame(slave_id=frame[0], function_code=frame[1], raw=frame)


def build_exception(slave_id: int, function_code: int, exception_code: int) -> bytes:
    return append_crc(bytes([slave_id, function_code | 0x80, exception_code]))
