from __future__ import annotations

import os
from pathlib import Path
import sys


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.ems_modbus_slave.app import MainWindow
from src.ems_modbus_slave.capture import CaptureRecord
from src.ems_modbus_slave.device_profile import DeviceProfile
from src.ems_modbus_slave.modbus_rtu import append_crc
from src.ems_modbus_slave.register_model import RegisterBank
from src.ems_modbus_slave.serial_slave import SerialSlaveServer
from src.ems_modbus_slave.widgets import RegisterTableModel


PROFILE_PATH = ROOT / "profiles" / "dm_hpwt18_u1.json"


def test_serial_refresh_policy() -> None:
    profile = DeviceProfile.from_json(PROFILE_PATH)
    bank = RegisterBank(profile)
    refresh_count = 0

    def request_refresh() -> None:
        nonlocal refresh_count
        refresh_count += 1

    server = SerialSlaveServer(profile, bank, lambda _text: None, request_refresh, lambda _text, _kind: None)
    read_registers = append_crc(bytes([1, 3, 0, 4, 0, 1]))
    read_coils = append_crc(bytes([1, 1, 0x19, 0, 0, 1]))
    for _ in range(100):
        server._handle_frame(read_registers)
        server._handle_frame(read_coils)
    assert refresh_count == 0, "FC01/FC03 polling must not refresh the register table"

    invalid_write = append_crc(bytes([1, 6, 0xFF, 0xFF, 0, 1]))
    server._handle_frame(invalid_write)
    assert refresh_count == 0, "failed writes must not refresh the register table"

    write_frame = append_crc(bytes([1, 6, 0, 4, 0, 170]))
    server._handle_frame(write_frame)
    assert refresh_count == 1, "a successful write must request one register refresh"


def test_incremental_model_refresh() -> None:
    profile = DeviceProfile.from_json(PROFILE_PATH)
    bank = RegisterBank(profile)
    model = RegisterTableModel(bank)
    reset_count = 0
    changed_count = 0

    def count_reset() -> None:
        nonlocal reset_count
        reset_count += 1

    def count_change(*_args) -> None:
        nonlocal changed_count
        changed_count += 1

    model.modelReset.connect(count_reset)
    model.dataChanged.connect(count_change)
    bank.set_direct(4, 170)
    changed_rows = model.reload()
    assert reset_count == 0, "value changes must not reset the complete table model"
    assert changed_count == 1
    assert changed_rows == [model.row_for("register", 4)]


def test_readonly_protocol_points_are_editable_in_simulator() -> None:
    profile = DeviceProfile.from_json(PROFILE_PATH)
    bank = RegisterBank(profile)
    model = RegisterTableModel(bank)
    value_index = model.index(model.row_for("register", 334), 4)

    assert "r" == model.data(model.index(value_index.row(), 3))
    assert model.flags(value_index) & Qt.ItemIsEditable
    assert model.setData(value_index, 280)
    assert bank.get(334) == 280


def test_capture_controls_and_panel(app: QApplication) -> None:
    window = MainWindow()
    capture_index = window.table_model.index(window.table_model.row_for("register", 334), 6)
    assert window.table_model.data(capture_index) == "OFF"
    assert window.table_model.toggle_tracking(capture_index)
    assert window.table_model.data(capture_index) == "ON"

    record = CaptureRecord(
        timestamp="12:00:00",
        profile_name=window.profile.name,
        function_code=3,
        points=("生活水箱温度1 (word 334)",),
        request=bytes([1, 3, 1, 0x4E, 0, 1]),
        response=bytes([1, 3, 2, 1, 24]),
        request_message="读取保持寄存器 word 334",
        response_message="已返回 1 个保持寄存器",
        is_error=False,
    )
    window._append_capture_records([record] * 501)
    assert window.capture_list.count() == 500
    window.capture_list.setCurrentRow(0)
    app.processEvents()
    detail_text = window.capture_detail_view.toPlainText()
    assert "Request:" in detail_text
    assert "RX:" in detail_text
    window.clear_capture()
    assert window.capture_list.count() == 0
    assert window.capture_detail_view.toPlainText() == ""
    window.close()


def test_state_fields_and_navigation(app: QApplication) -> None:
    window = MainWindow()
    default_count = len(window.profile.status_fields)
    assert window.status_layout.rowCount() == default_count

    window.bank.set_direct(4, 170)
    window._refresh_status_fields()
    assert window.status_labels[0].text() == window.profile.by_address[4].display_value(170)

    window.add_state_field("register", 334, "生活水箱温度1")
    assert window.status_layout.rowCount() == default_count + 1
    window.jump_to_state_point("register", 334)
    assert window.table.currentIndex().row() == window.table_model.row_for("register", 334)
    window.remove_state_field("register", 334)
    assert window.status_layout.rowCount() == default_count
    window.close()


def test_profile_import(app: QApplication) -> None:
    window = MainWindow()
    assert window.import_profile_action.isEnabled()
    assert window.import_profile_path(PROFILE_PATH)
    assert window.profile_path == PROFILE_PATH
    window.close()


def test_ui_event_batching(app: QApplication) -> None:
    window = MainWindow()
    window.bank.set_direct(4, 170)
    window.reset_button.click()
    assert window.bank.get(4) == window.profile.by_address[4].default
    window.flush_ui_updates()

    initial_blocks = window.log_view.document().blockCount()
    for index in range(200):
        window.enqueue_log(f"RX {index}")
        window.enqueue_message(f"读取 {index}", "received")

    assert window.log_view.document().blockCount() == initial_blocks
    assert window.pending_event_counts() == (200, 200)
    window.flush_ui_updates()
    app.processEvents()
    assert window.log_view.document().blockCount() >= 200
    assert window.message_view.document().blockCount() >= 200
    assert window.pending_event_counts() == (0, 0)
    window.close()


def test_register_refresh_does_not_navigate_search(app: QApplication) -> None:
    window = MainWindow()
    window.search_input.setText("word")
    window.select_search_match(1)
    selected_row = window.table.currentIndex().row()
    selected_match = window.search_match_index

    window.bank.set_direct(4, 170)
    window.refresh_registers()

    assert window.search_match_index == selected_match
    assert window.table.currentIndex().row() == selected_row
    window.close()


def main() -> int:
    app = QApplication([])
    test_serial_refresh_policy()
    test_incremental_model_refresh()
    test_readonly_protocol_points_are_editable_in_simulator()
    test_capture_controls_and_panel(app)
    test_state_fields_and_navigation(app)
    test_profile_import(app)
    test_ui_event_batching(app)
    test_register_refresh_does_not_navigate_search(app)
    print("UI performance smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
