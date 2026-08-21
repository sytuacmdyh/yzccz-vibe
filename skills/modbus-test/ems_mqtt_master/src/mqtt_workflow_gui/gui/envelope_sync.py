"""表单 ⇄ 信封双向同步（Mixin，供 MainWindow 组合使用）。"""
from __future__ import annotations

import json

from PySide6.QtWidgets import QMessageBox


class EnvelopeSyncMixin:
    """表单 <-> 信封双向同步逻辑。

    依赖宿主（MainWindow）提供的实例状态：_syncing、method_combo、id_spin、
    envelope_edit、schema，以及 ParamFormMixin 的 _rebuild_param_form / _apply_json_to_form。
    """

    # ── 信号连接 ───────────────────────────────────────────────────
    def _on_method_changed(self, _method: str) -> None:
        if self._syncing:
            return
        self._rebuild_param_form()
        self._sync_envelope_from_form()

    def _sync_envelope_from_form(self, *_args) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            envelope = {
                "id": self.id_spin.value(),
                "method": self.method_combo.currentText(),
                "params": self._params_from_form(),
            }
            envelope["params"] = self._merge_existing_params(envelope["params"])
            self.envelope_edit.setPlainText(json.dumps(envelope, ensure_ascii=False))
        finally:
            self._syncing = False

    def _merge_existing_params(self, new_params: object) -> object:
        """合并旧信封中表单无法表达的字段，避免切换参数模式时丢字段。"""
        text = self.envelope_edit.toPlainText().strip()
        if not text:
            return new_params
        try:
            existing = json.loads(text)
        except json.JSONDecodeError:
            return new_params
        if not isinstance(existing, dict) or existing.get("method") != self.method_combo.currentText():
            return new_params
        ex_params = existing.get("params")
        if isinstance(ex_params, dict) and isinstance(new_params, dict):
            merged = dict(ex_params)
            merged.update(new_params)
            if (
                self.method_combo.currentText() == "sync_workflow_config"
                and merged.get("op") == "delete"
            ):
                return {
                    "schema_version": int(merged.get("schema_version", 2)),
                    "op": "delete",
                    "flow_id": merged.get("flow_id"),
                    "revision": merged.get("revision"),
                }
            return merged
        if (
            isinstance(ex_params, list)
            and isinstance(new_params, list)
            and len(ex_params) == len(new_params)
        ):
            merged_list: list[object] = []
            for ex_item, new_item in zip(ex_params, new_params):
                if isinstance(ex_item, dict) and isinstance(new_item, dict):
                    merged_item = dict(ex_item)
                    merged_item.update(new_item)
                    merged_list.append(merged_item)
                else:
                    merged_list.append(new_item)
            return merged_list
        return new_params

    def _format_envelope(self) -> None:
        """将信封 JSON 重新缩进排版（2 空格缩进）；无效 JSON 时提示且不改动原文。"""
        text = self.envelope_edit.toPlainText().strip()
        if not text:
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 错误", f"信封不是合法 JSON:\n{exc}")
            return
        self.envelope_edit.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))

    def _sync_from_envelope(self) -> None:
        if self._syncing:
            return
        text = self.envelope_edit.toPlainText().strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        self._syncing = True
        try:
            method = data.get("method")
            methods = self.schema["methods"]
            method_matched = isinstance(method, str) and method in methods
            if method_matched:
                prev_method = self.method_combo.currentText()
                self._set_combo_value(self.method_combo, method)
                if prev_method != method:
                    self._rebuild_param_form()
            req_id = data.get("id")
            if isinstance(req_id, int):
                self.id_spin.blockSignals(True)
                try:
                    self.id_spin.setValue(req_id)
                finally:
                    self.id_spin.blockSignals(False)
            if method_matched:
                self._apply_json_to_form(data.get("params"))
        finally:
            self._syncing = False
            if hasattr(self, "_wf_refresh_layout"):
                self._wf_refresh_layout()
