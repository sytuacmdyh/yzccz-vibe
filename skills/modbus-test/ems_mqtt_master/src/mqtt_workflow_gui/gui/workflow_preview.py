"""Read-only workflow graph preview for JSON-RPC workflow envelopes."""
from __future__ import annotations

from dataclasses import dataclass
import json
from math import atan2, cos, pi, sin
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QRegion,
)
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class PreviewNode:
    """A renderable workflow node with a stable identifier."""

    key: str
    kind: str
    title: str
    detail: str = ""
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreviewEdge:
    """A directed connection between two preview nodes."""

    source: str
    target: str
    label: str = ""
    kind: str = "normal"


@dataclass(frozen=True)
class WorkflowPreviewModel:
    """UI-independent graph extracted from a workflow envelope."""

    title: str
    nodes: tuple[PreviewNode, ...]
    edges: tuple[PreviewEdge, ...]


@dataclass(frozen=True)
class PreviewParseResult:
    model: WorkflowPreviewModel | None
    message: str


def _short_value(value: object) -> str:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value)
        return text if len(text) <= 16 else f"{text[:13]}..."
    return "属性"


def _compact_text(text: str, limit: int = 25) -> str:
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def _trigger_detail(trigger: dict[str, Any]) -> str:
    kind = str(trigger.get("type", ""))
    if kind == "periodic":
        return f"{trigger.get('period_ticks', '?')} ticks"
    if kind == "time_of_day":
        hour = trigger.get("hour", "?")
        minute = trigger.get("minute", "?")
        days = trigger.get("days_of_week")
        suffix = f"  {','.join(map(str, days))}" if isinstance(days, list) and days else ""
        return _compact_text(f"{hour}:{str(minute).zfill(2)}{suffix}")
    if kind == "property_change":
        return "属性变化"
    if kind == "property_sustain":
        cmp_name = trigger.get("cmp", "?")
        value = _short_value(trigger.get("value"))
        return f"{cmp_name} {value} / {trigger.get('sustain_ticks', '?')} ticks"
    if kind == "env_change":
        param = str(trigger.get("env_param", "temperature"))
        param_cn = {"temperature": "温度", "humidity": "湿度", "pm25": "PM2.5"}.get(param, param)
        if param == "temperature":
            return _compact_text(f"{param_cn} {trigger.get('env_cmp', 'gt')} {trigger.get('env_value', '?')}℃")
        range_name = trigger.get("env_range")
        if isinstance(range_name, str) and range_name:
            labels = {
                "dry": "干燥(<40)",
                "comfort": "舒适(40~70)",
                "humid": "潮湿(≥70)",
                "good": "优(<35)",
                "moderate": "良(35~75)",
                "exceed": "超标(>75)",
            }
            return _compact_text(f"{param_cn} {labels.get(range_name, range_name)}")
        if "env_min" in trigger and "env_max" in trigger:
            return _compact_text(f"{param_cn} [{trigger.get('env_min', '?')}, {trigger.get('env_max', '?')}]")
        return _compact_text(f"{param_cn} 边沿触发")
    if kind == "manual":
        return "仅手动执行"
    return ""


def _action_detail(node: dict[str, Any]) -> str:
    actions = node.get("actions")
    if not isinstance(actions, list):
        return "无动作"
    ops = [str(action.get("op", "?")) for action in actions if isinstance(action, dict)]
    if not ops:
        return "无动作"
    shown = ops[:3]
    suffix = f" +{len(ops) - 3}" if len(ops) > 3 else ""
    return _compact_text(" + ".join(shown) + suffix)


def _action_lines(node: dict[str, Any]) -> tuple[str, ...]:
    actions = node.get("actions")
    if not isinstance(actions, list):
        return ()
    lines: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        op = str(action.get("op", "?"))
        if op == "write":
            lines.append(f"write {_short_value(action.get('value'))}")
        elif op == "add":
            lines.append(f"add += {_short_value(action.get('delta'))}")
        elif op == "copy":
            lines.append("copy")
        elif op == "clamp":
            lines.append("clamp")
        elif op in ("add_reg", "min", "max"):
            lines.append(op)
        else:
            lines.append(op)
    if not lines:
        return ()
    return tuple(_compact_text(line) for line in lines)


def _guard_summary(flow: dict[str, Any]) -> str:
    guards = flow.get("guards")
    if not isinstance(guards, list) or not guards:
        return "条件"
    parts: list[str] = []
    for guard in guards[:2]:
        if not isinstance(guard, dict):
            continue
        cmp_name = str(guard.get("cmp", "?"))
        right = guard.get("right_value", guard.get("right"))
        parts.append(f"{cmp_name} {_short_value(right)}")
    if not parts:
        return "条件"
    connector = " & " if flow.get("guard_conn", "and") == "and" else " | "
    label = connector.join(parts)
    return _compact_text(f"{label} ..." if len(guards) > 2 else label, 20)


def _target_key(target: object, node_count: int) -> str | None:
    if target == "end":
        return "end"
    if isinstance(target, int) and not isinstance(target, bool) and 0 <= target < node_count:
        return f"node:{target}"
    return None


def _rounded_orthogonal_path(
    points: list[QPointF], radius: float = 10.0
) -> QPainterPath:
    """Build a horizontal/vertical polyline with short rounded corners."""
    clean: list[QPointF] = []
    for point in points:
        if not clean or point != clean[-1]:
            clean.append(point)
    path = QPainterPath(clean[0])
    if len(clean) == 1:
        return path

    for index in range(1, len(clean) - 1):
        previous, corner, following = clean[index - 1 : index + 2]
        incoming = abs(corner.x() - previous.x()) + abs(corner.y() - previous.y())
        outgoing = abs(following.x() - corner.x()) + abs(following.y() - corner.y())
        corner_radius = min(radius, incoming / 2, outgoing / 2)
        if corner_radius <= 0:
            path.lineTo(corner)
            continue

        before = QPointF(corner)
        after = QPointF(corner)
        if previous.x() != corner.x():
            before.setX(corner.x() - corner_radius if previous.x() < corner.x() else corner.x() + corner_radius)
        else:
            before.setY(corner.y() - corner_radius if previous.y() < corner.y() else corner.y() + corner_radius)
        if following.x() != corner.x():
            after.setX(corner.x() + corner_radius if following.x() > corner.x() else corner.x() - corner_radius)
        else:
            after.setY(corner.y() + corner_radius if following.y() > corner.y() else corner.y() - corner_radius)
        path.lineTo(before)
        path.quadTo(corner, after)

    path.lineTo(clean[-1])
    return path


def parse_workflow_preview(text: str) -> PreviewParseResult:
    """Parse the current envelope text into a compact graph model."""
    if not text.strip():
        return PreviewParseResult(None, "暂无 JSON")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return PreviewParseResult(None, "JSON 格式无效")
    if not isinstance(envelope, dict):
        return PreviewParseResult(None, "JSON 顶层必须是对象")
    if envelope.get("method") != "sync_workflow_config":
        return PreviewParseResult(None, "当前消息不是工作流同步")
    params = envelope.get("params")
    if not isinstance(params, dict):
        return PreviewParseResult(None, "缺少工作流参数")
    if params.get("op") != "upsert":
        return PreviewParseResult(None, "删除操作没有可预览流程")
    trigger = params.get("trigger")
    raw_nodes = params.get("nodes")
    if not isinstance(trigger, dict) or not str(trigger.get("type", "")).strip():
        return PreviewParseResult(None, "缺少有效触发器")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return PreviewParseResult(None, "工作流没有节点")
    if any(not isinstance(node, dict) for node in raw_nodes):
        return PreviewParseResult(None, "工作流节点格式无效")

    nodes_data: list[dict[str, Any]] = raw_nodes
    graph_nodes: list[PreviewNode] = [
        PreviewNode(
            "trigger",
            "trigger",
            f"触发器 · {trigger.get('type')}",
            _trigger_detail(trigger),
        )
    ]
    graph_edges: list[PreviewEdge] = [PreviewEdge("trigger", "node:0")]
    for index, node in enumerate(nodes_data):
        kind = str(node.get("type", "unknown"))
        if kind == "service":
            detail = _action_detail(node)
            lines = _action_lines(node)
            title = f"节点 {index} · 服务"
        elif kind == "exclusive_gw":
            flows = node.get("flows")
            count = len(flows) if isinstance(flows, list) else 0
            detail = f"{count} 个条件分支"
            lines = ()
            title = f"节点 {index} · 条件网关"
        else:
            detail = kind
            lines = ()
            title = f"节点 {index} · 未知类型"
        graph_nodes.append(
            PreviewNode(f"node:{index}", kind, title, detail, lines)
        )

    graph_nodes.append(PreviewNode("end", "end", "结束"))
    invalid_index = 0

    def append_edge(source: str, target: object, label: str = "", kind: str = "normal") -> None:
        nonlocal invalid_index
        target_key = _target_key(target, len(nodes_data))
        if target_key is None:
            target_key = f"invalid:{invalid_index}"
            invalid_index += 1
            graph_nodes.append(
                PreviewNode(target_key, "invalid", "无效目标", _short_value(target))
            )
            kind = "invalid"
        graph_edges.append(PreviewEdge(source, target_key, label, kind))

    for index, node in enumerate(nodes_data):
        source = f"node:{index}"
        if node.get("type") == "exclusive_gw":
            flows = node.get("flows")
            if isinstance(flows, list):
                for flow in flows:
                    if isinstance(flow, dict):
                        append_edge(source, flow.get("target"), _guard_summary(flow), "branch")
            append_edge(source, node.get("default_target"), "默认", "default")
        else:
            append_edge(source, node.get("next"))

    name = str(params.get("name", "")).strip()
    flow_id = params.get("flow_id", "?")
    title = name or f"工作流 {flow_id}"
    return PreviewParseResult(
        WorkflowPreviewModel(title, tuple(graph_nodes), tuple(graph_edges)), ""
    )


class WorkflowGraphView(QGraphicsView):
    """A compact read-only graph view with optional interactive zoom."""

    NODE_W = 150.0
    NODE_H = 62.0
    STEP_X = 195.0
    STEP_X_MIN = NODE_W + 20.0
    STEP_X_MAX = 420.0
    ROW_GAP = 48.0
    ROW_GAP_MIN = 20.0
    ROW_GAP_MAX = 120.0
    LAYOUT_MARGIN = 48.0
    ROUTE_CLEARANCE = 24.0
    CORNER_RADIUS = 3.0
    LINE_SPACING = 15.0
    TITLE_TOP = 9.0
    LINE_TOP = 30.0
    PAD_BOTTOM = 10.0
    ACTION_INSET = 8.0
    ACTION_BOX_H = 22.0
    ACTION_GAP = 6.0
    HEADER_H = 26.0
    SERVICE_FILL = "#eaf7ef"
    SERVICE_BORDER = "#2f8a57"
    ACTION_FILL = "#ffffff"
    ACTION_BORDER = "#e05c5c"

    def __init__(
        self,
        interactive: bool = False,
        fill_view: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._interactive = interactive
        self._fill_view = fill_view
        self._model: WorkflowPreviewModel | None = None
        self._scene = QGraphicsScene(self)
        self._heights: dict[str, float] = {}
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QColor("#2D2D2D"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QGraphicsView { background: #2D2D2D; border-radius: 3px; }"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if interactive:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    @property
    def model(self) -> WorkflowPreviewModel | None:
        return self._model

    def set_model(self, model: WorkflowPreviewModel | None) -> None:
        self._model = model
        self.relayout_graph()

    def relayout_graph(self) -> None:
        self._scene.clear()
        if self._model is None:
            return
        self._draw_model(self._model)
        self._fit_scene()

    def fit_graph(self) -> None:
        self.relayout_graph()

    def _fit_scene(self) -> None:
        rect = self._scene.itemsBoundingRect().adjusted(-24, -24, 24, 24)
        if not rect.isValid() or rect.isEmpty():
            return
        self.setSceneRect(rect)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_by(self, factor: float) -> None:
        current = self.transform().m11()
        target = current * factor
        if 0.12 <= target <= 6.0:
            self.scale(factor, factor)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not self._interactive:
            event.ignore()
            return
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        clip = QPainterPath()
        clip.addRoundedRect(
            QRectF(self.viewport().rect()), self.CORNER_RADIUS, self.CORNER_RADIUS
        )
        self.viewport().setMask(QRegion(clip.toFillPolygon().toPolygon()))
        if self._model is not None and not self._interactive:
            if self._fill_view:
                self.relayout_graph()
            else:
                self._fit_scene()

    def _layout_spacing(
        self,
        leaf_count: int,
        row_heights: dict[int, float],
        end_height: float,
        has_end: bool,
    ) -> tuple[float, float]:
        step_x = self.STEP_X
        row_gap = self.ROW_GAP
        if not (self._fill_view or self._interactive):
            return step_x, row_gap

        viewport_size = self.viewport().size()
        if (
            not viewport_size.isValid()
            or viewport_size.width() <= 0
            or viewport_size.height() <= 0
        ):
            return step_x, row_gap

        target_ratio = viewport_size.width() / viewport_size.height()
        horizontal_slots = max(leaf_count - 1, 0)
        vertical_gaps = max(len(row_heights) - 1, 0) + (1 if has_end else 0)
        fixed_width = self.NODE_W + self.LAYOUT_MARGIN
        fixed_height = sum(row_heights.values()) + self.LAYOUT_MARGIN
        if has_end:
            fixed_height += end_height

        def clamp(value: float, minimum: float, maximum: float) -> float:
            return max(minimum, min(value, maximum))

        for _ in range(2):
            if horizontal_slots:
                desired_step = (
                    target_ratio * (fixed_height + vertical_gaps * row_gap)
                    - fixed_width
                ) / horizontal_slots
                step_x = clamp(desired_step, self.STEP_X_MIN, self.STEP_X_MAX)
            if vertical_gaps:
                desired_gap = (
                    (fixed_width + horizontal_slots * step_x) / target_ratio
                    - fixed_height
                ) / vertical_gaps
                row_gap = clamp(desired_gap, self.ROW_GAP_MIN, self.ROW_GAP_MAX)
        return step_x, row_gap

    def _positions(self, model: WorkflowPreviewModel) -> dict[str, QPointF]:
        node_keys = {node.key for node in model.nodes}
        tree_keys = node_keys - {"end"}
        children: dict[str, list[str]] = {key: [] for key in tree_keys}
        parent: dict[str, str] = {}
        depth = {"trigger": 0}
        queue = ["trigger"]

        # Build a spanning tree. Loop, back and join edges remain visible but do not
        # influence hierarchy, so they cannot flatten or destabilize the layout.
        while queue:
            source = queue.pop(0)
            for edge in model.edges:
                target = edge.target
                if edge.source != source or target not in tree_keys or target == source:
                    continue
                if target in depth:
                    continue
                parent[target] = source
                depth[target] = depth[source] + 1
                children[source].append(target)
                queue.append(target)

        # Keep malformed or disconnected nodes visible as additional root branches.
        detached = [
            node.key
            for node in model.nodes
            if node.key in tree_keys and node.key not in depth
        ]
        for key in detached:
            parent[key] = "trigger"
            depth[key] = 1
            children["trigger"].append(key)

        leaf_slot = 0
        x_units: dict[str, float] = {}

        def place_subtree(key: str) -> float:
            nonlocal leaf_slot
            placed_children = children.get(key, [])
            if not placed_children:
                x_units[key] = float(leaf_slot)
                leaf_slot += 1
                return x_units[key]
            child_x = [place_subtree(child) for child in placed_children]
            x_units[key] = (child_x[0] + child_x[-1]) / 2
            return x_units[key]

        place_subtree("trigger")
        center = x_units["trigger"]

        heights = {
            node.key: self._node_height(node)
            for node in model.nodes
            if node.key in depth or node.key == "end"
        }
        row_gaps: dict[int, float] = {0: 0.0}
        max_depth = max(depth.values(), default=0)
        row_height: dict[int, float] = {}
        for row in range(max_depth + 1):
            row_height[row] = max(
                (heights[key] for key, d in depth.items() if d == row),
                default=self.NODE_H,
            )
        has_end = "end" in node_keys
        step_x, row_gap = self._layout_spacing(
            leaf_slot,
            row_height,
            heights.get("end", self.NODE_H),
            has_end,
        )
        y_offset = 0.0
        for row in range(max_depth + 1):
            row_gaps[row] = y_offset
            y_offset += row_height[row] + row_gap

        positions = {
            key: QPointF((x_units[key] - center) * step_x, row_gaps[depth[key]])
            for key in depth
        }

        if has_end:
            positions["end"] = QPointF(0.0, y_offset)
        return positions

    def _service_action_lines(self, node: PreviewNode) -> tuple[str, ...]:
        if node.lines:
            return node.lines
        if node.detail:
            return (node.detail,)
        return ("无动作",)

    def _node_height(self, node: PreviewNode) -> float:
        if node.kind == "service":
            count = len(self._service_action_lines(node))
            return (
                self.HEADER_H
                + self.ACTION_INSET
                + count * self.ACTION_BOX_H
                + max(count - 1, 0) * self.ACTION_GAP
                + self.ACTION_INSET
            )
        count = len(node.lines) if node.lines else (1 if node.detail else 0)
        return self.LINE_TOP + count * self.LINE_SPACING + self.PAD_BOTTOM

    def _draw_model(self, model: WorkflowPreviewModel) -> None:
        positions = self._positions(model)
        self._heights = {node.key: self._node_height(node) for node in model.nodes}
        self._route_right = max(
            (pos.x() + self.NODE_W for pos in positions.values()), default=self.NODE_W
        ) + 55.0
        for edge_index, edge in enumerate(model.edges):
            self._draw_edge(edge, edge_index, positions)
        for node in model.nodes:
            pos = positions.get(node.key)
            if pos is not None:
                self._draw_node(node, pos)

    def _draw_node(self, node: PreviewNode, pos: QPointF) -> None:
        if node.kind == "service":
            self._draw_service_node(node, pos)
            return
        colors = {
            "trigger": ("#e8f2ff", "#3478c9"),
            "exclusive_gw": ("#fff5dc", "#c18413"),
            "end": ("#edf0f4", "#697386"),
            "invalid": ("#fff0f0", "#c43d3d"),
        }
        fill, border = colors.get(node.kind, ("#fff0f0", "#c43d3d"))
        height = self._heights.get(node.key, self.NODE_H)
        rect = QRectF(pos.x(), pos.y(), self.NODE_W, height)
        shape = QPainterPath()
        shape.addRoundedRect(rect, 6, 6)
        item = self._scene.addPath(
            shape, QPen(QColor(border), 1.6), QBrush(QColor(fill))
        )
        item.setZValue(2)
        title = self._scene.addSimpleText(node.title)
        title.setBrush(QColor("#20242b"))
        title.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
        title.setPos(pos.x() + 10, pos.y() + self.TITLE_TOP)
        title.setZValue(3)
        lines = node.lines if node.lines else (tuple([node.detail]) if node.detail else ())
        for index, line in enumerate(lines):
            detail = self._scene.addSimpleText(line)
            detail.setBrush(QColor("#5c6470"))
            detail.setFont(QFont("Microsoft YaHei", 8))
            detail.setPos(pos.x() + 10, pos.y() + self.LINE_TOP + index * self.LINE_SPACING)
            detail.setZValue(3)

    def _draw_service_node(self, node: PreviewNode, pos: QPointF) -> None:
        height = self._heights.get(node.key, self.NODE_H)
        outer = QRectF(pos.x(), pos.y(), self.NODE_W, height)
        shape = QPainterPath()
        shape.addRoundedRect(outer, 6, 6)
        frame = self._scene.addPath(
            shape,
            QPen(QColor(self.SERVICE_BORDER), 1.6),
            QBrush(QColor(self.SERVICE_FILL)),
        )
        frame.setZValue(2)
        title = self._scene.addSimpleText(node.title)
        title.setBrush(QColor("#20242b"))
        title.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
        title.setPos(pos.x() + self.ACTION_INSET, pos.y() + 6)
        title.setZValue(3)

        lines = self._service_action_lines(node)
        box_x = pos.x() + self.ACTION_INSET
        box_w = self.NODE_W - 2 * self.ACTION_INSET
        box_y = pos.y() + self.HEADER_H
        for index, line in enumerate(lines):
            box = QRectF(
                box_x,
                box_y + index * (self.ACTION_BOX_H + self.ACTION_GAP),
                box_w,
                self.ACTION_BOX_H,
            )
            inner = QPainterPath()
            inner.addRoundedRect(box, 3, 3)
            child = self._scene.addPath(
                inner,
                QPen(QColor(self.ACTION_BORDER), 1.4),
                QBrush(QColor(self.ACTION_FILL)),
            )
            child.setZValue(2)
            detail = self._scene.addSimpleText(line)
            detail.setBrush(QColor("#5c6470"))
            detail.setFont(QFont("Microsoft YaHei", 8))
            text_rect = detail.boundingRect()
            detail.setPos(
                box.x() + 6,
                box.y() + max((self.ACTION_BOX_H - text_rect.height()) / 2, 0),
            )
            detail.setZValue(3)

    def _row_lane_below(
        self, row_y: float, positions: dict[str, QPointF]
    ) -> float:
        row_bottom = max(
            (
                pos.y() + self._heights.get(key, self.NODE_H)
                for key, pos in positions.items()
                if abs(pos.y() - row_y) < 1
            ),
            default=row_y + self.NODE_H,
        )
        next_row = min(
            (pos.y() for pos in positions.values() if pos.y() > row_bottom),
            default=row_bottom + self.ROUTE_CLEARANCE * 2,
        )
        return (row_bottom + next_row) / 2

    def _row_lane_above(
        self, row_y: float, positions: dict[str, QPointF]
    ) -> float:
        previous_bottom = max(
            (
                pos.y() + self._heights.get(key, self.NODE_H)
                for key, pos in positions.items()
                if pos.y() + self._heights.get(key, self.NODE_H) < row_y
            ),
            default=row_y - self.ROUTE_CLEARANCE * 2,
        )
        return (previous_bottom + row_y) / 2

    def _path_crosses_unrelated_node(
        self,
        path: QPainterPath,
        edge: PreviewEdge,
        positions: dict[str, QPointF],
    ) -> bool:
        stroker = QPainterPathStroker()
        stroker.setWidth(1.5)
        stroke = stroker.createStroke(path)
        for key, pos in positions.items():
            if key in (edge.source, edge.target):
                continue
            rect = QRectF(
                pos.x(),
                pos.y(),
                self.NODE_W,
                self._heights.get(key, self.NODE_H),
            )
            obstacle = QPainterPath()
            obstacle.addRect(rect)
            if stroke.intersects(obstacle):
                return True
        return False

    def _draw_edge(
        self, edge: PreviewEdge, edge_index: int, positions: dict[str, QPointF]
    ) -> None:
        source = positions.get(edge.source)
        target = positions.get(edge.target)
        if source is None or target is None:
            return
        source_h = self._heights.get(edge.source, self.NODE_H)
        target_h = self._heights.get(edge.target, self.NODE_H)
        self_loop = edge.source == edge.target
        forward = target.y() > source.y()
        same_level = abs(target.y() - source.y()) < 1
        if same_level and not self_loop:
            left_to_right = target.x() > source.x()
            start_x = source.x() + self.NODE_W if left_to_right else source.x()
            end_x = target.x() if left_to_right else target.x() + self.NODE_W
            start = QPointF(start_x, source.y() + source_h / 2)
            end = QPointF(end_x, target.y() + target_h / 2)
        elif forward:
            start = QPointF(source.x() + self.NODE_W / 2, source.y() + source_h)
            end = QPointF(target.x() + self.NODE_W / 2, target.y())
        else:
            start = QPointF(source.x() + self.NODE_W / 2, source.y() + source_h)
            end = QPointF(target.x() + self.NODE_W / 2, target.y())
        color = QColor("#ffffff")
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if edge.kind == "default":
            pen.setStyle(Qt.PenStyle.DashLine)
        if self_loop:
            lane_x = self._route_right + (edge_index % 4) * 24.0
            source_lane_y = self._row_lane_below(source.y(), positions)
            target_lane_y = self._row_lane_above(target.y(), positions)
            points = [
                start,
                QPointF(start.x(), source_lane_y),
                QPointF(lane_x, source_lane_y),
                QPointF(lane_x, target_lane_y),
                QPointF(end.x(), target_lane_y),
                end,
            ]
        elif same_level:
            points = [start, end]
            candidate = _rounded_orthogonal_path(points)
            if self._path_crosses_unrelated_node(candidate, edge, positions):
                start = QPointF(source.x() + self.NODE_W / 2, source.y() + source_h)
                end = QPointF(target.x() + self.NODE_W / 2, target.y() + target_h)
                lane_y = self._row_lane_below(source.y(), positions)
                points = [
                    start,
                    QPointF(start.x(), lane_y),
                    QPointF(end.x(), lane_y),
                    end,
                ]
        elif forward:
            mid_y = (start.y() + end.y()) / 2
            points = [
                start,
                QPointF(start.x(), mid_y),
                QPointF(end.x(), mid_y),
                end,
            ]
        else:
            lane_x = self._route_right + (edge_index % 4) * 24.0
            source_lane_y = self._row_lane_below(source.y(), positions)
            target_lane_y = self._row_lane_above(target.y(), positions)
            points = [
                start,
                QPointF(start.x(), source_lane_y),
                QPointF(lane_x, source_lane_y),
                QPointF(lane_x, target_lane_y),
                QPointF(end.x(), target_lane_y),
                end,
            ]
        path = _rounded_orthogonal_path(points)
        path_item = QGraphicsPathItem(path)
        path_item.setPen(pen)
        path_item.setZValue(0)
        self._scene.addItem(path_item)

        arrow_tip = path.pointAtPercent(0.99)
        tangent = arrow_tip - path.pointAtPercent(0.94)
        angle = atan2(tangent.y(), tangent.x())
        arrow_size = 8.0
        p1 = arrow_tip - QPointF(
            cos(angle - pi / 6) * arrow_size, sin(angle - pi / 6) * arrow_size
        )
        p2 = arrow_tip - QPointF(
            cos(angle + pi / 6) * arrow_size, sin(angle + pi / 6) * arrow_size
        )
        arrow = QGraphicsPolygonItem(QPolygonF([arrow_tip, p1, p2]))
        arrow.setPen(QPen(color))
        arrow.setBrush(QBrush(color))
        arrow.setZValue(1)
        self._scene.addItem(arrow)

        if edge.label:
            label = QGraphicsSimpleTextItem(edge.label)
            label.setFont(QFont("Microsoft YaHei", 8))
            label.setBrush(color)
            midpoint = path.pointAtPercent(0.5)
            bounds = label.boundingRect()
            label.setPos(midpoint.x() - bounds.width() / 2, midpoint.y() - bounds.height() - 4)
            label.setZValue(1)
            self._scene.addItem(label)


class WorkflowPreviewDialog(QDialog):
    def __init__(self, model: WorkflowPreviewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"完整预览 - {model.title}")
        self.resize(1000, 620)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addStretch()
        zoom_out = QPushButton("缩小")
        zoom_in = QPushButton("放大")
        fit = QPushButton("适应窗口")
        controls.addWidget(zoom_out)
        controls.addWidget(zoom_in)
        controls.addWidget(fit)
        layout.addLayout(controls)
        self.graph_view = WorkflowGraphView(interactive=True)
        layout.addWidget(self.graph_view, 1)
        zoom_out.clicked.connect(lambda: self.graph_view.zoom_by(1 / 1.2))
        zoom_in.clicked.connect(lambda: self.graph_view.zoom_by(1.2))
        fit.clicked.connect(self.graph_view.fit_graph)
        self.graph_view.set_model(model)
        QTimer.singleShot(0, self.graph_view.fit_graph)


class WorkflowPreviewPanel(QGroupBox):
    """Left-column preview panel driven by the current envelope text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("工作流预览", parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(220)
        self.setMaximumHeight(330)
        self._model: WorkflowPreviewModel | None = None
        self._pending_text = ""
        self._dialog: WorkflowPreviewDialog | None = None
        layout = QVBoxLayout(self)
        preview_stack = QStackedLayout()
        preview_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.graph_view = WorkflowGraphView(fill_view=True)
        preview_stack.addWidget(self.graph_view)
        self.empty_label = QLabel("暂无 JSON")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(
            "color: #d1d6de; background: #2D2D2D; border-radius: 3px; padding: 12px;"
        )
        self.empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        preview_stack.addWidget(self.empty_label)
        layout.addLayout(preview_stack, 1)
        self.full_preview_button = QPushButton("完整预览")
        self.full_preview_button.setEnabled(False)
        layout.addWidget(self.full_preview_button)
        self.full_preview_button.clicked.connect(self.open_full_preview)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh_now)

    @property
    def model(self) -> WorkflowPreviewModel | None:
        return self._model

    def schedule_refresh(self, text: str) -> None:
        self._pending_text = text
        self._refresh_timer.start()

    def refresh_now(self) -> None:
        self._refresh_timer.stop()
        result = parse_workflow_preview(self._pending_text)
        self._model = result.model
        self.graph_view.set_model(result.model)
        self.empty_label.setText(result.message)
        self.empty_label.setVisible(result.model is None)
        if result.model is None:
            self.empty_label.raise_()
        else:
            self.graph_view.raise_()
        self.full_preview_button.setEnabled(result.model is not None)

    def open_full_preview(self) -> None:
        if self._model is None:
            return
        dialog = WorkflowPreviewDialog(self._model, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda *_: setattr(self, "_dialog", None))
        self._dialog = dialog
        dialog.show()
