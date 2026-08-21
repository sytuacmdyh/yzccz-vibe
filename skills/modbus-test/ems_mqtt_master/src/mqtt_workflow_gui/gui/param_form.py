"""参数表单（Mixin，供 MainWindow 组合使用）：字段构建、属性行、工作流编辑器。"""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ..time_fields import (
    COMMON_TIMEZONES,
    DEFAULT_OFFSET_SECONDS,
    DEFAULT_TIMEZONE_ID,
    TIME_SYNC_METHODS,
    default_timezone_label,
    local_time_str,
    now_unix,
    time_params,
    weather_params,
)

from .workflow_editor import WorkflowEditorMixin


class ParamFormMixin(WorkflowEditorMixin):
    """参数模式表单逻辑。

    依赖宿主（MainWindow）提供的实例状态：params_layout、params_hint、
    method_combo、schema、_param_inputs、_param_kinds、_prop_rows，以及
    EnvelopeSyncMixin 的 _sync_envelope_from_form。
    """

    def _method_cn(self, method: str) -> str:
        return str(self.schema["method_cn"].get(method, method))

    def _row_methods(self) -> list[str]:
        return list(self.schema["row_methods"])

    def _param_fields(self, method: str) -> list[dict[str, object]]:
        fields = self.schema["param_fields"].get(method, [])
        return list(fields) if isinstance(fields, list) else []

    def _prop_row_defaults(self) -> dict[str, object]:
        defaults = self.schema.get("prop_row_defaults", {})
        return dict(defaults) if isinstance(defaults, dict) else {}

    def _rebuild_param_form(self) -> None:
        while self.params_layout.count():
            item = self.params_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._param_inputs = {}
        self._param_kinds = {}
        self._param_specs: dict[str, dict[str, object]] = {}
        self._param_row_widgets: dict[str, QWidget] = {}
        self._prop_rows.clear()
        self._clear_workflow_editor()
        method = self.method_combo.currentText()

        if method in self._row_methods():
            hint = QLabel(
                f"「{self._method_cn(method)}」参数：每行一组属性，可添加多组；修改后自动同步到信封"
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888; font-size: 11px;")
            self.params_hint = hint
            self.params_layout.addRow(hint)
            self._add_prop_row()
            add_button = QPushButton("+ 添加属性")
            add_button.clicked.connect(self._add_prop_row)
            self.params_layout.addRow(add_button)
            return

        if method == "time":
            self._build_time_sync_ui()
            return
        if method == "sync_weather":
            self._build_weather_sync_ui()
            return

        fields = self._param_fields(method)
        if not fields:
            hint = QLabel(f"「{self._method_cn(method)}」无参数，直接发送 method 即可")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888; font-size: 11px;")
            self.params_hint = hint
            self.params_layout.addRow(hint)
            return
        hints = self.schema.get("method_hints", {})
        if isinstance(hints, dict) and method in hints:
            hint_text = str(hints[method])
        else:
            hint_text = f"「{self._method_cn(method)}」参数：修改后自动同步到信封"
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        self.params_hint = hint
        self.params_layout.addRow(hint)
        for spec in fields:
            key = str(spec["key"])
            label = str(spec["label"])
            kind = str(spec["kind"])
            if kind in ("str", "json"):
                widget: QWidget = QLineEdit(str(spec["default"]))
                if kind == "json":
                    widget.setToolTip("JSON 字面量：0 / 255 / true / \"字符串\"")
                widget.textChanged.connect(self._sync_envelope_from_form)
            elif kind == "select":
                combo = QComboBox()
                options = [str(o) for o in spec["options"]]
                combo.addItems(options)
                default = str(spec["default"])
                if default in options:
                    combo.setCurrentText(default)
                combo.currentTextChanged.connect(self._sync_envelope_from_form)
                widget = combo
            elif kind == "float":
                spin = QDoubleSpinBox()
                spin.setDecimals(3)
                spin.setRange(float(spec["min"]), float(spec["max"]))
                spin.setValue(float(spec["default"]))
                spin.valueChanged.connect(self._sync_envelope_from_form)
                widget = spin
            else:
                spin = QSpinBox()
                spin.setRange(int(spec["min"]), int(spec["max"]))
                spin.setValue(int(spec["default"]))
                spin.valueChanged.connect(self._sync_envelope_from_form)
                widget = spin
            self._param_inputs[key] = widget
            self._param_kinds[key] = kind
            self._param_specs[key] = spec
            row_wrap = QWidget()
            row_form = QFormLayout(row_wrap)
            row_form.setContentsMargins(0, 0, 0, 0)
            row_form.addRow(label, widget)
            self.params_layout.addRow(row_wrap)
            self._param_row_widgets[key] = row_wrap

        if method == "sync_workflow_config":
            self._build_workflow_editor_ui()
            self._connect_workflow_op_listener()
            self._update_workflow_scalar_visibility()

    # ── 时间同步 / 天气同步表单 ─────────────────────────────────────
    def _build_timezone_widgets(self) -> None:
        """常用时区下拉 + 可编辑偏移（秒）+ timezone_id + 夏令时开关。"""
        combo = QComboBox()
        combo.addItems([label for label, _, _ in COMMON_TIMEZONES])
        combo.setCurrentText(default_timezone_label())
        combo.currentIndexChanged.connect(self._on_timezone_preset_changed)
        self._timezone_combo = combo

        offset_spin = QSpinBox()
        offset_spin.setRange(-14 * 3600, 14 * 3600)
        offset_spin.setValue(DEFAULT_OFFSET_SECONDS)
        offset_spin.setSuffix(" 秒")
        offset_spin.valueChanged.connect(self._refresh_time_display)
        offset_spin.valueChanged.connect(self._sync_envelope_from_form)

        tz_id_edit = QLineEdit(DEFAULT_TIMEZONE_ID)
        tz_id_edit.textChanged.connect(self._sync_envelope_from_form)

        dst_combo = QComboBox()
        dst_combo.addItems(["false", "true"])
        dst_combo.setCurrentText("false")
        dst_combo.currentTextChanged.connect(self._sync_envelope_from_form)

        self._param_inputs["timezone_offset_seconds"] = offset_spin
        self._param_kinds["timezone_offset_seconds"] = "int"
        self._param_inputs["timezone_id"] = tz_id_edit
        self._param_kinds["timezone_id"] = "str"
        self._param_inputs["isDaylightSavingTime"] = dst_combo
        self._param_kinds["isDaylightSavingTime"] = "select"

        row = QWidget()
        row_form = QFormLayout(row)
        row_form.setContentsMargins(0, 0, 0, 0)
        row_form.addRow("常用时区", combo)
        row_form.addRow("时区偏移", offset_spin)
        row_form.addRow("timezone_id (IANA)", tz_id_edit)
        row_form.addRow("isDaylightSavingTime", dst_combo)
        self.params_layout.addRow(row)
        self._param_row_widgets["timezone"] = row

    def _build_time_sync_ui(self) -> None:
        method = "time"
        hints = self.schema.get("method_hints", {})
        hint_text = (
            str(hints[method])
            if isinstance(hints, dict) and method in hints
            else f"「{self._method_cn(method)}」参数"
        )
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        self.params_hint = hint
        self.params_layout.addRow(hint)

        self._build_timezone_widgets()

        ts_edit = QLineEdit(str(now_unix()))
        ts_edit.setReadOnly(True)
        ts_edit.setToolTip("Unix 秒。点「同步当前时间」刷新；发送前落后会自动更新")
        self._param_inputs["timestamp"] = ts_edit
        self._param_kinds["timestamp"] = "str"

        time_str_edit = QLineEdit()
        time_str_edit.setReadOnly(True)
        self._param_inputs["timeStr"] = time_str_edit
        self._param_kinds["timeStr"] = "str"

        sync_button = QPushButton("同步当前时间")
        sync_button.clicked.connect(self._refresh_now_fields)
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(sync_button)
        button_layout.addStretch()

        self.params_layout.addRow("timestamp (Unix 秒)", ts_edit)
        self.params_layout.addRow("timeStr (本地时间)", time_str_edit)
        self.params_layout.addRow("", button_row)
        self._refresh_time_display()

    def _build_weather_sync_ui(self) -> None:
        method = "sync_weather"
        hints = self.schema.get("method_hints", {})
        hint_text = (
            str(hints[method])
            if isinstance(hints, dict) and method in hints
            else f"「{self._method_cn(method)}」参数"
        )
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        self.params_hint = hint
        self.params_layout.addRow(hint)

        home_edit = QLineEdit()
        home_edit.setPlaceholderText("home_id（需与设备一致，如 24 位 hex）")
        home_edit.textChanged.connect(self._sync_envelope_from_form)
        self._param_inputs["home_id"] = home_edit
        self._param_kinds["home_id"] = "str"
        self.params_layout.addRow("home_id", home_edit)

        metric_specs = [
            ("temperature", "温度 ℃", -100.0, 100.0, 20.0),
            ("humidity", "湿度 %RH", 0.0, 100.0, 60.0),
            ("pm25", "PM2.5 µg/m³", 0.0, 10000.0, 20.0),
        ]
        for key, label, lo, hi, default in metric_specs:
            self._build_metric_widget(key, label, lo, hi, default)

        self._build_timezone_widgets()

        obs_edit = QLineEdit(str(now_unix()))
        obs_edit.setReadOnly(True)
        obs_edit.setToolTip("Unix 秒。点「同步当前时间」刷新；发送前落后会自动更新")
        self._param_inputs["observed_at"] = obs_edit
        self._param_kinds["observed_at"] = "str"

        time_str_edit = QLineEdit()
        time_str_edit.setReadOnly(True)
        self._param_inputs["timeStr"] = time_str_edit
        self._param_kinds["timeStr"] = "str"

        sync_button = QPushButton("同步当前时间")
        sync_button.clicked.connect(self._refresh_now_fields)
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(sync_button)
        button_layout.addStretch()

        self.params_layout.addRow("observed_at (Unix 秒)", obs_edit)
        self.params_layout.addRow("timeStr (本地时间)", time_str_edit)
        self.params_layout.addRow("", button_row)
        self._refresh_time_display()

    def _build_metric_widget(
        self, key: str, label: str, lo: float, hi: float, default: float
    ) -> None:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        check = QCheckBox("启用")
        check.setChecked(True)
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.setEnabled(True)
        check.toggled.connect(spin.setEnabled)
        check.toggled.connect(self._sync_envelope_from_form)
        spin.valueChanged.connect(self._sync_envelope_from_form)
        hl.addWidget(check)
        hl.addWidget(spin, 1)
        self._param_inputs[key] = row
        self._param_kinds[key] = "opt_float"
        self.params_layout.addRow(label, row)
        self._param_row_widgets[key] = row

    # ── 时间同步表单辅助 ────────────────────────────────────────────
    def _timezone_offset_value(self) -> int:
        widget = self._param_inputs.get("timezone_offset_seconds")
        if isinstance(widget, QSpinBox):
            return int(widget.value())
        return DEFAULT_OFFSET_SECONDS

    def _timezone_id_value(self) -> str:
        widget = self._param_inputs.get("timezone_id")
        if isinstance(widget, QLineEdit):
            return widget.text().strip() or DEFAULT_TIMEZONE_ID
        return DEFAULT_TIMEZONE_ID

    def _dst_value(self) -> bool:
        widget = self._param_inputs.get("isDaylightSavingTime")
        if isinstance(widget, QComboBox):
            return widget.currentText() == "true"
        return False

    def _int_text_value(self, key: str) -> int | None:
        widget = self._param_inputs.get(key)
        if not isinstance(widget, QLineEdit):
            return None
        try:
            return int(widget.text().strip())
        except ValueError:
            return None

    def _refresh_now_fields(self, *_args) -> None:
        for key in ("timestamp", "observed_at"):
            if key in self._param_inputs:
                self._param_inputs[key].setText(str(now_unix()))
        self._refresh_time_display()

    def _refresh_time_display(self, *_args) -> None:
        ts = self._int_text_value("timestamp")
        if ts is None:
            ts = self._int_text_value("observed_at")
        if ts is None:
            ts = now_unix()
        time_str_widget = self._param_inputs.get("timeStr")
        if isinstance(time_str_widget, QLineEdit):
            time_str_widget.setText(local_time_str(ts, self._timezone_offset_value()))
        self._sync_envelope_from_form()

    def _on_timezone_preset_changed(self, index: int) -> None:
        if 0 <= index < len(COMMON_TIMEZONES):
            _, tz_id, offset = COMMON_TIMEZONES[index]
            tz_widget = self._param_inputs.get("timezone_id")
            if isinstance(tz_widget, QLineEdit):
                tz_widget.setText(tz_id)
            offset_widget = self._param_inputs.get("timezone_offset_seconds")
            if isinstance(offset_widget, QSpinBox):
                offset_widget.setValue(offset)
        self._refresh_time_display()

    def _select_preset_for_timezone(self, tz_id: str, offset: int) -> None:
        combo = getattr(self, "_timezone_combo", None)
        if combo is None:
            return
        for index, (label, preset_id, preset_offset) in enumerate(COMMON_TIMEZONES):
            if preset_id == tz_id and preset_offset == offset:
                self._set_combo_value(combo, label)
                return

    def _time_sync_params_from_form(self) -> dict[str, object]:
        return time_params(
            offset_seconds=self._timezone_offset_value(),
            timezone_id=self._timezone_id_value(),
            is_dst=self._dst_value(),
            timestamp=self._int_text_value("timestamp"),
        )

    def _weather_params_from_form(self) -> dict[str, object]:
        metrics: dict[str, float | None] = {}
        for key in ("temperature", "humidity", "pm25"):
            row = self._param_inputs.get(key)
            if not isinstance(row, QWidget):
                metrics[key] = None
                continue
            check = row.findChild(QCheckBox)
            spin = row.findChild(QDoubleSpinBox)
            if check is not None and spin is not None and check.isChecked():
                metrics[key] = float(spin.value())
            else:
                metrics[key] = None
        home_widget = self._param_inputs.get("home_id")
        home_id = home_widget.text().strip() if isinstance(home_widget, QLineEdit) else ""
        return weather_params(
            home_id=home_id,
            temperature=metrics["temperature"],
            humidity=metrics["humidity"],
            pm25=metrics["pm25"],
            offset_seconds=self._timezone_offset_value(),
            timezone_id=self._timezone_id_value(),
            is_dst=self._dst_value(),
            observed_at=self._int_text_value("observed_at"),
        )

    def _apply_time_sync_json_to_form(self, params: object) -> None:
        if not isinstance(params, dict):
            return
        offset = params.get("timezone_offset_seconds")
        if isinstance(offset, (int, float)) and not isinstance(offset, bool):
            widget = self._param_inputs.get("timezone_offset_seconds")
            if isinstance(widget, QSpinBox):
                widget.setValue(int(offset))
        tz_id = params.get("timezone_id")
        if isinstance(tz_id, str):
            widget = self._param_inputs.get("timezone_id")
            if isinstance(widget, QLineEdit):
                widget.setText(tz_id)
            self._select_preset_for_timezone(tz_id, int(offset) if isinstance(offset, (int, float)) else DEFAULT_OFFSET_SECONDS)
        dst = params.get("isDaylightSavingTime")
        if isinstance(dst, bool):
            widget = self._param_inputs.get("isDaylightSavingTime")
            if isinstance(widget, QComboBox):
                self._set_combo_value(widget, "true" if dst else "false")
        ts = params.get("timestamp")
        if ts is None:
            ts = params.get("observed_at")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            for key in ("timestamp", "observed_at"):
                widget = self._param_inputs.get(key)
                if isinstance(widget, QLineEdit):
                    widget.setText(str(int(ts)))
                    break
        for key in ("temperature", "humidity", "pm25"):
            if key not in self._param_inputs:
                continue
            row = self._param_inputs.get(key)
            if not isinstance(row, QWidget):
                continue
            check = row.findChild(QCheckBox)
            spin = row.findChild(QDoubleSpinBox)
            if check is None or spin is None:
                continue
            value = params.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                check.setChecked(True)
                spin.setValue(float(value))
            else:
                check.setChecked(False)
        home_widget = self._param_inputs.get("home_id")
        home_id = params.get("home_id")
        if isinstance(home_id, str) and isinstance(home_widget, QLineEdit):
            home_widget.setText(home_id)
        self._refresh_time_display()

    # ── 属性行（set/get_properties）─────────────────────────────────
    def _add_prop_row(
        self,
        siid: int | None = None,
        piid: int | None = None,
        value: str | None = None,
    ) -> None:
        defaults = self._prop_row_defaults()
        siid_val = siid if siid is not None else int(defaults["siid"])
        piid_val = piid if piid is not None else int(defaults["piid"])
        is_set = self.method_combo.currentText() == "set_properties"
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)

        siid_spin = QSpinBox()
        siid_spin.setRange(0, 65535)
        siid_spin.setValue(siid_val)
        siid_spin.valueChanged.connect(self._sync_envelope_from_form)
        piid_spin = QSpinBox()
        piid_spin.setRange(0, 65535)
        piid_spin.setValue(piid_val)
        piid_spin.valueChanged.connect(self._sync_envelope_from_form)
        value_edit: QLineEdit | None = None
        if is_set:
            value_edit = QLineEdit(
                value if value is not None else str(defaults["value"])
            )
            value_edit.setPlaceholderText("值 (数字 / true / false / \"字符串\")")
            value_edit.setToolTip("支持数字、true/false、带引号的字符串")
            value_edit.textChanged.connect(self._sync_envelope_from_form)

        entry: dict[str, object] = {
            "siid": siid_spin,
            "piid": piid_spin,
            "value": value_edit,
            "widget": row,
        }
        self._prop_rows.append(entry)

        hl.addWidget(QLabel("SIID"))
        hl.addWidget(siid_spin)
        hl.addWidget(QLabel("PIID"))
        hl.addWidget(piid_spin)
        if value_edit is not None:
            hl.addWidget(value_edit, 1)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(lambda: self._remove_prop_row(entry))
        hl.addWidget(delete_button)
        self.params_layout.addRow(row)

    def _remove_prop_row(self, entry: dict[str, object]) -> None:
        widget = entry.get("widget")
        if widget is not None:
            self.params_layout.removeWidget(widget)
            widget.deleteLater()
        if entry in self._prop_rows:
            self._prop_rows.remove(entry)
        self._sync_envelope_from_form()

    def _clear_prop_rows(self) -> None:
        for entry in list(self._prop_rows):
            widget = entry.get("widget")
            if widget is not None:
                self.params_layout.removeWidget(widget)
                widget.deleteLater()
        self._prop_rows.clear()

    def _params_from_rows(self) -> list[dict[str, object]]:
        is_set = self.method_combo.currentText() == "set_properties"
        rows: list[dict[str, object]] = []
        for entry in self._prop_rows:
            item: dict[str, object] = {
                "siid": int(entry["siid"].value()),
                "piid": int(entry["piid"].value()),
            }
            value_edit = entry.get("value")
            if is_set and isinstance(value_edit, QLineEdit):
                parsed = self._parse_json_literal(value_edit.text())
                if parsed is not None:
                    item["value"] = parsed
            rows.append(item)
        return rows

    @staticmethod
    def _parse_json_literal(text: str) -> object:
        """解析 JSON 字面量：数字 / true / false / 引号字符串；失败回退为原始字符串。"""
        stripped = text.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        try:
            return int(stripped)
        except ValueError:
            return stripped

    @staticmethod
    def _json_literal_text(value: object) -> str:
        """把 JSON 值显示为可编辑的字面量文本（保留类型信息）。"""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return json.dumps(value, ensure_ascii=False)

    # ── 表单 → 参数 ────────────────────────────────────────────────
    def _params_from_form(self) -> object:
        method = self.method_combo.currentText()
        if method in self._row_methods():
            return self._params_from_rows()
        if method == "time":
            return self._time_sync_params_from_form()
        if method == "sync_weather":
            return self._weather_params_from_form()
        item: dict[str, object] = {}
        for key, w in self._param_inputs.items():
            spec = self._param_specs[key]
            kind = self._param_kinds[key]
            if kind == "int":
                value: object = int(w.value())
            elif kind == "float":
                value = float(w.value())
            elif kind == "str":
                value = w.text()
                if not value and spec.get("optional"):
                    continue
            elif kind == "json":
                value = self._parse_json_literal(w.text())
                if value is None:
                    continue
            elif kind == "select":
                value = w.currentText()
            else:
                continue
            item[key] = value
        if method == "sync_workflow_config":
            if item.get("op") == "delete":
                return {
                    "schema_version": int(item.get("schema_version", 2)),
                    "op": "delete",
                    "flow_id": int(item.get("flow_id", 0)),
                    "revision": int(item.get("revision", 1)),
                }
            trigger, nodes = self._workflow_parts_from_form()
            item["trigger"] = trigger
            item["nodes"] = nodes
        return item

    # ── 信封 → 表单 ────────────────────────────────────────────────
    def _apply_json_to_form(self, params: object) -> None:
        if params is None:
            return
        method = self.method_combo.currentText()
        if method in self._row_methods():
            self._apply_rows_from_json(params)
            return
        if method in TIME_SYNC_METHODS:
            self._apply_time_sync_json_to_form(params)
            return
        if not isinstance(params, dict):
            return
        for key, widget in self._param_inputs.items():
            if key not in params:
                continue
            kind = self._param_kinds[key]
            value = params[key]
            try:
                if kind == "str":
                    widget.setText(str(value))
                elif kind == "json":
                    widget.setText(self._json_literal_text(value))
                elif kind == "select":
                    self._set_combo_value(widget, str(value))
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(value))
                else:
                    widget.setText(str(value))
            except (TypeError, ValueError):
                pass
        if method == "sync_workflow_config":
            self._apply_workflow_from_json(params)
            self._update_workflow_scalar_visibility()

    def _apply_rows_from_json(self, params: object) -> None:
        self._clear_prop_rows()
        if not isinstance(params, list):
            return
        defaults = self._prop_row_defaults()
        default_siid = int(defaults["siid"])
        default_piid = int(defaults["piid"])
        is_set = self.method_combo.currentText() == "set_properties"
        for item in params[:64]:
            if not isinstance(item, dict):
                continue
            siid = item.get("siid", default_siid)
            piid = item.get("piid", default_piid)
            siid_val = (
                int(siid)
                if isinstance(siid, (int, float)) and not isinstance(siid, bool)
                else default_siid
            )
            piid_val = (
                int(piid)
                if isinstance(piid, (int, float)) and not isinstance(piid, bool)
                else default_piid
            )
            value_text = None
            if is_set and "value" in item:
                value_text = self._json_literal_text(item["value"])
            self._add_prop_row(siid_val, piid_val, value_text)
