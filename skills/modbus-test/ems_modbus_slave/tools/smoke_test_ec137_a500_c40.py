from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc, validate_crc
from src.ems_modbus_slave.register_model import RegisterBank


HOLDING = (0xD000, 0xD001, 0xD101, 0xD119, 0xD16C)
INPUT = (0xD010, 0xD011, 0xD012, 0xD013, 0xD014)


def request(bank: RegisterBank, payload: list[int]) -> bytes:
    frame = append_crc(bytes(payload))
    response = bank.handle_request(frame, payload[0])
    if response is None:
        raise AssertionError(f"missing response for {frame.hex(' ')}")
    assert validate_crc(response), response.hex()
    return response


def read_one(bank: RegisterBank, slave_id: int, function: int, address: int) -> int:
    response = request(
        bank,
        [slave_id, function, address >> 8, address & 0xFF, 0, 1],
    )
    assert response[0:2] == bytes([slave_id, function]), response.hex()
    assert response[2] == 2, response.hex()
    return (response[3] << 8) | response[4]


def write_one(bank: RegisterBank, slave_id: int, address: int, value: int) -> bytes:
    return request(
        bank,
        [slave_id, 6, address >> 8, address & 0xFF, value >> 8, value & 0xFF],
    )


def main() -> int:
    profile = DeviceProfile.from_json(ROOT / "profiles" / "ec137_a500_c40.json")
    bank = RegisterBank(profile)

    assert profile.profile_id == "ec137_a500_c40"
    assert profile.device_model == "ec_fan"
    assert profile.baudrate == 19200
    assert profile.function_codes == [3, 4, 6]
    assert profile.per_slave_addresses == frozenset(HOLDING + INPUT)
    assert all(profile.by_address[address].register_type == "hold" for address in HOLDING)
    assert all(profile.by_address[address].register_type == "input" for address in INPUT)
    assert profile.by_address[0xD000].default == 0
    assert profile.by_address[0xD001].default == 0
    assert profile.by_address[0xD010].default == 0
    assert profile.by_address[0xD101].default == 0
    assert profile.by_address[0xD119].default == 3500
    assert profile.by_address[0xD16C].default == 1
    assert profile.to_dict()["per_slave_addresses"] == sorted(HOLDING + INPUT)

    # Every fan register is isolated between node 1 and node 2.
    for index, address in enumerate(HOLDING + INPUT, start=1):
        bank.set_direct(address, index * 0x0111, slave_id=1)
        bank.set_direct(address, index * 0x0222, slave_id=2)
    for address in HOLDING:
        assert read_one(bank, 1, 3, address) == bank.get(address, 1)
        assert read_one(bank, 2, 3, address) == bank.get(address, 2)
    for address in INPUT:
        assert read_one(bank, 1, 4, address) == bank.get(address, 1)
        assert read_one(bank, 2, 4, address) == bank.get(address, 2)

    # FC06 returns the exact normal echo for all hp-52kw configuration writes.
    for address, value in ((0xD16C, 0), (0xD101, 1), (0xD119, 925), (0xD001, 0x1234)):
        response = write_one(bank, 1, address, value)
        assert response[0:6] == bytes([1, 6, address >> 8, address & 0xFF, value >> 8, value & 0xFF])
        assert bank.get(address, 1) == value

    # D000 reset clears only the addressed node after producing its echo.
    bank.set_direct(0xD011, 0x0010, slave_id=1)
    bank.set_direct(0xD011, 0x0040, slave_id=2)
    response = write_one(bank, 1, 0xD000, 4)
    assert response[0:6] == bytes([1, 6, 0xD0, 0, 0, 4])
    assert bank.get(0xD000, 1) == 0
    assert bank.get(0xD011, 1) == 0
    assert bank.get(0xD011, 2) == 0x0040
    bank.set_direct(0xD000, 4, slave_id=2)
    assert bank.get(0xD000, 2) == 4
    assert bank.get(0xD011, 2) == 0x0040

    # FC04 telemetry is five big-endian words with a valid RTU CRC.
    telemetry = [0x0102, 0x0304, 0x0506, 0x0708, 0x090A]
    for address, value in zip(INPUT, telemetry):
        bank.set_direct(address, value, slave_id=2)
    response = request(bank, [2, 4, 0xD0, 0x10, 0, 5])
    assert len(response) == 15
    assert response[0:3] == bytes([2, 4, 10])
    assert [((response[3 + i * 2] << 8) | response[4 + i * 2]) for i in range(5)] == telemetry

    # FC03/FC04 enforce their distinct register spaces.
    assert read_one(bank, 1, 3, 0xD001) == 0x1234
    holding_from_input = request(bank, [1, 3, 0xD0, 0x10, 0, 1])
    assert holding_from_input[1:3] == bytes([0x83, 0x02]), holding_from_input.hex()
    input_from_holding = request(bank, [1, 4, 0xD0, 0, 0, 1])
    assert input_from_holding[1:3] == bytes([0x84, 0x02]), input_from_holding.hex()

    # Standard exception behavior remains unchanged.
    invalid_function = request(bank, [1, 0x45, 0, 0, 0, 1])
    assert invalid_function[1:3] == bytes([0xC5, 0x01]), invalid_function.hex()
    invalid_address = request(bank, [1, 6, 0xFF, 0xFF, 0, 1])
    assert invalid_address[1:3] == bytes([0x86, 0x02]), invalid_address.hex()

    print("EC137 smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
