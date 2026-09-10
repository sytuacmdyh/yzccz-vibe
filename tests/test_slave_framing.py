"""Exercise the real slave receive loop with USB-style split/coalesced reads."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/modbus-test/ems_modbus_slave'))
from src.ems_modbus_slave.serial_slave import SerialSlaveServer
from src.ems_modbus_slave.modbus_rtu import append_crc, parse_request, extract_requests


class SlaveFramingTest(unittest.TestCase):
    def receive(self, chunks):
        profile = SimpleNamespace(serial_bytesize=8, serial_parity='N', serial_stopbits=2,
                                  baudrate=9600, slave_id=1)
        server = SerialSlaveServer(profile, None, lambda _: None, lambda: None, lambda *args: None)
        received, clock = [], [0.0]
        server._handle_frame = lambda frame: received.append(frame) if parse_request(frame) else None
        pending = iter(chunks)

        class Port:
            in_waiting = 256
            def __init__(self, **kwargs):
                pass
            def read(self, size):
                try:
                    elapsed, chunk = next(pending)
                    clock[0] += elapsed
                    return chunk
                except StopIteration:
                    clock[0] += .01
                    server._stop_event.set()
                    return b''
            def write(self, data):
                return len(data)

        with patch('src.ems_modbus_slave.serial_slave.serial.Serial', Port), \
             patch('src.ems_modbus_slave.serial_slave.time.monotonic', lambda: clock[0]):
            server._run()
        return received

    def test_complete_requests_do_not_need_an_empty_read(self):
        frames = [append_crc(bytes([sid, 3, 0x60, 5, 0, 18])) for sid in (1, 2, 1)]
        self.assertEqual(self.receive([(.2, frame) for frame in frames]), frames)

    def test_captured_truncated_prefix_and_coalesced_requests_resynchronize(self):
        frames = [append_crc(bytes([sid, 3, 0x60, 5, 0, 18])) for sid in (1, 2, 1)]
        self.assertEqual(self.receive([(.2, bytes.fromhex('00 12 cb f5') + b''.join(frames))]), frames)

    def test_fragmented_request_survives_host_delivery_gap(self):
        frame = append_crc(bytes.fromhex('01 06 80 00 04 01'))
        self.assertEqual(self.receive([(.01, frame[:3]), (.02, frame[3:])]), [frame])

    def test_usb_gap_must_not_discard_a_partial_supported_request(self):
        frame = append_crc(bytes.fromhex("01 03 60 05 00 12"))
        self.assertEqual(self.receive([(.01, frame[:6]), (.005, b""), (.003, frame[6:])]), [frame])

    def test_bad_crc_does_not_hide_the_next_valid_request(self):
        frame = append_crc(bytes.fromhex('02 06 80 01 02 58'))
        corrupt = frame[:-1] + bytes([frame[-1] ^ 1])
        self.assertEqual(self.receive([(.01, corrupt + frame)]), [frame])

    def test_variable_length_and_unknown_function_fallback(self):
        frame = append_crc(bytes.fromhex('01 10 00 01 00 02 04 00 05 00 06'))
        self.assertEqual(self.receive([(.01, frame[:8]), (.01, frame[8:])]), [frame])
        unknown = append_crc(bytes.fromhex('01 45 00 00'))
        self.assertEqual(self.receive([(.01, unknown)]), [unknown])

    def test_coils_input_reads_and_back_to_back_writes(self):
        payloads = ('01 01 00 00 00 08', '02 04 d0 10 00 05', '01 05 00 00 ff 00',
                    '01 0f 00 00 00 08 01 aa')
        frames = [append_crc(bytes.fromhex(payload)) for payload in payloads]
        self.assertEqual(self.receive([(.01, b''.join(frames))]), frames)

    def test_partial_write_with_incidental_crc_is_not_dispatched(self):
        prefix = append_crc(bytes.fromhex("01 10 00 00 00 03 06"))
        frame = append_crc(prefix + bytes.fromhex("00 01 00 02"))
        self.assertEqual(self.receive([(.01, prefix), (.005, b""), (.003, frame[len(prefix):])]), [frame])

    def test_noise_is_bounded_and_complete_frame_recovers(self):
        buffer = bytearray(b'\xff' * 4096)
        self.assertEqual(extract_requests(buffer), [])
        self.assertLessEqual(len(buffer), 256)
        frame = append_crc(bytes.fromhex('01 03 60 05 00 12'))
        buffer.extend(frame[:4])
        self.assertEqual(extract_requests(buffer), [])
        buffer.extend(frame[4:])
        self.assertEqual(extract_requests(buffer), [frame])
        self.assertEqual(buffer, bytearray())


if __name__ == '__main__':
    unittest.main()
