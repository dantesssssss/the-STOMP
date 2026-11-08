"""Settings window, subscription sheet, history browser and read-only history viewer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QSpinBox, QStackedWidget, QToolBar, QVBoxLayout, QWidget,
)

from ..core.frame import COMMON_HEADERS
from ..core.models import AppSettings
from ..core.session import Subscription
from ..core.storage import export_entries, list_history, read_history
from ..core.textutil import human_size
from .controls import FormSection, SegmentedControl, ThinSplitter, ToggleSwitch, icon_button, label, push_button
from .conversation import ConversationPanel
from .inspector import MessageInspector
from .log_model import LogModel
from .sidebar import ICON, OBJ, SUBTITLE, BADGE, SidebarDelegate
from .widgets import KeyValueTable

EXPORT_FILTERS = "HTML chat (*.html);;JSON Lines (*.jsonl);;JSON (*.json);;Plain text (*.txt)"
ACK_MODES = ["auto", "client", "client-individual"]


def ask_export_path(parent, default_name: str) -> Path | None:
    path, selected = QFileDialog.getSaveFileName(parent, "Export Conversation", default_name, EXPORT_FILTERS)
    if not path:
        return None
    p = Path(path)
    if not p.suffix:
        p = p.with_suffix(selected.split("*")[-1].rstrip(")"))
    return p


def _buttons(*widgets) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addStretch(1)
    for w in widgets:
        row.addWidget(w)
    return row


# --------------------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, default_history_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 560)
        s = settings

        self.tabs = SegmentedControl([("sliders", "General"), ("eye", "Appearance"), ("clock", "History")])
        pages = QStackedWidget()
        self.tabs.currentChanged.connect(pages.setCurrentIndex)

        # general
        general = self._page()
        sec = FormSection("Conversation")
        self.default_view = SegmentedControl(["Chat", "Table"], compact=True)
        self.default_view.setCurrentIndex(1 if s.default_view == "table" else 0, emit=False)
        sec.add_row("Default view", self.default_view)
        self.max_entries = QSpinBox()
        self.max_entries.setRange(500, 2_000_000)
        self.max_entries.setSingleStep(5000)
        self.max_entries.setValue(s.max_view_entries)
        self.max_entries.setMinimumWidth(120)
        sec.add_row("Keep in view", self.max_entries, "Older entries leave the view but stay in the history file.")
        self.confirm_exit = ToggleSwitch()
        self.confirm_exit.setChecked(s.confirm_exit_connected)
        sec.add_row("Ask before quitting while connected", self.confirm_exit)
        general.layout().addWidget(sec)
        general.layout().addWidget(label(
            "Connection, framing and behaviour options are stored per server profile — "
            "find them in the Connection tab of the inspector.", "caption", wrap=True))
        general.layout().addStretch(1)

        # appearance
        appearance = self._page()
        sec = FormSection("Appearance")
        self.theme = SegmentedControl(["System", "Light", "Dark"], compact=True)
        self.theme.setCurrentIndex(["system", "light", "dark"].index(s.theme) if s.theme in ("system", "light", "dark") else 0,
                                   emit=False)
        sec.add_row("Theme", self.theme)
        self.font_size = QSpinBox()
        self.font_size.setRange(0, 32)
        self.font_size.setSpecialValueText("Automatic")
        self.font_size.setSuffix(" pt")
        self.font_size.setValue(s.font_size)
        self.font_size.setMinimumWidth(120)
        sec.add_row("Code font size", self.font_size)
        self.ts = QComboBox()
        for text, value in (("12:34:56.789", "time_ms"), ("12:34:56", "time"),
                            ("2025-01-31 12:34:56.789", "datetime"), ("+1.234 s from first entry", "relative")):
            self.ts.addItem(text, value)
        self.ts.setCurrentIndex(max(0, self.ts.findData(s.timestamp_format)))
        sec.add_row("Timestamps", self.ts)
        appearance.layout().addWidget(sec)
        sec = FormSection("Chat Bubbles")
        self.show_headers = ToggleSwitch()
        self.show_headers.setChecked(s.chat_show_headers)
        sec.add_row("Show headers", self.show_headers)
        self.pretty = ToggleSwitch()
        self.pretty.setChecked(s.pretty_json)
        sec.add_row("Pretty-print JSON", self.pretty)
        self.preview_lines = QSpinBox()
        self.preview_lines.setRange(1, 500)
        self.preview_lines.setValue(s.chat_preview_lines)
        self.preview_lines.setSuffix(" lines")
        self.preview_lines.setMinimumWidth(120)
        sec.add_row("Body preview", self.preview_lines, "Longer bodies are shortened; the inspector shows everything.")
        self.wrap = ToggleSwitch()
        self.wrap.setChecked(s.wrap_lines)
        sec.add_row("Wrap long lines in the inspector", self.wrap)
        appearance.layout().addWidget(sec)
        appearance.layout().addStretch(1)

        # history
        history = self._page()
        sec = FormSection("Recording", "Every connection is written to a .jsonl file you can reopen or export later.")
        self.autosave = ToggleSwitch()
        self.autosave.setChecked(s.autosave_history)
        sec.add_row("Record sessions", self.autosave)
        folder = QWidget()
        fl = QHBoxLayout(folder)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(4)
        self.history_dir = QLineEdit(s.history_dir)
        self.history_dir.setPlaceholderText(str(default_history_dir))
        fl.addWidget(self.history_dir, 1)
        fl.addWidget(icon_button("folder", "Choose folder", self._browse, size=26))
        sec.add_row("Folder", folder, stretch=True)
        self.keep = QSpinBox()
        self.keep.setRange(0, 100000)
        self.keep.setSpecialValueText("Keep all")
        self.keep.setValue(s.history_keep)
        self.keep.setSuffix(" sessions")
        self.keep.setMinimumWidth(140)
        sec.add_row("Keep newest", self.keep)
        history.layout().addWidget(sec)
        history.layout().addStretch(1)

        for page in (general, appearance, history):
            pages.addWidget(page)

        cancel = push_button("Cancel", slot=self.reject)
        ok = push_button("Done", "primary", slot=self.accept)
        ok.setDefault(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        layout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(pages, 1)
        layout.addLayout(_buttons(cancel, ok))

    @staticmethod
    def _page() -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        return page

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "History folder", self.history_dir.text())
        if path:
            self.history_dir.setText(path)

    def result_settings(self, base: AppSettings) -> AppSettings:
        out = AppSettings.from_dict(base.to_dict())
        out.theme = ["system", "light", "dark"][self.theme.currentIndex()]
        out.font_size = self.font_size.value()
        out.timestamp_format = self.ts.currentData()
        out.default_view = "table" if self.default_view.currentIndex() == 1 else "chat"
        out.chat_preview_lines = self.preview_lines.value()
        out.chat_show_headers = self.show_headers.isChecked()
        out.pretty_json = self.pretty.isChecked()
        out.wrap_lines = self.wrap.isChecked()
        out.max_view_entries = self.max_entries.value()
        out.autosave_history = self.autosave.isChecked()
        out.history_dir = self.history_dir.text().strip()
        out.history_keep = self.keep.value()
        out.confirm_exit_connected = self.confirm_exit.isChecked()
        return out


# --------------------------------------------------------------------------- subscription sheet
class SubscriptionDialog(QDialog):
    def __init__(self, sub: Subscription | None, default_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Subscription" if sub else "New Subscription")
        self.setMinimumSize(480, 440)
        spec = sub.spec if sub else None
        self.destination = QLineEdit(spec.destination if spec else "")
        self.destination.setPlaceholderText("/topic/…")
        self.destination.setMinimumWidth(240)
        self.sub_id = QLineEdit(spec.id if spec else default_id)
        self.ack_switch = SegmentedControl(["auto", "client", "individual"], compact=True)
        ack = spec.ack if spec else "auto"
        self.ack_switch.setCurrentIndex(ACK_MODES.index(ack) if ack in ACK_MODES else 0, emit=False)
        self.ack_switch.setToolTips(["Server considers messages delivered immediately",
                                     "ACK acknowledges this and all earlier messages",
                                     "ACK acknowledges one message at a time"])
        self.headers = KeyValueTable(COMMON_HEADERS + ["selector", "durable", "x-queue-name", "prefetch-count"],
                                     "Header", add_text="Add Header")
        self.headers.set_rows(spec.headers if spec else [])
        sec = FormSection()
        sec.add_row("Destination", self.destination, stretch=True)
        sec.add_row("Subscription id", self.sub_id, stretch=True)
        sec.add_row("Acknowledgement", self.ack_switch)
        extra = FormSection("Extra SUBSCRIBE Headers", "{{placeholders}} work in destinations and headers.")
        self.headers.setMinimumHeight(120)
        extra.add_widget(self.headers)
        cancel = push_button("Cancel", slot=self.reject)
        ok = push_button("Save" if sub else "Subscribe", "primary", slot=self._accept)
        ok.setDefault(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)
        layout.addWidget(label(self.windowTitle(), "headline"))
        layout.addWidget(sec)
        layout.addWidget(extra, 1)
        layout.addLayout(_buttons(cancel, ok))

    @property
    def ack(self) -> str:
        return ACK_MODES[self.ack_switch.currentIndex()]

    def _accept(self) -> None:
        if not self.destination.text().strip():
            self.destination.setFocus()
            return
        self.accept()


# --------------------------------------------------------------------------- history
class HistoryWindow(QMainWindow):
    """Read-only replay of a recorded session with the same views as the live window."""

    action_requested = Signal(str, object)

    def __init__(self, path: Path, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.path = path
        self.meta, entries = read_history(path)
        self.setWindowTitle(f"{self.meta.get('profile', path.stem)} — {path.name}")
        self.resize(1200, 800)
        self.model = LogModel(self, max_entries=0)
        self.model.ts_format = settings.timestamp_format
        self.model.set_entries(entries)
        self.panel = ConversationPanel(self.model, settings, live=False)
        self.inspector = MessageInspector(settings)
        self.inspector.setObjectName("InspectorPane")
        self.inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.panel.entry_selected.connect(self.inspector.show_entry)
        self.panel.action_requested.connect(self.action_requested.emit)
        self.inspector.action_requested.connect(self.action_requested.emit)

        bar = QToolBar()
        bar.setObjectName("MainToolbar")
        bar.setMovable(False)
        started = self.meta.get("started")
        info = f"{self.meta.get('url', '')}"
        if started:
            info += "  ·  " + datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
        info += f"  ·  {len(entries)} entries"
        title = QWidget()
        tl = QVBoxLayout(title)
        tl.setContentsMargins(4, 0, 4, 0)
        tl.setSpacing(0)
        tl.addWidget(label(self.meta.get("profile", path.stem), "headline"))
        tl.addWidget(label(info, "caption"))
        bar.addWidget(title)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().Policy.Expanding, spacer.sizePolicy().Policy.Preferred)
        bar.addWidget(spacer)
        bar.addWidget(self.panel.filter_bar)
        bar.addWidget(push_button("Export…", icon_name="export", slot=self._export))
        self.addToolBar(bar)

        split = ThinSplitter(Qt.Orientation.Horizontal)
        split.set_fill(1, "bg", "window")
        split.addWidget(self.panel)
        split.addWidget(self.inspector)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 440])
        self.setCentralWidget(split)
        self.panel.refresh_render()

    def _export(self) -> None:
        path = ask_export_path(self, self.path.stem + ".html")
        if path:
            export_entries(path, self.panel.visible_entries(), self.meta)


class HistoryDialog(QDialog):
    open_requested = Signal(object)  # Path

    def __init__(self, directory: Path, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.setWindowTitle("History")
        self.setMinimumSize(640, 520)
        self.search = QLineEdit()
        self.search.setProperty("kind", "search")
        self.search.setPlaceholderText("Search sessions")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.list = QListWidget()
        self.list.setItemDelegate(SidebarDelegate(self.list))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.itemDoubleClicked.connect(lambda _: self._open())
        self.empty = label("No recorded sessions yet. Connect to a server and the conversation is saved automatically.",
                           "secondary", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        open_btn = push_button("Open", "primary", slot=self._open)
        open_btn.setDefault(True)
        export_btn = push_button("Export…", icon_name="export", slot=self._export)
        delete_btn = push_button("Delete", "destructive", slot=self._delete)
        folder_btn = push_button("Show Folder", icon_name="folder",
                                 slot=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory))))
        close_btn = push_button("Close", slot=self.reject)
        row = QHBoxLayout()
        row.setSpacing(8)
        for b in (folder_btn, delete_btn):
            row.addWidget(b)
        row.addStretch(1)
        for b in (export_btn, close_btn, open_btn):
            row.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Recorded Sessions", "headline"))
        head.addStretch(1)
        head.addWidget(self.search)
        layout.addLayout(head)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty, 1)
        where = label(f"Stored in {directory}", "caption", wrap=True, selectable=True)
        layout.addWidget(where)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        needle = self.search.text().lower().strip()
        items = [i for i in list_history(self.directory)
                 if not needle or needle in (i.profile + " " + i.url).lower()]
        self.list.clear()
        for info in items:
            started = datetime.fromtimestamp(info.started).strftime("%a %d %b %Y, %H:%M:%S")
            item = QListWidgetItem(info.profile)
            item.setData(OBJ, info.path)
            item.setData(SUBTITLE, f"{started}  ·  {info.url}")
            item.setData(ICON, "clock")
            item.setData(BADGE, human_size(info.size))
            item.setSizeHint(QSize(0, 46))
            self.list.addItem(item)
        has = bool(items)
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            self.list.setCurrentRow(0)

    def _selected_paths(self) -> list[Path]:
        return [item.data(OBJ) for item in self.list.selectedItems()]

    def _open(self) -> None:
        for path in self._selected_paths()[:5]:
            self.open_requested.emit(path)

    def _export(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        target = ask_export_path(self, paths[0].stem + ".html")
        if target:
            meta, entries = read_history(paths[0])
            export_entries(target, entries, meta)

    def _delete(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        answer = QMessageBox.question(self, "Delete Sessions", f"Delete {len(paths)} recorded session(s)? This can't be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for p in paths:
            try:
                p.unlink()
            except OSError:
                pass
        self.refresh()


__all__ = ["SettingsDialog", "SubscriptionDialog", "HistoryWindow", "HistoryDialog", "ask_export_path"]
