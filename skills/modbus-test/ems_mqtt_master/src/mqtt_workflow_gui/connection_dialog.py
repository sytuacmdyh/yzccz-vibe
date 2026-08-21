"""连接设置弹窗：编辑并保存 MQTT 连接参数。"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .config import merge_and_save


class ConnectionDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("连接设置")
        self.setModal(True)
        self.setMinimumWidth(360)
        self._cfg = dict(cfg)

        form = QFormLayout()
        self.host_edit = QLineEdit(str(cfg["host"]))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(cfg["port"]))
        self.path_edit = QLineEdit(str(cfg["path"]))
        self.user_edit = QLineEdit(str(cfg["username"]))
        self.password_edit = QLineEdit(str(cfg["password"]))
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.product_edit = QLineEdit(str(cfg["product_id"]))
        self.device_edit = QLineEdit(str(cfg["device_id"]))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(int(cfg["timeout"]))
        self.timeout_spin.setSuffix(" s")
        self.subscribe_all_check = QCheckBox("同时订阅 up/+/+ 观察其他设备")
        self.subscribe_all_check.setChecked(bool(cfg["subscribe_all"]))

        form.addRow("Host", self.host_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("WebSocket Path", self.path_edit)
        form.addRow("用户名", self.user_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("product_id", self.product_edit)
        form.addRow("device_id", self.device_edit)
        form.addRow("等待 ack 超时", self.timeout_spin)
        form.addRow(self.subscribe_all_check)

        self.save_button = QPushButton("保存为默认配置")
        self.save_button.setToolTip("写入 config.json")
        self.ok_button = QPushButton("确定")
        self.cancel_button = QPushButton("取消")
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addStretch()
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

        self.save_button.clicked.connect(self.save_defaults)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

    def current_cfg(self) -> dict[str, Any]:
        return {
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
            "path": self.path_edit.text().strip(),
            "username": self.user_edit.text(),
            "password": self.password_edit.text(),
            "product_id": self.product_edit.text().strip(),
            "device_id": self.device_edit.text().strip(),
            "timeout": self.timeout_spin.value(),
            "subscribe_all": self.subscribe_all_check.isChecked(),
        }

    def save_defaults(self) -> None:
        # 合并保存：只更新连接字段，不覆盖 custom_presets / method / qos 等
        merge_and_save(self.current_cfg())
        self.save_button.setText("已保存")