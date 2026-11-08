"""Connection settings for the active profile, laid out like System Settings."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFileDialog, QHBoxLayout, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.frame import COMMON_HEADERS
from .binding import ProfileHub
from .controls import FormSection, SegmentedControl, icon_button
from .widgets import KeyValueTable


def _spin(lo: int, hi: int, suffix: str = "", step: int = 1, special: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setSuffix(suffix)
    s.setMinimumWidth(96)
    if special:
        s.setSpecialValueText(special)
    return s


def _dspin(lo: float, hi: float, suffix: str = "", step: float = 0.5) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(1)
    s.setSuffix(suffix)
    s.setMinimumWidth(96)
    return s


def _line(placeholder: str = "", width: int = 0) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if width:
        edit.setMinimumWidth(width)
    return edit


class ConnectionForm(QScrollArea):
    def __init__(self, hub: ProfileHub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("InspectorInner")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(18)
        b = hub

        # transport ---------------------------------------------------------------
        s = FormSection("Transport", "The URL and transport type are in the toolbar.")
        s.add_row("Subprotocols", b.bind_line(_line("none", 110), "ws_subprotocols"), stretch=True)
        msg = SegmentedControl(["Text", "Binary"], compact=True)
        b.bind_segment(msg, "ws_send_binary", [False, True])
        s.add_row("WebSocket messages", msg)
        s.add_row("Connect timeout", b.bind_spin(_dspin(0.5, 300, " s"), "connect_timeout"))
        b.bind_check(s.add_switch("Verify TLS certificate", "Turn off for self-signed development certificates."), "tls_verify")
        ca_row = QWidget()
        ca_layout = QHBoxLayout(ca_row)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        ca_layout.setSpacing(4)
        ca = b.bind_line(_line("System trust store", 120), "tls_ca_file")
        ca_layout.addWidget(ca, 1)
        ca_layout.addWidget(icon_button("folder", "Choose a CA bundle (.pem)", self._pick_ca, size=26))
        s.add_row("CA certificate", ca_row, stretch=True)
        layout.addWidget(s)

        s = FormSection("Handshake Headers", "Sent with the WebSocket upgrade request, e.g. Authorization: Bearer {{token}}.")
        table = b.bind_table(KeyValueTable(["Authorization", "Cookie", "Origin", "Host", "User-Agent", "X-Api-Key"],
                                           "Header", add_text="Add Header"), "ws_headers")
        table.setMinimumHeight(118)
        table.setMaximumHeight(180)
        s.add_widget(table)
        layout.addWidget(s)

        # STOMP CONNECT -----------------------------------------------------------
        s = FormSection("CONNECT Frame")
        b.bind_check(s.add_switch("Send automatically", "Sent as soon as the transport is open."), "auto_connect_frame")
        cmd = SegmentedControl(["CONNECT", "STOMP"], compact=True)
        b.bind_segment(cmd, "connect_command", ["CONNECT", "STOMP"])
        s.add_row("Command", cmd)
        s.add_row("accept-version", b.bind_line(_line("1.2,1.1,1.0", 120), "accept_version"), stretch=True)
        s.add_row("host", b.bind_line(_line("Host from URL", 120), "vhost"), stretch=True)
        s.add_row("login", b.bind_line(_line("optional", 120), "login"), stretch=True)
        pw = QWidget()
        pw_layout = QHBoxLayout(pw)
        pw_layout.setContentsMargins(0, 0, 0, 0)
        pw_layout.setSpacing(4)
        self.passcode = b.bind_line(_line("optional", 100), "passcode")
        self.passcode.setEchoMode(QLineEdit.EchoMode.Password)
        reveal = icon_button("eye", "Show passcode", checkable=True, size=26)
        reveal.toggled.connect(lambda on: self.passcode.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        pw_layout.addWidget(self.passcode, 1)
        pw_layout.addWidget(reveal)
        s.add_row("passcode", pw, stretch=True)
        layout.addWidget(s)

        s = FormSection("Extra CONNECT Headers", "Added to, or overriding, the CONNECT headers. {{placeholders}} work here.")
        extra = b.bind_table(KeyValueTable(COMMON_HEADERS + ["X-Authorization", "token"], "Header", add_text="Add Header"),
                             "connect_headers")
        extra.setMinimumHeight(118)
        extra.setMaximumHeight(180)
        s.add_widget(extra)
        layout.addWidget(s)

        s = FormSection("Heart-beat", "0 disables a direction. The negotiated values appear in the status bar.")
        s.add_row("Send every", b.bind_spin(_spin(0, 3_600_000, " ms", 1000, "off"), "heartbeat_out"))
        s.add_row("Expect every", b.bind_spin(_spin(0, 3_600_000, " ms", 1000, "off"), "heartbeat_in"))
        s.add_row("Tolerance", b.bind_spin(_dspin(1.0, 20.0, " ×"), "heartbeat_tolerance"),
                  "How late a server heart-beat may be before a warning.")
        b.bind_check(s.add_switch("Disconnect when heart-beats stop"), "close_on_missed_heartbeat")
        layout.addWidget(s)

        # framing ---------------------------------------------------------------------
        s = FormSection("Framing", "How frames are written on the wire. The composer has quick toggles for the common ones.")
        b.bind_check(s.add_switch("Append NULL terminator", "Adds the \\0 byte every STOMP frame must end with."), "append_null")
        b.bind_check(s.add_switch("Line break after NULL", "Some servers expect an EOL after the terminator."), "trailing_eol")
        eol = SegmentedControl(["LF", "CRLF"], compact=True)
        b.bind_segment(eol, "eol", ["LF", "CRLF"])
        s.add_row("Line endings", eol)
        b.bind_check(s.add_switch("Escape header values", "STOMP 1.1+: \\n \\c \\\\ \\r inside header values."), "escape_headers")
        b.bind_check(s.add_switch("Add content-length"), "auto_content_length")
        b.bind_check(s.add_switch("Request a receipt for every frame", "Shows the round-trip time under each message."), "auto_receipt")
        b.bind_check(s.add_switch("Expand {{placeholders}}"), "placeholders")
        b.bind_check(s.add_switch("Raw editor: \\u0000 and ^@ mean NULL"), "raw_null_escapes")
        b.bind_check(s.add_switch("Raw editor: interpret all escapes", "\\n \\r \\t \\xHH \\uHHHH — careful with JSON."), "raw_all_escapes")
        b.bind_check(s.add_switch("Lenient parsing", "Treat every WebSocket message as a complete frame, for servers that forget \\0."),
                     "lenient_ws_frames")
        layout.addWidget(s)

        # behaviour ----------------------------------------------------------------
        s = FormSection("Behaviour")
        b.bind_check(s.add_switch("Subscribe after connecting", "Re-subscribes everything switched on in the sidebar."), "auto_subscribe")
        b.bind_check(s.add_switch("Acknowledge messages automatically", "For client and client-individual subscriptions."), "auto_ack")
        b.bind_check(s.add_switch("Receipt on DISCONNECT", "Waits for the server to confirm before closing."), "disconnect_receipt")
        b.bind_check(s.add_switch("Log raw transport chunks", "Shows every WebSocket message / TCP read."), "log_raw_chunks")
        layout.addWidget(s)

        s = FormSection("Reconnect")
        b.bind_check(s.add_switch("Reconnect automatically", "When the connection drops unexpectedly."), "auto_reconnect")
        s.add_row("Delay", b.bind_spin(_dspin(0.1, 3600, " s"), "reconnect_delay"))
        s.add_row("Attempts", b.bind_spin(_spin(0, 100000, "", 1, "unlimited"), "reconnect_max"))
        layout.addWidget(s)
        layout.addStretch(1)
        self.setWidget(inner)

    def _pick_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CA bundle", "", "Certificates (*.pem *.crt *.cer);;All files (*)")
        if path:
            self.hub.set("tls_ca_file", path)

