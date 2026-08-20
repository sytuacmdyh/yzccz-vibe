from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc, parse_request
from src.ems_modbus_slave.protocol_messages import describe_request, describe_response
from src.ems_modbus_slave.register_model import RegisterBank


def request(bank: RegisterBank, payload: list[int]) -> bytes:
    frame = append_crc(bytes(payload))
    response = bank.handle_request(frame, payload[0])
    if response is None:
        raise AssertionError(f"missing response for {frame.hex(' ')}")
    return response


def main() -> int:
    profile = DeviceProfile.from_json(ROOT / "profiles" / "dm_hpwt18_u1.json")
    bank = RegisterBank(profile)

    assert profile.baudrate == 9600
    assert profile.by_address[4].display_value(170).endswith("[170]")
    assert profile.coil_by_address[6400].display_value(True)
    assert profile.by_address[4].name == "设定开关机"
    assert bank.get(4) == 85
    assert profile.by_address[300].name == "通讯协议版本"
    assert profile.coil_by_address[6400].backing_register == 400
    assert profile.coil_by_address[8000].backing_register == 500

    fc03_word4 = request(bank, [1, 3, 0, 4, 0, 1])
    assert fc03_word4[3:5] == bytes([0x00, 0x55])

    request(bank, [1, 6, 1, 0x90, 0, 1])
    fc01_bit6400 = request(bank, [1, 1, 0x19, 0, 0, 1])
    assert fc01_bit6400[3] & 0x01

    request(bank, [1, 5, 0x19, 0, 0, 0])
    fc03_word400 = request(bank, [1, 3, 1, 0x90, 0, 1])
    assert fc03_word400[3:5] == bytes([0x00, 0x00])

    request(bank, [1, 15, 0x19, 0, 0, 2, 1, 3])
    fc03_word400 = request(bank, [1, 3, 1, 0x90, 0, 1])
    assert fc03_word400[3:5] == bytes([0x00, 0x03])

    bank.set_direct(4, 170)
    bank.set_coil_direct(6400, not profile.coil_by_address[6400].default)
    restarted_bank = RegisterBank(profile)
    assert restarted_bank.get(4) == profile.by_address[4].default
    assert restarted_bank.get_coil(6400) == profile.coil_by_address[6400].default

    bank.reset_defaults()
    assert bank.get(4) == profile.by_address[4].default
    assert bank.get_coil(6400) == profile.coil_by_address[6400].default

    write_frame = append_crc(bytes([1, 6, 0, 4, 0, 170]))
    parsed = parse_request(write_frame)
    assert parsed is not None
    assert "[170]" in describe_request(profile, parsed)
    write_response = bank.handle_request(write_frame, 1)
    assert write_response is not None
    assert describe_response(parsed, write_response) == ("已确认单点写入", False)

    invalid_address_frame = append_crc(bytes([1, 6, 0xFF, 0xFF, 0, 1]))
    parsed = parse_request(invalid_address_frame)
    assert parsed is not None
    invalid_address_response = bank.handle_request(invalid_address_frame, 1)
    assert invalid_address_response is not None
    assert describe_response(parsed, invalid_address_response) == (
        "功能码 0x06 请求失败：非法数据地址",
        True,
    )

    readonly_write_frame = append_crc(bytes([1, 6, 1, 0x4E, 1, 24]))
    readonly_response = bank.handle_request(readonly_write_frame, 1)
    assert readonly_response is not None
    assert readonly_response[1:3] == bytes([0x86, 0x02])

    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
