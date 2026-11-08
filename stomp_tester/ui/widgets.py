"""Reusable composite widgets: key/value table and flow layout."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCompleter, QHBoxLayout, QHeaderView, QLayout, QLineEdit, QSizePolicy,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .controls import icon_button, text_button


class _CompleterDelegate(QStyledItemDelegate):
    def __init__(self, words: list[str], parent=None):
        super().__init__(parent)
        self.words = words

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setProperty("frameless", "true")
        if self.words:
            completer = QCompleter(self.words, editor)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            editor.setCompleter(completer)
        return editor


class KeyValueTable(QWidget):
    """Editable [enabled, key, value] rows — tick a row to send it, untick to keep it for later."""

    changed = Signal()

    def __init__(self, key_words: list[str] | None = None, key_label: str = "Name",
                 value_label: str = "Value", value_words: list[str] | None = None,
                 add_text: str = "Add", parent=None):
        super().__init__(parent)
        self._loading = False
        self.table = QTableWidget(0, 3)
        self.table.setProperty("kind", "kv")
        self.table.setHorizontalHeaderLabels(["", key_label, value_label])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        self.table.setColumnWidth(0, 28)
        self.table.setColumnWidth(1, 130)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setItemDelegateForColumn(1, _CompleterDelegate(key_words or [], self.table))
        self.table.setItemDelegateForColumn(2, _CompleterDelegate(value_words or [], self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setMinimumHeight(84)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(2)
        bar.addWidget(text_button(add_text, "Add a row", lambda: self.add_row(edit=True), "plus"))
        bar.addStretch(1)
        bar.addWidget(icon_button("arrow.up", "Move up", lambda: self._move(-1), size=24, icon_size=13))
        bar.addWidget(icon_button("arrow.down", "Move down", lambda: self._move(1), size=24, icon_size=13))
        bar.addWidget(icon_button("trash", "Remove selected rows", self.remove_selected, size=24, icon_size=14))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.table, 1)
        layout.addLayout(bar)

    def _on_item_changed(self, _item) -> None:
        if not self._loading:
            self.changed.emit()

    def _make_row(self, row: int, enabled: bool, key: str, value: str) -> None:
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        check.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        check.setToolTip("Untick to keep the row without sending it")
        self.table.setItem(row, 0, check)
        self.table.setItem(row, 1, QTableWidgetItem(key))
        self.table.setItem(row, 2, QTableWidgetItem(value))

    def add_row(self, key: str = "", value: str = "", enabled: bool = True, edit: bool = False) -> None:
        self._loading = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._make_row(row, enabled, key, value)
        self._loading = False
        self.changed.emit()
        if edit:
            self.table.setFocus()
            self.table.setCurrentCell(row, 1)
            self.table.editItem(self.table.item(row, 1))

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.changed.emit()

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.table.rowCount()):
            return
        data = self.rows()
        data[row], data[target] = data[target], data[row]
        self.set_rows(data)
        self.table.setCurrentCell(target, 1)
        self.changed.emit()

    def set_rows(self, rows: list) -> None:
        self._loading = True
        self.table.setRowCount(0)
        for r in rows or []:
            if len(r) == 3:
                enabled, key, value = r
            else:
                enabled, (key, value) = True, r
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._make_row(row, bool(enabled), str(key), str(value))
        self._loading = False

    def rows(self) -> list[list]:
        out = []
        for row in range(self.table.rowCount()):
            check, key, value = (self.table.item(row, c) for c in range(3))
            out.append([
                check is not None and check.checkState() == Qt.CheckState.Checked,
                key.text() if key else "",
                value.text() if value else "",
            ])
        return out

    def enabled_pairs(self) -> list[tuple[str, str]]:
        return [(k, v) for en, k, v in self.rows() if en and k.strip()]


class FlowLayout(QLayout):
    """Lays children out left-to-right, wrapping onto new lines when out of width."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


class FlowWidget(QWidget):
    def __init__(self, widgets: list[QWidget] | None = None, spacing: int = 8, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, spacing)
        for w in widgets or []:
            self.flow.addWidget(w)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget: QWidget) -> None:
        self.flow.addWidget(widget)
