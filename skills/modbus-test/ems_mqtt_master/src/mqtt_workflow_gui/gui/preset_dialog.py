"""预置消息：PresetEditDialog 弹窗 + 预设 CRUD（Mixin，供 MainWindow 组合使用）。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import app_root, save_presets


class PresetEditDialog(QDialog):
    """编辑预设消息弹窗：预设名称 + JSON-RPC 信封内容。"""

    def __init__(self, name: str, payload: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑预设消息")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("预设名称:"))
        self.name_edit = QLineEdit(name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("消息内容 (JSON-RPC 信封):"))
        self.payload_edit = QPlainTextEdit()
        self.payload_edit.setPlainText(payload)
        layout.addWidget(self.payload_edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "名称不能为空", "请输入预设名称")
            return
        try:
            json.loads(self.payload_edit.toPlainText())
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 错误", f"消息内容不是合法 JSON:\n{exc}")
            return
        self.accept()

    def result(self) -> tuple[str, str]:  # type: ignore[override]
        return self.name_edit.text().strip(), self.payload_edit.toPlainText().strip()


class PresetMixin:
    """预置消息 CRUD 与导入。

    依赖宿主（MainWindow）提供的实例状态：cfg、preset_list、envelope_edit、tabs、
    append_log，以及 EnvelopeSyncMixin 的 _sync_from_envelope。
    """

    def _presets(self) -> dict[str, str]:
        """从 presets.json 读取。"""
        return dict(self.presets)

    def _reload_preset_list(self) -> None:
        self.preset_list.clear()
        for name in self._presets():
            item = QListWidgetItem(name)
            item.setToolTip("单击填入信封；右键编辑/删除")
            self.preset_list.addItem(item)

    def _preset_context_menu(self, pos) -> None:
        item = self.preset_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        edit_action = menu.addAction("编辑预设...")
        delete_action = menu.addAction("删除预设")
        chosen = menu.exec(self.preset_list.viewport().mapToGlobal(pos))
        if chosen is edit_action:
            self._edit_preset(item)
        elif chosen is delete_action:
            self._delete_preset(item)

    def _edit_preset(self, item: QListWidgetItem) -> None:
        name = item.text()
        presets = self._presets()
        if name not in presets:
            return
        dialog = PresetEditDialog(name, presets[name], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name, payload = dialog.result()
        if not new_name:
            return
        custom = dict(self.presets)
        if new_name != name:
            custom.pop(name, None)
        custom[new_name] = payload
        self.presets = custom
        save_presets(self.presets)
        self._reload_preset_list()
        self.append_log(f"已编辑预设: {name} → {new_name}")

    def _delete_preset(self, item: QListWidgetItem) -> None:
        name = item.text()
        if (
            QMessageBox.question(self, "删除预设", f"确认删除预设「{name}」?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        custom = dict(self.presets)
        custom.pop(name, None)
        self.presets = custom
        save_presets(self.presets)
        self._reload_preset_list()
        self.append_log(f"已删除预设: {name}")

    def fill_template(self, item: QListWidgetItem | None = None) -> None:
        if item is None or not isinstance(item, QListWidgetItem):
            item = self.preset_list.currentItem()
        if item is None:
            return
        name = item.text()
        presets = self._presets()
        if name not in presets:
            return
        payload = presets[name]
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False, indent=2)
        self.envelope_edit.blockSignals(True)
        try:
            self.envelope_edit.setPlainText(payload)
        finally:
            self.envelope_edit.blockSignals(False)
        self._sync_from_envelope()
        if hasattr(self, "_update_envelope_length"):
            self._update_envelope_length()
        if hasattr(self, "workflow_preview"):
            self.workflow_preview.schedule_refresh(payload)
        self.append_log(f"已填入预置消息: {name}")

    def add_preset(self) -> None:
        text = self.envelope_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "无内容", "请先在原始JSON中填写消息再添加预设")
            return
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 错误", f"信封不是合法 JSON:\n{exc}")
            return
        name, ok = QInputDialog.getText(self, "添加预设", "预设名称:")
        if not ok or not name.strip():
            return
        custom = dict(self.presets)
        custom[name.strip()] = text
        self.presets = custom
        save_presets(self.presets)
        self._reload_preset_list()
        self.append_log(f"已添加预设: {name.strip()}")

    def import_presets(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入预设", str(app_root()), "JSON Files (*.json)"
        )
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if isinstance(data, dict):
            entries = data
        elif isinstance(data, list):
            entries = {}
            for i, entry in enumerate(data):
                if isinstance(entry, dict) and "name" in entry:
                    name = str(entry["name"])
                    payload = entry.get("payload") or entry.get("message") or entry.get("json")
                    if payload is not None:
                        entries[name] = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                else:
                    entries[f"预设 {i + 1}"] = entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
        else:
            QMessageBox.warning(self, "格式错误", "预设文件应为 {名称: 信封JSON} 或 [{name,payload}...]")
            return
        added = 0
        custom = dict(self.presets)
        for name, payload in entries.items():
            if not isinstance(payload, str):
                continue
            try:
                json.loads(payload)
            except json.JSONDecodeError:
                continue
            if name in custom:
                choice = self._ask_preset_conflict(name)
                if choice == "skip":
                    continue
                if choice == "rename":
                    new_name, ok = QInputDialog.getText(self, "重命名导入预设", "新名称:", text=f"{name} (导入)")
                    if not ok or not new_name.strip():
                        continue
                    name = new_name.strip()
            custom[name] = payload
            added += 1
        if added == 0:
            QMessageBox.warning(self, "导入失败", "文件中没有可用的合法预设")
            return
        self.presets = custom
        save_presets(self.presets)
        self._reload_preset_list()
        self.append_log(f"已导入 {added} 条预设: {filename}")

    @staticmethod
    def _ask_preset_conflict(name: str) -> str:
        """导入同名预设时询问：跳过 / 覆盖 / 重命名。"""
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("预设名称冲突")
        box.setText(f"预设「{name}」已存在，如何处理?")
        skip_button = box.addButton("跳过", QMessageBox.ButtonRole.RejectRole)
        overwrite_button = box.addButton("覆盖", QMessageBox.ButtonRole.AcceptRole)
        rename_button = box.addButton("重命名", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_button:
            return "overwrite"
        if clicked is rename_button:
            return "rename"
        return "skip"
