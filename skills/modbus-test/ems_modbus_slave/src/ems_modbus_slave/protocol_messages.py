from __future__ import annotations

from .device_profile import DeviceProfile
from .modbus_rtu import (
    EX_ILLEGAL_DATA_ADDRESS,
    EX_ILLEGAL_DATA_VALUE,
    EX_ILLEGAL_FUNCTION,
    FC_READ_COILS,
    FC_READ_HOLDING,
    FC_WRITE_MULTIPLE,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_SINGLE,
    FC_WRITE_SINGLE_COIL,
    RequestFrame,
)


EXCEPTION_TEXT = {
    EX_ILLEGAL_FUNCTION: "非法功能码",
    EX_ILLEGAL_DATA_ADDRESS: "非法数据地址",
    EX_ILLEGAL_DATA_VALUE: "非法数据值",
}


def _names_for_range(profile: DeviceProfile, start: int, count: int, coil: bool) -> str:
    points = profile.coil_by_address if coil else profile.by_address
    names = [points[address].name for address in range(start, start + count) if address in points]
    if not names:
        return ""
    shown = "、".join(names[:6])
    return f"；点位：{shown}" + (f" 等{len(names)}项" if len(names) > 6 else "")


def _register_line(profile: DeviceProfile, address: int, value: int) -> str:
    definition = profile.by_address.get(address)
    if definition is None:
        return f"word {address}：{value}"
    return f"{definition.name}（{definition.address_label}）：{definition.display_value(value)}"


def _coil_line(profile: DeviceProfile, address: int, value: bool) -> str:
    definition = profile.coil_by_address.get(address)
    if definition is None:
        return f"bit {address}：{'ON' if value else 'OFF'}"
    return f"{definition.name}（{definition.address_label}）：{definition.display_value(value)}"


def describe_request(profile: DeviceProfile, request: RequestFrame) -> str:
    frame = request.raw
    function = request.function_code
    if len(frame) < 8:
        return f"收到功能码 0x{function:02X} 的短帧"
    start = (frame[2] << 8) | frame[3]
    count_or_value = (frame[4] << 8) | frame[5]
    if function == FC_READ_HOLDING:
        return f"读取保持寄存器 word {start}~{start + count_or_value - 1}（{count_or_value}项）" + _names_for_range(profile, start, count_or_value, False)
    if function == FC_READ_COILS:
        return f"读取线圈 bit {start}~{start + count_or_value - 1}（{count_or_value}项）" + _names_for_range(profile, start, count_or_value, True)
    if function == FC_WRITE_SINGLE:
        return "写入寄存器：" + _register_line(profile, start, count_or_value)
    if function == FC_WRITE_SINGLE_COIL:
        if count_or_value not in (0x0000, 0xFF00):
            return f"写入线圈 bit {start}：非法值 0x{count_or_value:04X}"
        return "写入线圈：" + _coil_line(profile, start, count_or_value == 0xFF00)
    if function == FC_WRITE_MULTIPLE:
        if len(frame) < 9:
            return "批量写入寄存器：帧长度不足"
        values = [((frame[7 + index * 2] << 8) | frame[8 + index * 2]) for index in range(count_or_value) if 8 + index * 2 < len(frame) - 2]
        lines = [_register_line(profile, start + index, value) for index, value in enumerate(values)]
        return "批量写入寄存器：\n" + "\n".join(lines)
    if function == FC_WRITE_MULTIPLE_COILS:
        if len(frame) < 9:
            return "批量写入线圈：帧长度不足"
        values = [bool(frame[7 + index // 8] & (1 << (index % 8))) for index in range(count_or_value) if 7 + index // 8 < len(frame) - 2]
        lines = [_coil_line(profile, start + index, value) for index, value in enumerate(values)]
        return "批量写入线圈：\n" + "\n".join(lines)
    return f"收到未支持功能码 0x{function:02X}"


def describe_response(request: RequestFrame, response: bytes) -> tuple[str, bool]:
    if len(response) >= 3 and response[1] & 0x80:
        code = response[2]
        return f"功能码 0x{request.function_code:02X} 请求失败：{EXCEPTION_TEXT.get(code, f'异常码 0x{code:02X}')}", True
    function = request.function_code
    if function == FC_READ_HOLDING:
        return f"已返回 {response[2] // 2 if len(response) > 2 else 0} 个保持寄存器", False
    if function == FC_READ_COILS:
        return f"已返回 {response[2] if len(response) > 2 else 0} 字节线圈数据", False
    if function in (FC_WRITE_SINGLE, FC_WRITE_SINGLE_COIL):
        return "已确认单点写入", False
    if function in (FC_WRITE_MULTIPLE, FC_WRITE_MULTIPLE_COILS):
        return "已确认批量写入", False
    return "已发送 Modbus 应答", False
