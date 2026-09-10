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

FIXED_REQUEST_FUNCTIONS = frozenset((FC_READ_COILS, FC_READ_HOLDING, FC_READ_INPUT,
                                     FC_WRITE_SINGLE_COIL, FC_WRITE_SINGLE))
VARIABLE_REQUEST_FUNCTIONS = frozenset((FC_WRITE_MULTIPLE_COILS, FC_WRITE_MULTIPLE))
SUPPORTED_REQUEST_FUNCTIONS = FIXED_REQUEST_FUNCTIONS | VARIABLE_REQUEST_FUNCTIONS

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


def extract_requests(buffer: bytearray) -> list[bytes]:
    """Recover complete supported requests without relying on USB read boundaries.

    Serial/USB delivery can coalesce frames or begin midway through one. Scan
    for a plausible length and valid CRC, retaining incomplete tails. Unknown
    functions remain available to the receiver's idle-gap fallback.
    """
    frames = []
    offset = 0
    while len(buffer) - offset >= 8:
        if not 0 <= buffer[offset] <= 247:
            offset += 1
            continue
        function = buffer[offset + 1]
        if function in FIXED_REQUEST_FUNCTIONS:
            size = 8
        elif function in VARIABLE_REQUEST_FUNCTIONS:
            size = 9 + buffer[offset + 6]
            if size > 256:
                offset += 1
                continue
        else:
            offset += 1
            continue
        frame = bytes(buffer[offset:offset + size])
        if len(frame) == size and validate_crc(frame):
            frames.append(frame)
            del buffer[:offset + size]
            offset = 0
        else:
            offset += 1
    # An RTU ADU cannot exceed 256 bytes; bound noise retained between reads.
    if len(buffer) > 256:
        del buffer[:-256]
    return frames


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
