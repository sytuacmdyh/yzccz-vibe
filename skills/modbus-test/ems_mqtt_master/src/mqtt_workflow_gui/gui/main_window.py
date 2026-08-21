"""MQTT workflow 下发测试工具（PySide6 GUI）：主窗口、连接、发送与监控。"""
from __future__ import annotations

import math
from datetime import datetime
import json
from pathlib import Path
import sys

from PySide6.QtCore import QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    ConfigError,
    app_root,
    load_config,
    load_last_params,
    load_methods_schema,
    load_presets,
    merge_and_save,
    save_last_params,
)
from ..mqtt_worker import MqttSession, SignalBus, describe_code
from ..time_fields import NO_ACK_METHODS, TIME_SYNC_METHODS, refresh_time_fields
from .envelope_sync import EnvelopeSyncMixin
from .param_form import ParamFormMixin
from .preset_dialog import PresetMixin
from .workflow_preview import WorkflowPreviewPanel


class MainWindow(QMainWindow, EnvelopeSyncMixin, ParamFormMixin, PresetMixin):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EMS Workflow MQTT 下发工具")
        self.resize(1280, 780)

        self.cfg = load_config()
        self.schema = load_methods_schema()
        self.presets = load_presets()
        self.bus = SignalBus()
        self.session: MqttSession | None = None
        self._pending_acks: dict[str, tuple[str, QTimer]] = {}  # str(id) -> (method, timer)
        self._syncing = False  # 表单 <-> 信封 双向同步防递归
        self._param_inputs: dict[str, QWidget] = {}
        self._param_kinds: dict[str, str] = {}  # 字段 key -> 控件类型
        self._param_specs: dict[str, dict[str, object]] = {}  # 字段 key -> 完整 spec
        self._prop_rows: list[dict[str, object]] = []  # set/get_properties 属性行
        self._init_workflow_editor_state()

        self._build_ui()
        self._load_cfg_to_ui()
        self._connect_signals()
        self.workflow_preview.schedule_refresh(self.envelope_edit.toPlainText())

    # ── UI 构建 ────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        self.menu_bar = self.menuBar()
        file_menu = self.menu_bar.addMenu("文件")
        self.connection_settings_action = file_menu.addAction("连接设置...")
        file_menu.addSeparator()
        self.open_payload_action = file_menu.addAction("打开 Workflow JSON...")
        self.save_payload_action = file_menu.addAction("保存 Workflow JSON 为...")

        left_column = QWidget()
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._build_status_group())
        left_layout.addWidget(self._build_template_group(), 3)
        self.workflow_preview = WorkflowPreviewPanel()
        left_layout.addWidget(self.workflow_preview, 2)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(left_column)
        main_splitter.addWidget(self._build_payload_group())
        main_splitter.addWidget(self._build_monitor_group())
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 5)
        main_splitter.setStretchFactor(2, 4)
        main_splitter.setSizes([220, 490, 500])
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setCollapsible(0, True)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(main_splitter)

    def _build_status_group(self) -> QGroupBox:
        box = QGroupBox("连接")
        layout = QVBoxLayout(box)
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color: #999; font-weight: bold;")
        layout.addWidget(self.status_label)
        self.toggle_button = QPushButton("连接")
        self.toggle_button.setToolTip("点击连接；已连接时点击断开")
        layout.addWidget(self.toggle_button)
        self.settings_button = QPushButton("连接设置...")
        layout.addWidget(self.settings_button)
        return box

    def _build_template_group(self) -> QGroupBox:
        box = QGroupBox("预置消息")
        layout = QVBoxLayout(box)

        self.preset_list = QListWidget()
        self.preset_list.setSelectionMode(QListWidget.SingleSelection)
        self.preset_list.setWordWrap(True)
        self.preset_list.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.preset_list, 1)

        self.add_preset_button = QPushButton("添加当前消息为预设")
        self.import_preset_button = QPushButton("导入预设文件")
        row = QHBoxLayout()
        row.addWidget(self.add_preset_button)
        row.addWidget(self.import_preset_button)
        layout.addLayout(row)

        return box

    def _build_payload_group(self) -> QGroupBox:
        box = QGroupBox("下发消息")
        layout = QVBoxLayout(box)

        self.tabs = QTabWidget()
        params_tab = QWidget()
        params_tab_layout = QVBoxLayout(params_tab)
        params_tab_layout.setContentsMargins(0, 0, 0, 0)

        self.params_scroll = QScrollArea()
        self.params_scroll.setWidgetResizable(True)
        self.params_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.params_scroll.setFrameShape(QScrollArea.NoFrame)

        params_scroll_content = QWidget()
        params_scroll_content.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self._params_scroll_content = params_scroll_content
        params_form = QFormLayout(params_scroll_content)
        params_form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.method_combo = QComboBox()
        self.method_combo.addItems(self.schema["methods"])
        self.id_spin = QSpinBox()
        self.id_spin.setRange(1, 2**31 - 1)
        self.params_container = QWidget()
        self.params_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.params_layout = QFormLayout(self.params_container)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_layout.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.params_hint = QLabel()
        self.params_hint.setWordWrap(True)
        self.params_hint.setStyleSheet("color: #888; font-size: 11px;")
        self.params_layout.addRow(self.params_hint)
        self._param_inputs: dict[str, QWidget] = {}
        params_form.addRow("method", self.method_combo)
        params_form.addRow("id", self.id_spin)
        params_form.addRow("params", self.params_container)
        self.params_scroll.setWidget(params_scroll_content)
        params_tab_layout.addWidget(self.params_scroll)

        envelope_tab = QWidget()
        envelope_layout = QVBoxLayout(envelope_tab)
        self.envelope_edit = QPlainTextEdit()
        self.envelope_edit.setPlaceholderText("完整 JSON-RPC 信封，原样发送")
        envelope_layout.addWidget(self.envelope_edit)

        self.format_button = QPushButton("格式化 JSON")
        self.format_button.setToolTip("将信封 JSON 重新缩进排版（无效 JSON 时提示）")
        self.envelope_length_label = QLabel("JSON 长度: 0 字节")
        self.envelope_length_label.setToolTip("原始 JSON 内容的 UTF-8 字节数")
        self.envelope_length_label.setStyleSheet("color: #666; font-size: 11px;")
        format_row = QHBoxLayout()
        format_row.addWidget(self.format_button)
        format_row.addStretch()
        format_row.addWidget(self.envelope_length_label)
        envelope_layout.addLayout(format_row)

        self.tabs.addTab(params_tab, "参数模式")
        self.tabs.addTab(envelope_tab, "原始JSON")
        layout.addWidget(self.tabs, 1)

        self.qos_combo = QComboBox()
        self.qos_combo.addItems(["0", "1", "2"])
        self.qos_combo.setCurrentText("1")
        qos_row = QHBoxLayout()
        qos_row.addWidget(QLabel("QoS"))
        qos_row.addWidget(self.qos_combo)
        qos_row.addStretch()
        layout.addLayout(qos_row)

        self.send_button = QPushButton("发送 (下发到 down/...)")
        layout.addWidget(self.send_button)

        self.result_label = QLabel("最近结果: -")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.result_label)

        return box

    def _build_monitor_group(self) -> QGroupBox:
        box = QGroupBox("消息 / 日志")
        layout = QVBoxLayout(box)

        self.message_view = QListWidget()
        self.message_view.setTextElideMode(Qt.ElideRight)
        self.message_view.setWordWrap(True)
        self.message_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        hint = QLabel("点击消息展开/收起详情（接收=左绿，发送=右蓝）")
        hint.setStyleSheet("color: #888; font-size: 11px;")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(int(self.cfg["log_max_blocks"]))

        splitter = QSplitter(Qt.Horizontal)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(hint)
        right_layout.addWidget(self.message_view, 1)
        splitter.addWidget(right_panel)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([360, 260])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        self.clear_messages_button = QPushButton("清空消息")
        self.clear_log_button = QPushButton("清空日志")
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.clear_messages_button)
        row.addWidget(self.clear_log_button)
        layout.addLayout(row)
        return box

    # ── 信号连接 ───────────────────────────────────────────────────
    def _connect_signals(self) -> None:
        self.toggle_button.clicked.connect(self.toggle_connection)
        self.settings_button.clicked.connect(self.open_connection_settings)
        self.connection_settings_action.triggered.connect(self.open_connection_settings)
        self.send_button.clicked.connect(self.send_message)
        self.preset_list.itemClicked.connect(self.fill_template)
        self.preset_list.customContextMenuRequested.connect(self._preset_context_menu)
        self.add_preset_button.clicked.connect(self.add_preset)
        self.import_preset_button.clicked.connect(self.import_presets)
        self.method_combo.currentTextChanged.connect(self._on_method_changed)
        self.id_spin.valueChanged.connect(self._sync_envelope_from_form)
        self.envelope_edit.textChanged.connect(self._sync_from_envelope)
        self.envelope_edit.textChanged.connect(self._update_envelope_length)
        self.envelope_edit.textChanged.connect(
            lambda: self.workflow_preview.schedule_refresh(
                self.envelope_edit.toPlainText()
            )
        )
        self.format_button.clicked.connect(self._format_envelope)
        self.clear_messages_button.clicked.connect(self.message_view.clear)
        self.message_view.itemClicked.connect(self._toggle_message_detail)
        self.message_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.message_view.customContextMenuRequested.connect(self._message_context_menu)
        self.clear_log_button.clicked.connect(self.log_view.clear)
        self.open_payload_action.triggered.connect(self.open_payload)
        self.save_payload_action.triggered.connect(self.save_payload)

        self.bus.connected.connect(self.on_connected)
        self.bus.disconnected.connect(self.on_disconnected)
        self.bus.message_received.connect(self.on_message_received)
        self.bus.ack_received.connect(self.on_ack_received)
        self.bus.log.connect(self.append_log)

        self._update_envelope_length()

    def _update_envelope_length(self) -> None:
        byte_count = len(self.envelope_edit.toPlainText().encode("utf-8"))
        self.envelope_length_label.setText(f"JSON 长度: {byte_count} 字节")

    # ── 配置 ↔ UI ──────────────────────────────────────────────────
    def _load_cfg_to_ui(self) -> None:
        cfg = self.cfg
        method = cfg["method"]
        if method in self.schema["methods"]:
            self.method_combo.setCurrentText(method)
        self.id_spin.setValue(int(cfg["request_id"]))
        self.qos_combo.setCurrentText(str(cfg["qos"]))
        self._rebuild_param_form()
        self._sync_envelope_from_form()
        last_params = load_last_params()
        if isinstance(last_params, str) and last_params.strip():
            try:
                json.loads(last_params)
            except json.JSONDecodeError:
                pass
            else:
                self.envelope_edit.setPlainText(last_params)
                self._sync_from_envelope()
        self._reload_preset_list()

    def _current_cfg(self) -> dict:
        cfg = dict(self.cfg)
        cfg["qos"] = int(self.qos_combo.currentText())
        cfg["method"] = self.method_combo.currentText()
        cfg["request_id"] = self.id_spin.value()
        return cfg

    def open_connection_settings(self) -> None:
        if self.session is not None and self.session.is_connected:
            QMessageBox.warning(self, "已连接", "请先断开连接再修改连接设置")
            return
        from ..connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(self.cfg, self)
        if dialog.exec() == ConnectionDialog.DialogCode.Accepted:
            self.cfg.update(dialog.current_cfg())
            self.append_log("连接设置已更新")

    # ── 连接管理 ───────────────────────────────────────────────────
    def toggle_connection(self) -> None:
        if self.session is not None and self.session.is_connected:
            self.disconnect_broker()
        else:
            self.connect_broker()

    def connect_broker(self) -> None:
        cfg = self._current_cfg()
        if not cfg["device_id"] or not cfg["username"] or not cfg["password"]:
            QMessageBox.warning(
                self, "配置不完整", "请先通过 文件→连接设置 配置用户名、密码和 device_id"
            )
            return
        if self.session is not None:
            self.disconnect_broker()
        self.session = MqttSession(
            bus=self.bus,
            host=cfg["host"],
            port=cfg["port"],
            path=cfg["path"],
            username=cfg["username"],
            password=cfg["password"],
            product_id=cfg["product_id"],
            device_id=cfg["device_id"],
            subscribe_all=cfg["subscribe_all"],
        )
        self.toggle_button.setText("断开")
        self.status_label.setText("连接中...")
        self.status_label.setStyleSheet("color: #f57c00; font-weight: bold;")
        self.session.start()

    def disconnect_broker(self) -> None:
        if self.session is not None:
            self.session.stop()
            self.session = None
        self._cancel_ack_wait()
        self.status_label.setText("已断开")
        self.status_label.setStyleSheet("color: #999; font-weight: bold;")
        self.toggle_button.setText("连接")

    def on_connected(self, ok: bool, detail: str) -> None:
        if ok:
            self.status_label.setText("已连接")
            self.status_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
            self.toggle_button.setText("断开")
        else:
            self.status_label.setText(f"连接失败: {detail}")
            self.status_label.setStyleSheet("color: #c62828; font-weight: bold;")
            self.toggle_button.setText("连接")

    def on_disconnected(self, reason: str) -> None:
        self._cancel_ack_wait()
        self.status_label.setText(f"已断开 ({reason})")
        self.status_label.setStyleSheet("color: #999; font-weight: bold;")
        self.toggle_button.setText("连接")

    # ── 发送与 ack ─────────────────────────────────────────────────
    def _build_envelope(self) -> bytes | None:
        if self.tabs.currentIndex() == 1:
            text = self.envelope_edit.toPlainText().strip()
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                QMessageBox.warning(self, "JSON 错误", f"信封不是合法 JSON:\n{exc}")
                return None
            return text.encode("utf-8")
        envelope = {
            "id": self.id_spin.value(),
            "method": self.method_combo.currentText(),
            "params": self._params_from_form(),
        }
        # 优先使用已同步的信封（含表单无法表达的字段，如 workflow 嵌套 trigger/action）
        text = self.envelope_edit.toPlainText().strip()
        if text:
            try:
                existing = json.loads(text)
            except json.JSONDecodeError:
                existing = None
            if isinstance(existing, dict) and existing.get("method") == envelope["method"]:
                envelope = existing
        return json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    def send_message(self) -> None:
        if self.session is None or not self.session.is_connected:
            QMessageBox.warning(self, "未连接", "请先连接 MQTT")
            return
        payload = self._build_envelope()
        if payload is None:
            return
        parsed: object = None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            pass
        req_id: object = self.id_spin.value()
        method = self.method_combo.currentText()
        if isinstance(parsed, dict):
            if isinstance(parsed.get("id"), (int, str)):
                req_id = parsed["id"]
            if isinstance(parsed.get("method"), str):
                method = parsed["method"]
            # 时间同步类方法：发送前自动刷新过期时间戳，并回写信封显示
            if method in TIME_SYNC_METHODS and isinstance(parsed.get("params"), dict):
                fresh_params = refresh_time_fields(method, parsed["params"])
                if fresh_params is not parsed["params"]:
                    fresh = dict(parsed)
                    fresh["params"] = fresh_params
                    payload = json.dumps(fresh, ensure_ascii=False).encode("utf-8")
                    self.envelope_edit.setPlainText(
                        json.dumps(fresh, ensure_ascii=False, indent=2)
                    )
        cfg = self._current_cfg()
        down_topic = f"down/{cfg['product_id']}/{cfg['device_id']}"
        if not self.session.publish(down_topic, payload, cfg["qos"]):
            self._cancel_ack_wait()
            QMessageBox.warning(self, "发送失败", "publish 失败（连接可能已断开）")
            return
        self.append_message(payload.decode("utf-8", errors="replace"), "sent")
        if method in NO_ACK_METHODS:
            self.result_label.setText(
                f"最近结果: 已发送 ({method} 无 ack，用日志/上行消息确认)"
            )
            self.result_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
            self.append_log(f"发送: {down_topic} ({len(payload)}B, {method} 无 ack 等待)")
            return
        self._track_ack(req_id, method)
        self.result_label.setText(f"最近结果: 已发送，等待 ack (id={req_id}) ...")
        self.result_label.setStyleSheet("color: #f57c00; font-weight: bold;")
        self.append_log(f"发送: {down_topic} ({len(payload)}B)")

    def _track_ack(self, req_id: object, method: str) -> None:
        key = str(req_id)
        self._cancel_ack(key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(int(self.cfg["timeout"]) * 1000)
        timer.timeout.connect(lambda: self._on_ack_timeout(key))
        timer.start()
        self._pending_acks[key] = (method, timer)

    def _cancel_ack(self, key: str) -> None:
        entry = self._pending_acks.pop(key, None)
        if entry is not None:
            entry[1].stop()
            entry[1].deleteLater()

    def _cancel_ack_wait(self) -> None:
        for key in list(self._pending_acks):
            self._cancel_ack(key)

    def _on_ack_timeout(self, key: str) -> None:
        entry = self._pending_acks.pop(key, None)
        if entry is None:
            return
        entry[1].deleteLater()
        method = entry[0]
        self.result_label.setText(f"最近结果: 超时未收到 ack (id={key})")
        self.result_label.setStyleSheet("color: #c62828; font-weight: bold;")
        self.append_log(f"ack 超时: id={key} method={method}")

    def on_ack_received(self, req_id, method: str, code: int) -> None:
        key = str(req_id)
        entry = self._pending_acks.get(key)
        if entry is None or entry[0] != method:
            expected = entry[0] if entry is not None else "无"
            self.append_log(
                f"收到 ack 不匹配: id={req_id} method={method} (期望 method={expected}) code={code}"
            )
            return
        self._cancel_ack(key)
        meaning = describe_code(code)
        ok = code == 0
        color = "#2e7d32" if ok else "#c62828"
        self.result_label.setText(f"最近结果: code={code} ({meaning})")
        self.result_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.append_log(f"ack: id={req_id} method={method} code={code} - {meaning}")

    # ── 接收与日志 ─────────────────────────────────────────────────
    def on_message_received(self, topic: str, text: str) -> None:
        self.append_message(text, "received")

    @staticmethod
    def _message_parts(text: str) -> tuple[str, str]:
        """解析消息，返回 (摘要一行, 详情多行)。"""
        text = text.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            one_line = " ".join(text.split())
            return one_line[:100], text
        parts: list[str] = []
        if isinstance(data, dict):
            if "id" in data:
                parts.append(f"id={data['id']}")
            if "method" in data:
                parts.append(f"method={data['method']}")
            if "code" in data:
                parts.append(f"code={data['code']}")
            params = data.get("params")
            if isinstance(params, dict) and params.get("run_id"):
                parts.append(f"run_id={params['run_id']}")
            if not parts:
                parts.append(f"{len(data)} 字段")
        elif isinstance(data, list):
            parts.append(f"数组[{len(data)}]")
        else:
            parts.append(str(data))
        summary = "  ".join(parts)
        detail = json.dumps(data, ensure_ascii=False, indent=2)
        return summary, detail

    def _set_message_text(self, item: QListWidgetItem, text: str) -> None:
        fm = self.message_view.fontMetrics()
        width = max(self.message_view.viewport().width() - 24, 80)
        chars_per_line = max(int(width / max(fm.averageCharWidth(), 1)), 12)
        lines = sum(max(1, math.ceil(len(part) / chars_per_line)) for part in text.split("\n"))
        item.setText(text)
        item.setSizeHint(QSize(width, fm.lineSpacing() * lines + 10))

    def append_message(self, text: str, kind: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        styles = {
            "received": ("#2e7d32", Qt.AlignLeft),   # 接收: 左对齐 绿色
            "sent": ("#1565c0", Qt.AlignRight),      # 发送: 右对齐 蓝色
            "ack": ("#2e7d32", Qt.AlignLeft),
            "error": ("#c62828", Qt.AlignLeft),
        }
        color, alignment = styles.get(kind, styles["received"])
        summary, detail = self._message_parts(text)
        summary_text = f"[{stamp}] {summary}"
        item = QListWidgetItem(summary_text)
        item.setTextAlignment(alignment)
        item.setForeground(QColor(color))
        item.setData(Qt.UserRole, summary_text)
        item.setData(Qt.UserRole + 1, f"[{stamp}] {detail}")
        item.setData(Qt.UserRole + 2, int(alignment))
        item.setToolTip("点击展开/收起详情")
        self._set_message_text(item, summary_text)
        self.message_view.addItem(item)
        while self.message_view.count() > int(self.cfg["msg_max_blocks"]):
            self.message_view.takeItem(0)
        scrollbar = self.message_view.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum():
            scrollbar.setValue(scrollbar.maximum())
        self.append_log(self._describe_chinese(text, kind))

    def _describe_chinese(self, text: str, kind: str) -> str:
        """把解析后的消息用中文描述，写入日志栏。"""
        direction = "发送" if kind == "sent" else "接收"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            one_line = " ".join(text.split())
            return f"{direction}: 非JSON原始数据 ({len(text)}B) {one_line[:80]}"
        if isinstance(data, dict):
            method = data.get("method")
            name = self.schema["method_cn"].get(str(method), str(method)) if method else "数据"
            parts = [f"{direction}: {name}"]
            req_id = data.get("id")
            if isinstance(req_id, (int, float)):
                parts.append(f"id={req_id}")
            code = data.get("code")
            if isinstance(code, (int, float)):
                meaning = describe_code(int(code))
                parts.append(f"code={code} ({meaning})" if meaning else f"code={code}")
            if "params" in data:
                parts.append(f"params={len(json.dumps(data['params'], ensure_ascii=False))}B")
            return " ".join(parts)
        if isinstance(data, list):
            return f"{direction}: 数组[{len(data)}]"
        return f"{direction}: {str(data)[:80]}"

    def _message_context_menu(self, pos: QPoint) -> None:
        item = self.message_view.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        copy_action = menu.addAction("复制内容")
        chosen = menu.exec(self.message_view.viewport().mapToGlobal(pos))
        if chosen is not copy_action:
            return
        text = self._copy_message_content(item)
        if text is not None:
            QApplication.clipboard().setText(text)
            self.append_log(f"已复制消息 ({len(text)}B)")

    def _copy_message_content(self, item: QListWidgetItem) -> str | None:
        text = item.data(Qt.UserRole + 1) or item.text()
        if text.startswith("["):
            _, _, text = text.partition("] ")
        return text

    def _toggle_message_detail(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        summary = item.data(Qt.UserRole) or ""
        detail = item.data(Qt.UserRole + 1) or ""
        original_alignment = item.data(Qt.UserRole + 2)
        if item.text() == summary:
            # 展开: 详情左对齐
            item.setTextAlignment(Qt.AlignLeft)
            self._set_message_text(item, detail)
        else:
            # 收起: 恢复原始对齐
            if isinstance(original_alignment, int):
                item.setTextAlignment(Qt.AlignmentFlag(original_alignment))
            self._set_message_text(item, summary)

    def append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        text = f"[{stamp}] {message}"
        if not self.log_view.document().isEmpty():
            text = "\n" + text
        cursor.insertText(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── 文件 ───────────────────────────────────────────────────────
    def open_payload(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "打开 Workflow JSON", str(app_root()), "JSON Files (*.json)"
        )
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        if isinstance(data, dict) and "method" in data:
            self.tabs.setCurrentIndex(1)
            self.envelope_edit.setPlainText(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
        else:
            # 非信封 JSON：包一层当前 method，同步进表单/信封
            self.tabs.setCurrentIndex(1)
            wrapped = {
                "id": self.id_spin.value(),
                "method": self.method_combo.currentText(),
                "params": data,
            }
            self.envelope_edit.setPlainText(
                json.dumps(wrapped, ensure_ascii=False, indent=2)
            )
        self.append_log(f"已打开: {filename}")

    def save_payload(self) -> None:
        payload = self._build_envelope()
        if payload is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存 Workflow JSON", str(app_root() / "workflow.json"), "JSON Files (*.json)"
        )
        if not filename:
            return
        try:
            Path(filename).write_text(payload.decode("utf-8"), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.append_log(f"已保存: {filename}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.session is not None:
            self.session.stop()
            self.session = None
        merge_and_save(self._current_cfg())
        save_last_params(self.envelope_edit.toPlainText())
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    try:
        window = MainWindow()
    except ConfigError as exc:
        QMessageBox.critical(None, "配置错误", str(exc))
        return 1
    window.show()
    return app.exec()
