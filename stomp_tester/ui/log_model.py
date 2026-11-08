"""Conversation model (batched inserts, capped size) and its filter proxy."""

from __future__ import annotations

import re
from bisect import bisect_left
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QFont

from ..core.models import LogEntry
from ..core.textutil import human_size
from .theme import Colors

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
COLUMNS = ["#", "Time", "Direction", "Command", "Destination", "Size", "Receipt", "Summary"]
COL_SUMMARY = 7
DIR_LABELS = {"in": "↓ Received", "out": "↑ Sent", "sys": "Event"}


def format_ts(ts: float, fmt: str, start: float | None) -> str:
    if fmt == "relative" and start is not None:
        return f"+{ts - start:.3f}s"
    dt = datetime.fromtimestamp(ts)
    if fmt == "time":
        return dt.strftime("%H:%M:%S")
    if fmt == "datetime":
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return dt.strftime("%H:%M:%S.%f")[:-3]


class LogModel(QAbstractTableModel):
    def __init__(self, parent=None, max_entries: int = 20000):
        super().__init__(parent)
        self.entries: list[LogEntry] = []
        self._ids: list[int] = []
        self._pending: list[LogEntry] = []
        self.max_entries = max_entries
        self.ts_format = "time_ms"
        self.start_ts: float | None = None
        self.colors = Colors()
        self._bold = QFont()
        self._bold.setBold(True)
        self._timer = QTimer(self, singleShot=True, interval=30, timeout=self.flush)

    # structure ----------------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    # mutation -----------------------------------------------------------
    def add(self, entry: LogEntry) -> None:
        self._pending.append(entry)
        if not self._timer.isActive():
            self._timer.start()

    def set_entries(self, entries: list[LogEntry]) -> None:
        self.beginResetModel()
        self._pending.clear()
        self.entries = list(entries[-self.max_entries:]) if self.max_entries > 0 else list(entries)
        self._ids = [e.id for e in self.entries]
        self.start_ts = self.entries[0].ts if self.entries else None
        self.endResetModel()

    def flush(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        if self.start_ts is None:
            self.start_ts = batch[0].ts
        first = len(self.entries)
        self.beginInsertRows(QModelIndex(), first, first + len(batch) - 1)
        self.entries.extend(batch)
        self._ids.extend(e.id for e in batch)
        self.endInsertRows()
        overflow = len(self.entries) - self.max_entries if self.max_entries > 0 else 0
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self.entries[:overflow]
            del self._ids[:overflow]
            self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.entries.clear()
        self._ids.clear()
        self._pending.clear()
        self.start_ts = None
        self.endResetModel()

    def row_of(self, entry: LogEntry) -> int:
        i = bisect_left(self._ids, entry.id)
        if i < len(self.entries) and self.entries[i] is entry:
            return i
        for j, e in enumerate(self.entries):
            if e is entry:
                return j
        return -1

    def update_entry(self, entry: LogEntry) -> None:
        row = self.row_of(entry)
        if row >= 0:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def refresh_all(self) -> None:
        self.colors = Colors()
        if self.entries:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.entries) - 1, len(COLUMNS) - 1))

    def entry_at(self, row: int) -> LogEntry:
        return self.entries[row]

    # data ---------------------------------------------------------------
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        col = index.column()
        if role == ENTRY_ROLE:
            return e
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return e.id
            if col == 1:
                return format_ts(e.ts, self.ts_format, self.start_ts)
            if col == 2:
                return DIR_LABELS.get(e.direction, e.direction)
            if col == 3:
                if e.kind == "frame":
                    return e.command or "(no command)"
                return {"heartbeat": "♥", "raw": "RAW", "event": e.level.upper() if e.level != "info" else "INFO"}.get(e.kind, e.kind)
            if col == 4:
                return e.target()
            if col == 7:
                return e.summary()
            if col == 5:
                return human_size(e.size) if e.size else ""
            if col == 6:
                if e.rtt_ms is not None:
                    return f"{e.rtt_ms:.1f} ms"
                return "…" if e.receipt else ""
        elif role == Qt.ItemDataRole.ForegroundRole:
            c = self.colors
            if e.level in ("error", "warn") or e.command == "ERROR":
                return c.error if (e.level == "error" or e.command == "ERROR") else c.warn
            if col == 2:
                return c.accent if e.direction == "out" else (c.success if e.direction == "in" else c.dim)
            if e.kind == "heartbeat":
                return c.dim
            if e.level == "success" and col in (3, COL_SUMMARY):
                return c.success
        elif role == Qt.ItemDataRole.FontRole:
            if col == 3 and e.kind == "frame":
                return self._bold
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == COL_SUMMARY:
                text = e.frame_text() if e.kind == "frame" else e.text
                return text[:1500] + ("…" if len(text) > 1500 else "")
            if col == 6 and e.receipt:
                return f"receipt: {e.receipt}"
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 5, 6):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class LogFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_in = True
        self.show_out = True
        self.show_sys = True
        self.show_heartbeats = False
        self.show_raw = True
        self.command = ""
        self.text = ""
        self.regex: re.Pattern | None = None
        self.regex_error = ""
        self._tokens: list[str] = []

    def set_filter(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        self._compile()
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:  # pragma: no cover - older Qt
            self.invalidateFilter()

    def _compile(self) -> None:
        text = self.text.strip()
        self.regex, self.regex_error, self._tokens = None, "", []
        if len(text) >= 2 and text.startswith("/") and text.endswith("/"):
            try:
                self.regex = re.compile(text[1:-1], re.IGNORECASE)
            except re.error as exc:
                self.regex_error = str(exc)
        elif text:
            self._tokens = text.lower().split()

    def entry(self, proxy_index) -> LogEntry | None:
        if not proxy_index.isValid():
            return None
        return self.sourceModel().entry_at(self.mapToSource(proxy_index).row())

    def filterAcceptsRow(self, row, parent):
        e: LogEntry = self.sourceModel().entries[row]
        if e.direction == "in" and not self.show_in:
            return False
        if e.direction == "out" and not self.show_out:
            return False
        if e.direction == "sys" and not self.show_sys:
            return False
        if e.kind == "heartbeat" and not self.show_heartbeats:
            return False
        if e.kind == "raw" and not self.show_raw:
            return False
        if self.command:
            if self.command == "@events":
                if e.kind != "event":
                    return False
            elif self.command == "@problems":
                if not (e.level in ("warn", "error") or e.warnings or e.command == "ERROR"):
                    return False
            elif e.command != self.command:
                return False
        if self.regex is not None:
            return bool(self.regex.search(e.search_text()))
        if self.regex_error:
            return False
        if self._tokens:
            haystack = e.search_text()
            return all(tok in haystack for tok in self._tokens)
        return True
