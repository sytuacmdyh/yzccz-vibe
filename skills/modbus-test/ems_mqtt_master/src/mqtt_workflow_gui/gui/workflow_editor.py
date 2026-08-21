"""工作流 trigger / nodes 可视化编辑器（Mixin，供 ParamFormMixin 组合使用）。"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..workflow_model import (
    ACTION_OPS,
    CMP_OPS,
    ENV_PARAMS,
    ENV_RANGE_HUMIDITY,
    ENV_RANGE_PM25,
    ENV_TEMP_CMP,
    GUARD_CONN,
    NODE_TYPES,
    TRIGGER_TYPES,
    VALUE_TYPES,
    WEEKDAYS,
    coerce_typed_value,
    default_exclusive_gw_node,
    default_flow,
    default_guard,
    default_nodes,
    default_trigger,
    format_days_of_week,
    format_next_target,
    parse_days_of_week,
    parse_next_target,
    parse_prop_ref,
    parse_target_field,
    parse_typed_value,
    prop_ref,
    typed_value,
    write_target,
)


class WorkflowEditorMixin:
    """sync_workflow_config 的 trigger / nodes 表单编辑。"""

    def _wf_ids(self) -> tuple[str, str]:
        device_id = str(self.cfg.get("device_id", ""))
        product_id = str(self.cfg.get("product_id", ""))
        return device_id, product_id

    def _init_workflow_editor_state(self) -> None:
        self._wf_trigger_box: QGroupBox | None = None
        self._wf_nodes_box: QGroupBox | None = None
        self._wf_trigger_type: QComboBox | None = None
        self._wf_trigger_container: QWidget | None = None
        self._wf_trigger_fields: dict[str, QWidget] = {}
        self._wf_node_rows: list[dict[str, object]] = []
        self._wf_layout_refresh_pending = False

    def _wf_size_compact(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

    def _wf_set_section_visible(self, section: QWidget | None, visible: bool) -> None:
        if section is None:
            return
        section.setVisible(visible)
        if visible:
            section.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
        else:
            section.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
            )

    def _clear_workflow_editor(self) -> None:
        for box in (self._wf_trigger_box, self._wf_nodes_box):
            if box is not None:
                self.params_layout.removeWidget(box)
                box.deleteLater()
        self._wf_trigger_box = None
        self._wf_nodes_box = None
        self._wf_trigger_type = None
        self._wf_trigger_container = None
        self._wf_trigger_fields = {}
        self._wf_node_rows = []

    def _build_workflow_editor_ui(self) -> None:
        self._clear_workflow_editor()
        device_id, product_id = self._wf_ids()

        trigger_box = QGroupBox("触发器 (trigger)")
        self._wf_size_compact(trigger_box)
        trigger_layout = QVBoxLayout(trigger_box)
        type_row = QHBoxLayout()
        self._wf_trigger_type = QComboBox()
        self._wf_trigger_type.addItems(TRIGGER_TYPES)
        self._wf_trigger_type.currentTextChanged.connect(self._on_trigger_type_changed)
        self._wf_trigger_type.currentTextChanged.connect(self._sync_envelope_from_form)
        type_row.addWidget(QLabel("类型"))
        type_row.addWidget(self._wf_trigger_type, 1)
        trigger_layout.addLayout(type_row)

        self._wf_trigger_container = QWidget()
        self._wf_size_compact(self._wf_trigger_container)
        trigger_panel_layout = QVBoxLayout(self._wf_trigger_container)
        trigger_panel_layout.setContentsMargins(0, 0, 0, 0)
        trigger_panel_layout.setSpacing(6)
        self._wf_trigger_fields = {
            "periodic": self._build_trigger_periodic_panel(),
            "time_of_day": self._build_trigger_time_panel(),
            "property_change": self._build_trigger_prop_panel(device_id, product_id),
            "property_sustain": self._build_trigger_sustain_panel(device_id, product_id),
            "env_change": self._build_trigger_env_panel(),
            "manual": self._build_trigger_manual_panel(),
        }
        for name in TRIGGER_TYPES:
            panel = self._wf_trigger_fields[name]
            panel.setVisible(False)
            panel.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
            )
            trigger_panel_layout.addWidget(panel)
        trigger_layout.addWidget(self._wf_trigger_container)
        self._wf_trigger_box = trigger_box
        self.params_layout.addRow(trigger_box)

        nodes_box = QGroupBox("节点 (nodes)")
        self._wf_size_compact(nodes_box)
        nodes_layout = QVBoxLayout(nodes_box)
        nodes_layout.setSpacing(6)
        self._wf_nodes_container = QWidget()
        self._wf_size_compact(self._wf_nodes_container)
        self._wf_nodes_layout = QVBoxLayout(self._wf_nodes_container)
        self._wf_nodes_layout.setContentsMargins(0, 0, 0, 0)
        self._wf_nodes_layout.setSpacing(8)
        nodes_layout.addWidget(self._wf_nodes_container)
        add_node_btn = QPushButton("+ 添加节点")
        add_node_btn.clicked.connect(lambda: self._add_service_node())
        nodes_layout.addWidget(add_node_btn)
        self._wf_nodes_box = nodes_box
        self.params_layout.addRow(nodes_box)

        self._apply_trigger_to_editor(default_trigger(device_id, product_id))
        self._apply_nodes_to_editor(default_nodes(device_id, product_id))
        self._on_trigger_type_changed(self._wf_trigger_type.currentText())
        self._update_workflow_scalar_visibility()
        self._update_workflow_editor_visibility()
        self._wf_refresh_layout()

    def _update_workflow_editor_visibility(self) -> None:
        visible = (
            self.method_combo.currentText() == "sync_workflow_config"
            and self._param_inputs.get("op") is not None
            and self._param_inputs["op"].currentText() == "upsert"
        )
        for box in (self._wf_trigger_box, self._wf_nodes_box):
            if box is not None:
                box.setVisible(visible)
        self._wf_refresh_layout()

    def _connect_workflow_op_listener(self) -> None:
        op_widget = self._param_inputs.get("op")
        if isinstance(op_widget, QComboBox):
            op_widget.currentTextChanged.connect(self._update_workflow_scalar_visibility)
            op_widget.currentTextChanged.connect(self._update_workflow_editor_visibility)
            op_widget.currentTextChanged.connect(self._sync_envelope_from_form)

    def _wf_section(self, title: str = "") -> tuple[QWidget, QFormLayout]:
        """可整块显示/隐藏的分组（避免 QFormLayout 只藏控件不藏标签）。"""
        section = QWidget()
        self._wf_size_compact(section)
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        if title:
            label = QLabel(title)
            label.setStyleSheet("color: #666; font-size: 11px;")
            outer.addWidget(label)
        body = QWidget()
        form = QFormLayout(body)
        form.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)
        return section, form

    def _wf_set_visible(self, widget: QWidget | None, visible: bool) -> None:
        if widget is not None:
            widget.setVisible(visible)

    def _wf_refresh_layout(self) -> None:
        """切换显示块后延迟重算高度，使栏高随当前可见内容收缩。"""
        if self._wf_layout_refresh_pending:
            return
        self._wf_layout_refresh_pending = True

        def do_refresh() -> None:
            self._wf_layout_refresh_pending = False
            chain: list[QWidget | None] = [
                getattr(self, "_wf_trigger_container", None),
                getattr(self, "_wf_trigger_box", None),
                getattr(self, "_wf_nodes_container", None),
                getattr(self, "_wf_nodes_box", None),
                getattr(self, "params_container", None),
                getattr(self, "_params_scroll_content", None),
            ]
            for widget in chain:
                if widget is None:
                    continue
                layout = widget.layout()
                if layout is not None:
                    layout.invalidate()
                    layout.activate()
                widget.setMaximumHeight(16777215)
                widget.updateGeometry()
                widget.adjustSize()

            scroll = getattr(self, "params_scroll", None)
            if scroll is not None:
                content = scroll.widget()
                if content is not None:
                    content.setMaximumHeight(16777215)
                    content.updateGeometry()
                    content.adjustSize()
                scroll.updateGeometry()

        QTimer.singleShot(0, do_refresh)

    def _spin(self, value: int, min_v: int, max_v: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(min_v, max_v)
        spin.setValue(value)
        spin.valueChanged.connect(self._sync_envelope_from_form)
        return spin

    def _line(self, text: str = "", placeholder: str = "") -> QLineEdit:
        edit = QLineEdit(text)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.textChanged.connect(self._sync_envelope_from_form)
        return edit

    def _combo(self, options: tuple[str, ...] | list[str], current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(options))
        if current in options:
            combo.setCurrentIndex(list(options).index(current))
        combo.currentTextChanged.connect(self._sync_envelope_from_form)
        return combo

    def _set_combo_value(self, combo: QComboBox, text: str) -> None:
        """程序化改值时不弹出下拉框、不重复触发同步。"""
        combo.blockSignals(True)
        try:
            idx = combo.findText(str(text))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentText(str(text))
        finally:
            combo.hidePopup()
            combo.blockSignals(False)

    def _prop_ref_widgets(
        self, device_id: str, product_id: str, siid: int, piid: int
    ) -> dict[str, QWidget]:
        return {
            "device_id": self._line(device_id),
            "product_id": self._line(product_id),
            "siid": self._spin(siid, 0, 65535),
            "piid": self._spin(piid, 0, 65535),
        }

    def _read_prop_widgets(self, fields: dict[str, QWidget]) -> dict[str, object]:
        return prop_ref(
            fields["device_id"].text().strip(),
            fields["product_id"].text().strip(),
            int(fields["siid"].value()),
            int(fields["piid"].value()),
        )

    def _fill_prop_widgets(self, fields: dict[str, QWidget], ref: object) -> None:
        device_id, product_id, siid, piid = parse_prop_ref(ref)
        fields["device_id"].setText(device_id)
        fields["product_id"].setText(product_id)
        fields["siid"].setValue(siid)
        fields["piid"].setValue(piid)

    def _prop_ref_section(
        self,
        title: str,
        device_id: str,
        product_id: str,
        siid: int,
        piid: int,
    ) -> tuple[QWidget, dict[str, QWidget]]:
        section, form = self._wf_section(title)
        fields = self._prop_ref_widgets(device_id, product_id, siid, piid)
        self._add_prop_ref_rows(form, fields, "")
        return section, fields

    def _update_workflow_scalar_visibility(self, _op: str = "") -> None:
        """op=delete 时隐藏 upsert 专属标量字段。"""
        if self.method_combo.currentText() != "sync_workflow_config":
            return
        op_widget = self._param_inputs.get("op")
        is_delete = isinstance(op_widget, QComboBox) and op_widget.currentText() == "delete"
        for key in ("enabled", "name", "description"):
            row = getattr(self, "_param_row_widgets", {}).get(key)
            if row is not None:
                row.setVisible(not is_delete)
        self._update_workflow_editor_visibility()
        self._wf_refresh_layout()

    def _add_prop_ref_rows(
        self, form: QFormLayout, fields: dict[str, QWidget], prefix: str
    ) -> None:
        form.addRow(f"{prefix}device_id", fields["device_id"])
        form.addRow(f"{prefix}product_id", fields["product_id"])
        form.addRow(f"{prefix}siid", fields["siid"])
        form.addRow(f"{prefix}piid", fields["piid"])

    def _build_trigger_periodic_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        ticks = self._spin(10, 1, 65535)
        ticks.setToolTip("单位 100ms，10 = 1 秒，600 = 1 分钟")
        form.addRow("period_ticks", ticks)
        panel._fields = {"period_ticks": ticks}  # type: ignore[attr-defined]
        return panel

    def _build_trigger_time_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        hour = self._spin(22, 0, 23)
        minute = self._spin(0, 0, 59)
        weekday_row = QWidget()
        weekday_layout = QHBoxLayout(weekday_row)
        weekday_layout.setContentsMargins(0, 0, 0, 0)
        weekday_boxes: dict[str, QCheckBox] = {}
        for day in WEEKDAYS:
            box = QCheckBox(day)
            box.stateChanged.connect(self._sync_envelope_from_form)
            weekday_boxes[day] = box
            weekday_layout.addWidget(box)
        weekday_layout.addStretch()
        form.addRow("hour", hour)
        form.addRow("minute", minute)
        form.addRow("days_of_week", weekday_row)
        panel._fields = {"hour": hour, "minute": minute, "weekdays": weekday_boxes}  # type: ignore[attr-defined]
        return panel

    def _build_trigger_prop_panel(self, device_id: str, product_id: str) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        fields = self._prop_ref_widgets(device_id, product_id, 2, 1)
        self._add_prop_ref_rows(form, fields, "property.")
        panel._fields = fields  # type: ignore[attr-defined]
        return panel

    def _build_trigger_sustain_panel(self, device_id: str, product_id: str) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        prop_sec, prop_form = self._wf_section("监视属性")
        fields = self._prop_ref_widgets(device_id, product_id, 2, 4938)
        self._add_prop_ref_rows(prop_form, fields, "")
        layout.addWidget(prop_sec)
        cond_sec, cond_form = self._wf_section("持续条件")
        cmp_combo = self._combo(CMP_OPS, "le")
        value_type = self._combo(VALUE_TYPES, "int")
        value_edit = self._line("100")
        sustain = self._spin(600, 1, 65535)
        sustain.setToolTip("持续满足条件的周期数（100ms/tick）")
        cond_form.addRow("cmp", cmp_combo)
        cond_form.addRow("value.type", value_type)
        cond_form.addRow("value", value_edit)
        cond_form.addRow("sustain_ticks", sustain)
        layout.addWidget(cond_sec)
        panel._fields = {  # type: ignore[attr-defined]
            **fields,
            "cmp": cmp_combo,
            "value_type": value_type,
            "value": value_edit,
            "sustain_ticks": sustain,
        }
        return panel

    def _build_trigger_env_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        sec, form = self._wf_section("室外环境指标")
        env_param = self._combo(ENV_PARAMS, "temperature")
        env_cmp = self._combo(ENV_TEMP_CMP, "gt")
        form.addRow("env_param", env_param)
        form.addRow("env_cmp", env_cmp)
        layout.addWidget(sec)

        value_sec, value_form = self._wf_section("阈值（温度）")
        env_value = self._line("5", "比较阈值（℃，范围 -100~100）")
        value_form.addRow("env_value", env_value)
        layout.addWidget(value_sec)

        range_sec, range_form = self._wf_section("预设标签 / 显式区间（湿度、PM2.5）")
        env_range = QComboBox()
        env_range.currentTextChanged.connect(self._sync_envelope_from_form)
        env_min = self._line("", "显式区间下界（闭区间）")
        env_max = self._line("", "显式区间上界（闭区间）")
        range_form.addRow("env_range", env_range)
        range_form.addRow("env_min", env_min)
        range_form.addRow("env_max", env_max)
        layout.addWidget(range_sec)

        fields: dict[str, object] = {
            "env_param": env_param,
            "env_cmp": env_cmp,
            "env_value": env_value,
            "env_range": env_range,
            "env_min": env_min,
            "env_max": env_max,
            "env_value_sec": value_sec,
            "env_range_sec": range_sec,
        }
        env_param.currentTextChanged.connect(
            lambda param: self._sync_env_param_fields(fields, str(param))
        )
        self._sync_env_param_fields(fields, "temperature")
        panel._fields = fields  # type: ignore[attr-defined]
        return panel

    def _sync_env_param_fields(self, fields: dict[str, object], param: str) -> None:
        """按 env_param 切换 cmp 选项、标签集合与显隐。"""
        is_temp = param == "temperature"
        cmp_combo = fields["env_cmp"]
        range_combo = fields["env_range"]
        cmp_combo.blockSignals(True)
        try:
            cmp_combo.clear()
            cmp_combo.addItems(list(ENV_TEMP_CMP) if is_temp else ["in_range"])
        finally:
            cmp_combo.blockSignals(False)
        range_combo.blockSignals(True)
        try:
            range_combo.clear()
            range_combo.addItem("（不使用标签，走显式区间）", None)
            labels = (
                ENV_RANGE_HUMIDITY if param == "humidity" else ENV_RANGE_PM25
            )
            for label in labels:
                range_combo.addItem(label, label)
            range_combo.setCurrentIndex(0 if is_temp else 1)
        finally:
            range_combo.blockSignals(False)
        self._wf_set_section_visible(fields["env_value_sec"], is_temp)
        self._wf_set_section_visible(fields["env_range_sec"], not is_temp)
        self._wf_refresh_layout()

    def _build_trigger_manual_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(
            "仅接受 type=manual，周期扫描永不自动触发。\n"
            "配置并启用后，请改用 execute_workflow 立即入队；"
            "ACK code=0 表示已入队（含相同 run_id 重投），不是节点已跑完。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)
        panel._fields = {}  # type: ignore[attr-defined]
        return panel

    def _on_trigger_type_changed(self, type_name: str) -> None:
        for name, panel in self._wf_trigger_fields.items():
            visible = name == type_name
            panel.setVisible(visible)
            if visible:
                self._wf_size_compact(panel)
            else:
                panel.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
                )
        self._wf_refresh_layout()

    def _trigger_from_editor(self) -> dict[str, object]:
        if self._wf_trigger_type is None:
            device_id, product_id = self._wf_ids()
            return default_trigger(device_id, product_id)
        type_name = self._wf_trigger_type.currentText()
        panel = self._wf_trigger_fields.get(type_name)
        fields = getattr(panel, "_fields", {}) if panel is not None else {}
        trigger: dict[str, object] = {"type": type_name}
        if type_name == "periodic":
            trigger["period_ticks"] = int(fields["period_ticks"].value())
        elif type_name == "time_of_day":
            trigger["hour"] = int(fields["hour"].value())
            trigger["minute"] = int(fields["minute"].value())
            days = [
                day for day, box in fields["weekdays"].items() if box.isChecked()
            ]
            if days:
                trigger["days_of_week"] = days
        elif type_name == "property_change":
            trigger["property"] = self._read_prop_widgets(fields)
        elif type_name == "property_sustain":
            trigger["property"] = self._read_prop_widgets(fields)
            trigger["cmp"] = fields["cmp"].currentText()
            trigger["value"] = typed_value(
                fields["value_type"].currentText(),
                coerce_typed_value(
                    fields["value_type"].currentText(), fields["value"].text()
                ),
            )
            trigger["sustain_ticks"] = int(fields["sustain_ticks"].value())
        elif type_name == "env_change":
            param = fields["env_param"].currentText()
            trigger["env_param"] = param
            if param == "temperature":
                trigger["env_cmp"] = fields["env_cmp"].currentText()
                try:
                    trigger["env_value"] = float(fields["env_value"].text().strip())
                except ValueError:
                    trigger["env_value"] = 0.0
            else:
                trigger["env_cmp"] = "in_range"
                range_label = fields["env_range"].currentData()
                if range_label:
                    trigger["env_range"] = str(range_label)
                else:
                    min_text = fields["env_min"].text().strip()
                    max_text = fields["env_max"].text().strip()
                    try:
                        trigger["env_min"] = float(min_text)
                    except ValueError:
                        trigger["env_min"] = 0.0
                    try:
                        trigger["env_max"] = float(max_text)
                    except ValueError:
                        trigger["env_max"] = (
                            100.0 if param == "humidity" else 10000.0
                        )
        return trigger

    def _apply_trigger_to_editor(self, trigger: object) -> None:
        if not isinstance(trigger, dict) or self._wf_trigger_type is None:
            return
        type_name = str(trigger.get("type", "periodic"))
        if type_name not in TRIGGER_TYPES:
            type_name = "periodic"
        self._set_combo_value(self._wf_trigger_type, type_name)
        self._on_trigger_type_changed(type_name)
        panel = self._wf_trigger_fields.get(type_name)
        fields = getattr(panel, "_fields", {}) if panel is not None else {}
        if type_name == "periodic":
            ticks = trigger.get("period_ticks", 10)
            if isinstance(ticks, (int, float)):
                fields["period_ticks"].setValue(int(ticks))
        elif type_name == "time_of_day":
            hour = trigger.get("hour", 0)
            minute = trigger.get("minute", 0)
            if isinstance(hour, (int, float)):
                fields["hour"].setValue(int(hour))
            if isinstance(minute, (int, float)):
                fields["minute"].setValue(int(minute))
            selected = set(parse_days_of_week(trigger.get("days_of_week")))
            for day, box in fields["weekdays"].items():
                box.setChecked(day in selected)
        elif type_name == "property_change":
            self._fill_prop_widgets(fields, trigger.get("property"))
        elif type_name == "property_sustain":
            self._fill_prop_widgets(fields, trigger.get("property"))
            cmp_name = str(trigger.get("cmp", "eq"))
            if cmp_name in CMP_OPS:
                self._set_combo_value(fields["cmp"], cmp_name)
            value_type, value_text = parse_typed_value(trigger.get("value"))
            if value_type in VALUE_TYPES:
                self._set_combo_value(fields["value_type"], value_type)
            fields["value"].setText(value_text)
            sustain = trigger.get("sustain_ticks", 600)
            if isinstance(sustain, (int, float)):
                fields["sustain_ticks"].setValue(int(sustain))
        elif type_name == "env_change":
            param = str(trigger.get("env_param", "temperature"))
            if param not in ENV_PARAMS:
                param = "temperature"
            self._set_combo_value(fields["env_param"], param)
            self._sync_env_param_fields(fields, param)
            if param == "temperature":
                cmp_name = str(trigger.get("env_cmp", "gt"))
                if cmp_name in ENV_TEMP_CMP:
                    self._set_combo_value(fields["env_cmp"], cmp_name)
                value = trigger.get("env_value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    fields["env_value"].setText(str(value))
            else:
                self._set_combo_value(fields["env_cmp"], "in_range")
                range_name = trigger.get("env_range")
                range_index = (
                    fields["env_range"].findData(range_name)
                    if isinstance(range_name, str) and range_name
                    else 0
                )
                fields["env_range"].setCurrentIndex(range_index if range_index >= 0 else 0)
                min_v = trigger.get("env_min")
                max_v = trigger.get("env_max")
                if isinstance(min_v, (int, float)) and not isinstance(min_v, bool):
                    fields["env_min"].setText(str(min_v))
                if isinstance(max_v, (int, float)) and not isinstance(max_v, bool):
                    fields["env_max"].setText(str(max_v))

    def _clear_node_rows(self) -> None:
        for entry in list(self._wf_node_rows):
            widget = entry.get("widget")
            if widget is not None:
                self._wf_nodes_layout.removeWidget(widget)
                widget.deleteLater()
        self._wf_node_rows = []

    def _add_service_node(self, node: dict[str, object] | None = None) -> None:
        if not isinstance(node, dict):
            node = None
        device_id, product_id = self._wf_ids()
        if node is None:
            node = default_nodes(device_id, product_id)[0]
        entry = self._create_node_row(len(self._wf_node_rows), node)
        self._wf_node_rows.append(entry)
        self._wf_nodes_layout.addWidget(entry["widget"])
        self._wf_refresh_layout()
        self._sync_envelope_from_form()

    def _create_node_row(self, index: int, node: dict[str, object]) -> dict[str, object]:
        box = QGroupBox(f"节点 {index}")
        self._wf_size_compact(box)
        layout = QVBoxLayout(box)
        type_combo = self._combo(NODE_TYPES, str(node.get("type", "service")))
        layout.addWidget(type_combo)

        service_panel, service_fields = self._build_service_node_panel(node)
        gw_panel, gw_fields = self._build_exclusive_gw_panel(node)
        service_panel.hide()
        gw_panel.hide()
        layout.addWidget(service_panel)
        layout.addWidget(gw_panel)

        is_service = str(node.get("type", "service")) != "exclusive_gw"
        service_panel.setVisible(is_service)
        gw_panel.setVisible(not is_service)
        if is_service:
            self._wf_size_compact(service_panel)
            gw_panel.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
            )
        else:
            self._wf_size_compact(gw_panel)
            service_panel.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
            )

        def on_type_changed(type_name: str) -> None:
            show_service = type_name == "service"
            service_panel.setVisible(show_service)
            gw_panel.setVisible(not show_service)
            if show_service:
                self._wf_size_compact(service_panel)
                gw_panel.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
                )
            else:
                self._wf_size_compact(gw_panel)
                service_panel.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
                )
            self._wf_refresh_layout()

        type_combo.currentTextChanged.connect(on_type_changed)

        delete_btn = QPushButton("删除节点")
        delete_btn.clicked.connect(lambda: self._remove_node_row(entry_holder))
        layout.addWidget(delete_btn)

        entry_holder: dict[str, object] = {
            "widget": box,
            "type_combo": type_combo,
            "service_panel": service_panel,
            "gw_panel": gw_panel,
            "service_fields": service_fields,
            "gw_fields": gw_fields,
        }
        return entry_holder

    def _build_service_node_panel(
        self, node: dict[str, object]
    ) -> tuple[QWidget, dict[str, object]]:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        actions: list[dict[str, object]] = []
        raw_actions = node.get("actions")
        if isinstance(raw_actions, list):
            actions = [a for a in raw_actions if isinstance(a, dict)]
        if not actions:
            device_id, product_id = self._wf_ids()
            actions = default_nodes(device_id, product_id)[0]["actions"]  # type: ignore[index]

        action_rows: list[dict[str, object]] = []
        actions_box = QWidget()
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        for action in actions:
            action_rows.append(self._add_action_row(actions_layout, action))
        add_action_btn = QPushButton("+ 添加动作")
        add_action_btn.clicked.connect(
            lambda: self._append_action_row(actions_layout, action_rows)
        )
        layout.addWidget(actions_box)
        layout.addWidget(add_action_btn)

        next_edit = self._line(parse_next_target(node.get("next", "end")))
        next_edit.setPlaceholderText("end 或节点索引数字")
        form = QFormLayout()
        form.addRow("next", next_edit)
        layout.addLayout(form)
        return panel, {
            "action_rows": action_rows,
            "actions_layout": actions_layout,
            "next_edit": next_edit,
        }

    def _append_action_row(
        self,
        actions_layout: QVBoxLayout,
        action_rows: list[dict[str, object]],
    ) -> None:
        device_id, product_id = self._wf_ids()
        default_action = default_nodes(device_id, product_id)[0]["actions"][0]  # type: ignore[index]
        action_rows.append(self._add_action_row(actions_layout, default_action))
        self._sync_envelope_from_form()

    def _add_action_row(
        self, parent_layout: QVBoxLayout, action: dict[str, object]
    ) -> dict[str, object]:
        device_id, product_id = self._wf_ids()
        ids, t_prod, t_siid, t_piid = parse_target_field(action.get("target"))
        row_box = QGroupBox("动作")
        self._wf_size_compact(row_box)
        form = QFormLayout(row_box)
        op_combo = self._combo(ACTION_OPS, str(action.get("op", "write")))

        target_sec, target_form = self._wf_section("target")
        device_ids_edit = self._line(
            ",".join(ids) or device_id,
            "device_ids，逗号分隔，空=广播",
        )
        target_product = self._line(t_prod or product_id)
        target_siid = self._spin(t_siid, 0, 65535)
        target_piid = self._spin(t_piid, 0, 65535)
        target_form.addRow("device_ids", device_ids_edit)
        target_form.addRow("product_id", target_product)
        target_form.addRow("siid", target_siid)
        target_form.addRow("piid", target_piid)

        value_sec, value_form = self._wf_section("value / delta")
        value_type = self._combo(VALUE_TYPES, "bool")
        value_edit = self._line("true")
        value_form.addRow("type", value_type)
        value_form.addRow("value", value_edit)

        source_sec, source = self._prop_ref_section("source", device_id, product_id, 2, 1)
        left_sec, left = self._prop_ref_section("left", device_id, product_id, 2, 1)
        right_sec, right = self._prop_ref_section("right", device_id, product_id, 2, 2)
        min_sec, min_ref = self._prop_ref_section("min", device_id, product_id, 2, 1)
        max_sec, max_ref = self._prop_ref_section("max", device_id, product_id, 2, 3)

        form.addRow("op", op_combo)
        form.addRow(target_sec)
        form.addRow(value_sec)
        form.addRow(source_sec)
        form.addRow(left_sec)
        form.addRow(right_sec)
        form.addRow(min_sec)
        form.addRow(max_sec)

        delete_btn = QPushButton("删除动作")
        form.addRow(delete_btn)

        entry = {
            "widget": row_box,
            "op": op_combo,
            "device_ids": device_ids_edit,
            "target_product": target_product,
            "target_siid": target_siid,
            "target_piid": target_piid,
            "value_type": value_type,
            "value": value_edit,
            "source": source,
            "left": left,
            "right": right,
            "min": min_ref,
            "max": max_ref,
            "sections": {
                "value": value_sec,
                "source": source_sec,
                "left": left_sec,
                "right": right_sec,
                "min": min_sec,
                "max": max_sec,
            },
        }

        def refresh_visibility() -> None:
            op = op_combo.currentText()
            sections = entry["sections"]
            self._wf_set_section_visible(sections["value"], op in ("write", "add"))
            self._wf_set_section_visible(sections["source"], op == "copy")
            self._wf_set_section_visible(sections["left"], op in ("add_reg", "min", "max"))
            self._wf_set_section_visible(sections["right"], op in ("add_reg", "min", "max"))
            self._wf_set_section_visible(sections["min"], op == "clamp")
            self._wf_set_section_visible(sections["max"], op == "clamp")
            self._wf_refresh_layout()

        op_combo.currentTextChanged.connect(lambda _t: refresh_visibility())
        op_combo.currentTextChanged.connect(self._sync_envelope_from_form)
        self._apply_action_to_row(entry, action)
        refresh_visibility()

        delete_btn.clicked.connect(lambda: self._remove_action_row(parent_layout, entry))
        parent_layout.addWidget(row_box)
        return entry

    def _apply_action_to_row(self, entry: dict[str, object], action: dict[str, object]) -> None:
        op = str(action.get("op", "write"))
        if op == "write":
            vtype, vtext = parse_typed_value(action.get("value"))
            if vtype in VALUE_TYPES:
                self._set_combo_value(entry["value_type"], vtype)
            entry["value"].setText(vtext)
        elif op == "add":
            vtype, vtext = parse_typed_value(action.get("delta"))
            if vtype in VALUE_TYPES:
                self._set_combo_value(entry["value_type"], vtype)
            entry["value"].setText(vtext)
        elif op == "copy":
            self._fill_prop_widgets(entry["source"], action.get("source"))
        elif op in ("add_reg", "min", "max"):
            self._fill_prop_widgets(entry["left"], action.get("left"))
            self._fill_prop_widgets(entry["right"], action.get("right"))
        elif op == "clamp":
            self._fill_prop_widgets(entry["min"], action.get("min"))
            self._fill_prop_widgets(entry["max"], action.get("max"))

    def _remove_action_row(self, parent_layout: QVBoxLayout, entry: dict[str, object]) -> None:
        widget = entry.get("widget")
        if widget is not None:
            parent_layout.removeWidget(widget)
            widget.deleteLater()
        for node_entry in self._wf_node_rows:
            service_fields = node_entry.get("service_fields")
            if not isinstance(service_fields, dict):
                continue
            rows = service_fields.get("action_rows")
            if isinstance(rows, list) and entry in rows:
                rows.remove(entry)
        self._sync_envelope_from_form()

    def _build_exclusive_gw_panel(
        self, node: dict[str, object]
    ) -> tuple[QWidget, dict[str, object]]:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        flows_box = QWidget()
        flows_layout = QVBoxLayout(flows_box)
        flows_layout.setContentsMargins(0, 0, 0, 0)
        flow_rows: list[dict[str, object]] = []
        raw_flows = node.get("flows")
        flows = (
            [f for f in raw_flows if isinstance(f, dict)]
            if isinstance(raw_flows, list)
            else []
        )
        device_id, product_id = self._wf_ids()
        if not flows:
            flows = [default_flow(device_id, product_id)]
        for flow in flows:
            flow_rows.append(self._add_flow_row(flows_layout, flow_rows, flow))
        add_flow_btn = QPushButton("+ 添加条件流")
        add_flow_btn.clicked.connect(
            lambda: self._append_flow_row(flows_layout, flow_rows)
        )
        layout.addWidget(flows_box)
        layout.addWidget(add_flow_btn)
        default_target = self._line(parse_next_target(node.get("default_target", "end")))
        default_target.setPlaceholderText("end 或节点索引")
        form = QFormLayout()
        form.addRow("default_target", default_target)
        layout.addLayout(form)
        return panel, {
            "flow_rows": flow_rows,
            "flows_layout": flows_layout,
            "default_target": default_target,
        }

    def _append_flow_row(
        self,
        flows_layout: QVBoxLayout,
        flow_rows: list[dict[str, object]],
    ) -> None:
        device_id, product_id = self._wf_ids()
        flow_rows.append(
            self._add_flow_row(flows_layout, flow_rows, default_flow(device_id, product_id))
        )
        self._sync_envelope_from_form()

    def _add_flow_row(
        self,
        parent_layout: QVBoxLayout,
        flow_rows: list[dict[str, object]],
        flow: dict[str, object],
    ) -> dict[str, object]:
        index = len(flow_rows)
        box = QGroupBox(f"条件流 {index}")
        layout = QVBoxLayout(box)
        guard_conn = self._combo(GUARD_CONN, str(flow.get("guard_conn", "and")))
        layout.addWidget(QLabel("guard_conn"))
        layout.addWidget(guard_conn)

        guards_box = QWidget()
        guards_layout = QVBoxLayout(guards_box)
        guards_layout.setContentsMargins(0, 0, 0, 0)
        guard_rows: list[dict[str, object]] = []
        raw_guards = flow.get("guards")
        guards = (
            [g for g in raw_guards if isinstance(g, dict)]
            if isinstance(raw_guards, list)
            else []
        )
        device_id, product_id = self._wf_ids()
        if not guards:
            guards = [default_guard(device_id, product_id)]
        for guard in guards:
            guard_rows.append(self._add_guard_row(guards_layout, guard_rows, guard))
        add_guard_btn = QPushButton("+ 添加条件")
        add_guard_btn.clicked.connect(
            lambda: self._append_guard_row(guards_layout, guard_rows)
        )
        layout.addWidget(guards_box)
        layout.addWidget(add_guard_btn)

        target_edit = self._line(parse_next_target(flow.get("target", "end")))
        target_edit.setPlaceholderText("命中后跳转：end 或节点索引")
        form = QFormLayout()
        form.addRow("target", target_edit)
        layout.addLayout(form)

        delete_btn = QPushButton("删除条件流")
        delete_btn.clicked.connect(
            lambda: self._remove_flow_row(parent_layout, flow_rows, entry_holder)
        )
        layout.addWidget(delete_btn)

        entry_holder: dict[str, object] = {
            "widget": box,
            "guard_conn": guard_conn,
            "guard_rows": guard_rows,
            "guards_layout": guards_layout,
            "target": target_edit,
        }
        parent_layout.addWidget(box)
        return entry_holder

    def _append_guard_row(
        self,
        guards_layout: QVBoxLayout,
        guard_rows: list[dict[str, object]],
    ) -> None:
        device_id, product_id = self._wf_ids()
        guard_rows.append(
            self._add_guard_row(guards_layout, guard_rows, default_guard(device_id, product_id))
        )
        self._sync_envelope_from_form()

    def _add_guard_row(
        self,
        parent_layout: QVBoxLayout,
        guard_rows: list[dict[str, object]],
        guard: dict[str, object],
    ) -> dict[str, object]:
        device_id, product_id = self._wf_ids()
        box = QGroupBox("条件 (guard)")
        form = QFormLayout(box)
        cmp_combo = self._combo(CMP_OPS, str(guard.get("cmp", "eq")))
        left_sec, left = self._prop_ref_section("left", device_id, product_id, 2, 1)
        right_kind = self._combo(("字面值", "属性引用"), "字面值")

        value_right_sec, value_form = self._wf_section("右操作数（字面值）")
        value_type = self._combo(VALUE_TYPES, "int")
        value_edit = self._line("0")
        value_form.addRow("type", value_type)
        value_form.addRow("value", value_edit)

        ref_right_sec, right_ref = self._prop_ref_section(
            "右操作数（属性引用）", device_id, product_id, 2, 1
        )

        self._fill_prop_widgets(left, guard.get("left"))
        if "right" in guard:
            self._set_combo_value(right_kind, "属性引用")
            self._fill_prop_widgets(right_ref, guard.get("right"))
        else:
            vtype, vtext = parse_typed_value(guard.get("right_value"))
            if vtype in VALUE_TYPES:
                self._set_combo_value(value_type, vtype)
            value_edit.setText(vtext)

        form.addRow("cmp", cmp_combo)
        form.addRow(left_sec)
        form.addRow("右操作数类型", right_kind)
        form.addRow(value_right_sec)
        form.addRow(ref_right_sec)

        delete_btn = QPushButton("删除条件")
        form.addRow(delete_btn)

        entry = {
            "widget": box,
            "cmp": cmp_combo,
            "left": left,
            "right_kind": right_kind,
            "value_type": value_type,
            "value": value_edit,
            "right_ref": right_ref,
            "value_right_sec": value_right_sec,
            "ref_right_sec": ref_right_sec,
        }

        def on_right_kind_changed(kind: str) -> None:
            self._wf_set_section_visible(value_right_sec, kind == "字面值")
            self._wf_set_section_visible(ref_right_sec, kind == "属性引用")
            self._wf_refresh_layout()

        right_kind.currentTextChanged.connect(on_right_kind_changed)
        on_right_kind_changed(right_kind.currentText())

        delete_btn.clicked.connect(
            lambda: self._remove_guard_row(parent_layout, guard_rows, entry)
        )
        parent_layout.addWidget(box)
        return entry

    def _remove_guard_row(
        self,
        parent_layout: QVBoxLayout,
        guard_rows: list[dict[str, object]],
        entry: dict[str, object],
    ) -> None:
        widget = entry.get("widget")
        if widget is not None:
            parent_layout.removeWidget(widget)
            widget.deleteLater()
        if entry in guard_rows:
            guard_rows.remove(entry)
        self._sync_envelope_from_form()

    def _remove_flow_row(
        self,
        parent_layout: QVBoxLayout,
        flow_rows: list[dict[str, object]],
        entry: dict[str, object],
    ) -> None:
        widget = entry.get("widget")
        if widget is not None:
            parent_layout.removeWidget(widget)
            widget.deleteLater()
        if entry in flow_rows:
            flow_rows.remove(entry)
        for idx, row in enumerate(flow_rows):
            box = row.get("widget")
            if isinstance(box, QGroupBox):
                box.setTitle(f"条件流 {idx}")
        self._sync_envelope_from_form()

    def _guard_from_row(self, row: dict[str, object]) -> dict[str, object]:
        guard: dict[str, object] = {
            "cmp": row["cmp"].currentText(),
            "left": self._read_prop_widgets(row["left"]),
        }
        if row["right_kind"].currentText() == "属性引用":
            guard["right"] = self._read_prop_widgets(row["right_ref"])
        else:
            guard["right_value"] = typed_value(
                row["value_type"].currentText(),
                coerce_typed_value(
                    row["value_type"].currentText(), row["value"].text()
                ),
            )
        return guard

    def _flow_from_row(self, row: dict[str, object]) -> dict[str, object]:
        guards: list[dict[str, object]] = []
        for guard_row in row.get("guard_rows", []):
            if isinstance(guard_row, dict):
                guards.append(self._guard_from_row(guard_row))
        return {
            "guard_conn": row["guard_conn"].currentText(),
            "guards": guards,
            "target": format_next_target(row["target"].text()),
        }

    def _remove_node_row(self, entry: dict[str, object]) -> None:
        widget = entry.get("widget")
        if widget is not None:
            self._wf_nodes_layout.removeWidget(widget)
            widget.deleteLater()
        if entry in self._wf_node_rows:
            self._wf_node_rows.remove(entry)
        for idx, row in enumerate(self._wf_node_rows):
            box = row.get("widget")
            if isinstance(box, QGroupBox):
                box.setTitle(f"节点 {idx}")
        self._wf_refresh_layout()
        self._sync_envelope_from_form()

    def _read_target_from_row(self, row: dict[str, object]) -> dict[str, object]:
        device_ids = [
            part.strip() for part in row["device_ids"].text().split(",") if part.strip()
        ]
        return write_target(
            device_ids,
            row["target_product"].text().strip(),
            int(row["target_siid"].value()),
            int(row["target_piid"].value()),
        )

    def _action_from_row(self, row: dict[str, object]) -> dict[str, object]:
        op = row["op"].currentText()
        action: dict[str, object] = {"op": op, "target": self._read_target_from_row(row)}
        if op == "write":
            action["value"] = typed_value(
                row["value_type"].currentText(),
                coerce_typed_value(row["value_type"].currentText(), row["value"].text()),
            )
        elif op == "add":
            action["delta"] = typed_value(
                row["value_type"].currentText(),
                coerce_typed_value(row["value_type"].currentText(), row["value"].text()),
            )
        elif op == "copy":
            action["source"] = self._read_prop_widgets(row["source"])
        elif op in ("add_reg", "min", "max"):
            action["left"] = self._read_prop_widgets(row["left"])
            action["right"] = self._read_prop_widgets(row["right"])
        elif op == "clamp":
            action["min"] = self._read_prop_widgets(row["min"])
            action["max"] = self._read_prop_widgets(row["max"])
        return action

    def _node_from_row(self, entry: dict[str, object]) -> dict[str, object]:
        node_type = entry["type_combo"].currentText()
        if node_type == "exclusive_gw":
            gw_fields = entry["gw_fields"]
            flows: list[dict[str, object]] = []
            for flow_row in gw_fields.get("flow_rows", []):
                if isinstance(flow_row, dict):
                    flows.append(self._flow_from_row(flow_row))
            return {
                "type": "exclusive_gw",
                "flows": flows,
                "default_target": format_next_target(gw_fields["default_target"].text()),
            }
        service_fields = entry["service_fields"]
        actions: list[dict[str, object]] = []
        for row in service_fields["action_rows"]:
            if isinstance(row, dict):
                actions.append(self._action_from_row(row))
        return {
            "type": "service",
            "actions": actions,
            "next": format_next_target(service_fields["next_edit"].text()),
        }

    def _nodes_from_editor(self) -> list[dict[str, object]]:
        if not self._wf_node_rows:
            device_id, product_id = self._wf_ids()
            return default_nodes(device_id, product_id)
        return [self._node_from_row(entry) for entry in self._wf_node_rows]

    def _apply_nodes_to_editor(self, nodes: object) -> None:
        self._clear_node_rows()
        if not isinstance(nodes, list) or not nodes:
            device_id, product_id = self._wf_ids()
            nodes = default_nodes(device_id, product_id)
        for node in nodes[:16]:
            if isinstance(node, dict):
                self._add_service_node(node)

    def _workflow_parts_from_form(self) -> tuple[dict[str, object], list[dict[str, object]]]:
        return self._trigger_from_editor(), self._nodes_from_editor()

    def _apply_workflow_from_json(self, params: object) -> None:
        if not isinstance(params, dict):
            return
        if "trigger" in params:
            self._apply_trigger_to_editor(params["trigger"])
        if "nodes" in params:
            self._apply_nodes_to_editor(params["nodes"])
