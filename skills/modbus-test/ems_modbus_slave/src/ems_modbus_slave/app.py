from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableView,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QStyle,
)
from serial.tools import list_ports

from .capture import CaptureRecord, CaptureTracker
from .device_profile import DeviceProfile
from .paths import app_icon_path, app_root
from .preset_loader import SimulatorPreset, load_startup_preset
from .profile_repository import discover_profiles
from .register_model import RegisterBank
from .serial_slave import SerialSlaveServer
from .widgets import CaptureToggleDelegate, RegisterTableModel


ROOT = app_root()
PROFILE_DIR = ROOT / "profiles"
BAUDRATE_OPTIONS = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800"]
LOG_MAX_BLOCKS = 1000
MESSAGE_MAX_BLOCKS = 500
CAPTURE_MAX_RECORDS = 500
UI_FLUSH_INTERVAL_MS = 50


def classify_json_file(path: Path) -> str | None:
    """Return ``"preset"``, ``"profile"`` or ``None`` based on JSON content."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "preset_id" in data:
        return "preset"
    registers = data.get("registers")
    if isinstance(registers, list):
        return "profile"
    if "slave_id" in data or "baudrate" in data:
        return "profile"
    return None


def scan_dir_for_imports(dir_path: Path) -> tuple[Path | None, Path | None]:
    """Scan a directory for one preset and one profile JSON file."""
    preset_path: Path | None = None
    profile_path: Path | None = None
    for json_file in sorted(dir_path.glob("*.json")):
        kind = classify_json_file(json_file)
        if kind == "preset" and preset_path is None:
            preset_path = json_file
        elif kind == "profile" and profile_path is None:
            profile_path = json_file
    return preset_path, profile_path


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EMS Modbus Slave - MVP")
        self.resize(1540, 760)

        self.profile_options = discover_profiles(PROFILE_DIR)
        if not self.profile_options:
            raise RuntimeError(f"No device profiles found in {PROFILE_DIR}")
        self.profile_path = self.profile_options[0][1]
        self.profile = DeviceProfile.from_json(self.profile_path)
        self.startup_preset = load_startup_preset(
            ROOT, self.profile_path, self.profile.startup_preset
        )
        self.bank = RegisterBank(self.profile, self.startup_preset)
        self.capture_tracker = CaptureTracker()
        self.table_model = RegisterTableModel(
            self.bank, self.capture_tracker, self.profile.slave_id
        )
        self._event_lock = threading.Lock()
        self._pending_logs: deque[tuple[str, str]] = deque(maxlen=LOG_MAX_BLOCKS)
        self._pending_messages: deque[tuple[str, str, str]] = deque(maxlen=MESSAGE_MAX_BLOCKS)
        self._pending_captures: deque[CaptureRecord] = deque(maxlen=CAPTURE_MAX_RECORDS)
        self._register_dirty = False
        self.profile_select_combo = QComboBox()
        for label, path in self.profile_options:
            self.profile_select_combo.addItem(label, str(path))
        self.server = SerialSlaveServer(
            profile=self.profile,
            bank=self.bank,
            log_fn=self.enqueue_log,
            refresh_fn=self.request_register_refresh,
            message_fn=self.enqueue_message,
            capture_tracker=self.capture_tracker,
            capture_fn=self.enqueue_capture,
        )

        self.port_combo = QComboBox()
        self.refresh_ports()
        self.baudrate_combo = QComboBox()
        self.baudrate_combo.addItems(BAUDRATE_OPTIONS)
        self.baudrate_combo.setCurrentText(str(self.profile.baudrate))
        self.slave_id_spin = QSpinBox()
        self.slave_id_spin.setRange(1, 247)
        self.slave_id_spin.setValue(self.profile.slave_id)
        self.respond_range_check = QCheckBox("Respond ID 1-40")
        self.view_slave_id_spin = QSpinBox()
        self.view_slave_id_spin.setRange(1, 247)
        self.view_slave_id_spin.setValue(self.profile.slave_id)
        self.view_slave_id_spin.setEnabled(False)

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.refresh_button = QPushButton("Refresh COM")
        self.reset_button = QPushButton("Reset Simulator")
        self.reset_button.setToolTip("Restore profile defaults and re-apply the startup preset")

        self.profile_name = QLineEdit(self.profile.name)
        self.profile_name.setReadOnly(True)
        self.profile_desc = QPlainTextEdit(self.profile.description)
        self.profile_desc.setReadOnly(True)
        self.profile_desc.setMinimumHeight(80)

        self.status_labels: list[QLabel] = []
        self.state_fields: list[dict[str, object]] = []
        self.default_state_keys: set[tuple[str, int]] = set()

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(LOG_MAX_BLOCKS)

        self.message_view = QTextEdit()
        self.message_view.setReadOnly(True)
        self.message_view.setAcceptRichText(True)
        self.message_view.document().setMaximumBlockCount(MESSAGE_MAX_BLOCKS)

        self.capture_list = QListWidget()
        self.capture_list.setTextElideMode(Qt.ElideRight)
        self.capture_detail_view = QPlainTextEdit()
        self.capture_detail_view.setReadOnly(True)
        self.capture_detail_view.setPlaceholderText("Select a capture entry to view packet details.")
        self.clear_capture_button = QToolButton()
        self.clear_capture_button.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        self.clear_capture_button.setToolTip("Clear packet capture")


        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索类型、地址、名称、权限、值或描述")
        self.search_previous_button = QToolButton()
        self.search_previous_button.setArrowType(Qt.UpArrow)
        self.search_previous_button.setToolTip("上一个匹配项")
        self.search_next_button = QToolButton()
        self.search_next_button.setArrowType(Qt.DownArrow)
        self.search_next_button.setToolTip("下一个匹配项")
        self.search_matches: list[int] = []
        self.search_match_index = -1
        self._register_refresh_pending = False

        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column in range(self.table_model.columnCount()):
            if column == 5:
                header.setSectionResizeMode(column, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        header.resizeSection(6, 76)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setItemDelegateForColumn(6, CaptureToggleDelegate(self.table))

        self.ui_flush_timer = QTimer(self)
        self.ui_flush_timer.setInterval(UI_FLUSH_INTERVAL_MS)
        self.ui_flush_timer.timeout.connect(self.flush_ui_updates)

        self._build_ui()
        self._connect_signals()
        self.reset_state_fields()
        self.refresh_registers()
        self.ui_flush_timer.start()
        if self.startup_preset is not None:
            self.enqueue_log(
                f"Loaded startup preset: {self.startup_preset.name} ({self.startup_preset.preset_id})"
            )

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        menu_bar = self.menuBar()
        self.file_menu = menu_bar.addMenu("File")
        self.import_profile_action = QAction("Import Register Profile JSON...", self)
        self.import_profile_action.triggered.connect(self.import_profile_json)
        self.file_menu.addAction(self.import_profile_action)
        self.import_preset_action = QAction("Import Simulator Preset JSON...", self)
        self.import_preset_action.triggered.connect(self.import_preset_json)
        self.file_menu.addAction(self.import_preset_action)
        self.file_menu.addSeparator()
        self.save_profile_action = QAction("Save Profile As...", self)
        self.save_profile_action.triggered.connect(self.save_profile_as)
        self.file_menu.addAction(self.save_profile_action)
        self.save_preset_action = QAction("Save Preset As...", self)
        self.save_preset_action.triggered.connect(self.save_preset_as)
        self.file_menu.addAction(self.save_preset_action)
        for title in ("Edit", "Connection", "Setup", "Display", "View", "Window", "Help"):
            menu_bar.addMenu(title)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)

        main_splitter = QSplitter(Qt.Horizontal)
        left_splitter = QSplitter(Qt.Vertical)

        left_column = QSplitter(Qt.Vertical)
        left_column.addWidget(self._build_connection_group())
        left_column.addWidget(self._build_status_group())
        left_column.setStretchFactor(0, 3)
        left_column.setStretchFactor(1, 2)
        left_column.setSizes([220, 160])
        left_column.setChildrenCollapsible(False)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        top_right_splitter = QSplitter(Qt.Horizontal)
        top_right_splitter.addWidget(self._build_profile_group())
        top_right_splitter.addWidget(self._build_log_group())
        top_right_splitter.setStretchFactor(0, 3)
        top_right_splitter.setStretchFactor(1, 4)
        top_right_splitter.setSizes([280, 360])
        top_right_splitter.setChildrenCollapsible(False)

        right_layout.addWidget(top_right_splitter)
        right_layout.addWidget(self._build_message_group(), 1)

        upper_splitter = QSplitter(Qt.Horizontal)
        upper_splitter.addWidget(left_column)
        upper_splitter.addWidget(right_panel)
        upper_splitter.setStretchFactor(0, 2)
        upper_splitter.setStretchFactor(1, 5)
        upper_splitter.setSizes([260, 640])
        upper_splitter.setChildrenCollapsible(False)

        left_splitter.addWidget(upper_splitter)
        left_splitter.addWidget(self._build_register_group())
        left_splitter.setStretchFactor(0, 2)
        left_splitter.setStretchFactor(1, 3)
        left_splitter.setSizes([320, 420])
        left_splitter.setChildrenCollapsible(False)

        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(self._build_capture_group())
        main_splitter.setStretchFactor(0, 7)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setSizes([1080, 420])
        main_splitter.setChildrenCollapsible(False)

        layout.addWidget(main_splitter)

    def _build_connection_group(self) -> QGroupBox:
        box = QGroupBox("Serial")
        form = QFormLayout(box)
        form.addRow("COM Port", self.port_combo)
        form.addRow("Baudrate", self.baudrate_combo)
        form.addRow("Slave ID", self.slave_id_spin)
        form.addRow("Scan Mode", self.respond_range_check)
        form.addRow("View Slave ID", self.view_slave_id_spin)
        form.addRow("Simulated Slave", self.profile_select_combo)

        buttons = QHBoxLayout()
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        form.addRow(buttons)
        form.addRow("Device State", self.reset_button)
        return box

    def _build_profile_group(self) -> QGroupBox:
        box = QGroupBox("Device Profile")
        layout = QVBoxLayout(box)
        profile_label = QLabel("Profile:")
        layout.addWidget(profile_label)
        layout.addWidget(self.profile_name)
        description_label = QLabel("Description:")
        layout.addWidget(description_label)
        layout.addWidget(self.profile_desc, 1)
        return box

    def _build_status_group(self) -> QGroupBox:
        box = QGroupBox("State")
        self.status_layout = QFormLayout(box)
        return box

    def _build_log_group(self) -> QGroupBox:
        box = QGroupBox("Log")
        layout = QVBoxLayout(box)
        layout.addWidget(self.log_view)
        return box

    def _build_message_group(self) -> QGroupBox:
        box = QGroupBox("Message")
        layout = QVBoxLayout(box)
        layout.addWidget(self.message_view)
        return box

    def _build_capture_group(self) -> QGroupBox:
        box = QGroupBox("Packet Capture")
        layout = QVBoxLayout(box)
        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(self.clear_capture_button)
        layout.addLayout(header)

        capture_splitter = QSplitter(Qt.Vertical)
        capture_splitter.addWidget(self.capture_list)
        capture_splitter.addWidget(self.capture_detail_view)
        capture_splitter.setStretchFactor(0, 1)
        capture_splitter.setStretchFactor(1, 2)
        capture_splitter.setSizes([180, 360])
        capture_splitter.setChildrenCollapsible(False)
        layout.addWidget(capture_splitter, 1)
        return box

    def _build_register_group(self) -> QGroupBox:
        box = QGroupBox("Registers")
        layout = QVBoxLayout(box)
        hint = QLabel("Value can be edited locally; Modbus access remains unchanged.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        search_layout = QHBoxLayout()
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_previous_button)
        search_layout.addWidget(self.search_next_button)
        layout.addLayout(search_layout)
        layout.addWidget(self.table)
        return box

    def _connect_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.reset_button.clicked.connect(self.reset_simulator)
        self.clear_capture_button.clicked.connect(self.clear_capture)
        self.capture_list.currentItemChanged.connect(self.show_capture_details)
        self.profile_select_combo.currentIndexChanged.connect(self.on_profile_changed)
        self.slave_id_spin.valueChanged.connect(self.on_slave_id_changed)
        self.respond_range_check.toggled.connect(self.on_respond_range_changed)
        self.view_slave_id_spin.valueChanged.connect(self.on_view_slave_id_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_register_context_menu)
        self.search_input.textChanged.connect(lambda _text: self.update_search_matches())
        self.search_previous_button.clicked.connect(lambda: self.select_search_match(-1))
        self.search_next_button.clicked.connect(lambda: self.select_search_match(1))
        self.table.itemDelegate().closeEditor.connect(self.on_register_editor_closed)
        self.update_search_matches()

    def refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        ports = [port.device for port in list_ports.comports()]
        self.port_combo.addItems(ports)
        if current:
            index = self.port_combo.findText(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    def on_slave_id_changed(self, slave_id: int) -> None:
        if not self.respond_range_check.isChecked():
            self.view_slave_id_spin.setValue(slave_id)

    def on_respond_range_changed(self, enabled: bool) -> None:
        self.view_slave_id_spin.setEnabled(enabled)
        if enabled:
            self.view_slave_id_spin.setRange(1, 40)
            self.view_slave_id_spin.setValue(
                min(max(self.slave_id_spin.value(), 1), 40)
            )
        else:
            self.view_slave_id_spin.setRange(1, 247)
            self.view_slave_id_spin.setValue(self.slave_id_spin.value())

    def on_view_slave_id_changed(self, slave_id: int) -> None:
        self.table_model.set_view_slave_id(slave_id)
        self.refresh_registers()

    def start_server(self) -> None:
        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.warning(self, "No COM Port", "Please select a COM port.")
            return
        self.server.configure(
            port=port,
            baudrate=int(self.baudrate_combo.currentText()),
            slave_id=self.slave_id_spin.value(),
            respond_id_min=1 if self.respond_range_check.isChecked() else None,
            respond_id_max=40 if self.respond_range_check.isChecked() else None,
        )
        self.server.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.profile_select_combo.setEnabled(False)
        self.respond_range_check.setEnabled(False)
        self.import_profile_action.setEnabled(False)
        self.import_preset_action.setEnabled(False)
        self.save_profile_action.setEnabled(False)
        self.save_preset_action.setEnabled(False)

    def stop_server(self) -> None:
        self.server.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.profile_select_combo.setEnabled(True)
        self.respond_range_check.setEnabled(True)
        self.import_profile_action.setEnabled(True)
        self.import_preset_action.setEnabled(True)
        self.save_profile_action.setEnabled(True)
        self.save_preset_action.setEnabled(True)

    def import_profile_json(self) -> None:
        if self.server.running:
            QMessageBox.warning(self, "Serial Running", "Stop the serial slave before importing a profile.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import Register Profile JSON",
            str(PROFILE_DIR),
            "JSON Files (*.json)",
        )
        if filename:
            self.import_profile_path(Path(filename))

    def import_profile_path(self, path: Path) -> bool:
        try:
            profile = DeviceProfile.from_json(path)
            load_startup_preset(ROOT, path, profile.startup_preset)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(self, "Invalid Profile", f"Unable to import profile:\n{exc}")
            return False
        path_text = str(path)
        index = self.profile_select_combo.findData(path_text)
        if index < 0:
            self.profile_select_combo.addItem(f"{profile.name} (Imported)", path_text)
            index = self.profile_select_combo.count() - 1
        if index == self.profile_select_combo.currentIndex():
            self.on_profile_changed()
        else:
            self.profile_select_combo.setCurrentIndex(index)
        return True

    def import_preset_json(self) -> None:
        if self.server.running:
            QMessageBox.warning(self, "Serial Running", "Stop the serial slave before importing a preset.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import Simulator Preset JSON",
            str(ROOT / "presets"),
            "JSON Files (*.json)",
        )
        if filename:
            self.import_preset_path(Path(filename))

    def import_preset_path(self, path: Path) -> bool:
        try:
            preset = SimulatorPreset.from_json(path)
            if preset.profile_id != self.profile.profile_id:
                raise ValueError(
                    f"Preset profile_id={preset.profile_id!r} does not match "
                    f"the current profile_id={self.profile.profile_id!r}"
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(self, "Invalid Preset", f"Unable to import preset:\n{exc}")
            return False
        self.startup_preset = preset
        self.bank.apply_preset(preset)
        self.capture_tracker.set_enabled_points(preset.capture_points, self.profile)
        self.table_model.refresh_tracking()
        if preset.state_fields:
            self.reset_state_fields(preset.state_fields)
        else:
            self.reset_state_fields()
        self.refresh_registers()
        self.enqueue_log(f"Imported preset: {preset.name} ({preset.preset_id})")
        return True

    def _find_profile_path_by_id(self, profile_id: str) -> Path | None:
        for _label, path_text in self.profile_options:
            candidate = Path(path_text)
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(data.get("profile_id", data.get("name"))) == profile_id:
                return candidate
        return None

    def apply_startup_imports(
        self,
        preset_path: Path | None = None,
        profile_path: Path | None = None,
    ) -> bool:
        """Import profile then preset on startup (non-interactive friendly)."""
        if profile_path is not None:
            if not self.import_profile_path(Path(profile_path)):
                return False
        if preset_path is not None:
            preset_path = Path(preset_path)
            try:
                preset = SimulatorPreset.from_json(preset_path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.enqueue_log(f"Startup preset import failed: {exc}")
                return False
            if preset.profile_id != self.profile.profile_id:
                found = self._find_profile_path_by_id(preset.profile_id)
                if found is not None and found != self.profile_path:
                    if not self.import_profile_path(found):
                        return False
            return self.import_preset_path(preset_path)
        return True

    def save_profile_as(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Profile As",
            str(PROFILE_DIR / f"{self.profile.profile_id}.json"),
            "JSON Files (*.json)",
        )
        if not filename:
            return
        try:
            Path(filename).write_text(
                json.dumps(self.profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Save Profile Failed", str(exc))
            return
        self.enqueue_log(f"Saved profile: {filename}")

    def save_preset_as(self) -> None:
        default_name = f"{self.profile.profile_id}_preset.json"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Preset As",
            str(ROOT / "presets" / default_name),
            "JSON Files (*.json)",
        )
        if not filename:
            return
        registers, coils = self.bank.current_values(self.view_slave_id_spin.value())
        preset = SimulatorPreset(
            preset_id=Path(filename).stem,
            name=(
                f"{self.startup_preset.name} Snapshot"
                if self.startup_preset is not None
                else f"{self.profile.name} Snapshot"
            ),
            profile_id=self.profile.profile_id,
            description="Runtime snapshot exported by EMS Modbus Slave.",
            registers=registers,
            coils=coils,
            ems_web_registers=(
                list(self.startup_preset.ems_web_registers)
                if self.startup_preset is not None
                else []
            ),
            capture_points=self.capture_tracker.enabled_points(),
            state_fields=[dict(field) for field in self.state_fields],
        )
        try:
            Path(filename).write_text(
                json.dumps(preset.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Save Preset Failed", str(exc))
            return
        self.enqueue_log(f"Saved preset: {filename}")

    def reset_simulator(self) -> None:
        self.bank.reset_defaults()
        self.refresh_registers()
        if self.startup_preset is not None:
            self.enqueue_log(
                f"Simulator reset with preset: {self.startup_preset.name}"
            )
        else:
            self.enqueue_log(f"Simulator reset to profile defaults: {self.profile.name}")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def enqueue_log(self, message: str) -> None:
        with self._event_lock:
            self._pending_logs.append((self._timestamp(), message))

    def enqueue_message(self, message: str, kind: str) -> None:
        with self._event_lock:
            self._pending_messages.append((self._timestamp(), message, kind))

    def enqueue_capture(self, record: CaptureRecord) -> None:
        with self._event_lock:
            self._pending_captures.append(record)

    def request_register_refresh(self) -> None:
        with self._event_lock:
            self._register_dirty = True

    def pending_event_counts(self) -> tuple[int, int]:
        with self._event_lock:
            return len(self._pending_logs), len(self._pending_messages)

    def flush_ui_updates(self) -> None:
        with self._event_lock:
            logs = list(self._pending_logs)
            messages = list(self._pending_messages)
            captures = list(self._pending_captures)
            registers_dirty = self._register_dirty
            self._pending_logs.clear()
            self._pending_messages.clear()
            self._pending_captures.clear()
            self._register_dirty = False
        if logs:
            self._append_log_entries(logs)
        if messages:
            self._append_message_entries(messages)
        if captures:
            self._append_capture_records(captures)
        if registers_dirty:
            self.refresh_registers()

    def append_log(self, message: str) -> None:
        self._append_log_entries([(self._timestamp(), message)])

    def _append_log_entries(self, entries: list[tuple[str, str]]) -> None:
        scrollbar = self.log_view.verticalScrollBar()
        follow_latest = scrollbar.value() >= scrollbar.maximum() - 2
        previous_position = scrollbar.value()
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        text = "\n".join(f"[{stamp}] {message}" for stamp, message in entries)
        if not self.log_view.document().isEmpty():
            text = "\n" + text
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()
        if follow_latest:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(previous_position)

    def append_message(self, message: str, kind: str) -> None:
        self._append_message_entries([(self._timestamp(), message, kind)])

    def clear_capture(self) -> None:
        with self._event_lock:
            self._pending_captures.clear()
        self.capture_list.clear()
        self.capture_detail_view.clear()

    @staticmethod
    def _capture_summary(record: CaptureRecord) -> str:
        points = ", ".join(record.points)
        result = record.response_message.splitlines()[0]
        return (
            f"[{record.timestamp}] FC{record.function_code:02X} "
            f"@{record.address_summary()} | {points}: {result}"
        )

    @staticmethod
    def _format_capture_detail(record: CaptureRecord) -> str:
        point_lines = "\n".join(
            f"  - {kind} {address}: {label}"
            for (kind, address), label in zip(record.addresses, record.points, strict=False)
        )
        return "\n".join(
            [
                f"Profile: {record.profile_name}",
                f"Function: FC{record.function_code:02X}",
                f"Address: {record.address_summary()}",
                "Points:",
                point_lines or "  -",
                "",
                "Request:",
                record.request_message,
                "",
                "Response:",
                record.response_message,
                "",
                "RX:",
                record.request.hex(" "),
                "",
                "TX:",
                record.response.hex(" "),
            ]
        )

    def show_capture_details(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self.capture_detail_view.clear()
            return
        record = current.data(Qt.UserRole)
        if isinstance(record, CaptureRecord):
            self.capture_detail_view.setPlainText(self._format_capture_detail(record))

    def _append_capture_records(self, records: list[CaptureRecord]) -> None:
        follow_latest = (
            self.capture_list.currentRow() == self.capture_list.count() - 1
            or self.capture_list.currentRow() < 0
        )
        for record in records:
            item = QListWidgetItem(self._capture_summary(record))
            item.setData(Qt.UserRole, record)
            item.setToolTip(self._format_capture_detail(record))
            self.capture_list.addItem(item)
        while self.capture_list.count() > CAPTURE_MAX_RECORDS:
            self.capture_list.takeItem(0)
        if follow_latest and self.capture_list.count() > 0:
            self.capture_list.setCurrentRow(self.capture_list.count() - 1)

    def _append_message_entries(self, entries: list[tuple[str, str, str]]) -> None:
        scrollbar = self.message_view.verticalScrollBar()
        follow_latest = scrollbar.value() >= scrollbar.maximum() - 2
        previous_position = scrollbar.value()
        styles = {
            "received": ("#1565c0", Qt.AlignLeft),
            "sent": ("#2e7d32", Qt.AlignRight),
            "error": ("#c62828", Qt.AlignHCenter),
        }
        cursor = self.message_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        for stamp, message, kind in entries:
            color, alignment = styles.get(kind, styles["received"])
            if not self.message_view.document().isEmpty():
                cursor.insertBlock()
            block_format = cursor.blockFormat()
            block_format.setAlignment(alignment)
            cursor.setBlockFormat(block_format)
            text_format = QTextCharFormat()
            text_format.setForeground(QColor(color))
            cursor.insertText(f"[{stamp}] {message}", text_format)
        cursor.endEditBlock()
        if follow_latest:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(previous_position)

    def update_search_matches(self, reset_navigation: bool = True) -> None:
        query = self.search_input.text().strip().casefold()
        selected_row = None
        if not reset_navigation and 0 <= self.search_match_index < len(self.search_matches):
            selected_row = self.search_matches[self.search_match_index]
        self.search_matches = []
        if query:
            for row in range(self.table_model.rowCount()):
                values = [str(self.table_model.data(self.table_model.index(row, column))) for column in range(self.table_model.columnCount())]
                if query in " ".join(values).casefold():
                    self.search_matches.append(row)
        if reset_navigation:
            self.search_match_index = -1
        elif selected_row in self.search_matches:
            self.search_match_index = self.search_matches.index(selected_row)
        else:
            self.search_match_index = -1
        enabled = bool(self.search_matches)
        self.search_previous_button.setEnabled(enabled)
        self.search_next_button.setEnabled(enabled)

    def select_search_match(self, direction: int) -> None:
        if not self.search_matches:
            return
        self.search_match_index = (self.search_match_index + direction) % len(self.search_matches)
        row = self.search_matches[self.search_match_index]
        index = self.table_model.index(row, 0)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index, QAbstractItemView.PositionAtCenter)

    def show_register_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        kind, address, name = self.table_model.point_at(index.row())
        key = (kind, address)
        state_keys = {(str(field["kind"]), int(field["address"])) for field in self.state_fields}
        menu = QMenu(self)
        if key in state_keys:
            action = menu.addAction("Remove from State")
            action.setEnabled(key not in self.default_state_keys)
            action.triggered.connect(lambda: self.remove_state_field(kind, address))
        else:
            action = menu.addAction(f"Add {name} to State")
            action.triggered.connect(lambda: self.add_state_field(kind, address, name))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def add_state_field(self, kind: str, address: int, name: str) -> None:
        if any(str(field["kind"]) == kind and int(field["address"]) == address for field in self.state_fields):
            return
        self.state_fields.append({"label": name, "kind": kind, "address": address})
        self.rebuild_state_fields()
        self._refresh_status_fields()

    def remove_state_field(self, kind: str, address: int) -> None:
        if (kind, address) in self.default_state_keys:
            return
        self.state_fields = [
            field
            for field in self.state_fields
            if (str(field["kind"]), int(field["address"])) != (kind, address)
        ]
        self.rebuild_state_fields()

    def jump_to_state_point(self, kind: str, address: int) -> None:
        row = self.table_model.row_for(kind, address)
        index = self.table_model.index(row, 0)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index, QAbstractItemView.PositionAtCenter)

    def refresh_registers(self) -> None:
        if self.table.state() == QAbstractItemView.EditingState:
            self._register_refresh_pending = True
            return
        self._register_refresh_pending = False
        changed_rows = self.table_model.reload()
        if changed_rows and self.search_input.text().strip():
            self.update_search_matches(reset_navigation=False)
        self._refresh_status_fields()

    def _refresh_status_fields(self) -> None:
        for index, field in enumerate(self.state_fields):
            if index >= len(self.status_labels):
                continue
            kind = str(field.get("kind", "register"))
            address = int(field.get("address", 0))
            scale = float(field.get("scale", 1))
            suffix = str(field.get("suffix", ""))
            try:
                if kind == "coil":
                    definition = self.profile.coil_by_address[address]
                    value: object = definition.display_value(self.bank.get_coil(address))
                else:
                    raw = self.bank.get(address, self.view_slave_id_spin.value())
                    definition = self.profile.by_address[address]
                    if definition.enum:
                        value = definition.display_value(raw)
                    elif scale not in (0, 1):
                        value = f"{raw / scale:.1f}"
                    else:
                        value = definition.display_value(raw)
                self.status_labels[index].setText(f"{value}{suffix}")
            except KeyError:
                self.status_labels[index].setText("N/A")

    def on_register_editor_closed(self, *_args) -> None:
        if self._register_refresh_pending:
            QTimer.singleShot(0, self.refresh_registers)
        else:
            if self.search_input.text().strip():
                self.update_search_matches(reset_navigation=False)
            QTimer.singleShot(0, self._refresh_status_fields)

    def on_profile_changed(self) -> None:
        if self.server.running:
            return
        selected_path = self.profile_select_combo.currentData()
        if not selected_path:
            return
        self.profile = DeviceProfile.from_json(Path(selected_path))
        self.profile_path = Path(selected_path)
        self.startup_preset = load_startup_preset(
            ROOT, self.profile_path, self.profile.startup_preset
        )
        self.bank = RegisterBank(self.profile, self.startup_preset)
        self.capture_tracker.retain_available(self.profile)
        self.table_model = RegisterTableModel(
            self.bank, self.capture_tracker, self.profile.slave_id
        )
        self.table.setModel(self.table_model)
        self.table.setItemDelegateForColumn(6, CaptureToggleDelegate(self.table))
        self.profile_name.setText(self.profile.name)
        self.profile_desc.setPlainText(self.profile.description)
        self.baudrate_combo.setCurrentText(str(self.profile.baudrate))
        self.slave_id_spin.setValue(self.profile.slave_id)
        self.on_respond_range_changed(self.respond_range_check.isChecked())
        self.server = SerialSlaveServer(
            profile=self.profile,
            bank=self.bank,
            log_fn=self.enqueue_log,
            refresh_fn=self.request_register_refresh,
            message_fn=self.enqueue_message,
            capture_tracker=self.capture_tracker,
            capture_fn=self.enqueue_capture,
        )
        self.reset_state_fields()
        self.refresh_registers()
        self.update_search_matches()
        if self.startup_preset is not None:
            self.enqueue_log(
                f"Loaded startup preset: {self.startup_preset.name} ({self.startup_preset.preset_id})"
            )

    def reset_state_fields(self, configured_fields: list[dict[str, object]] | None = None) -> None:
        fields = configured_fields if configured_fields is not None else self.profile.status_fields
        if not fields:
            fields = [
                {"label": register.name, "kind": "register", "address": register.address}
                for register in self.profile.registers[:4]
            ] + [
                {"label": coil.name, "kind": "coil", "address": coil.address}
                for coil in self.profile.coils[:2]
            ]
        self.state_fields = [dict(field) for field in fields]
        default_fields = self.profile.status_fields or fields
        self.default_state_keys = {
            (str(field["kind"]), int(field["address"])) for field in default_fields
        }
        self.rebuild_state_fields()

    def rebuild_state_fields(self) -> None:
        while self.status_layout.rowCount():
            self.status_layout.removeRow(0)
        self.status_labels = []
        for field in self.state_fields:
            label = QLabel("0")
            self.status_labels.append(label)
            jump_button = QToolButton()
            jump_button.setArrowType(Qt.RightArrow)
            jump_button.setToolTip("Jump to register")
            jump_button.clicked.connect(
                lambda _checked=False, kind=str(field["kind"]), address=int(field["address"]): self.jump_to_state_point(kind, address)
            )
            value_layout = QHBoxLayout()
            value_layout.setContentsMargins(0, 0, 0, 0)
            value_layout.addWidget(label)
            value_layout.addStretch()
            value_layout.addWidget(jump_button)
            value_widget = QWidget()
            value_widget.setLayout(value_layout)
            self.status_layout.addRow(str(field.get("label", "Value")), value_widget)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.server.stop()
        super().closeEvent(event)


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="EMS Modbus Slave GUI. Optionally auto-import preset/profile on startup.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Preset/Profile JSON file or a directory containing them; auto-imported on startup",
    )
    parser.add_argument("--preset", help="Preset JSON path to import on startup")
    parser.add_argument("--profile", help="Profile JSON path to import on startup")
    parser.add_argument("--port", help="COM port to select")
    parser.add_argument("--slave-id", type=int, help="Slave ID (1-247)")
    parser.add_argument(
        "--respond-range",
        action="store_true",
        help="Enable Respond ID 1-40 scan mode",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Auto-start the serial slave after setup",
    )
    return parser.parse_args(argv)


def resolve_import_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    preset_path = Path(args.preset) if args.preset else None
    profile_path = Path(args.profile) if args.profile else None
    if args.path:
        target = Path(args.path)
        if target.is_dir():
            sp, sprof = scan_dir_for_imports(target)
            if preset_path is None:
                preset_path = sp
            if profile_path is None:
                profile_path = sprof
        elif target.is_file():
            kind = classify_json_file(target)
            if kind == "preset" and preset_path is None:
                preset_path = target
            elif kind == "profile" and profile_path is None:
                profile_path = target
            elif kind is None:
                print(
                    f"WARNING: cannot classify {target} as preset or profile",
                    file=sys.stderr,
                )
        elif not target.exists():
            print(f"WARNING: path does not exist: {target}", file=sys.stderr)
    return preset_path, profile_path


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_cli_args(argv)
    preset_path, profile_path = resolve_import_paths(args)

    app = QApplication(sys.argv)
    icon_path = app_icon_path()
    if icon_path is not None:
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    window = MainWindow()
    if icon_path is not None:
        window.setWindowIcon(icon)
    window.show()

    def apply_startup() -> None:
        if preset_path is not None or profile_path is not None:
            window.apply_startup_imports(preset_path, profile_path)
        if args.port:
            index = window.port_combo.findText(args.port)
            if index >= 0:
                window.port_combo.setCurrentIndex(index)
            else:
                window.enqueue_log(f"COM port not found: {args.port}")
        if args.slave_id:
            window.slave_id_spin.setValue(args.slave_id)
        if args.respond_range:
            window.respond_range_check.setChecked(True)
        if args.start:
            window.start_server()

    QTimer.singleShot(0, apply_startup)
    return app.exec()
