"""Main window: toolbar, sidebar | conversation + composer | inspector, menus and history recording."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QSizePolicy,
    QToolBar, QWidget,
)

from .. import APP_NAME, __version__
from ..core.frame import Frame
from ..core.models import AppSettings, Profile, Template
from ..core.session import (
    CONNECTED, CONNECTING, DISCONNECTED, DISCONNECTING, OPEN, RECONNECTING, STATE_LABELS, StompSession,
)
from ..core.storage import HistoryWriter, Store, export_entries, prune_history
from ..core.templating import BUILTINS_HELP
from ..core.textutil import human_size
from ..core.transport import TRANSPORT_LABELS, resolve_kind
from .binding import ProfileHub
from .composer import Composer
from .controls import AddressBar, ThinSplitter, icon_button, push_button, set_kind
from .conversation import ConversationPanel
from .dialogs import HistoryDialog, HistoryWindow, SettingsDialog, SubscriptionDialog, ask_export_path
from .icons import set_icon
from .inspector import InspectorPane
from .log_model import LogModel
from .sidebar import Sidebar
from .theme import app_icon, apply_theme, current_pref, tokens

LAYOUT_VERSION = 3
SHORT_TRANSPORT = {"websocket": "WS", "sockjs": "SockJS", "tcp": "TCP"}
PILL_STATES = {
    CONNECTED: ("connected", "Connected"), OPEN: ("open", "Handshaking"), CONNECTING: ("connecting", "Connecting"),
    RECONNECTING: ("reconnecting", "Reconnecting"), DISCONNECTING: ("disconnecting", "Disconnecting"),
    DISCONNECTED: ("disconnected", "Offline"),
}


class MainWindow(QMainWindow):
    def __init__(self, store: Store, settings: AppSettings):
        super().__init__()
        self.store = store
        self.settings = settings
        self.setWindowIcon(app_icon())
        self.resize(1480, 940)
        self.setMinimumSize(1060, 660)
        self.setUnifiedTitleAndToolBarOnMac(False)

        self.profiles, last = store.load_profiles()
        self.profile = next((p for p in self.profiles if p.name == last), self.profiles[0])
        self.hub = ProfileHub(self.profile, self)
        self.session = StompSession(self)
        self.session.set_profile(self.profile)
        self.templates = store.load_templates()
        self.history_writer: HistoryWriter | None = None
        self.history_windows: list[HistoryWindow] = []
        self._seen_destinations: set[str] = set()

        self.model = LogModel(self, settings.max_view_entries)
        self.model.ts_format = settings.timestamp_format

        self._build_widgets()
        self._build_toolbar()
        self._build_layout()
        self._build_menus()
        self._build_statusbar()
        self._wire()
        self._restore_layout()

        self.composer.recent = store.load_recent()
        draft = store.load_draft()
        if draft is not None:
            self.composer.load_template(draft)
        self.sidebar.set_templates(self.templates)
        self.sidebar.refresh_subscriptions()
        self._update_state(self.session.state)
        self._refresh_destinations()
        self._update_title()
        self._update_transport_label()

    # ------------------------------------------------------------------ construction
    def _build_widgets(self) -> None:
        s = self.settings
        self.conversation = ConversationPanel(self.model, s, live=True)
        self.inspector = InspectorPane(self.hub, s)
        self.composer = Composer(self.session, self.hub, s.font_size)
        self.sidebar = Sidebar(self.session)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Toolbar")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb

        self.btn_sidebar = icon_button("sidebar.left", "Show or hide the sidebar (⌃⌘S)", self.toggle_sidebar, size=30, icon_size=18)
        self.address = AddressBar()
        self.address.setMaximumWidth(820)
        self.address.edit.setPlaceholderText("ws://host:8080/ws  ·  http://host/ws for SockJS  ·  tcp://host:61613")
        self.hub.bind_line(self.address.edit, "url")
        self.address.edit.returnPressed.connect(lambda: self.session.state == DISCONNECTED and self.toggle_connection())
        group = QActionGroup(self)
        group.setExclusive(True)
        self.transport_actions = {}
        for key, text in TRANSPORT_LABELS.items():
            action = QAction("Automatic (from the URL)" if key == "auto" else text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, k=key: self.hub.set("transport", k))
            group.addAction(action)
            self.address.transport_menu.addAction(action)
            self.transport_actions[key] = action
            if key == "auto":
                self.address.transport_menu.addSeparator()

        self.connect_btn = push_button("Connect", "primary", "power", self.toggle_connection, "Connect or disconnect (F5 / ⌘R)")
        self.connect_btn.setMinimumWidth(118)
        self.connect_btn.setMinimumHeight(30)
        self.btn_inspector = icon_button("sidebar.right", "Show or hide the inspector (⌥⌘I)", self.toggle_inspector, size=30, icon_size=18)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setMinimumWidth(8)
        tb.addWidget(self.btn_sidebar)
        tb.addWidget(self.address)
        tb.addWidget(self.connect_btn)
        tb.addWidget(spacer)
        tb.addWidget(self.conversation.filter_bar)
        tb.addWidget(self.btn_inspector)

    def _build_layout(self) -> None:
        self.center_split = ThinSplitter(Qt.Orientation.Vertical)
        self.center_split.set_fill(1, "bg", "window")
        self.center_split.addWidget(self.conversation)
        self.center_split.addWidget(self.composer)
        self.center_split.setStretchFactor(0, 1)
        self.center_split.setStretchFactor(1, 0)
        self.center_split.setSizes([560, 330])

        self.main_split = ThinSplitter(Qt.Orientation.Horizontal)
        self.main_split.set_fill(1, "sidebar", "bg")
        self.main_split.set_fill(2, "bg", "window")
        self.main_split.addWidget(self.sidebar)
        self.main_split.addWidget(self.center_split)
        self.main_split.addWidget(self.inspector)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setStretchFactor(2, 0)
        self.main_split.setSizes([250, 850, 380])
        self.setCentralWidget(self.main_split)

    def _action(self, menu: QMenu, text: str, slot, shortcut=None, checkable=False, role=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.setCheckable(checkable)
        if role is not None:
            action.setMenuRole(role)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._action(m, "New Server…", lambda: self._profile_action("new", -1), "Ctrl+N")
        self._action(m, "Duplicate Server", lambda: self._profile_action("duplicate", self._current_index()))
        self._action(m, "Rename Server…", lambda: self._profile_action("rename", self._current_index()))
        self._action(m, "Delete Server…", lambda: self._profile_action("delete", self._current_index()))
        m.addSeparator()
        self._action(m, "Import Profiles…", lambda: self._profile_action("import", -1))
        self._action(m, "Export Profiles…", lambda: self._profile_action("export", -1))
        m.addSeparator()
        self._action(m, "Export Conversation…", lambda: self.export_conversation(False), "Ctrl+E")
        self._action(m, "Export Filtered Conversation…", lambda: self.export_conversation(True), "Ctrl+Shift+E")
        m.addSeparator()
        self._action(m, "History…", self.open_history_browser, "Ctrl+H")
        self._action(m, "Open Recorded Session…", self.open_history_file, "Ctrl+O")
        self._action(m, "Show Data Folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.base))))
        m.addSeparator()
        self._action(m, "Settings…", self.open_settings, "Ctrl+,", role=QAction.MenuRole.PreferencesRole)
        self._action(m, "Quit", self.close, "Ctrl+Q", role=QAction.MenuRole.QuitRole)

        m = mb.addMenu("&Connection")
        self.act_connect = self._action(m, "Connect", self.toggle_connection, "F5")
        self.act_connect.setShortcuts([QKeySequence("F5"), QKeySequence("Ctrl+R")])
        self._action(m, "Force Close", lambda: self.session.disconnect_(force=True), "Shift+F5")
        m.addSeparator()
        self._action(m, "Send CONNECT Frame", lambda: self.session.send_frame(self.session.build_connect_frame()))
        self._action(m, "Send DISCONNECT Frame", lambda: self.session.send_frame(Frame("DISCONNECT", [("receipt", "disconnect")])))
        m.addSeparator()
        self._action(m, "Reset {{counter}}", self._reset_counter)
        self._action(m, "Reset Statistics", self._reset_stats)

        m = mb.addMenu("C&ompose")
        self._action(m, "Send", self.composer.on_send_clicked, "Ctrl+Return")
        self._action(m, "Save as Template…", self.composer.save_as_template, "Ctrl+S")
        self._action(m, "Show Byte Preview", self.composer.btn_preview.toggle, "Ctrl+P")
        m.addSeparator()
        self._action(m, "Structured Editor", lambda: self._show_composer_mode(0), "Ctrl+Shift+1")
        self._action(m, "Raw Frame Editor", lambda: self._show_composer_mode(1), "Ctrl+Shift+2")
        self._action(m, "Edit Selected Message", self._selected_to_composer, "Ctrl+Shift+L")

        m = mb.addMenu("&View")
        self._action(m, "Chat", lambda: self.conversation.set_view(0), "Ctrl+1")
        self._action(m, "Table", lambda: self.conversation.set_view(1), "Ctrl+2")
        m.addSeparator()
        self.act_sidebar = self._action(m, "Show Sidebar", self.toggle_sidebar, "Meta+Ctrl+S", checkable=True)
        self.act_inspector = self._action(m, "Show Inspector", self.toggle_inspector, "Alt+Ctrl+I", checkable=True)
        self.act_composer = self._action(m, "Show Composer", self.toggle_composer, "Ctrl+Shift+Y", checkable=True)
        m.addSeparator()
        self._action(m, "Message Inspector", lambda: self._show_inspector_tab(0), "Alt+Ctrl+1")
        self._action(m, "Connection Settings", lambda: self._show_inspector_tab(1), "Alt+Ctrl+2")
        self._action(m, "Variables", lambda: self._show_inspector_tab(2), "Alt+Ctrl+3")
        m.addSeparator()
        self._action(m, "Search", self._focus_search, "Ctrl+F")
        self._action(m, "Edit URL", lambda: (self.address.edit.setFocus(), self.address.edit.selectAll()), "Ctrl+L")
        self._action(m, "Clear Chat", lambda: self.on_entry_action("clear", None), "Ctrl+K")
        m.addSeparator()
        self._action(m, "Reset Layout", self.reset_layout)

        m = mb.addMenu("&Help")
        self._action(m, "STOMP Tester Help", self.show_help, "F1")
        self._action(m, f"About {APP_NAME}", self.show_about, role=QAction.MenuRole.AboutRole)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_info = QLabel()
        self.status_count = QLabel()
        self.status_stats = QLabel()
        self.status_history = QLabel()
        for w in (self.status_info, self.status_count, self.status_stats, self.status_history):
            sb.addPermanentWidget(w)
        self._stats_timer = QTimer(self, interval=500, timeout=self._update_stats)
        self._stats_timer.start()

    def _wire(self) -> None:
        s = self.session
        s.entry_added.connect(self._on_entry)
        s.entry_updated.connect(self._on_entry_updated)
        s.state_changed.connect(self._update_state)
        s.info_changed.connect(self._update_info)
        s.session_started.connect(self._start_history)
        s.session_ended.connect(self._end_history)
        s.subscriptions_changed.connect(self._schedule_profile_save)
        s.subscriptions_changed.connect(self._refresh_destinations)
        self.hub.changed.connect(self._on_profile_attr_changed)

        self.conversation.entry_selected.connect(self._entry_selected)
        self.conversation.action_requested.connect(self.on_entry_action)
        self.conversation.count_changed.connect(self._update_count)
        self.inspector.action_requested.connect(self.on_entry_action)

        self.composer.status_message.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        self.composer.save_template_requested.connect(self.add_template)
        self.composer.recent_changed.connect(lambda: self._safe(self.store.save_recent, self.composer.recent))

        sb = self.sidebar
        sb.profile_selected.connect(self._switch_profile)
        sb.profile_action.connect(self._profile_action)
        sb.subscription_filter.connect(self._filter_subscription)
        sb.subscribe_requested.connect(lambda dest: self.session.add_subscription(dest))
        sb.subscription_edit.connect(self.edit_subscription)
        sb.template_load.connect(self._load_template)
        sb.template_send.connect(self._send_template)
        sb.template_action.connect(self._template_action)
        sb.template_save_current.connect(self.composer.save_as_template)
        sb.history_requested.connect(self.open_history_browser)
        sb.settings_requested.connect(self.open_settings)

        self._profile_save_timer = QTimer(self, singleShot=True, interval=700, timeout=self.save_profiles)
        self._dest_timer = QTimer(self, singleShot=True, interval=1000, timeout=self._refresh_destinations)
        self._history_flush = QTimer(self, interval=1000, timeout=self._flush_history)
        self._history_flush.start()
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_scheme_changed)

    # ------------------------------------------------------------------ layout
    def _ui_settings(self) -> QSettings:
        return QSettings(str(self.store.base / "ui.ini"), QSettings.Format.IniFormat)

    def _restore_layout(self) -> None:
        q = self._ui_settings()
        self._layout_restored = q.value("layout_version") == str(LAYOUT_VERSION)
        if not self._layout_restored:
            self._sync_toggles()
            return
        geometry = q.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        for key, split in (("main_split", self.main_split), ("center_split", self.center_split)):
            state = q.value(key)
            if isinstance(state, QByteArray):
                split.restoreState(state)
        self.sidebar.setVisible(q.value("sidebar", "true") == "true")
        self.inspector.setVisible(q.value("inspector", "true") == "true")
        self.composer.setVisible(q.value("composer", "true") == "true")
        try:
            self.inspector.show_tab(int(q.value("inspector_tab", 0)))
        except (TypeError, ValueError):
            pass
        self._sync_toggles()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            if not self._layout_restored:
                QTimer.singleShot(0, self.reset_layout)

    def _save_layout(self) -> None:
        q = self._ui_settings()
        q.setValue("layout_version", str(LAYOUT_VERSION))
        q.setValue("geometry", self.saveGeometry())
        q.setValue("main_split", self.main_split.saveState())
        q.setValue("center_split", self.center_split.saveState())
        q.setValue("sidebar", "true" if self.sidebar.isVisible() else "false")
        q.setValue("inspector", "true" if self.inspector.isVisible() else "false")
        q.setValue("composer", "true" if self.composer.isVisible() else "false")
        q.setValue("inspector_tab", self.inspector.current_tab())

    def reset_layout(self) -> None:
        for w in (self.sidebar, self.inspector, self.composer):
            w.show()
        width = max(self.width(), self.main_split.width())
        height = max(self.height() - 100, self.center_split.height())
        self.main_split.setSizes([255, max(420, width - 255 - 385), 385])
        self.center_split.setSizes([max(220, height - 330), 330])
        self._sync_toggles()

    def _sync_toggles(self) -> None:
        self.act_inspector.setChecked(not self.inspector.isHidden())
        self.act_composer.setChecked(not self.composer.isHidden())
        self.act_sidebar.setChecked(not self.sidebar.isHidden())

    def toggle_sidebar(self) -> None:
        self.sidebar.setVisible(self.sidebar.isHidden())
        self._sync_toggles()

    def toggle_inspector(self) -> None:
        self.inspector.setVisible(self.inspector.isHidden())
        self._sync_toggles()

    def toggle_composer(self) -> None:
        self.composer.setVisible(self.composer.isHidden())
        self._sync_toggles()

    def _show_inspector_tab(self, index: int) -> None:
        self.inspector.show()
        self.inspector.show_tab(index)
        self._sync_toggles()

    # ------------------------------------------------------------------ session events
    def _on_entry(self, entry) -> None:
        self.model.add(entry)
        if self.history_writer is not None:
            self.history_writer.write(entry)
        if entry.kind == "frame":
            dest = entry.destination
            if dest and dest not in self._seen_destinations and len(self._seen_destinations) < 1000:
                self._seen_destinations.add(dest)
                self._dest_timer.start()

    def _on_entry_updated(self, entry) -> None:
        self.model.flush()
        self.model.update_entry(entry)
        self.conversation.entry_updated(entry)
        self.inspector.message.refresh_if(entry)
        if self.history_writer is not None:
            self.history_writer.write_update(entry)

    def _entry_selected(self, entry) -> None:
        self.inspector.show_entry(entry)
        if self.inspector.isVisible() and self.inspector.current_tab() != 0:
            self.inspector.show_tab(0)

    def _update_state(self, state: str) -> None:
        pill_state, text = PILL_STATES.get(state, ("disconnected", STATE_LABELS.get(state, state)))
        self.address.status.set_state(pill_state, text)
        if state == DISCONNECTED:
            label, kind, icon_name, color = "Connect", "primary", "power", "#FFFFFF"
        elif state in (CONNECTING, RECONNECTING):
            label, kind, icon_name, color = "Cancel", None, "xmark", "label"
        else:
            label, kind, icon_name, color = "Disconnect", "destructive", "power", "red_text"
        self.connect_btn.setText(label)
        set_kind(self.connect_btn, kind)
        set_icon(self.connect_btn, icon_name, 14, color=color, hover=color)
        self.act_connect.setText(label)
        idle = state == DISCONNECTED
        self.address.edit.setReadOnly(not idle)
        self.address.transport.setEnabled(idle)
        self.sidebar.set_profiles(self.profiles, self.profile, state)
        self.conversation.set_connected_hint(state in (CONNECTED, OPEN))
        self._update_info()

    def _update_info(self) -> None:
        s = self.session
        if s.state == CONNECTED and s.version:
            bits = [f"STOMP {s.version}"]
            if s.server_info.get("server"):
                bits.append(s.server_info["server"])
            bits.append(f"♥ ↑{s.heartbeat_out} ↓{s.heartbeat_in} ms")
            self.status_info.setText("  ·  ".join(bits))
            self.address.status.setToolTip("\n".join(bits + ([f"session {s.server_info['session']}"]
                                                             if s.server_info.get("session") else [])))
        else:
            self.status_info.setText("")
            self.address.status.setToolTip("")

    def _update_count(self, shown: int, total: int) -> None:
        if total == 0:
            self.status_count.setText("")
        elif shown == total:
            self.status_count.setText(f"{total} entries")
        else:
            self.status_count.setText(f"{shown} of {total} entries")

    def _update_stats(self) -> None:
        st = self.session.stats
        if not any(st.values()):
            self.status_stats.setText("")
            return
        self.status_stats.setText(
            f"↑ {st['frames_out']} sent · {human_size(st['bytes_out'])}    "
            f"↓ {st['frames_in']} received · {human_size(st['bytes_in'])}    "
            f"♥ {st['heartbeats_out']}/{st['heartbeats_in']}"
        )

    def toggle_connection(self) -> None:
        if self.session.state == DISCONNECTED:
            if not self.profile.url.strip():
                self.address.edit.setFocus()
                self.statusBar().showMessage("Enter the server URL first", 4000)
                return
            self.session.connect_()
        else:
            self.session.disconnect_()

    def _reset_counter(self) -> None:
        self.session.ctx.counter = 0
        self.statusBar().showMessage("{{counter}} reset to 0", 3000)

    def _reset_stats(self) -> None:
        for key in self.session.stats:
            self.session.stats[key] = 0
        self._update_stats()

    # ------------------------------------------------------------------ history
    def _start_history(self) -> None:
        self._end_history()
        if not self.settings.autosave_history:
            return
        try:
            self.history_writer = HistoryWriter(self.store.history_dir(self.settings), self.profile.name, self.profile.url)
        except OSError as exc:
            self.statusBar().showMessage(f"Can't record this session: {exc}", 8000)
            return
        self.status_history.setText(f"<span style='color:{tokens().red}'>●</span> Recording")
        self.status_history.setToolTip(f"Recording to {self.history_writer.path}")

    def _end_history(self) -> None:
        if self.history_writer is None:
            return
        self.model.flush()
        try:
            self.history_writer.close()
            prune_history(self.store.history_dir(self.settings), self.settings.history_keep)
        except OSError:
            pass
        self.history_writer = None
        self.status_history.setText("")

    def _flush_history(self) -> None:
        if self.history_writer is not None:
            try:
                self.history_writer.flush()
            except OSError:
                pass

    def open_history_browser(self) -> None:
        dlg = HistoryDialog(self.store.history_dir(self.settings), self)
        dlg.open_requested.connect(self.open_history_path)
        dlg.exec()

    def open_history_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Recorded Session", str(self.store.history_dir(self.settings)),
                                              "STOMP sessions (*.jsonl);;All files (*)")
        if path:
            self.open_history_path(Path(path))

    def open_history_path(self, path: Path) -> None:
        try:
            win = HistoryWindow(Path(path), self.settings, self)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Open Session", f"Can't read {path}:\n{exc}")
            return
        win.setWindowFlag(Qt.WindowType.Window, True)
        win.action_requested.connect(self.on_entry_action)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        win.destroyed.connect(lambda *_: self.history_windows.remove(win) if win in self.history_windows else None)
        self.history_windows.append(win)
        win.show()

    def export_conversation(self, filtered: bool) -> None:
        self.model.flush()
        entries = self.conversation.visible_entries() if filtered else list(self.model.entries)
        if not entries:
            self.statusBar().showMessage("Nothing to export yet", 4000)
            return
        path = ask_export_path(self, f"{self.profile.name}.html")
        if path is None:
            return
        try:
            export_entries(path, entries, {"profile": self.profile.name, "url": self.profile.url})
        except OSError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        self.statusBar().showMessage(f"Exported {len(entries)} entries to {path}", 8000)

    # ------------------------------------------------------------------ entry actions
    def on_entry_action(self, action: str, entry) -> None:
        if action == "clear":
            self.model.clear()
            self.conversation.clear_caches()
            self.inspector.show_entry(None)
        elif action == "connect":
            if self.session.state == DISCONNECTED:
                self.toggle_connection()
        elif action == "composer":
            self.composer.load_entry(entry)
            self.composer.show()
            self._sync_toggles()
        elif action == "template":
            default = f"{entry.command or 'Raw'} {entry.destination}".strip()
            name, ok = QInputDialog.getText(self, "Save as Template", "Template name:", text=default)
            if ok and name.strip():
                self.add_template(self.composer.template_from_entry(entry, name.strip()))
        elif action == "resend":
            self.session.send_bytes(entry.raw)
        elif action in ("ack", "nack"):
            self.session.ack(entry, nack=action == "nack")

    def _selected_to_composer(self) -> None:
        entry = self.conversation.current_entry()
        if entry is not None:
            self.on_entry_action("composer", entry)

    def _show_composer_mode(self, index: int) -> None:
        self.composer.show()
        self._sync_toggles()
        self.composer.set_mode(index)
        (self.composer.raw if index else self.composer.body).setFocus()

    def _focus_search(self) -> None:
        self.conversation.filter_bar.search.setFocus()
        self.conversation.filter_bar.search.selectAll()

    def _filter_subscription(self, sub) -> None:
        if sub is not None:
            self.conversation.set_search(f"subscription:{sub.spec.id}")
        elif self.conversation.search_text().startswith("subscription:"):
            self.conversation.set_search("")

    def edit_subscription(self, sub) -> None:
        dlg = SubscriptionDialog(sub, self.session.new_subscription_id(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dest = dlg.destination.text().strip()
        sub_id = dlg.sub_id.text().strip() or self.session.new_subscription_id()
        if sub is None:
            self.session.add_subscription(dest, dlg.ack, dlg.headers.rows(), sub_id)
        else:
            self.session.update_subscription(sub, dest, dlg.ack, dlg.headers.rows(), sub_id)

    # ------------------------------------------------------------------ templates
    def add_template(self, template: Template) -> None:
        existing = next((t for t in self.templates if t.name == template.name), None)
        if existing is not None:
            answer = QMessageBox.question(self, "Replace Template", f"A template named “{template.name}” already exists. Replace it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.templates.remove(existing)
        self.templates.append(template)
        self._templates_changed()
        self.statusBar().showMessage(f"Saved template “{template.name}”", 4000)

    def _template_action(self, action: str, t: Template) -> None:
        if action == "rename":
            name, ok = QInputDialog.getText(self, "Rename Template", "New name:", text=t.name)
            if ok and name.strip():
                t.name = name.strip()
        elif action == "duplicate":
            clone = Template.from_dict(t.to_dict())
            clone.name = t.name + " copy"
            self.templates.append(clone)
        elif action == "delete":
            if QMessageBox.question(self, "Delete Template", f"Delete “{t.name}”?") != QMessageBox.StandardButton.Yes:
                return
            self.templates.remove(t)
        self._templates_changed()

    def _load_template(self, t) -> None:
        self.composer.load_template(t)
        self.composer.show()
        self._sync_toggles()

    def _send_template(self, t) -> None:
        self._load_template(t)
        self.composer.send_once()

    def _templates_changed(self) -> None:
        self.sidebar.set_templates(self.templates)
        self._safe(self.store.save_templates, self.templates)
        self._refresh_destinations()

    def _refresh_destinations(self) -> None:
        dests = set(self._seen_destinations)
        dests.update(s.spec.destination for s in self.session.subscriptions)
        dests.update(t.destination for t in self.templates if t.destination)
        self.composer.set_destinations(sorted(dests))

    # ------------------------------------------------------------------ profiles
    def _current_index(self) -> int:
        return self.profiles.index(self.profile)

    def _refresh_profiles(self) -> None:
        self.sidebar.set_profiles(self.profiles, self.profile, self.session.state)

    def _switch_profile(self, index: int) -> None:
        if not (0 <= index < len(self.profiles)) or self.profiles[index] is self.profile:
            return
        if self.session.is_active:
            self.statusBar().showMessage("Disconnect before switching to another server", 5000)
            self._refresh_profiles()
            return
        self.activate_profile(self.profiles[index])

    def activate_profile(self, profile: Profile) -> None:
        self.session.sync_subscriptions_to_profile()
        self.profile = profile
        self.hub.set_profile(profile)
        self.session.set_profile(profile)
        self._refresh_profiles()
        self._update_title()
        self._update_transport_label()
        self._refresh_destinations()
        self._schedule_profile_save()

    def _unique_name(self, base: str) -> str:
        names = {p.name for p in self.profiles}
        if base not in names:
            return base
        n = 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def _profile_action(self, action: str, index: int) -> None:
        target = self.profiles[index] if 0 <= index < len(self.profiles) else self.profile
        busy = self.session.is_active
        if action in ("new", "duplicate", "delete", "import") and busy:
            QMessageBox.information(self, "Disconnect First", "Disconnect before adding, removing or switching servers.")
            return
        if action == "new":
            name, ok = QInputDialog.getText(self, "New Server", "Name:", text=self._unique_name("New server"))
            if ok and name.strip():
                profile = Profile(name=self._unique_name(name.strip()))
                self.profiles.append(profile)
                self.activate_profile(profile)
                self._show_inspector_tab(1)
                self.address.edit.setFocus()
                self.address.edit.selectAll()
        elif action == "duplicate":
            self.session.sync_subscriptions_to_profile()
            clone = target.copy(self._unique_name(target.name + " copy"))
            self.profiles.insert(self.profiles.index(target) + 1, clone)
            self.activate_profile(clone)
        elif action == "rename":
            name, ok = QInputDialog.getText(self, "Rename Server", "New name:", text=target.name)
            name = name.strip()
            if not ok or not name or name == target.name:
                return
            if any(p.name == name for p in self.profiles):
                QMessageBox.warning(self, "Rename Server", f"A server named “{name}” already exists.")
                return
            target.name = name
            self._refresh_profiles()
            self._update_title()
            self._schedule_profile_save()
        elif action == "delete":
            if len(self.profiles) <= 1:
                QMessageBox.information(self, "Delete Server", "You need at least one server profile.")
                return
            answer = QMessageBox.question(self, "Delete Server",
                                          f"Delete “{target.name}” with its settings, subscriptions and variables?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            pos = self.profiles.index(target)
            self.profiles.remove(target)
            if target is self.profile:
                self.activate_profile(self.profiles[max(0, pos - 1)])
            else:
                self._refresh_profiles()
            self.save_profiles()
        elif action == "import":
            self.import_profiles()
        elif action == "export":
            self.export_profiles()

    def import_profiles(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Profiles", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            items = data.get("profiles", data) if isinstance(data, dict) else data
            imported = [Profile.from_dict(d) for d in items]
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            QMessageBox.warning(self, "Import Profiles", f"Can't import this file:\n{exc}")
            return
        for p in imported:
            p.name = self._unique_name(p.name)
            self.profiles.append(p)
        self._refresh_profiles()
        self.save_profiles()
        self.statusBar().showMessage(f"Imported {len(imported)} profile(s)", 5000)

    def export_profiles(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Profiles", "stomp-profiles.json", "JSON (*.json)")
        if not path:
            return
        self.session.sync_subscriptions_to_profile()
        include_secrets = QMessageBox.question(
            self, "Export Profiles", "Include passcodes and handshake header values (tokens)?"
        ) == QMessageBox.StandardButton.Yes
        out = []
        for p in self.profiles:
            d = p.to_dict()
            if not include_secrets:
                d["passcode"] = ""
                d["ws_headers"] = [[r[0], r[1], ""] if len(r) == 3 else [True, r[0], ""] for r in d["ws_headers"]]
            out.append(d)
        Path(path).write_text(json.dumps({"profiles": out}, indent=2, ensure_ascii=False), encoding="utf-8")

    def _on_profile_attr_changed(self, attr: str) -> None:
        self._schedule_profile_save()
        if attr in ("url", "transport"):
            self._update_title()
            self._update_transport_label()
            if attr == "url":
                self._refresh_profiles()

    def _update_transport_label(self) -> None:
        kind = self.profile.transport
        resolved = resolve_kind(self.profile.url, kind)
        short = SHORT_TRANSPORT.get(resolved, resolved)
        self.address.transport.setText(f"{short} ▾")
        self.address.transport.setToolTip(
            f"Transport: {TRANSPORT_LABELS.get(resolved, resolved)}" + (" (picked from the URL)" if kind == "auto" else ""))
        action = self.transport_actions.get(kind)
        if action is not None:
            action.setChecked(True)

    def _schedule_profile_save(self) -> None:
        self._profile_save_timer.start()

    def save_profiles(self) -> None:
        self.session.sync_subscriptions_to_profile()
        self._safe(self.store.save_profiles, self.profiles, self.profile.name)

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except OSError as exc:
            self.statusBar().showMessage(f"Couldn't save: {exc}", 8000)

    def _update_title(self) -> None:
        self.setWindowTitle(f"{self.profile.name} — {APP_NAME}")

    # ------------------------------------------------------------------ settings & theme
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.store.base / "history", self)
        if dlg.exec():
            new = dlg.result_settings(self.settings)
            for key, value in new.to_dict().items():
                setattr(self.settings, key, value)
            self.apply_settings()
            self._safe(self.store.save_settings, self.settings)

    def apply_settings(self) -> None:
        s = self.settings
        apply_theme(QApplication.instance(), s.theme)
        self.model.ts_format = s.timestamp_format
        self.model.max_entries = s.max_view_entries
        self.conversation.apply_settings(s)
        self._refresh_theme()

    def _system_scheme_changed(self, *_args) -> None:
        if current_pref() == "system":
            apply_theme(QApplication.instance(), "system")
            QTimer.singleShot(0, self._refresh_theme)

    def _refresh_theme(self) -> None:
        self.conversation.refresh_render()
        self.inspector.apply_settings(self.settings)
        self.composer.set_font_size(self.settings.font_size)
        self.composer._style_send(self.composer._repeat_timer.isActive())
        self.sidebar.refresh_theme()
        self.address.status.refresh()
        self.address.update()
        self._update_state(self.session.state)
        for split in (self.main_split, self.center_split):
            for i in range(split.count()):
                split.handle(i).update()

    def show_help(self) -> None:
        rows = "".join(f"<tr><td><code>{t}</code></td><td>&nbsp;&nbsp;{d}</td></tr>" for t, d in BUILTINS_HELP)
        QMessageBox.information(self, "STOMP Tester Help", f"""
<h3>Getting started</h3>
<ol>
<li>Pick a server in the sidebar (or add one with ＋) and check the URL in the toolbar:
<code>ws://host:8080/ws</code> (WebSocket), <code>http://host:8080/ws</code> (SockJS), <code>tcp://host:61613</code>.</li>
<li>Press <b>Connect</b> (F5 or ⌘R). The CONNECT frame is sent for you — tune it in the inspector's Connection tab.</li>
<li>Type a destination under <b>Subscriptions</b> and press Return. Compose frames below and press <b>⌘↩</b>.</li>
</ol>
<h3>The NULL terminator</h3>
<p>Every STOMP frame ends with a NULL byte. The <b>␀ NULL</b> chip adds it for you. In the raw editor you can
type <code>\\u0000</code>, <code>\\x00</code> or <code>^@</code> instead. The eye button shows the exact bytes
(␊ LF, ␀ NULL) and warns when the terminator is missing.</p>
<h3>Placeholders</h3><table>{rows}</table>
<h3>Search</h3>
<p>Type words, <code>destination:/topic/x</code> for a header, or <code>/regex/</code>.
Clicking a subscription in the sidebar shows only its messages.</p>
""")

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3><p>Test STOMP servers over WebSocket, SockJS and TCP.</p>"
                          f"<p>Data folder:<br><code>{self.store.base}</code></p>")

    # ------------------------------------------------------------------ shutdown
    def closeEvent(self, event) -> None:
        if self.session.is_active and self.settings.confirm_exit_connected:
            answer = QMessageBox.question(self, "Quit", "You're still connected. Disconnect and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.composer.stop_repeat()
        if self.session.is_active:
            self.session.disconnect_(force=True)
        self.settings.show_heartbeats = self.conversation.proxy.show_heartbeats
        self.settings.show_raw_chunks = self.conversation.proxy.show_raw
        self.settings.autoscroll = self.conversation.autoscroll
        self.save_profiles()
        self._safe(self.store.save_templates, self.templates)
        self._safe(self.store.save_settings, self.settings)
        self._safe(self.store.save_recent, self.composer.recent)
        self._safe(self.store.save_draft, self.composer.snapshot("draft"))
        self._save_layout()
        self._end_history()
        for win in list(self.history_windows):
            win.close()
        event.accept()
