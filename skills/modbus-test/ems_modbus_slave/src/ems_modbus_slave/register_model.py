from __future__ import annotations

import threading
from typing import Dict, List

from .device_profile import CoilDefinition, DeviceProfile, RegisterDefinition
from .preset_loader import SimulatorPreset
from .modbus_rtu import (
    EX_ILLEGAL_FUNCTION,
    EX_ILLEGAL_DATA_ADDRESS,
    EX_ILLEGAL_DATA_VALUE,
    FC_READ_COILS,
    FC_READ_HOLDING,
    FC_READ_INPUT,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE,
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE,
    append_crc,
    build_exception,
)

# EMS 群控按从站 ID 轮询时，各机组独立维护的控制/状态 holding（其余地址仍共享）
PER_SLAVE_HOLDING_ADDRESSES = frozenset(
    {
        0,  # 群控字
        1,  # 开关机
        2,  # 运行模式
        10,
        11,
        101,
        102,
        300,
        301,
        600,  # 运行状态
        637,  # 除霜
        646,
        800,
        803,
    }
)

def _flatten_profile_description(description: str) -> str:
    skip_prefixes = (
        "地址：", "地址:",
        "读写：", "读写:",
        "数据类型：", "数据类型:",
        "默认值：", "默认值:",
        "传输关系：", "传输关系:",
        "取值范围：", "取值范围:",
    )
    lines: list[str] = []
    for line in description.splitlines():
        stripped = line.strip()
        if not stripped or stripped in ("值/状态：", "值/状态:"):
            continue
        if any(stripped.startswith(prefix) for prefix in skip_prefixes):
            continue
        lines.append(stripped)
    return " | ".join(lines)


def format_register_row_description(register: RegisterDefinition, raw_value: int) -> str:
    parts: list[str] = []
    static_text = _flatten_profile_description(register.description)
    if static_text:
        parts.append(static_text)
    state_label = register.enum.get(str(raw_value)) if register.enum else None
    current_state = state_label or register.display_value(raw_value)
    parts.append(f"当前状态：{current_state}")
    return " | ".join(parts)


def format_coil_row_description(coil: CoilDefinition, coil_value: bool) -> str:
    parts: list[str] = []
    static_text = _flatten_profile_description(coil.description)
    if static_text:
        parts.append(static_text)
    parts.append(f"当前状态：{coil.display_value(coil_value)}")
    return " | ".join(parts)


class RegisterBank:
    def __init__(self, profile: DeviceProfile, preset: SimulatorPreset | None = None) -> None:
        self.profile = profile
        self._preset = preset
        self._lock = threading.RLock()
        self._values = self._build_default_values()
        self._per_slave_values: Dict[tuple[int, int], int] = {}
        if preset is not None:
            self._apply_preset_unlocked(preset)

    def _is_per_slave_address(self, address: int) -> bool:
        return address in PER_SLAVE_HOLDING_ADDRESSES

    def _read_holding(self, address: int, slave_id: int) -> int:
        key = (slave_id, address)
        if self._is_per_slave_address(address) and key in self._per_slave_values:
            return self._per_slave_values[key]
        return self._values[address]

    def _write_holding(self, address: int, value: int, slave_id: int) -> bool:
        register = self.profile.by_address.get(address)
        if register is None or not register.writable:
            return False
        value = register.clamp(value)
        if self._is_per_slave_address(address):
            self._per_slave_values[(slave_id, address)] = value
            if address in (1, 2):
                self._sync_run_status_from_power(slave_id)
            return True
        self._values[address] = value
        self.apply_binding(register.name, value)
        return True

    def _sync_run_status_from_power(self, slave_id: int) -> None:
        power = self._read_holding(1, slave_id)
        mode = self._read_holding(2, slave_id)
        if power == 0:
            self._per_slave_values[(slave_id, 600)] = 5
        elif mode == 0:
            self._per_slave_values[(slave_id, 600)] = 1
        elif mode == 1:
            self._per_slave_values[(slave_id, 600)] = 0
        elif mode == 2:
            self._per_slave_values[(slave_id, 600)] = 2
        else:
            self._per_slave_values[(slave_id, 600)] = self._values.get(600, 5)

    def _build_default_values(self) -> Dict[int, int]:
        values: Dict[int, int] = {
            register.address: register.default for register in self.profile.registers
        }
        for coil in self.profile.coils:
            values.setdefault(coil.backing_register, 0)
            if coil.default:
                values[coil.backing_register] |= 1 << coil.bit_offset
            else:
                values[coil.backing_register] &= ~(1 << coil.bit_offset)
        return values

    def reset_defaults(self) -> None:
        with self._lock:
            self._values = self._build_default_values()
            self._per_slave_values = {}
            if self._preset is not None:
                self._apply_preset_unlocked(self._preset)

    def _apply_preset_unlocked(self, preset: SimulatorPreset) -> None:
        for address, value in preset.registers.items():
            register = self.profile.by_address.get(address)
            if register is None:
                continue
            self._values[address] = register.clamp(value)
        for address, value in preset.coils.items():
            coil = self.profile.coil_by_address.get(address)
            if coil is None:
                continue
            self._set_coil_value(coil, value)

    def get(self, address: int, slave_id: int | None = None) -> int:
        with self._lock:
            if slave_id is not None:
                return self._read_holding(address, slave_id)
            return self._values[address]

    def set_direct(self, address: int, value: int, slave_id: int | None = None) -> None:
        with self._lock:
            register = self.profile.by_address[address]
            value = register.clamp(value)
            if slave_id is not None and self._is_per_slave_address(address):
                self._per_slave_values[(slave_id, address)] = value
                if address in (1, 2):
                    self._sync_run_status_from_power(slave_id)
                return
            self._values[address] = value

    def get_coil(self, address: int) -> bool:
        with self._lock:
            coil = self.profile.coil_by_address[address]
            return bool(self._values.get(coil.backing_register, 0) & (1 << coil.bit_offset))

    def set_coil_direct(self, address: int, value: bool) -> None:
        with self._lock:
            coil = self.profile.coil_by_address[address]
            self._set_coil_value(coil, value)

    def current_values(
        self, slave_id: int | None = None
    ) -> tuple[Dict[int, int], Dict[int, bool]]:
        with self._lock:
            registers = {
                register.address: (
                    self._read_holding(register.address, slave_id)
                    if slave_id is not None
                    else self._values[register.address]
                )
                for register in self.profile.registers
            }
            coils = {coil.address: self.get_coil(coil.address) for coil in self.profile.coils}
            return registers, coils

    def apply_preset(self, preset: SimulatorPreset) -> None:
        with self._lock:
            self._values = self._build_default_values()
            self._per_slave_values = {}
            self._apply_preset_unlocked(preset)
            self._preset = preset

    def snapshot(self, slave_id: int | None = None) -> List[Dict[str, object]]:
        with self._lock:
            return self._snapshot_locked(slave_id)

    def _snapshot_locked(self, slave_id: int | None = None) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for register in self.profile.registers:
            raw_value = (
                self._read_holding(register.address, slave_id)
                if slave_id is not None
                else self._values[register.address]
            )
            rows.append(
                {
                    "kind": "register",
                    "address": register.address,
                    "address_label": register.address_label or f"word {register.address}",
                    "name": register.name,
                    "access": register.access,
                    "value": raw_value,
                    "description": format_register_row_description(register, raw_value),
                }
            )
        for coil in self.profile.coils:
            coil_value = self.get_coil(coil.address)
            rows.append(
                {
                    "kind": "coil",
                    "address": coil.address,
                    "address_label": coil.address_label or f"bit {coil.address}",
                    "name": coil.name,
                    "access": coil.access,
                    "value": 1 if coil_value else 0,
                    "description": format_coil_row_description(coil, coil_value),
                }
            )
        return rows

    def apply_binding(self, register_name: str, value: int) -> None:
        target_name = self.profile.bindings.get(register_name)
        if not target_name:
            return
        target = self.profile.by_name.get(target_name)
        if target is None:
            return
        self._values[target.address] = target.clamp(value)

    def write(self, address: int, value: int, slave_id: int | None = None) -> bool:
        with self._lock:
            if slave_id is not None and self._is_per_slave_address(address):
                ok = self._write_holding(address, value, slave_id)
                if ok and address in (1, 2):
                    self._sync_run_status_from_power(slave_id)
                return ok
            register = self.profile.by_address.get(address)
            if register is None or not register.writable:
                return False
            value = register.clamp(value)
            self._values[address] = value
            self.apply_binding(register.name, value)
            return True

    def write_coil(self, address: int, value: bool) -> bool:
        with self._lock:
            coil = self.profile.coil_by_address.get(address)
            if coil is None or not coil.writable:
                return False
            self._set_coil_value(coil, value)
            return True

    def _set_coil_value(self, coil: CoilDefinition, value: bool) -> None:
        register_value = self._values.get(coil.backing_register, 0)
        mask = 1 << coil.bit_offset
        if value:
            register_value |= mask
        else:
            register_value &= ~mask
        self._values[coil.backing_register] = register_value & 0xFFFF

    def handle_request(self, frame: bytes, slave_id: int) -> bytes | None:
        with self._lock:
            return self._handle_request_locked(frame, slave_id)

    def _handle_request_locked(self, frame: bytes, slave_id: int) -> bytes | None:
        function_code = frame[1]
        if function_code == FC_READ_COILS:
            if len(frame) < 8:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc01(frame, slave_id)
        if function_code == FC_READ_HOLDING:
            if len(frame) < 8:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc03(frame, slave_id)
        if function_code == FC_READ_INPUT:
            if len(frame) < 8:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc04(frame, slave_id)
        if function_code == FC_WRITE_SINGLE_COIL:
            if len(frame) < 8:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc05(frame, slave_id)
        if function_code == FC_WRITE_SINGLE:
            if len(frame) < 8:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc06(frame, slave_id)
        if function_code == FC_WRITE_MULTIPLE_COILS:
            if len(frame) < 9:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc0f(frame, slave_id)
        if function_code == FC_WRITE_MULTIPLE:
            if len(frame) < 9:
                return build_exception(slave_id, function_code, EX_ILLEGAL_DATA_VALUE)
            return self._handle_fc10(frame, slave_id)
        return build_exception(slave_id, function_code, EX_ILLEGAL_FUNCTION)

    def _handle_fc01(self, frame: bytes, slave_id: int) -> bytes:
        start_addr = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        if count <= 0 or count > 2000:
            return build_exception(slave_id, FC_READ_COILS, EX_ILLEGAL_DATA_VALUE)

        values: List[bool] = []
        for offset in range(count):
            address = start_addr + offset
            coil = self.profile.coil_by_address.get(address)
            if coil is None or not coil.readable:
                return build_exception(slave_id, FC_READ_COILS, EX_ILLEGAL_DATA_ADDRESS)
            values.append(self.get_coil(address))

        byte_count = (count + 7) // 8
        payload = bytearray([slave_id, FC_READ_COILS, byte_count])
        payload.extend([0] * byte_count)
        for index, value in enumerate(values):
            if value:
                payload[3 + index // 8] |= 1 << (index % 8)
        return append_crc(bytes(payload))

    def _handle_fc03(self, frame: bytes, slave_id: int) -> bytes:
        start_addr = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        if count <= 0 or count > 125:
            return build_exception(slave_id, FC_READ_HOLDING, EX_ILLEGAL_DATA_VALUE)

        values: List[int] = []
        for offset in range(count):
            address = start_addr + offset
            register = self.profile.by_address.get(address)
            if register is None:
                # Protocol reserved gaps (e.g. 677-679); real firmware returns 0.
                values.append(0)
                continue
            if not register.readable:
                return build_exception(
                    slave_id, FC_READ_HOLDING, EX_ILLEGAL_DATA_ADDRESS
                )
            values.append(self._read_holding(address, slave_id))

        payload = bytearray([slave_id, FC_READ_HOLDING, count * 2])
        for value in values:
            payload.extend([(value >> 8) & 0xFF, value & 0xFF])
        return append_crc(bytes(payload))

    def _handle_fc04(self, frame: bytes, slave_id: int) -> bytes:
        start_addr = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        if count <= 0 or count > 125:
            return build_exception(slave_id, FC_READ_INPUT, EX_ILLEGAL_DATA_VALUE)

        values: List[int] = []
        for offset in range(count):
            address = start_addr + offset
            register = self.profile.by_address.get(address)
            if register is None:
                # Protocol reserved gaps; real firmware returns 0.
                values.append(0)
                continue
            if not register.readable:
                return build_exception(
                    slave_id, FC_READ_INPUT, EX_ILLEGAL_DATA_ADDRESS
                )
            values.append(self._read_holding(address, slave_id))

        payload = bytearray([slave_id, FC_READ_INPUT, count * 2])
        for value in values:
            payload.extend([(value >> 8) & 0xFF, value & 0xFF])
        return append_crc(bytes(payload))

    def _handle_fc06(self, frame: bytes, slave_id: int) -> bytes:
        address = (frame[2] << 8) | frame[3]
        value = (frame[4] << 8) | frame[5]
        if not self.write(address, value, slave_id):
            return build_exception(slave_id, FC_WRITE_SINGLE, EX_ILLEGAL_DATA_ADDRESS)
        return frame

    def _handle_fc05(self, frame: bytes, slave_id: int) -> bytes:
        address = (frame[2] << 8) | frame[3]
        raw_value = (frame[4] << 8) | frame[5]
        if raw_value == 0xFF00:
            value = True
        elif raw_value == 0x0000:
            value = False
        else:
            return build_exception(slave_id, FC_WRITE_SINGLE_COIL, EX_ILLEGAL_DATA_VALUE)
        if not self.write_coil(address, value):
            return build_exception(slave_id, FC_WRITE_SINGLE_COIL, EX_ILLEGAL_DATA_ADDRESS)
        return frame

    def _handle_fc10(self, frame: bytes, slave_id: int) -> bytes:
        start_addr = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        byte_count = frame[6]
        if count <= 0 or count > 123 or byte_count != count * 2 or len(frame) < 9 + byte_count:
            return build_exception(slave_id, FC_WRITE_MULTIPLE, EX_ILLEGAL_DATA_VALUE)

        values: List[int] = []
        for index in range(count):
            base = 7 + index * 2
            values.append((frame[base] << 8) | frame[base + 1])

        for offset in range(count):
            address = start_addr + offset
            register = self.profile.by_address.get(address)
            if register is None or not register.writable:
                return build_exception(
                    slave_id, FC_WRITE_MULTIPLE, EX_ILLEGAL_DATA_ADDRESS
                )
        for offset, value in enumerate(values):
            if not self.write(start_addr + offset, value, slave_id):
                return build_exception(
                    slave_id, FC_WRITE_MULTIPLE, EX_ILLEGAL_DATA_ADDRESS
                )

        payload = bytes(
            [
                slave_id,
                FC_WRITE_MULTIPLE,
                (start_addr >> 8) & 0xFF,
                start_addr & 0xFF,
                (count >> 8) & 0xFF,
                count & 0xFF,
            ]
        )
        return append_crc(payload)

    def _handle_fc0f(self, frame: bytes, slave_id: int) -> bytes:
        start_addr = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        byte_count = frame[6]
        if count <= 0 or count > 1968 or byte_count != (count + 7) // 8 or len(frame) < 9 + byte_count:
            return build_exception(slave_id, FC_WRITE_MULTIPLE_COILS, EX_ILLEGAL_DATA_VALUE)

        values: List[bool] = []
        for index in range(count):
            byte = frame[7 + index // 8]
            values.append(bool(byte & (1 << (index % 8))))

        for offset in range(count):
            address = start_addr + offset
            coil = self.profile.coil_by_address.get(address)
            if coil is None or not coil.writable:
                return build_exception(
                    slave_id, FC_WRITE_MULTIPLE_COILS, EX_ILLEGAL_DATA_ADDRESS
                )
        for offset, value in enumerate(values):
            self.write_coil(start_addr + offset, value)

        payload = bytes(
            [
                slave_id,
                FC_WRITE_MULTIPLE_COILS,
                (start_addr >> 8) & 0xFF,
                start_addr & 0xFF,
                (count >> 8) & 0xFF,
                count & 0xFF,
            ]
        )
        return append_crc(payload)
