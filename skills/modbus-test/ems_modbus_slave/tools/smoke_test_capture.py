from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ems_modbus_slave.capture import CaptureTracker
from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc
from src.ems_modbus_slave.register_model import RegisterBank
from src.ems_modbus_slave.serial_slave import SerialSlaveServer


def main() -> int:
    profile = DeviceProfile.from_json(ROOT / "profiles" / "dm_hpwt18_u1.json")
    bank = RegisterBank(profile)
    tracker = CaptureTracker()
    records = []
    server = SerialSlaveServer(
        profile,
        bank,
        lambda _text: None,
        lambda: None,
        lambda _text, _kind: None,
        tracker,
        records.append,
    )

    read_word4 = append_crc(bytes([1, 3, 0, 4, 0, 1]))
    server._handle_frame(read_word4)
    assert not records, "untracked packets must not be captured"

    tracker.toggle("register", 4)
    server._handle_frame(read_word4)
    assert not records, "read packets must not be captured even when tracked"

    write_word4 = append_crc(bytes([1, 6, 0, 4, 0, 0xAA]))
    server._handle_frame(write_word4)
    assert len(records) == 1
    assert records[-1].address_summary() == "word 4"
    assert records[-1].points == ("设定开关机 (word 4（YD）)",)
    assert records[-1].request == write_word4
    assert records[-1].response[1] == 6

    tracker.toggle("register", 5)
    server._handle_frame(append_crc(bytes([1, 16, 0, 4, 0, 2, 4, 0, 0xAA, 0, 0x55])))
    assert len(records) == 2, "a multi-write with two tracked points must create one record"
    assert records[-1].address_summary() == "word 4~5"
    assert len(records[-1].points) == 2

    tracker.toggle("coil", 6400)
    server._handle_frame(append_crc(bytes([1, 1, 0x19, 0, 0, 1])))
    assert len(records) == 2, "coil read packets must not be captured"

    server._handle_frame(append_crc(bytes([1, 5, 0x19, 0, 0xFF, 0x00])))
    assert len(records) == 3
    assert records[-1].function_code == 5
    assert "bit 6400" in records[-1].points[0]

    tracker.toggle("register", 334)
    server._handle_frame(append_crc(bytes([1, 6, 1, 0x4E, 1, 24])))
    assert records[-1].is_error
    assert records[-1].response[1:3] == bytes([0x86, 0x02])

    print("capture smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
