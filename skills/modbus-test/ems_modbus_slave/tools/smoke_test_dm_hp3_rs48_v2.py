from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc
from src.ems_modbus_slave.register_model import RegisterBank


def request(bank: RegisterBank, payload: list[int]) -> bytes:
    frame = append_crc(bytes(payload))
    response = bank.handle_request(frame, payload[0])
    if response is None:
        raise AssertionError(f"missing response for {payload}")
    return response


def read_holding(bank: RegisterBank, slave_id: int, address: int) -> int:
    response = request(bank, [slave_id, 3, address >> 8, address & 0xFF, 0, 1])
    assert not (response[1] & 0x80), response.hex()
    return (response[3] << 8) | response[4]


def write_holding(bank: RegisterBank, slave_id: int, address: int, value: int) -> None:
    request(
        bank,
        [slave_id, 6, address >> 8, address & 0xFF, value >> 8, value & 0xFF],
    )


def main() -> int:
    profile = DeviceProfile.from_json(ROOT / "profiles" / "dm_hp3_rs48_v2.json")
    bank = RegisterBank(profile)

    assert profile.profile_id == "dm_hp3_rs48_v2"
    assert profile.baudrate == 115200
    assert profile.function_codes == [3, 6, 16]
    assert len(profile.coils) == 0
    assert profile.by_address[1].name == "开关机"
    assert profile.by_address[1].access == "rw"
    assert profile.by_address[1].enum["0"] == "关机"
    assert profile.by_address[1].enum["1"] == "开机"
    assert profile.by_address[600].access == "r"
    assert profile.by_address[602].transfer == "传输值=实际值*10"
    assert bank.get(1) == 0
    assert bank.get(600) == 5
    assert bank.get(602) == 250

    resp = request(bank, [1, 3, 2, 0x58, 0, 1])
    assert resp[3:5] == bytes([0x00, 0x05]), resp.hex()

    write_holding(bank, 1, 1, 1)
    write_holding(bank, 1, 2, 0)
    assert read_holding(bank, 1, 1) == 1
    assert read_holding(bank, 1, 600) == 1
    assert read_holding(bank, 2, 1) == 0
    assert read_holding(bank, 2, 600) == 5
    assert bank.get(1, 1) == 1
    assert bank.get(1, 2) == 0
    assert bank.get(600, 1) == 1
    assert bank.get(600, 2) == 5

    write_holding(bank, 1, 2, 1)
    assert read_holding(bank, 1, 600) == 0
    write_holding(bank, 1, 2, 2)
    assert read_holding(bank, 1, 600) == 2

    bank.set_direct(1, 1, 2)
    bank.set_direct(2, 1, 2)
    assert bank.get(600, 2) == 0
    assert bank.current_values(1)[0][600] == 2
    assert bank.current_values(2)[0][600] == 0
    snapshot_1 = {int(row["address"]): row["value"] for row in bank.snapshot(1)}
    snapshot_2 = {int(row["address"]): row["value"] for row in bank.snapshot(2)}
    assert snapshot_1[600] == 2
    assert snapshot_2[600] == 0

    readonly_frame = append_crc(bytes([1, 6, 2, 0x58, 0, 6]))
    readonly_response = bank.handle_request(readonly_frame, 1)
    assert readonly_response is not None
    assert readonly_response[1] & 0x80

    for start in (0x0000, 0x0258, 0x02BC):
        block = request(bank, [1, 3, start >> 8, start & 0xFF, 0, 0x64])
        assert not (block[1] & 0x80), f"FC03 block read failed at {start}: {block.hex()}"
        assert block[2] == 200, block.hex()

    rw_count = sum(1 for item in profile.registers if item.access == "rw")
    ro_count = sum(1 for item in profile.registers if item.access == "r")
    print("validation ok")
    print(f"registers={len(profile.registers)}, rw={rw_count}, ro={ro_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
