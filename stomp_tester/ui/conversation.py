"""Conversation pane: chat view + table view over one filtered model, with a toolbar filter bar."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QMenu, QStackedWidget, QTableView, QToolButton,
    QVBoxLayout, QWidget,
)

from ..core.frame import ALL_COMMANDS
from ..core.models import AppSettings, LogEntry
from .chat_view import ChatView
from .controls import EmptyState, SegmentedControl, icon_button
from .icons import icon
from .log_model import LogFilterProxy, LogModel
from .theme import tokens


class FilterBar(QWidget):
    """(clear)  [Chat|Table]  (filter ▾)  [🔍 Search]  — lives in a toolbar."""

    def __init__(self, panel: "ConversationPanel", parent=None):
        super().__init__(parent)
        self.panel = panel
        self.view_switch = SegmentedControl([("message", ""), ("list", "")])
        self.view_switch.setToolTips(["Chat view (⌘1)", "Table view (⌘2)"])
        self.view_switch.currentChanged.connect(panel.set_view)

        self.filter_button = icon_button("filter", "Filter the conversation", checkable=True, size=30)
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.filter_button)
        self.filter_button.setMenu(self.menu)
        self.menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self._sync_indicator))
        self._build_menu()

        self.search = QLineEdit()
        self.search.setProperty("kind", "search")
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(170)
        self.search.setMaximumWidth(280)
        self.search.setToolTip("Words, header:value (e.g. destination:/topic/x) or /regex/")
        self._search_action = self.search.addAction(icon("magnifier", "tertiary", 14), QLineEdit.ActionPosition.LeadingPosition)
        self._timer = QTimer(self, singleShot=True, interval=160, timeout=lambda: panel.set_search(self.search.text(), from_bar=True))
        self.search.textChanged.connect(lambda _: self._timer.start())

        self.clear_button = None
        if panel.live:
            self.clear_button = icon_button(
                "trash", "Clear Chat (⌘K)\nEmpties the view — the recorded history file is kept",
                lambda: panel.action_requested.emit("clear", None), size=30)
            self.clear_button.setText("Clear")
            self.clear_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.clear_button.setMinimumWidth(0)
            self.clear_button.setMaximumWidth(16777215)
            self.clear_button.setStyleSheet("padding: 4px 9px 4px 7px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if self.clear_button is not None:
            layout.addWidget(self.clear_button)
            layout.addSpacing(2)
        layout.addWidget(self.view_switch)
        layout.addWidget(self.filter_button)
        layout.addWidget(self.search)

    def _check(self, text: str, checked: bool, slot) -> QAction:
        action = QAction(text, self.menu)
        action.setCheckable(True)
        action.setChecked(checked)
        action.toggled.connect(slot)
        self.menu.addAction(action)
        return action

    def _build_menu(self) -> None:
        p = self.panel
        self.menu.addSection("Show")
        self.act_in = self._check("Received frames", True, lambda on: p.set_filter(show_in=on))
        self.act_out = self._check("Sent frames", True, lambda on: p.set_filter(show_out=on))
        self.act_sys = self._check("Connection events", True, lambda on: p.set_filter(show_sys=on))
        self.act_hb = self._check("Heart-beats", p.proxy.show_heartbeats, lambda on: p.set_filter(show_heartbeats=on))
        self.act_raw = self._check("Raw transport chunks", p.proxy.show_raw, lambda on: p.set_filter(show_raw=on))
        self.menu.addSeparator()
        commands = self.menu.addMenu("Only command")
        group = QActionGroup(commands)
        group.setExclusive(True)
        self.command_actions = []
        for label, value in [("All commands", ""), ("Problems only", "@problems"), ("Events only", "@events")] + [
            (c, c) for c in ALL_COMMANDS
        ]:
            action = QAction(label, commands)
            action.setCheckable(True)
            action.setChecked(value == "")
            action.setData(value)
            action.triggered.connect(lambda _=False, v=value: p.set_filter(command=v))
            group.addAction(action)
            commands.addAction(action)
            self.command_actions.append(action)
            if value == "@events":
                commands.addSeparator()
        self.menu.addSeparator()
        self.act_follow = self._check("Follow newest messages", p.autoscroll, p.set_autoscroll)
        self.menu.addSeparator()
        reset = QAction("Reset filters", self.menu)
        reset.triggered.connect(self.reset)
        self.menu.addAction(reset)

    def reset(self) -> None:
        for action in (self.act_in, self.act_out, self.act_sys):
            action.setChecked(True)
        self.act_hb.setChecked(False)
        self.act_raw.setChecked(True)
        self.command_actions[0].setChecked(True)
        self.panel.set_filter(command="")
        self.search.clear()
        self._sync_indicator()

    def _sync_indicator(self) -> None:
        self.filter_button.setChecked(self.panel.filters_active())


class ConversationPanel(QWidget):
    entry_selected = Signal(object)
    action_requested = Signal(str, object)
    count_changed = Signal(int, int)

    def __init__(self, model: LogModel, settings: AppSettings, live: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("ConversationPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.model = model
        self.settings = settings
        self.live = live
        self.autoscroll = settings.autoscroll
        self.proxy = LogFilterProxy(self)
        self.proxy.setSourceModel(model)
        self.proxy.show_heartbeats = settings.show_heartbeats
        self.proxy.show_raw = settings.show_raw_chunks

        self.chat = ChatView()
        self.chat.setModel(self.proxy)
        self.table = QTableView()
        self._setup_table()
        if live:
            self.empty = EmptyState("antenna", "No Messages Yet",
                                    "Connect to a server — every frame you send and receive will appear here.",
                                    "Connect")
        else:
            self.empty = EmptyState("tray", "Empty Session", "This history file contains no entries.")
        self._empty_action = "connect"
        self._connected = False
        self.empty.action.connect(self._on_empty_action)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.chat)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        self._view = 1 if settings.default_view == "table" else 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self.filter_bar = FilterBar(self)
        self.filter_bar.view_switch.setCurrentIndex(self._view, emit=False)

        for view in (self.chat, self.table):
            view.selectionModel().currentChanged.connect(self._on_current_changed)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, v=view: self._context_menu(v, pos))

        self._at_bottom = {self.chat: True, self.table: True}
        self.proxy.rowsAboutToBeInserted.connect(self._before_insert)
        self.proxy.rowsInserted.connect(self._after_insert)
        for signal in (self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset, self.proxy.layoutChanged):
            signal.connect(self._update_state)

        copy = QAction(self)
        copy.setShortcut(QKeySequence.StandardKey.Copy)
        copy.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy.triggered.connect(lambda: self._copy(self.current_entry(), "frame"))
        self.addAction(copy)
        self.apply_settings(settings)
        self._update_state()

    def _setup_table(self) -> None:
        t = self.table
        t.setModel(self.proxy)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(26)
        t.setWordWrap(False)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setFrameShape(QTableView.Shape.NoFrame)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = t.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col, width in enumerate([44, 126, 116, 132, 170, 70, 78, 320]):
            t.setColumnWidth(col, width)

    # settings ----------------------------------------------------------------
    def apply_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        d = self.chat.delegate
        d.preview_lines = settings.chat_preview_lines
        d.show_headers = settings.chat_show_headers
        d.pretty_json = settings.pretty_json
        d.ts_format = settings.timestamp_format
        d.mono_size = settings.font_size
        self.refresh_render()

    def refresh_render(self) -> None:
        d = self.chat.delegate
        d.start_ts = self.model.start_ts
        d.invalidate()
        self.model.refresh_all()
        self.empty.refresh()
        self.chat.scheduleDelayedItemsLayout()
        self.chat.viewport().update()
        self.table.viewport().update()

    # view & filters ---------------------------------------------------------------
    def set_view(self, index: int) -> None:
        self._view = index
        self.filter_bar.view_switch.setCurrentIndex(index, emit=False)
        self._update_state()
        other = self.table if index == 0 else self.chat
        idx = other.currentIndex()
        if idx.isValid():
            view = self.current_view()
            view.setCurrentIndex(self.proxy.index(idx.row(), 0))
            view.scrollTo(view.currentIndex())

    def set_filter(self, **kwargs) -> None:
        self.proxy.set_filter(**kwargs)
        self.filter_bar._sync_indicator()
        self._update_state()

    def set_search(self, text: str, from_bar: bool = False) -> None:
        if not from_bar and self.filter_bar.search.text() != text:
            self.filter_bar.search.blockSignals(True)
            self.filter_bar.search.setText(text)
            self.filter_bar.search.blockSignals(False)
        self.proxy.set_filter(text=text)
        err = self.proxy.regex_error
        self.filter_bar.search.setStyleSheet(f"color:{tokens().red_text}" if err else "")
        self.filter_bar.search.setToolTip(f"Invalid regex: {err}" if err else
                                          "Words, header:value (e.g. destination:/topic/x) or /regex/")
        self.filter_bar._sync_indicator()
        self._update_state()

    def search_text(self) -> str:
        return self.filter_bar.search.text()

    def set_autoscroll(self, on: bool) -> None:
        self.autoscroll = on
        if on:
            self.current_view().scrollToBottom()

    def filters_active(self) -> bool:
        p = self.proxy
        return bool(p.text.strip() or p.command or not (p.show_in and p.show_out and p.show_sys))

    def _update_state(self, *args) -> None:
        shown, total = self.proxy.rowCount(), self.model.rowCount()
        if shown:
            self.stack.setCurrentIndex(self._view)
        else:
            if total == 0 and not self.model._pending:
                if not self.live:
                    self.empty.set_content("tray", "Empty Session", "This history file contains no entries.")
                    self._empty_action = ""
                elif self._connected:
                    self.empty.set_content("antenna", "Connected",
                                           "Subscribe to a destination in the sidebar, or compose a frame below and press Send.")
                    self._empty_action = ""
                else:
                    self.empty.set_content("antenna", "No Messages Yet",
                                           "Connect to a server — every frame you send and receive will appear here.",
                                           "Connect")
                    self._empty_action = "connect"
            else:
                self.empty.set_content("magnifier", "No Matching Messages",
                                       "Nothing in this conversation matches the current search and filters.",
                                       "Reset Filters")
                self._empty_action = "reset-filters"
            self.stack.setCurrentWidget(self.empty)
        clear = getattr(getattr(self, "filter_bar", None), "clear_button", None)
        if clear is not None:
            clear.setEnabled(total > 0 or bool(self.model._pending))
        self.count_changed.emit(shown, total)

    def set_connected_hint(self, connected: bool) -> None:
        self._connected = connected
        self._update_state()

    def _on_empty_action(self) -> None:
        if self._empty_action == "reset-filters":
            self.filter_bar.reset()
        elif self._empty_action:
            self.action_requested.emit(self._empty_action, None)

    # selection -----------------------------------------------------------------
    def current_view(self):
        return self.chat if self._view == 0 else self.table

    def current_entry(self) -> LogEntry | None:
        return self.proxy.entry(self.current_view().currentIndex())

    def visible_entries(self) -> list[LogEntry]:
        return [self.proxy.entry(self.proxy.index(r, 0)) for r in range(self.proxy.rowCount())]

    def _on_current_changed(self, current, _previous) -> None:
        entry = self.proxy.entry(current)
        if entry is not None:
            self.entry_selected.emit(entry)

    def entry_updated(self, entry: LogEntry) -> None:
        row = self.model.row_of(entry)
        if row < 0:
            return
        proxy_index = self.proxy.mapFromSource(self.model.index(row, 0))
        if proxy_index.isValid():
            self.chat.delegate.entry_changed(proxy_index)

    def clear_caches(self) -> None:
        self.chat.delegate.start_ts = None
        self.chat.delegate.invalidate()
        self._update_state()

    # auto-scroll ---------------------------------------------------------------
    def _before_insert(self, *args) -> None:
        for view in (self.chat, self.table):
            bar = view.verticalScrollBar()
            self._at_bottom[view] = bar.value() >= bar.maximum() - 8

    def _after_insert(self, *args) -> None:
        if self.model.start_ts is not None and self.chat.delegate.start_ts is None:
            self.chat.delegate.start_ts = self.model.start_ts
        if not self.autoscroll:
            return
        for view in (self.chat, self.table):
            if self._at_bottom.get(view, True):
                QTimer.singleShot(0, view.scrollToBottom)

    # context menu --------------------------------------------------------------
    def _context_menu(self, view, pos) -> None:
        entry = self.proxy.entry(view.indexAt(pos))
        if entry is None:
            return
        menu = QMenu(self)
        is_frame = entry.kind in ("frame", "raw")
        if is_frame:
            menu.addAction(icon("pencil"), "Edit in Composer", lambda: self.action_requested.emit("composer", entry))
        if entry.direction == "out" and entry.raw:
            menu.addAction(icon("arrow.clockwise"), "Resend", lambda: self.action_requested.emit("resend", entry))
        if is_frame:
            menu.addAction(icon("bookmark"), "Save as Template…", lambda: self.action_requested.emit("template", entry))
        if entry.command == "MESSAGE":
            menu.addSeparator()
            menu.addAction(icon("check"), "Acknowledge (ACK)", lambda: self.action_requested.emit("ack", entry))
            menu.addAction(icon("xmark"), "Reject (NACK)", lambda: self.action_requested.emit("nack", entry))
        menu.addSeparator()
        menu.addAction(icon("copy"), "Copy Frame", lambda: self._copy(entry, "frame"))
        if entry.body:
            menu.addAction("Copy Body", lambda: self._copy(entry, "body"))
        if entry.raw:
            menu.addAction("Copy Raw Bytes (escaped)", lambda: self._copy(entry, "raw"))
        filters = []
        if entry.destination:
            filters.append((f"Show Only {entry.destination}", f"destination:{entry.destination}"))
        if entry.header("subscription"):
            filters.append(("Show Only This Subscription", f"subscription:{entry.header('subscription')}"))
        if entry.receipt:
            filters.append(("Show Request and Receipt", entry.receipt))
        if filters:
            menu.addSeparator()
            for text, query in filters:
                menu.addAction(icon("filter"), text, lambda q=query: self.set_search(q))
        if self.live:
            menu.addSeparator()
            menu.addAction(icon("trash"), "Clear Chat", lambda: self.action_requested.emit("clear", None))
        menu.exec(view.viewport().mapToGlobal(pos))

    def _copy(self, entry: LogEntry | None, what: str) -> None:
        if entry is None:
            return
        if what == "body":
            text = entry.body_text
        elif what == "raw":
            text = entry.raw.decode("utf-8", errors="backslashreplace").replace("\x00", "\\u0000")
        else:
            text = entry.frame_text()
        QGuiApplication.clipboard().setText(text)
