"""Inspector pane: Message details, Connection settings and Variables, switched by a segmented control."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QMenu, QPlainTextEdit, QScrollArea, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.models import AppSettings, LogEntry
from ..core.templating import BUILTINS_HELP
from ..core.textutil import hexdump, human_size, try_pretty_json, visualize
from .binding import ProfileHub
from .connection_form import ConnectionForm
from .controls import (
    Banner, EmptyState, FormSection, SegmentedControl, ToggleSwitch, label, push_button, separator,
)
from .highlighters import JsonHighlighter
from .log_model import format_ts
from .theme import mono_font, repolish, tokens
from .widgets import FlowWidget, KeyValueTable

TITLES = {"event": "Event", "heartbeat": "Heart-beat", "raw": "Raw Data"}


def _code_view(font) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setProperty("kind", "code")
    edit.setReadOnly(True)
    edit.setFont(font)
    edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    edit.setFrameShape(QPlainTextEdit.Shape.NoFrame)
    return edit


class MessageInspector(QWidget):
    action_requested = Signal(str, object)

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.entry: LogEntry | None = None
        font = mono_font(settings.font_size)

        # header ------------------------------------------------------------------
        self.title = label("", "title")
        self.pill = label("")
        self.meta = label("", "caption", wrap=True, selectable=True)
        self.receipt = label("", "caption")
        self.banner = Banner("warn")
        self.banner.hide()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.title)
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)

        # content -----------------------------------------------------------------
        self.tabs = SegmentedControl(["Body", "Headers", "Raw", "Hex"], compact=True, expanding=True)
        self.tabs.currentChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        self.body = _code_view(font)
        self.body_hl = JsonHighlighter(self.body.document())
        self.headers = QTableWidget(0, 2)
        self.headers.setHorizontalHeaderLabels(["Header", "Value"])
        self.headers.verticalHeader().setVisible(False)
        self.headers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.headers.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.headers.horizontalHeader().setHighlightSections(False)
        self.headers.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.headers.setShowGrid(False)
        self.headers.setWordWrap(True)
        self.headers.setFont(font)
        self.headers.setFrameShape(QTableWidget.Shape.NoFrame)
        self.raw = _code_view(font)
        self.raw.setToolTip("Exact bytes on the wire: ␊ = LF, ␍ = CR, ␀ = NULL terminator")
        self.hex = _code_view(font)
        self.hex.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.stack = QStackedWidget()
        for w in (self.body, self.headers, self.raw, self.hex):
            self.stack.addWidget(w)
        card = QWidget()
        card.setObjectName("CardHolder")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(1, 1, 1, 1)
        card_layout.addWidget(self.stack)
        self.card = card
        self._style_card()

        options = QHBoxLayout()
        options.setSpacing(6)
        self.pretty = ToggleSwitch(small=True)
        self.pretty.setChecked(settings.pretty_json)
        self.pretty.toggled.connect(self._render_body)
        self.wrap = ToggleSwitch(small=True)
        self.wrap.setChecked(settings.wrap_lines)
        self.wrap.toggled.connect(self._apply_wrap)
        self.body_info = label("", "caption")
        options.addWidget(self.pretty)
        options.addWidget(label("Pretty JSON", "caption"))
        options.addSpacing(8)
        options.addWidget(self.wrap)
        options.addWidget(label("Wrap", "caption"))
        options.addStretch(1)
        options.addWidget(self.body_info)

        # actions -------------------------------------------------------------
        self.btn_compose = push_button("Edit in Composer", icon_name="pencil", slot=lambda: self._emit("composer"))
        self.btn_resend = push_button("Resend", icon_name="arrow.clockwise", slot=lambda: self._emit("resend"),
                                      tooltip="Send exactly the same bytes again")
        self.btn_template = push_button("Save Template", icon_name="bookmark", slot=lambda: self._emit("template"))
        self.btn_ack = push_button("ACK", icon_name="check", slot=lambda: self._emit("ack"))
        self.btn_nack = push_button("NACK", icon_name="xmark", slot=lambda: self._emit("nack"))
        self.btn_copy = push_button("Copy", icon_name="copy")
        self.btn_copy.setProperty("menu", "true")
        copy_menu = QMenu(self.btn_copy)
        copy_menu.addAction("Copy Body", lambda: self._copy("body"))
        copy_menu.addAction("Copy Frame", lambda: self._copy("frame"))
        copy_menu.addAction("Copy Raw Bytes (escaped)", lambda: self._copy("raw"))
        copy_menu.addAction("Copy Headers", lambda: self._copy("headers"))
        self.btn_copy.setMenu(copy_menu)
        actions = FlowWidget([self.btn_compose, self.btn_resend, self.btn_template, self.btn_ack,
                              self.btn_nack, self.btn_copy], spacing=6)

        self.details = QWidget()
        d = QVBoxLayout(self.details)
        d.setContentsMargins(14, 12, 14, 12)
        d.setSpacing(8)
        d.addLayout(head)
        d.addWidget(self.meta)
        d.addWidget(self.receipt)
        d.addWidget(self.banner)
        d.addSpacing(2)
        d.addWidget(self.tabs)
        d.addWidget(card, 1)
        d.addLayout(options)
        d.addWidget(separator())
        d.addWidget(actions)

        self.empty = EmptyState("message", "No Selection", "Select a message in the conversation to see its headers, body and raw bytes.")
        self.pages = QStackedWidget()
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.details)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.pages)
        self._apply_wrap(settings.wrap_lines)
        self.show_entry(None)

    def _style_card(self) -> None:
        t = tokens()
        self.card.setStyleSheet(
            f"#CardHolder {{ background: {t.bg}; border: 1px solid {t.field_border}; border-radius: 8px; }}"
        )

    def apply_settings(self, settings: AppSettings) -> None:
        font = mono_font(settings.font_size)
        for w in (self.body, self.raw, self.hex, self.headers):
            w.setFont(font)
        self.body_hl.refresh_colors()
        self._style_card()
        self.empty.refresh()
        self.banner.set_level(self.banner.property("level") or "warn")
        self.show_entry(self.entry)

    def _apply_wrap(self, wrap: bool) -> None:
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if wrap else QPlainTextEdit.LineWrapMode.NoWrap
        self.body.setLineWrapMode(mode)
        self.raw.setLineWrapMode(mode)

    def _emit(self, action: str) -> None:
        if self.entry is not None:
            self.action_requested.emit(action, self.entry)

    def _copy(self, what: str) -> None:
        e = self.entry
        if e is None:
            return
        if what == "body":
            text = e.body_text
        elif what == "raw":
            text = (e.raw or e.body).decode("utf-8", errors="backslashreplace").replace("\x00", "\\u0000")
        elif what == "headers":
            text = "\n".join(f"{k}:{v}" for k, v in e.headers)
        else:
            text = e.frame_text()
        QGuiApplication.clipboard().setText(text)

    def refresh_if(self, entry: LogEntry) -> None:
        if entry is self.entry:
            self.show_entry(entry)

    def show_entry(self, e: LogEntry | None) -> None:
        self.entry = e
        if e is None:
            self.pages.setCurrentWidget(self.empty)
            return
        self.pages.setCurrentWidget(self.details)
        t = tokens()
        is_frame = e.kind in ("frame", "raw")
        self.btn_compose.setEnabled(is_frame)
        self.btn_template.setEnabled(is_frame)
        self.btn_resend.setEnabled(e.direction == "out" and bool(e.raw))
        self.btn_ack.setVisible(e.command == "MESSAGE")
        self.btn_nack.setVisible(e.command == "MESSAGE")

        self.title.setText(e.command or TITLES.get(e.kind, e.kind.title()))
        if e.command == "ERROR" or e.level == "error":
            self.title.setStyleSheet(f"color:{t.red_text}")
        else:
            self.title.setStyleSheet("")
        role, text = {"in": ("pill-received", "Received"), "out": ("pill-sent", "Sent")}.get(e.direction, ("pill-event", "Event"))
        self.pill.setProperty("role", role)
        self.pill.setText(text)
        repolish(self.pill)
        meta = [f"#{e.id}", format_ts(e.ts, "datetime", None)]
        if e.size:
            meta.append(f"{e.size} bytes")
        if e.binary:
            meta.append("binary message")
        self.meta.setText("  ·  ".join(meta))
        if e.rtt_ms is not None:
            self.receipt.setText(f"<span style='color:{t.green_text}'>✓ Receipt confirmed in {e.rtt_ms:.2f} ms</span>")
        elif e.receipt:
            self.receipt.setText(f"Waiting for receipt “{e.receipt}”…")
        elif e.note:
            self.receipt.setText(e.note)
        else:
            self.receipt.setText("")
        self.receipt.setVisible(bool(self.receipt.text()))
        problems = list(e.warnings)
        if problems:
            self.banner.set_level("error" if e.level == "error" else "warn")
            self.banner.text.setText("\n".join(problems))
            self.banner.show()
        else:
            self.banner.hide()

        self.headers.setRowCount(len(e.headers))
        for row, (key, value) in enumerate(e.headers):
            k = QTableWidgetItem(key)
            k.setForeground(QColor(t.secondary))
            self.headers.setItem(row, 0, k)
            self.headers.setItem(row, 1, QTableWidgetItem(value))
        self.headers.resizeRowsToContents()
        self._render_body()
        raw = e.raw or e.body
        self.raw.setPlainText(visualize(raw) if raw else "")
        self.hex.setPlainText(hexdump(raw) if raw else "")

    def _render_body(self) -> None:
        e = self.entry
        if e is None:
            return
        if e.kind in ("event", "heartbeat"):
            self.body.setPlainText(e.text or "heart-beat")
            self.body_info.setText(f"{len(e.headers)} headers" if e.headers else "")
            return
        text = e.body_text if e.kind == "frame" else visualize(e.raw)
        pretty = try_pretty_json(text) if self.pretty.isChecked() else None
        self.body.setPlainText(pretty or text)
        info = human_size(len(e.body)) if e.body else "Empty body"
        if pretty:
            info += " · JSON"
        self.body_info.setText(info)


class VariablesPage(QScrollArea):
    def __init__(self, hub: ProfileHub, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("InspectorInner")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(18)
        s = FormSection("Variables", "Use {{name}} anywhere: destinations, headers, bodies, raw frames, "
                                     "CONNECT and handshake headers. Variables belong to this profile.")
        table = hub.bind_table(KeyValueTable([], "Name", "Value", add_text="Add Variable"), "variables")
        table.setMinimumHeight(170)
        s.add_widget(table)
        layout.addWidget(s)
        ref = FormSection("Built-in Placeholders")
        mono = mono_font()
        for token, desc in BUILTINS_HELP:
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(1)
            name = label(token, wrap=True, selectable=True)
            name.setFont(mono)
            rl.addWidget(name)
            rl.addWidget(label(desc, "caption", wrap=True))
            ref.add_widget(row, (10, 6, 10, 6))
        layout.addWidget(ref)
        layout.addStretch(1)
        self.setWidget(inner)


class InspectorPane(QWidget):
    action_requested = Signal(str, object)

    def __init__(self, hub: ProfileHub, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("InspectorPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.switch = SegmentedControl(["Message", "Connection", "Variables"], expanding=True)
        self.message = MessageInspector(settings)
        self.connection = ConnectionForm(hub)
        self.variables = VariablesPage(hub)
        self.message.action_requested.connect(self.action_requested.emit)
        self.stack = QStackedWidget()
        for page in (self.message, self.connection, self.variables):
            self.stack.addWidget(page)
        self.switch.currentChanged.connect(self.stack.setCurrentIndex)
        top = QHBoxLayout()
        top.setContentsMargins(12, 10, 12, 6)
        top.addWidget(self.switch)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.stack, 1)
        self.setMinimumWidth(330)

    def show_tab(self, index: int) -> None:
        self.switch.setCurrentIndex(index)
        self.stack.setCurrentIndex(index)

    def current_tab(self) -> int:
        return self.switch.currentIndex()

    def show_entry(self, entry: LogEntry | None) -> None:
        self.message.show_entry(entry)

    def apply_settings(self, settings: AppSettings) -> None:
        self.message.apply_settings(settings)
