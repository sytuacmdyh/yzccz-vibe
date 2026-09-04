from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ems_modbus_slave.cli import _resolve_profile_path
from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc, parse_request, validate_crc
from src.ems_modbus_slave.register_model import RegisterBank

PROFILE_ID = "compressor_inverter_v24"
STATUS_ADDRESSES = (0x6005, 0x6006, 0x6008, 0x6009, 0x600A, 0x600B, 0x600F, 0x6016)
ALL_ADDRESSES = STATUS_ADDRESSES + (0x8000, 0x8001)


def request(bank: RegisterBank, payload: list[int], slave_id: int) -> bytes:
    frame = append_crc(bytes(payload))
    parsed = parse_request(frame)
    assert parsed is not None, frame.hex(" ")
    response = bank.handle_request(frame, slave_id)
    assert response is not None, frame.hex(" ")
    assert validate_crc(response), response.hex(" ")
    return response


def read_status(bank: RegisterBank, slave_id: int) -> bytes:
    return request(bank, [slave_id, 0x03, 0x60, 0x05, 0x00, 0x12], slave_id)


def write_one(bank: RegisterBank, slave_id: int, address: int, value: int) -> bytes:
    return request(
        bank,
        [slave_id, 0x06, address >> 8, address & 0xFF, value >> 8, value & 0xFF],
        slave_id,
    )


def words(response: bytes) -> list[int]:
    assert response[2] == 36, response.hex(" ")
    return [(response[index] << 8) | response[index + 1] for index in range(3, 39, 2)]


def main() -> int:
    profile_path = _resolve_profile_path(PROFILE_ID)
    assert profile_path.name == "compressor_inverter_v24.json"
    profile = DeviceProfile.from_json(profile_path)
    assert profile.profile_id == PROFILE_ID
    assert (profile.baudrate, profile.serial_bytesize, profile.serial_parity, profile.serial_stopbits) == (
        9600,
        8,
        "N",
        2,
    )
    assert profile.function_codes == [3, 6]
    assert profile.per_slave_addresses == frozenset(ALL_ADDRESSES)
    assert profile.coils == []

    by_address = profile.by_address
    assert all(by_address[address].register_type == "hold" for address in ALL_ADDRESSES)
    assert all(not by_address[address].writable for address in STATUS_ADDRESSES)
    assert all(by_address[address].readable for address in ALL_ADDRESSES)
    assert by_address[0x8000].default == 0
    assert by_address[0x8000].enum == {"0": "停机", "1025": "启动", "4": "故障复位"}
    assert by_address[0x8001].min_value == 0 and by_address[0x8001].max_value == 32767
    assert {field["address"] for field in profile.status_fields} == set(STATUS_ADDRESSES)
    assert {field["address"] for field in profile.status_fields if field.get("scale") == 10} == {
        0x6006,
        0x6008,
        0x600B,
    }

    bank = RegisterBank(profile)
    values = {
        0x6005: 700,
        0x6006: 123,
        0x6008: 456,
        0x6009: 78,
        0x600A: 380,
        0x600B: 250,
        0x600F: 9,
        0x6016: 0xFF38,
        0x8000: 0,
        0x8001: 1200,
    }
    other_values = {address: value + 1 for address, value in values.items()}
    for address, value in values.items():
        bank.set_direct(address, value, 1)
    for address, value in other_values.items():
        bank.set_direct(address, value, 2)

    response = read_status(bank, 1)
    assert len(response) == 41, response.hex(" ")
    assert response[:3] == bytes([1, 0x03, 36])
    actual = words(response)
    expected = [values.get(0x6005 + offset, 0) for offset in range(18)]
    assert actual == expected, (actual, expected)
    assert actual[0] == 700 and actual[1] == 123 and actual[3] == 456
    assert actual[5] == 380 and actual[10] == 9 and actual[17] == 0xFF38
    assert all(actual[offset] == 0 for offset in (2, 7, 8, 9, 11, 12, 13, 14, 15, 16))

    response_other = read_status(bank, 2)
    assert len(response_other) == 41
    assert words(response_other) == [other_values.get(0x6005 + offset, 0) for offset in range(18)]
    assert actual != words(response_other)

    for address, value in ((0x8001, 1200), (0x8000, 0x0401), (0x8000, 0), (0x8000, 4)):
        response = write_one(bank, 1, address, value)
        assert len(response) == 8 and response == append_crc(bytes([1, 6, address >> 8, address & 0xFF, value >> 8, value & 0xFF]))
    assert bank.get(0x8001, 1) == 1200
    assert bank.get(0x8000, 1) == 4
    assert bank.get(0x8000, 2) == other_values[0x8000]
    assert bank.get(0x8001, 2) == other_values[0x8001]
    write_one(bank, 1, 0x8000, 0)
    assert bank.get(0x8000, 1) == 0

    fc04 = request(bank, [1, 0x04, 0x60, 0x05, 0, 1], 1)
    assert fc04[:3] == bytes([1, 0x84, 0x02])
    invalid_fc = request(bank, [1, 0x02, 0, 0, 0, 1], 1)
    assert invalid_fc[:3] == bytes([1, 0x82, 0x01])
    unmapped = request(bank, [1, 0x06, 0x60, 0x07, 0, 1], 1)
    assert unmapped[:3] == bytes([1, 0x86, 0x02])
    bad_crc = append_crc(bytes([1, 0x03, 0x60, 0x05, 0, 1]))[:-1] + b"\x00"
    assert parse_request(bad_crc) is None

    print("compressor inverter V2.4 smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
