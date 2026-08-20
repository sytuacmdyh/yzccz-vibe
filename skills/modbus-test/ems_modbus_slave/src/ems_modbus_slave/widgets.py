from __future__ import annotations

from typing import List

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QStyledItemDelegate

from .capture import CaptureTracker
from .register_model import RegisterBank


class RegisterTableModel(QAbstractTableModel):
    HEADERS = ["Type", "Address", "Name", "Access", "Value", "Description", "Capture"]

    def __init__(
        self,
        bank: RegisterBank,
        capture_tracker: CaptureTracker | None = None,
        slave_id: int | None = None,
    ) -> None:
        super().__init__()
        self.bank = bank
        self.capture_tracker = capture_tracker
        self.view_slave_id = slave_id
        self._rows = self.bank.snapshot(self.view_slave_id)
        self._rebuild_row_index()

    def reload(self) -> List[int]:
        new_rows = self.bank.snapshot(self.view_slave_id)
        old_keys = [(row["kind"], row["address"]) for row in self._rows]
        new_keys = [(row["kind"], row["address"]) for row in new_rows]
        if old_keys != new_keys:
            self.beginResetModel()
            self._rows = new_rows
            self._rebuild_row_index()
            self.endResetModel()
            return list(range(len(new_rows)))

        changed_rows = [
            row_index
            for row_index, (old_row, new_row) in enumerate(zip(self._rows, new_rows))
            if old_row != new_row
        ]
        self._rows = new_rows
        for first_row, last_row in self._contiguous_ranges(changed_rows):
            self.dataChanged.emit(
                self.index(first_row, 4),
                self.index(last_row, 5),
                [Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole],
            )
        return changed_rows

    def set_view_slave_id(self, slave_id: int) -> None:
        if self.view_slave_id == slave_id:
            return
        self.view_slave_id = slave_id
        self.reload()

    def _rebuild_row_index(self) -> None:
        self._row_by_key = {
            (str(row["kind"]), int(row["address"])): index
            for index, row in enumerate(self._rows)
        }

    @staticmethod
    def _contiguous_ranges(rows: List[int]) -> List[tuple[int, int]]:
        if not rows:
            return []
        ranges: List[tuple[int, int]] = []
        first = previous = rows[0]
        for row in rows[1:]:
            if row != previous + 1:
                ranges.append((first, previous))
                first = row
            previous = row
        ranges.append((first, previous))
        return ranges

    def row_for(self, kind: str, address: int) -> int:
        return self._row_by_key[(kind, address)]

    def point_at(self, row: int) -> tuple[str, int, str]:
        point = self._rows[row]
        return str(point["kind"]), int(point["address"]), str(point["name"])

    def _is_tracked(self, row: dict[str, object]) -> bool:
        return bool(self.capture_tracker and self.capture_tracker.is_enabled(str(row["kind"]), int(row["address"])))

    def toggle_tracking(self, index: QModelIndex) -> bool:
        if not index.isValid() or index.column() != 6 or self.capture_tracker is None:
            return False
        row = self._rows[index.row()]
        self.capture_tracker.toggle(str(row["kind"]), int(row["address"]))
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.ToolTipRole])
        return True

    def refresh_tracking(self) -> None:
        if self.rowCount() > 0:
            self.dataChanged.emit(
                self.index(0, 6),
                self.index(self.rowCount() - 1, 6),
                [Qt.DisplayRole, Qt.ToolTipRole],
            )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            if index.column() == 0:
                return row["kind"]
            if index.column() == 1:
                return row["address_label"]
            if index.column() == 2:
                return row["name"]
            if index.column() == 3:
                return row["access"]
            if index.column() == 4:
                return row["value"]
            if index.column() == 5:
                return row["description"]
            if index.column() == 6:
                return "ON" if self._is_tracked(row) else "OFF"
        if role == Qt.ToolTipRole:
            if index.column() == 5:
                return row["description"]
            if index.column() == 2:
                return row["name"]
            if index.column() == 6:
                return "Click to enable or disable capture for write requests to this point (FC05/06/0F/10)"
        if role == Qt.EditRole:
            if index.column() == 4:
                return row["value"]
            return self.data(index, Qt.DisplayRole)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() == 4:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if role != Qt.EditRole or index.column() != 4:
            return False
        row = self._rows[index.row()]
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return False
        if row["kind"] == "coil":
            self.bank.set_coil_direct(int(row["address"]), numeric != 0)
        else:
            self.bank.set_direct(int(row["address"]), numeric, self.view_slave_id)
        self.reload()
        return True


class CaptureToggleDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        button = QStyleOptionButton()
        button.rect = option.rect.adjusted(4, 3, -4, -3)
        button.text = str(index.data(Qt.DisplayRole) or "OFF")
        button.state = QStyle.State_Enabled
        if button.text == "ON":
            button.state |= QStyle.State_On
        QApplication.style().drawControl(QStyle.CE_PushButton, button, painter)

    def editorEvent(self, event, model, option, index) -> bool:  # type: ignore[override]
        if event.type() != QEvent.MouseButtonRelease:
            return False
        if not isinstance(event, QMouseEvent) or event.button() != Qt.LeftButton:
            return False
        toggle = getattr(model, "toggle_tracking", None)
        return bool(toggle and toggle(index))
