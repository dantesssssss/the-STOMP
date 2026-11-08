"""Frame composer: structured or raw editing, framing chips, live byte preview, repeat sending."""

from __future__ import annotations

import json
import re
import time

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QPlainTextEdit, QSizePolicy,
    QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.frame import (
    CLIENT_COMMANDS, COMMON_HEADERS, REQUIRED_HEADERS, EncodeOptions, Frame, encode_frame, frame_head_text,
)
from ..core.models import LogEntry, Template
from ..core.session import StompSession
from ..core.templating import BUILTINS_HELP
from ..core.textutil import interpret_escapes, interpret_null_escapes, try_pretty_json, visualize
from ..core.transport import url_host
from .binding import ProfileHub
from .controls import (
    SegmentedControl, ThinSplitter, chip, icon_button, label, push_button, separator, text_button,
)
from .highlighters import FrameHighlighter, JsonHighlighter
from .icons import pixmap, set_icon
from .theme import mono_font, tokens
from .widgets import KeyValueTable

NO_DESTINATION = {"ACK", "NACK", "UNSUBSCRIBE", "BEGIN", "COMMIT", "ABORT", "DISCONNECT", "CONNECT", "STOMP"}
CONTENT_TYPES = ["", "application/json", "text/plain", "text/plain;charset=UTF-8", "application/xml", "application/octet-stream"]
SERVER_ONLY_HEADERS = {"subscription", "message-id", "ack", "content-length", "destination", "receipt-id", "server", "session"}

RAW_SKELETONS = {
    "CONNECT": "CONNECT\naccept-version:1.2\nhost:{host}\nheart-beat:0,0\n\n",
    "SUBSCRIBE": "SUBSCRIBE\nid:sub-{{{{counter}}}}\ndestination:/topic/test\nack:auto\n\n",
    "SEND (JSON)": "SEND\ndestination:/app/test\ncontent-type:application/json\n\n{{\"hello\":\"world\"}}",
    "UNSUBSCRIBE": "UNSUBSCRIBE\nid:sub-0\n\n",
    "DISCONNECT": "DISCONNECT\nreceipt:bye\n\n",
}


def _editor(font, placeholder: str) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setFont(font)
    edit.setPlaceholderText(placeholder)
    edit.setTabStopDistance(font.pointSizeF() * 2.5)
    edit.setMinimumHeight(64)
    return edit


class Composer(QWidget):
    status_message = Signal(str)
    save_template_requested = Signal(object)  # Template
    recent_changed = Signal()

    def __init__(self, session: StompSession, hub: ProfileHub, font_size: int, parent=None):
        super().__init__(parent)
        self.setObjectName("Composer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.session = session
        self.hub = hub
        self.recent: list[Template] = []
        mono = mono_font(font_size)

        # ---------------------------------------------------------------- header row
        self.mode = SegmentedControl(["Structured", "Raw"])
        self.mode.setToolTips(["Build the frame from fields (⇧⌘1)", "Type the whole frame by hand (⇧⌘2)"])
        self.command = QComboBox()
        self.command.setEditable(True)
        self.command.setProperty("kind", "command")
        self.command.addItems(CLIENT_COMMANDS)
        self.command.setFixedWidth(138)
        self.command.setToolTip("STOMP command — editable, so you can send anything (even invalid commands)")
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("Destination, e.g. /app/hello")
        self._dest_model = QStringListModel(self)
        completer = QCompleter(self._dest_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.destination.setCompleter(completer)
        self.content_type = QComboBox()
        self.content_type.setEditable(True)
        self.content_type.addItems(CONTENT_TYPES)
        self.content_type.lineEdit().setPlaceholderText("content-type")
        self.content_type.setMinimumWidth(150)
        self.content_type.setMaximumWidth(230)
        self.content_type.setToolTip("content-type header (left empty = not sent)")

        self.raw_tools = QWidget()
        rt = QHBoxLayout(self.raw_tools)
        rt.setContentsMargins(0, 0, 0, 0)
        rt.setSpacing(2)
        rt.addWidget(text_button("From Structured", "Copy the structured frame into the raw editor", self.structured_to_raw, "arrow.down"))
        rt.addWidget(text_button("Insert \\u0000", "Insert a NULL escape at the cursor", lambda: self.raw.insertPlainText("\\u0000")))
        skeleton = text_button("Skeleton", "Start from a frame skeleton", icon_name="doc")
        skeleton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(skeleton)
        for name in RAW_SKELETONS:
            menu.addAction(name, lambda n=name: self._insert_skeleton(n))
        skeleton.setMenu(menu)
        rt.addWidget(skeleton)
        raw_ph = text_button("Placeholder", "Insert a {{placeholder}}", icon_name="braces")
        raw_ph.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        raw_ph.setMenu(self._placeholder_menu(lambda text: self.raw.insertPlainText(text)))
        rt.addWidget(raw_ph)
        rt.addStretch(1)
        self.raw_tools.hide()

        self.btn_preview = icon_button("eye", "Show the exact bytes that will be sent (⌘P)", checkable=True)
        self.btn_save = icon_button("bookmark", "Save as template (⌘S)", self.save_as_template)
        self.btn_recent = icon_button("clock", "Recent sends")
        self.btn_recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(self.btn_recent)
        self.btn_recent.setMenu(self.recent_menu)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.mode)
        header.addSpacing(4)
        header.addWidget(self.command)
        header.addWidget(self.destination, 1)
        self.header_spacer = QWidget()
        self.header_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.header_spacer.hide()
        header.addWidget(self.header_spacer, 1)
        header.addSpacing(4)
        header.addWidget(self.btn_preview)
        header.addWidget(self.btn_save)
        header.addWidget(self.btn_recent)

        # ---------------------------------------------------------------- structured page
        self.headers = KeyValueTable(COMMON_HEADERS, "Header", value_words=["auto", "client", "client-individual",
                                                                             "application/json", "text/plain"],
                                     add_text="Add Header")
        self.required_label = label("", "caption")
        self.required_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.btn_required = text_button("Add Required", "Add the headers this command requires", self.add_required_headers, "wand")
        head_caption = QHBoxLayout()
        head_caption.setSpacing(6)
        head_caption.addWidget(label("Headers", "caption-strong"))
        head_caption.addWidget(self.required_label, 1)
        head_caption.addWidget(self.btn_required)
        headers_box = QWidget()
        hl = QVBoxLayout(headers_box)
        hl.setContentsMargins(0, 0, 6, 0)
        hl.setSpacing(4)
        hl.addLayout(head_caption)
        hl.addWidget(self.headers, 1)

        self.body = _editor(mono, "Body (optional) — {{placeholders}} are expanded when enabled")
        self.body_hl = JsonHighlighter(self.body.document())
        body_caption = QHBoxLayout()
        body_caption.setSpacing(2)
        body_caption.addWidget(label("Body", "caption-strong"))
        body_caption.addSpacing(6)
        body_caption.addWidget(self.content_type)
        body_caption.addStretch(1)
        body_caption.addWidget(text_button("Format", "Pretty-print the JSON body", self.format_json))
        body_caption.addWidget(text_button("Minify", "Compact the JSON body", self.minify_json))
        placeholder = icon_button("braces", "Insert a {{placeholder}}", size=26, icon_size=15, color="accent", hover="accent")
        placeholder.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        placeholder.setMenu(self._placeholder_menu(lambda text: self.body.insertPlainText(text)))
        body_caption.addWidget(placeholder)
        body_box = QWidget()
        bl = QVBoxLayout(body_box)
        bl.setContentsMargins(6, 0, 0, 0)
        bl.setSpacing(4)
        bl.addLayout(body_caption)
        bl.addWidget(self.body, 1)

        split = ThinSplitter(Qt.Orientation.Horizontal)
        split.set_fill(1, "window", "window")
        split.addWidget(headers_box)
        split.addWidget(body_box)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self.split = split

        # ---------------------------------------------------------------- raw page
        self.raw = _editor(mono, "Type the whole frame exactly as it goes on the wire, e.g.\n\n"
                                 "SEND\ndestination:/app/hello\n\nhello^@\n\n"
                                 "\\u0000, \\x00 and ^@ become the NULL byte. With ␀ NULL on you can leave it out.")
        self.raw_hl = FrameHighlighter(self.raw.document())

        raw_page = QWidget()
        rp = QVBoxLayout(raw_page)
        rp.setContentsMargins(0, 0, 0, 0)
        rp.setSpacing(4)
        rp.addWidget(self.raw_tools)
        rp.addWidget(self.raw, 1)
        self.pages = QStackedWidget()
        self.pages.addWidget(split)
        self.pages.addWidget(raw_page)

        # ---------------------------------------------------------------- preview
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        self.preview.setMaximumHeight(110)
        self.preview.setMinimumHeight(54)
        self.preview_info = label("", "caption")
        self.preview_info.setTextFormat(Qt.TextFormat.RichText)
        self.preview_box = QWidget()
        pl = QVBoxLayout(self.preview_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(3)
        pl.addWidget(self.preview_info)
        pl.addWidget(self.preview)
        self.preview_box.hide()

        # ---------------------------------------------------------------- footer
        self.chip_null = hub.bind_check(chip("NULL \\0", "Append the NULL terminator (\\0 / \\u0000) every STOMP frame needs"), "append_null")
        self.chip_receipt = hub.bind_check(chip("Receipt", "Ask for a RECEIPT and show the round-trip time"), "auto_receipt")
        self.chip_ph = hub.bind_check(chip("{{ }}", "Expand {{placeholders}}"), "placeholders")
        self.more = QToolButton()
        self.more.setProperty("kind", "chip")
        self.more.setText("More  ▾")
        self.more.setToolTip("Other framing options")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        more_menu = QMenu(self.more)
        for text, attr in [
            ("Add content-length header", "auto_content_length"),
            ("Raw editor: \\u0000 and ^@ mean NULL", "raw_null_escapes"),
            ("Raw editor: interpret all escapes", "raw_all_escapes"),
            ("Line break after NULL", "trailing_eol"),
            ("Escape header values", "escape_headers"),
        ]:
            more_menu.addAction(hub.bind_action(QAction(text, more_menu), attr))
        more_menu.addSeparator()
        more_menu.addAction(hub.bind_action(QAction("CRLF line endings", more_menu), "eol", "CRLF", "LF"))
        self.more.setMenu(more_menu)

        self.repeat = QSpinBox()
        self.repeat.setRange(1, 1_000_000)
        self.repeat.setPrefix("× ")
        self.repeat.setToolTip("Send this many times — placeholders are re-rendered each time")
        self.repeat.setFixedWidth(84)
        self.interval = QSpinBox()
        self.interval.setRange(0, 3_600_000)
        self.interval.setValue(1000)
        self.interval.setSuffix(" ms")
        self.interval.setPrefix("every ")
        self.interval.setSingleStep(100)
        self.interval.setFixedWidth(128)
        self.interval.setToolTip("Delay between repeated sends")
        self.interval.hide()
        self.repeat.valueChanged.connect(lambda v: self.interval.setVisible(v > 1))
        self.btn_send = push_button("Send", "primary", "paperplane", tooltip="Send (⌘↩)")
        self.btn_send.setMinimumWidth(104)
        self.btn_send.setMinimumHeight(30)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        for w in (self.chip_null, self.chip_receipt, self.chip_ph, self.more):
            footer.addWidget(w)
        footer.addStretch(1)
        repeat_icon = QLabel()
        repeat_icon.setPixmap(pixmap("repeat", "secondary", 15))
        repeat_icon.setToolTip("Repeat sending")
        footer.addWidget(repeat_icon)
        footer.addWidget(self.repeat)
        footer.addWidget(self.interval)
        footer.addSpacing(4)
        footer.addWidget(self.btn_send)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.preview_box)
        layout.addWidget(separator())
        layout.addLayout(footer)

        # ---------------------------------------------------------------- wiring
        self._preview_timer = QTimer(self, singleShot=True, interval=150, timeout=self.update_preview)
        self._repeat_timer = QTimer(self, timeout=self._repeat_tick)
        self._repeat_left = 0
        self._repeat_total = 0
        for signal in (
            self.command.currentTextChanged, self.destination.textChanged, self.content_type.currentTextChanged,
            self.headers.changed, self.body.textChanged, self.raw.textChanged, self.mode.currentChanged,
        ):
            signal.connect(self._schedule_preview)
        hub.changed.connect(self._schedule_preview)
        hub.switched.connect(self._schedule_preview)
        session.state_changed.connect(self._schedule_preview)
        self.mode.currentChanged.connect(self._mode_changed)
        self.command.currentTextChanged.connect(self._command_changed)
        self.btn_preview.toggled.connect(self._toggle_preview)
        self.btn_send.clicked.connect(self.on_send_clicked)
        self._command_changed(self.command.currentText())
        self.load_template(Template(name="", command="SEND", destination="/app/hello",
                                    content_type="application/json", body='{\n  "hello": "world"\n}'))

    # ------------------------------------------------------------------ mode & helpers
    @property
    def is_raw(self) -> bool:
        return self.mode.currentIndex() == 1

    def set_mode(self, index: int) -> None:
        self.mode.setCurrentIndex(index)
        self._mode_changed(index)

    def _mode_changed(self, index: int) -> None:
        raw = index == 1
        self.pages.setCurrentIndex(index)
        for w in (self.command, self.destination):
            w.setVisible(not raw)
        self.raw_tools.setVisible(True)
        self.header_spacer.setVisible(raw)

    def _style_send(self, running: bool) -> None:
        if running:
            self.btn_send.setProperty("kind", "destructive")
            set_icon(self.btn_send, "xmark", 14, color="red_text", hover="red_text")
        else:
            self.btn_send.setProperty("kind", "primary")
            set_icon(self.btn_send, "paperplane", 14, color="#FFFFFF", hover="#FFFFFF")
        self.btn_send.style().unpolish(self.btn_send)
        self.btn_send.style().polish(self.btn_send)

    def _placeholder_menu(self, insert) -> QMenu:
        menu = QMenu(self)
        for token, desc in BUILTINS_HELP:
            first = token.split(" ")[0]
            menu.addAction(f"{token}    {desc}", lambda t=first: insert(t))
        vars_menu = menu.addMenu("Variables")
        vars_menu.aboutToShow.connect(lambda: self._fill_vars_menu(vars_menu, insert))
        return menu

    def _fill_vars_menu(self, menu: QMenu, insert) -> None:
        menu.clear()
        names = list(self.hub.profile.variables_dict())
        if not names:
            menu.addAction("No variables — add them in the Variables inspector").setEnabled(False)
        for name in names:
            menu.addAction("{{" + name + "}}", lambda n=name: insert("{{" + n + "}}"))

    def set_font_size(self, size: int) -> None:
        font = mono_font(size)
        for edit in (self.body, self.raw, self.preview):
            edit.setFont(font)
        self.body_hl.refresh_colors()
        self.raw_hl.refresh_colors()

    def set_destinations(self, destinations: list[str]) -> None:
        self._dest_model.setStringList(sorted(set(d for d in destinations if d)))

    def _command_changed(self, command: str) -> None:
        cmd = command.strip()
        self.destination.setEnabled(cmd not in NO_DESTINATION)
        self.destination.setPlaceholderText("Destination, e.g. /app/hello" if cmd not in NO_DESTINATION
                                            else f"{cmd} has no destination")
        required = REQUIRED_HEADERS.get(cmd, [])
        self.required_label.setText(f"· {cmd} needs {', '.join(required)}" if required else "")
        self.btn_required.setVisible(bool(required))

    def add_required_headers(self) -> None:
        cmd = self.command.currentText().strip()
        present = {k for _, k, _ in self.headers.rows()}
        if self.destination.isEnabled() and self.destination.text().strip():
            present.add("destination")
        defaults = {
            "destination": "/app/test",
            "id": "sub-{{counter}}" if cmd == "SUBSCRIBE" else "",
            "transaction": "tx-1",
            "accept-version": "1.2",
            "host": self.hub.profile.vhost or url_host(self.hub.profile.url) or "localhost",
        }
        for name in REQUIRED_HEADERS.get(cmd, []):
            if name not in present:
                if name == "destination" and self.destination.isEnabled():
                    self.destination.setText(defaults["destination"])
                    self.destination.setFocus()
                else:
                    self.headers.add_row(name, defaults.get(name, ""))
        if cmd == "SUBSCRIBE" and "ack" not in present:
            self.headers.add_row("ack", "auto")

    def format_json(self) -> None:
        pretty = try_pretty_json(self.body.toPlainText())
        if pretty is None:
            self.status_message.emit("The body is not valid JSON")
        else:
            self.body.setPlainText(pretty)

    def minify_json(self) -> None:
        try:
            self.body.setPlainText(json.dumps(json.loads(self.body.toPlainText()), separators=(",", ":"), ensure_ascii=False))
        except ValueError:
            self.status_message.emit("The body is not valid JSON")

    def structured_to_raw(self) -> None:
        """Copy the structured frame into the raw editor (placeholders kept, no escaping)."""
        frame = self.build_frame(lambda text: text)
        opts = EncodeOptions(eol="\n", escape_headers=False, content_length=False)
        text = frame_head_text(frame, opts) + self.body.toPlainText()
        if not self.hub.profile.append_null:
            text += "\\u0000"
        self.raw.setPlainText(text)
        self.set_mode(1)

    def _insert_skeleton(self, name: str) -> None:
        host = self.hub.profile.vhost or url_host(self.hub.profile.url) or "localhost"
        text = RAW_SKELETONS[name].format(host=host)
        if not self.hub.profile.append_null:
            text += "\\u0000"
        self.raw.setPlainText(text)
        self.set_mode(1)

    # ------------------------------------------------------------------ building
    def build_frame(self, render) -> Frame:
        cmd = self.command.currentText().strip()
        rows = [(render(k), render(v)) for en, k, v in self.headers.rows() if en and k.strip()]
        keys = {k for k, _ in rows}
        headers: list[tuple[str, str]] = []
        dest = self.destination.text().strip()
        if dest and cmd not in NO_DESTINATION and "destination" not in keys:
            headers.append(("destination", render(dest)))
        headers += rows
        ctype = self.content_type.currentText().strip()
        if ctype and "content-type" not in keys:
            headers.append(("content-type", ctype))
        return Frame(cmd, headers, render(self.body.toPlainText()).encode("utf-8"))

    def build_raw(self, render) -> bytes:
        p = self.hub.profile
        text = render(self.raw.toPlainText())
        if p.raw_all_escapes:
            text = interpret_escapes(text)
        elif p.raw_null_escapes:
            text = interpret_null_escapes(text)
        if p.eol == "CRLF":
            text = re.sub(r"(?<!\r)\n", "\r\n", text)
        data = text.encode("utf-8")
        if p.append_null and data and not data.rstrip(b"\r\n").endswith(b"\x00"):
            data += b"\x00"
        if p.trailing_eol and data.endswith(b"\x00"):
            data += p.eol_str.encode("ascii")
        return data

    def preview_bytes(self) -> tuple[bytes, list[str]]:
        unknown: list[str] = []
        counter = self.session.ctx.counter + 1

        def render(text: str) -> str:
            return self.session.render(text, unknown, counter)

        if self.is_raw:
            return self.build_raw(render), unknown
        frame = self.build_frame(render)
        if self.hub.profile.auto_receipt and frame.command not in ("CONNECT", "STOMP") and not frame.has("receipt"):
            frame.headers.append(("receipt", "rcpt-<n>"))
        return encode_frame(frame, self.session.encode_options()), unknown

    # ------------------------------------------------------------------ preview
    def _toggle_preview(self, on: bool) -> None:
        self.preview_box.setVisible(on)
        if on:
            self.update_preview()

    def _schedule_preview(self, *args) -> None:
        if self.btn_preview.isChecked():
            self._preview_timer.start()

    def update_preview(self) -> None:
        if not self.btn_preview.isChecked():
            return
        data, unknown = self.preview_bytes()
        self.preview.setPlainText(visualize(data))
        t = tokens()
        info = [f"<b>{len(data)} bytes</b>"]
        if self.is_raw and not data:
            info.append("empty")
        elif data.rstrip(b"\r\n").endswith(b"\x00"):
            info.append(f"<span style='color:{t.green_text}'>✓ ends with NULL</span>")
        else:
            info.append(f"<span style='color:{t.orange_text}'>⚠ no NULL terminator — most servers will wait forever</span>")
        nulls = data.count(b"\x00")
        if nulls > 1:
            info.append(f"{nulls} NULL bytes")
        if unknown:
            info.append(f"<span style='color:{t.orange_text}'>unknown placeholders: {', '.join(sorted(set(unknown)))}</span>")
        info.append(f"<span style='color:{t.tertiary}'>␊ LF · ␍ CR · ␀ NULL</span>")
        self.preview_info.setText(" · ".join(info))

    # ------------------------------------------------------------------ sending
    def on_send_clicked(self) -> None:
        if self._repeat_timer.isActive():
            self.stop_repeat()
            return
        count = self.repeat.value()
        if count <= 1:
            self.send_once()
            return
        self._repeat_total = self._repeat_left = count
        self._style_send(True)
        self._repeat_tick()
        if self._repeat_left > 0:
            self._repeat_timer.start(self.interval.value())

    def _repeat_tick(self) -> None:
        ok = self.send_once(record=self._repeat_left == self._repeat_total)
        self._repeat_left -= 1
        done = self._repeat_total - self._repeat_left
        if not ok or self._repeat_left <= 0:
            self.stop_repeat()
            self.status_message.emit(f"Sent {done if ok else done - 1} of {self._repeat_total}")
        else:
            self.btn_send.setText(f"Stop  {done}/{self._repeat_total}")

    def stop_repeat(self) -> None:
        self._repeat_timer.stop()
        self._repeat_left = 0
        self.btn_send.setText("Send")
        self._style_send(False)

    def send_once(self, record: bool = True) -> bool:
        if not self.session.is_open:
            self.status_message.emit("Not connected — press Connect first")
            self.session.send_bytes(b"")  # logs the warning in the conversation
            return False
        unknown: list[str] = []
        counter = self.session.ctx.next_send()

        def render(text: str) -> str:
            return self.session.render(text, unknown, counter)

        if self.is_raw:
            data = self.build_raw(render)
            if not data:
                self.status_message.emit("The raw frame is empty")
                return False
            ok = self.session.send_bytes(data)
        else:
            if not self.command.currentText().strip():
                self.status_message.emit("Choose a command first")
                return False
            ok = self.session.send_frame(self.build_frame(render))
        if unknown:
            self.status_message.emit(f"Unknown placeholders sent as-is: {', '.join(sorted(set(unknown)))}")
        if ok and record:
            self._remember()
        if ok and self.btn_preview.isChecked():
            self._schedule_preview()
        return ok

    # ------------------------------------------------------------------ templates
    def snapshot(self, name: str = "") -> Template:
        return Template(
            name=name,
            mode="raw" if self.is_raw else "structured",
            command=self.command.currentText().strip(),
            destination=self.destination.text(),
            headers=self.headers.rows(),
            content_type=self.content_type.currentText(),
            body=self.body.toPlainText(),
            raw=self.raw.toPlainText(),
        )

    def load_template(self, t: Template) -> None:
        if t.mode == "raw":
            self.raw.setPlainText(t.raw)
            self.set_mode(1)
            if t.name != "draft":
                return
        self.command.setCurrentText(t.command)
        self.destination.setText(t.destination)
        self.headers.set_rows(t.headers)
        self.content_type.setCurrentText(t.content_type)
        self.body.setPlainText(t.body)
        if t.raw:
            self.raw.setPlainText(t.raw)
        self.set_mode(1 if t.mode == "raw" else 0)

    def template_from_entry(self, e: LogEntry, name: str = "") -> Template:
        if e.kind != "frame":
            raw = e.raw.decode("utf-8", errors="replace").replace("\x00", "\\u0000")
            return Template(name=name, mode="raw", raw=raw)
        incoming = e.direction == "in"
        command = "SEND" if incoming and e.command == "MESSAGE" else e.command
        skip = SERVER_ONLY_HEADERS if incoming else {"content-length", "destination"}
        if self.hub.profile.auto_receipt:
            skip = skip | {"receipt"}
        headers = [[True, k, v] for k, v in e.headers if k not in skip and k != "content-type"]
        raw = e.raw.decode("utf-8", errors="replace").replace("\x00", "\\u0000")
        return Template(
            name=name, mode="structured", command=command, destination=e.destination,
            headers=headers, content_type=e.header("content-type"), body=e.body_text, raw=raw,
        )

    def load_entry(self, e: LogEntry) -> None:
        mode = self.mode.currentIndex()
        t = self.template_from_entry(e)
        self.load_template(t)
        if t.mode == "structured":
            self.set_mode(mode)

    def save_as_template(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Template", "Template name:", text=self._suggest_name())
        if ok and name.strip():
            self.save_template_requested.emit(self.snapshot(name.strip()))

    def _suggest_name(self) -> str:
        if self.is_raw:
            first = (self.raw.toPlainText().strip().splitlines() or ["Raw frame"])[0]
            return f"Raw {first}"
        return f"{self.command.currentText()} {self.destination.text()}".strip()

    def _remember(self) -> None:
        snap = self.snapshot(time.strftime("%H:%M:%S"))
        key = snap.to_dict()
        key.pop("name")
        if self.recent:
            last = self.recent[0].to_dict()
            last.pop("name")
            if last == key:
                return
        self.recent.insert(0, snap)
        del self.recent[30:]
        self.recent_changed.emit()

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        self.recent_menu.addSection("Recent Sends")
        if not self.recent:
            self.recent_menu.addAction("Nothing sent yet").setEnabled(False)
            return
        for t in self.recent:
            self.recent_menu.addAction(f"{t.name}    {t.summary()}", lambda t=t: self.load_template(t))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear Recent", self._clear_recent)

    def _clear_recent(self) -> None:
        self.recent.clear()
        self.recent_changed.emit()
