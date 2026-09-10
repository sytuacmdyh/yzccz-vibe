"""Protocol examples and boundaries for the Rujing V1.3 compressor profile."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'skills/modbus-test/ems_modbus_slave'
sys.path.insert(0, str(ROOT))
from src.ems_modbus_slave.cli import _resolve_profile_path
from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc, parse_request, validate_crc
from src.ems_modbus_slave.register_model import RegisterBank


class RujingCompressorTest(unittest.TestCase):
    def setUp(self):
        self.profile = DeviceProfile.from_json(_resolve_profile_path('rujing_compressor_inverter_v13'))
        self.bank = RegisterBank(self.profile)

    def exchange(self, payload):
        frame = append_crc(bytes.fromhex(payload))
        self.assertIsNotNone(parse_request(frame))
        response = self.bank.handle_request(frame, frame[0])
        self.assertIsNotNone(response)
        self.assertTrue(validate_crc(response))
        return response

    def words(self, response):
        self.assertEqual(response[2], len(response) - 5)
        return [int.from_bytes(response[i:i+2], 'big') for i in range(3, len(response)-2, 2)]

    def test_pdf_write_examples_and_manual_feedback(self):
        self.bank.set_direct(2102, 42, 1)  # document 2103: running frequency
        self.bank.set_direct(2099, 0x280, 1)
        self.bank.set_direct(2100, 0x8001, 1)
        for target, crc in [('003c', 'a051'), ('0000', 'f06d')]:
            payload = f'01 10 07cf 0007 0e {target} 0003 0000 0000 0000 01f4 01f4'
            self.assertEqual(append_crc(bytes.fromhex(payload))[-2:], bytes.fromhex(crc))
            self.assertEqual(self.exchange(payload), append_crc(bytes.fromhex('01 10 07cf 0007')))
            self.assertEqual(self.words(self.exchange('01 03 07cf 0007')),
                             [int(target, 16), 3, 0, 0, 0, 500, 500])
        self.assertEqual(self.bank.get(2102, 1), 42)
        self.assertEqual(self.bank.get(2099, 1), 0x280)
        self.assertEqual(self.bank.get(2100, 1), 0x8001)

    def test_pdf_read_and_raw_units(self):
        values = {2099: 0x201, 2100: 0x8001, 2101: 0x100, 2102: 60,
                  2106: 123, 2107: 234, 2108: 700, 2109: 0x400, 2110: 35}
        for addr, value in values.items():
            self.bank.set_direct(addr, value, 1)
        self.assertEqual(append_crc(bytes.fromhex('01 03 0833 0016'))[-2:], bytes.fromhex('36 6b'))
        response = self.exchange('01 03 0833 0016')
        self.assertEqual(len(response), 49)
        expected = [self.bank.get(addr, 1) for addr in range(2099, 2121)]
        self.assertEqual(self.words(response), expected)
        self.assertEqual(expected[7:10], [123, 234, 700])
        self.assertEqual(expected[11], 35)  # -20 C, no signed-wire conversion
        self.exchange('01 06 07d1 fc22')  # -990 in signed uint16 container
        self.assertEqual(self.words(self.exchange('01 03 07d1 0001')), [0xFC22])

    def test_single_write_and_node_isolation(self):
        for sid in (1, 2):
            for register in self.profile.registers:
                self.bank.set_direct(register.address, sid, sid)
        for register in self.profile.registers:
            self.assertEqual(self.bank.get(register.address, 1), 1)
            self.assertEqual(self.bank.get(register.address, 2), 2)
        payload = '01 06 07cf 003c'
        self.assertEqual(self.exchange(payload), append_crc(bytes.fromhex(payload)))
        self.exchange('02 10 07cf 0002 04 0050 0003')
        self.assertEqual(self.words(self.exchange('01 03 07cf 0002')), [60, 1])
        self.assertEqual(self.words(self.exchange('02 03 07cf 0002')), [80, 3])
        self.bank.reset_defaults()
        self.assertEqual(self.bank.get(1999, 1), 0)
        self.assertEqual(self.bank.get(1999, 2), 0)
        self.assertEqual(self.bank.get(2103, 2), 120)

    def test_read_limit_and_zero_filled_gaps(self):
        response = self.exchange('01 03 0833 0032')
        self.assertEqual(len(self.words(response)), 50)
        self.assertEqual(self.words(response)[29], 0)  # omitted FCT point
        for count in ('0000', '0033', '007e'):
            self.assertEqual(self.exchange(f'01 03 0833 {count}')[1:3], b'\x83\x03')
        legacy = DeviceProfile.from_json(ROOT / 'profiles/compressor_inverter_v24.json')
        frame = append_crc(bytes.fromhex('01 03 6005 007d'))
        self.assertEqual(len(self.words(RegisterBank(legacy).handle_request(frame, 1))), 125)

    def test_write_protection_and_atomicity(self):
        self.bank.set_direct(2005, 77, 1)
        for payload, exception in [
            ('01 06 0833 0001', b'\x86\x02'),  # telemetry
            ('01 06 07d6 0001', b'\x86\x02'),  # omitted fan
            ('01 10 0833 0002 04 0001 0002', b'\x90\x02'),
            ('01 10 07d5 0002 04 0001 0002', b'\x90\x02'),  # valid then unmapped
            ('01 10 07d5 0002 02 0001', b'\x90\x03'),
        ]:
            self.assertEqual(self.exchange(payload)[1:3], exception)
            self.assertEqual(self.bank.get(2005, 1), 77)
        self.assertEqual(self.bank.get(2099, 1), 0)

    def test_profile_metadata_and_export(self):
        p = self.profile
        self.assertEqual((p.baudrate, p.serial_bytesize, p.serial_parity, p.serial_stopbits),
                         (4800, 8, 'N', 1))
        self.assertEqual(p.function_codes, [3, 6, 16])
        addresses = set(range(1999, 2006)) | set(range(2099, 2128)) | {2139}
        self.assertEqual(set(p.by_address), addresses)
        self.assertEqual(p.per_slave_addresses, addresses)
        self.assertEqual(p.max_read_registers, 50)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.json'
            path.write_text(json.dumps(p.to_dict()))
            self.assertEqual(DeviceProfile.from_json(path).max_read_registers, 50)
            data = p.to_dict()
            for invalid in (0, 126, 1.5, '50', True, None):
                data['max_read_registers'] = invalid
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    DeviceProfile.from_json(path)
        # Exercise serialization of profiles constructed without an original JSON document.
        p._raw_data = None
        self.assertEqual(p.to_dict()['max_read_registers'], 50)


if __name__ == '__main__':
    unittest.main()
