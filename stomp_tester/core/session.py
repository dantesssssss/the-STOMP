"""STOMP session: connection state machine, heart-beats, receipts, subscriptions, auto-ACK.

All logic runs on the Qt main thread. Transport callbacks arrive from reader threads and are
re-queued onto the main thread through `_Bridge` signals, tagged with a generation number so
events from a previous (reconnected) transport are ignored.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .frame import EncodeOptions, Frame, FrameParser, encode_frame, parse_heartbeat
from .models import LogEntry, Profile, SubscriptionSpec, enabled_pairs
from .templating import TemplateContext, render
from .transport import (
    SOCKJS,
    TRANSPORT_LABELS,
    WEBSOCKET,
    Transport,
    TransportCallbacks,
    TransportConfig,
    create_transport,
    url_host,
)

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
OPEN = "open"  # transport up, STOMP CONNECTED not (yet) received
CONNECTED = "connected"
DISCONNECTING = "disconnecting"
RECONNECTING = "reconnecting"

STATE_LABELS = {
    DISCONNECTED: "Disconnected",
    CONNECTING: "Connecting…",
    OPEN: "Transport open",
    CONNECTED: "Connected",
    DISCONNECTING: "Disconnecting…",
    RECONNECTING: "Waiting to reconnect",
}


SPRING_INBOUND_ERROR = "ExecutorSubscribableChannel[clientInboundChannel]"
AUTH_HEADERS = {"login", "passcode", "authorization", "x-authorization", "token", "access-token", "access_token"}


def explain_error(message: str, during_connect: bool, connect_headers: list[tuple[str, str]]) -> str:
    """Turn a server ERROR into an actionable hint (empty string if there is nothing useful to add)."""
    sent_auth = sorted({k for k, v in connect_headers if k.lower() in AUTH_HEADERS and v})
    hints = []
    if during_connect:
        hints.append("The server rejected the CONNECT frame, so the session never started.")
    if SPRING_INBOUND_ERROR in message:
        hints.append("This is Spring's generic wrapper: a ChannelInterceptor (typically an authentication check "
                     "on CONNECT) or a message handler threw an exception — the real cause is in the server log.")
    if during_connect:
        if sent_auth:
            hints.append(f"The CONNECT carried {', '.join(sent_auth)}: check that the credentials/token are valid "
                         "and not expired.")
        else:
            hints.append("The CONNECT carried no login, passcode or token. If the server expects one, set it in "
                         "Inspector › Connection › CONNECT Frame (e.g. login = {{token}} with a 'token' variable).")
    elif SPRING_INBOUND_ERROR in message:
        hints.append("Check the destination and the headers of the frame you just sent.")
    return " ".join(hints)


def max_version(accept: str) -> str:
    versions = [v.strip() for v in (accept or "").split(",") if v.strip() in ("1.0", "1.1", "1.2")]
    return max(versions) if versions else "1.2"


class Subscription:
    def __init__(self, spec: SubscriptionSpec):
        self.spec = spec
        self.active = False
        self.count = 0
        self.last_ts = 0.0


class _Bridge(QObject):
    opened = Signal(int, object)
    data = Signal(int, object, bool)
    control = Signal(int, str)
    closed = Signal(int, str, bool)
    log = Signal(int, str, str, object)


class StompSession(QObject):
    state_changed = Signal(str)
    entry_added = Signal(object)
    entry_updated = Signal(object)
    subscriptions_changed = Signal()
    info_changed = Signal()
    session_started = Signal()
    session_ended = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.profile = Profile()
        self.state = DISCONNECTED
        self.transport: Transport | None = None
        self.transport_kind = ""
        self.parser = FrameParser()
        self.version: str | None = None
        self.server_info: dict[str, str] = {}
        self.heartbeat_out = 0
        self.heartbeat_in = 0
        self.connected_since = 0.0
        self.subscriptions: list[Subscription] = []
        self.ctx = TemplateContext()
        self.stats = dict(frames_in=0, frames_out=0, bytes_in=0, bytes_out=0, heartbeats_in=0, heartbeats_out=0)

        self._gen = 0
        self._seq = 0
        self._receipt_seq = 0
        self._pending_receipts: dict[str, tuple[LogEntry, float]] = {}
        self._disconnect_receipt: str | None = None
        self._requested_hb = (0, 0)
        self._connect_headers: list[tuple[str, str]] = []
        self._user_closing = False
        self._reconnect_attempt = 0
        self._last_recv = 0.0
        self._hb_warned = False
        self._partial_warned = False

        bridge = self._bridge = _Bridge(self)
        queued = Qt.ConnectionType.QueuedConnection
        bridge.opened.connect(self._on_opened, queued)
        bridge.data.connect(self._on_data, queued)
        bridge.control.connect(self._on_control, queued)
        bridge.closed.connect(self._on_closed, queued)
        bridge.log.connect(self._on_log, queued)

        self._hb_send_timer = QTimer(self, timeout=self._send_heartbeat)
        self._hb_check_timer = QTimer(self, timeout=self._check_heartbeat)
        self._reconnect_timer = QTimer(self, singleShot=True, timeout=self._open_transport)
        self._disconnect_timer = QTimer(self, singleShot=True, timeout=self._disconnect_timeout)
        self._connected_wait_timer = QTimer(self, singleShot=True, timeout=self._connected_timeout)
        self._subs_timer = QTimer(self, singleShot=True, interval=120, timeout=self.subscriptions_changed.emit)

    # ------------------------------------------------------------------ public API
    @property
    def is_open(self) -> bool:
        return self.transport is not None and self.state in (OPEN, CONNECTED, DISCONNECTING)

    @property
    def is_active(self) -> bool:
        return self.state != DISCONNECTED

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.subscriptions = [Subscription(SubscriptionSpec.from_dict(d)) for d in profile.subscriptions]
        self.subscriptions_changed.emit()

    def sync_subscriptions_to_profile(self) -> None:
        self.profile.subscriptions = [s.spec.to_dict() for s in self.subscriptions]

    def log_event(self, text: str, level: str = "info", headers: list | None = None) -> None:
        """Add a local event line to the conversation."""
        self._sys(text, level, headers)

    def render(self, text: str, unknown: list[str] | None = None, counter: int | None = None) -> str:
        if not self.profile.placeholders:
            return text
        return render(text, self.ctx, self.profile.variables_dict(), unknown, counter)

    def encode_options(self) -> EncodeOptions:
        p = self.profile
        return EncodeOptions(
            version=self.version or max_version(p.accept_version),
            eol=p.eol_str,
            escape_headers=p.escape_headers,
            content_length=p.auto_content_length,
            append_null=p.append_null,
            trailing_eol=p.trailing_eol,
        )

    def connect_(self) -> None:
        if self.state == RECONNECTING:
            self._reconnect_timer.stop()
        elif self.state != DISCONNECTED:
            return
        else:
            self._reconnect_attempt = 0
            self.session_started.emit()
        self._user_closing = False
        self._open_transport()

    def disconnect_(self, force: bool = False) -> None:
        self._user_closing = True
        if self.state == RECONNECTING:
            self._reconnect_timer.stop()
            self._sys("Reconnect cancelled")
            self._finish()
            return
        if self.transport is None:
            if self.state != DISCONNECTED:
                self._finish()
            return
        if self.state == CONNECTING:
            # The reader thread may be stuck in DNS/TCP connect: detach from it right away.
            transport = self.transport
            self._gen += 1
            self.transport = None
            transport.abort()
            self._sys("Connection attempt cancelled")
            self._finish()
            return
        if force or self.state == DISCONNECTING:
            self._sys("Closing connection (forced)", level="warn")
            self.transport.abort()
            return
        if self.state == CONNECTED:
            frame = Frame("DISCONNECT")
            if self.profile.disconnect_receipt:
                self._receipt_seq += 1
                frame.headers.append(("receipt", f"disconnect-{self._receipt_seq}"))
            if self.send_frame(frame, auto_receipt=False):
                return
        self.transport.close()

    def send_frame(self, frame: Frame, auto_receipt: bool | None = None) -> bool:
        use_receipt = self.profile.auto_receipt if auto_receipt is None else auto_receipt
        if use_receipt and frame.command not in ("CONNECT", "STOMP") and not frame.has("receipt"):
            self._receipt_seq += 1
            frame.headers.append(("receipt", f"rcpt-{self._receipt_seq}"))
        return self.send_bytes(encode_frame(frame, self.encode_options()))

    def send_bytes(self, data: bytes) -> bool:
        transport = self.transport
        if transport is None or not self.is_open:
            self._sys("Not connected — nothing was sent.", level="warn")
            return False
        entries = self._describe_outgoing(data)
        try:
            transport.send(data)
        except Exception as exc:
            self._sys(f"Send failed: {exc}", level="error")
            return False
        self.stats["bytes_out"] += len(data)
        for entry in entries:
            self._emit(entry)
            if entry.kind == "frame":
                self.stats["frames_out"] += 1
                self._track_outgoing(entry)
            elif entry.kind == "heartbeat":
                self.stats["heartbeats_out"] += 1
        return True

    def build_connect_frame(self) -> Frame:
        p = self.profile
        host = p.vhost or url_host(p.url) or "localhost"
        frame = Frame(p.connect_command or "CONNECT", [("accept-version", p.accept_version), ("host", host)])
        if p.login:
            frame.headers.append(("login", self.render(p.login)))
        if p.passcode:
            frame.headers.append(("passcode", self.render(p.passcode)))
        frame.headers.append(("heart-beat", f"{max(0, p.heartbeat_out)},{max(0, p.heartbeat_in)}"))
        for key, value in enabled_pairs(p.connect_headers):
            frame.set(self.render(key), self.render(value))
        return frame

    def ack(self, entry: LogEntry, nack: bool = False) -> bool:
        version = self.version or "1.2"
        if version >= "1.2":
            headers = [("id", entry.header("ack") or entry.header("message-id"))]
        else:
            headers = [("message-id", entry.header("message-id")), ("subscription", entry.header("subscription"))]
        return self.send_frame(Frame("NACK" if nack else "ACK", headers))

    # subscriptions -------------------------------------------------------
    def find_subscription(self, sub_id: str) -> Subscription | None:
        return next((s for s in self.subscriptions if s.spec.id == sub_id), None)

    def new_subscription_id(self) -> str:
        n = 0
        while self.find_subscription(f"sub-{n}"):
            n += 1
        return f"sub-{n}"

    def add_subscription(self, destination: str, ack: str = "auto", headers: list | None = None,
                         sub_id: str | None = None) -> Subscription:
        spec = SubscriptionSpec(destination, sub_id or self.new_subscription_id(), ack, headers or [], True)
        sub = Subscription(spec)
        self.subscriptions.append(sub)
        if self.state == CONNECTED:
            self._send_subscribe(sub)
        self._subs_changed()
        return sub

    def set_subscription_enabled(self, sub: Subscription, enabled: bool) -> None:
        sub.spec.enabled = enabled
        if self.state == CONNECTED:
            if enabled and not sub.active:
                self._send_subscribe(sub)
            elif not enabled and sub.active:
                self._send_unsubscribe(sub)
        self._subs_changed()

    def update_subscription(self, sub: Subscription, destination: str, ack: str, headers: list, sub_id: str) -> None:
        was_active = sub.active and self.state == CONNECTED
        if was_active:
            self._send_unsubscribe(sub)
        sub.spec.destination, sub.spec.ack, sub.spec.headers, sub.spec.id = destination, ack, headers, sub_id
        if was_active or (sub.spec.enabled and self.state == CONNECTED):
            sub.spec.enabled = True
            self._send_subscribe(sub)
        self._subs_changed()

    def remove_subscription(self, sub: Subscription) -> None:
        if sub.active and self.state == CONNECTED:
            self._send_unsubscribe(sub)
        if sub in self.subscriptions:
            self.subscriptions.remove(sub)
        self._subs_changed()

    def resubscribe(self, sub: Subscription) -> None:
        if self.state == CONNECTED:
            sub.spec.enabled = True
            self._send_subscribe(sub)

    def reset_counts(self) -> None:
        for sub in self.subscriptions:
            sub.count = 0
        self._subs_changed()

    # ------------------------------------------------------------------ internals
    def _emit(self, entry: LogEntry) -> None:
        self._seq += 1
        entry.id = self._seq
        self.entry_added.emit(entry)

    def _sys(self, text: str, level: str = "info", headers: list | None = None) -> None:
        self._emit(LogEntry("sys", "event", text=text, level=level, headers=list(headers or [])))

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    def _finish(self) -> None:
        self._stop_timers()
        self.transport = None
        self.version = None
        for sub in self.subscriptions:
            sub.active = False
        self._subs_changed()
        self._set_state(DISCONNECTED)
        self.info_changed.emit()
        self.session_ended.emit()

    def _subs_changed(self) -> None:
        if not self._subs_timer.isActive():
            self._subs_timer.start()

    def _stop_timers(self) -> None:
        for timer in (self._hb_send_timer, self._hb_check_timer, self._disconnect_timer, self._connected_wait_timer):
            timer.stop()

    def _open_transport(self) -> None:
        p = self.profile
        self._gen += 1
        gen = self._gen
        bridge = self._bridge
        cfg = TransportConfig(
            url=p.url.strip(),
            kind=p.transport,
            headers=[(self.render(k), self.render(v)) for k, v in enabled_pairs(p.ws_headers)],
            subprotocols=[s.strip() for s in p.ws_subprotocols.split(",") if s.strip()],
            tls_verify=p.tls_verify,
            ca_file=p.tls_ca_file,
            timeout=p.connect_timeout,
            send_binary=p.ws_send_binary,
        )
        callbacks = TransportCallbacks(
            on_open=lambda info: bridge.opened.emit(gen, info),
            on_data=lambda data, binary: bridge.data.emit(gen, data, binary),
            on_control=lambda text: bridge.control.emit(gen, text),
            on_close=lambda reason, error: bridge.closed.emit(gen, reason, error),
            on_log=lambda level, text, headers: bridge.log.emit(gen, level, text, headers),
        )
        transport = create_transport(cfg, callbacks)
        self.transport = transport
        self.transport_kind = cfg.kind
        self.parser = FrameParser()
        self.version = None
        self.server_info = {}
        self.heartbeat_in = self.heartbeat_out = 0
        self._pending_receipts.clear()
        self._disconnect_receipt = None
        self._partial_warned = False
        self._hb_warned = False
        self._set_state(CONNECTING)
        self._sys(f"Connecting to {transport.describe()} via {TRANSPORT_LABELS.get(cfg.kind, cfg.kind)}…")
        transport.start()

    def _describe_outgoing(self, data: bytes) -> list[LogEntry]:
        parser = FrameParser(self.version or "1.2")
        entries: list[LogEntry] = []
        for ev in parser.feed(data) + parser.flush():
            if ev.kind == "heartbeat":
                last = entries[-1] if entries else None
                if last is not None and last.kind == "frame":
                    last.raw += ev.raw  # trailing EOL after NULL belongs to the frame
                else:
                    entries.append(LogEntry("out", "heartbeat", raw=ev.raw, text="heart-beat ↑"))
            elif ev.kind == "frame":
                f = ev.frame
                entries.append(LogEntry(
                    "out", "frame", command=f.command, headers=f.headers, body=f.body, raw=ev.raw,
                    warnings=f.warnings, level="warn" if f.warnings else "info",
                ))
            else:
                entries.append(LogEntry("out", "raw", raw=ev.raw, text=ev.message, level="warn", warnings=[ev.message]))
        if not entries:
            entries.append(LogEntry("out", "raw", raw=data, text="(empty payload)", level="warn"))
        return entries

    def _track_outgoing(self, entry: LogEntry) -> None:
        cmd = entry.command
        if cmd in ("CONNECT", "STOMP"):
            self._requested_hb = parse_heartbeat(entry.header("heart-beat", "0,0"))
            self._connect_headers = list(entry.headers)
            wait = int(max(5.0, self.profile.connect_timeout) * 1000)
            self._connected_wait_timer.start(wait)
        elif cmd == "SUBSCRIBE":
            sid = entry.header("id")
            sub = self.find_subscription(sid)
            if sub is None:
                extra = [[True, k, v] for k, v in entry.headers if k not in ("destination", "id", "ack", "receipt")]
                sub = Subscription(SubscriptionSpec(entry.header("destination"), sid, entry.header("ack") or "auto", extra))
                self.subscriptions.append(sub)
            sub.active = True
            sub.spec.enabled = True
            self._subs_changed()
        elif cmd == "UNSUBSCRIBE":
            sub = self.find_subscription(entry.header("id"))
            if sub is not None:
                sub.active = False
                sub.spec.enabled = False
                self._subs_changed()
        elif cmd == "DISCONNECT":
            self._user_closing = True
            self._disconnect_receipt = entry.header("receipt") or None
            self._set_state(DISCONNECTING)
            self._disconnect_timer.start(3000)
        receipt = entry.header("receipt")
        if receipt:
            entry.receipt = receipt
            self._pending_receipts[receipt] = (entry, time.perf_counter())

    def _send_subscribe(self, sub: Subscription) -> None:
        spec = sub.spec
        headers = [("destination", self.render(spec.destination)), ("id", spec.id), ("ack", spec.ack or "auto")]
        for key, value in enabled_pairs(spec.headers):
            headers.append((self.render(key), self.render(value)))
        self.send_frame(Frame("SUBSCRIBE", headers))

    def _send_unsubscribe(self, sub: Subscription) -> None:
        self.send_frame(Frame("UNSUBSCRIBE", [("id", sub.spec.id)]))

    # transport events ---------------------------------------------------------
    def _on_opened(self, gen: int, info: dict) -> None:
        if gen != self._gen:
            return
        self._last_recv = time.time()
        self._set_state(OPEN)
        parts = []
        if info.get("status"):
            parts.append(f"HTTP {info['status']}")
        if info.get("subprotocol"):
            parts.append(f"subprotocol {info['subprotocol']}")
        elif self.transport_kind in (WEBSOCKET, SOCKJS):
            parts.append("no subprotocol negotiated")
        if self.transport_kind == SOCKJS:
            parts.append("SockJS session open")
        text = "Transport open" + (f" — {', '.join(parts)}" if parts else "")
        self._sys(text, level="success", headers=info.get("headers", []))
        if self.profile.auto_connect_frame:
            self.send_frame(self.build_connect_frame())
        else:
            self._sys("Automatic CONNECT is off — compose a CONNECT or STOMP frame yourself.")

    def _on_log(self, gen: int, level: str, text: str, headers: list) -> None:
        if gen == self._gen:
            self._sys(text, level=level, headers=headers)

    def _on_control(self, gen: int, text: str) -> None:
        if gen != self._gen:
            return
        self._last_recv = time.time()
        self._emit(LogEntry("in", "heartbeat", text=text))

    def _on_data(self, gen: int, data: bytes, binary: bool) -> None:
        if gen != self._gen:
            return
        self._last_recv = time.time()
        self.stats["bytes_in"] += len(data)
        if self._hb_warned:
            self._hb_warned = False
            self._sys("Server traffic resumed", level="success")
        if self.profile.log_raw_chunks:
            kind = "binary" if binary else "text"
            self._emit(LogEntry("in", "raw", raw=data, binary=binary, text=f"{kind} chunk · {len(data)} bytes"))
        events = self.parser.feed(data)
        is_ws = self.transport_kind in (WEBSOCKET, SOCKJS)
        if is_ws and self.profile.lenient_ws_frames and self.parser.has_partial_frame():
            events += self.parser.flush()
        for ev in events:
            self._handle_event(ev, binary)
            if gen != self._gen:
                return
        if self.parser.has_partial_frame():
            if is_ws and not self._partial_warned:
                self._partial_warned = True
                self._sys(
                    f"Partial frame buffered ({self.parser.pending} bytes), waiting for the NULL terminator. "
                    "If the server never sends \\0, enable 'Treat each WebSocket message as a complete frame'.",
                    level="warn",
                )
        else:
            self._partial_warned = False

    def _handle_event(self, ev, binary: bool) -> None:
        if ev.kind == "heartbeat":
            self.stats["heartbeats_in"] += ev.count
            text = "heart-beat ↓" + (f" ×{ev.count}" if ev.count > 1 else "")
            self._emit(LogEntry("in", "heartbeat", raw=ev.raw, text=text))
            return
        if ev.kind == "error":
            self._emit(LogEntry("in", "raw", raw=ev.raw, text=ev.message, level="warn", warnings=[ev.message]))
            return
        f = ev.frame
        self.stats["frames_in"] += 1
        entry = LogEntry(
            "in", "frame", command=f.command, headers=f.headers, body=f.body, raw=ev.raw,
            warnings=list(f.warnings), binary=binary, level="warn" if f.warnings else "info",
        )
        cmd = f.command
        if cmd == "CONNECTED":
            self._on_connected(f, entry)
        elif cmd == "MESSAGE":
            sub = self.find_subscription(f.get("subscription", ""))
            if sub is not None:
                sub.count += 1
                sub.last_ts = entry.ts
                self._subs_changed()
            elif f.get("subscription"):
                entry.note = "no matching subscription"
            self._emit(entry)
            if sub is not None and sub.spec.ack != "auto" and self.profile.auto_ack:
                self.ack(entry)
        elif cmd == "RECEIPT":
            rid = f.get("receipt-id", "")
            pending = self._pending_receipts.pop(rid, None)
            entry.level = "success" if not f.warnings else "warn"
            if pending is not None:
                sent, started = pending
                sent.rtt_ms = (time.perf_counter() - started) * 1000
                sent.touch()
                entry.note = f"for #{sent.id} {sent.command} · {sent.rtt_ms:.1f} ms"
                self.entry_updated.emit(sent)
            elif rid:
                entry.note = "unknown receipt-id"
            self._emit(entry)
            if rid and rid == self._disconnect_receipt and self.transport is not None:
                self.transport.close()
        elif cmd == "ERROR":
            entry.level = "error"
            entry.note = f.get("message", "")
            during_connect = self.state == OPEN
            self._emit(entry)
            hint = explain_error(f.get("message", "") + " " + f.body_text, during_connect, self._connect_headers)
            if hint:
                self._sys(hint, level="warn")
        else:
            self._emit(entry)

    def _on_connected(self, f: Frame, entry: LogEntry) -> None:
        self._connected_wait_timer.stop()
        version = f.get("version") or "1.0"
        self.version = version
        self.parser.version = version
        sx, sy = parse_heartbeat(f.get("heart-beat"))
        cx, cy = self._requested_hb
        self.heartbeat_out = max(cx, sy) if cx and sy else 0
        self.heartbeat_in = max(cy, sx) if cy and sx else 0
        self.server_info = {
            k: v for k, v in (
                ("server", f.get("server", "")),
                ("session", f.get("session", "")),
                ("user-name", f.get("user-name", "")),
            ) if v
        }
        self.connected_since = time.time()
        entry.level = "success" if not f.warnings else "warn"
        entry.note = f"STOMP {version} · heart-beat ↑{self.heartbeat_out} ms ↓{self.heartbeat_in} ms"
        self._emit(entry)
        self._reconnect_attempt = 0
        self._set_state(CONNECTED)
        if self.heartbeat_out > 0:
            self._hb_send_timer.start(self.heartbeat_out)
        if self.heartbeat_in > 0:
            self._hb_check_timer.start(max(250, self.heartbeat_in // 2))
        self.info_changed.emit()
        if self.profile.auto_subscribe:
            for sub in self.subscriptions:
                if sub.spec.enabled and not sub.active:
                    self._send_subscribe(sub)

    def _on_closed(self, gen: int, reason: str, error: bool) -> None:
        if gen != self._gen:
            return
        self._stop_timers()
        self.transport = None
        self._sys(f"Disconnected: {reason}", level="error" if error else "info")
        if self._pending_receipts:
            self._sys(f"{len(self._pending_receipts)} receipt(s) never arrived: "
                      + ", ".join(list(self._pending_receipts)[:10]), level="warn")
            self._pending_receipts.clear()
        p = self.profile
        can_retry = p.reconnect_max <= 0 or self._reconnect_attempt < p.reconnect_max
        if not self._user_closing and p.auto_reconnect and can_retry:
            self._reconnect_attempt += 1
            for sub in self.subscriptions:
                sub.active = False
            self._subs_changed()
            self.version = None
            self.info_changed.emit()
            delay = max(0.1, p.reconnect_delay)
            limit = f"/{p.reconnect_max}" if p.reconnect_max > 0 else ""
            self._sys(f"Reconnecting in {delay:g}s (attempt {self._reconnect_attempt}{limit})…", level="warn")
            self._set_state(RECONNECTING)
            self._reconnect_timer.start(int(delay * 1000))
        else:
            self._finish()

    # timers ---------------------------------------------------------------
    def _send_heartbeat(self) -> None:
        if self.transport is None or self.state != CONNECTED:
            return
        data = self.profile.eol_str.encode("ascii")
        try:
            self.transport.send(data)
        except Exception as exc:
            self._sys(f"Heart-beat send failed: {exc}", level="error")
            return
        self.stats["bytes_out"] += len(data)
        self.stats["heartbeats_out"] += 1
        self._emit(LogEntry("out", "heartbeat", raw=data, text="heart-beat ↑"))

    def _check_heartbeat(self) -> None:
        if self.transport is None or self.heartbeat_in <= 0 or self._hb_warned:
            return
        elapsed = (time.time() - self._last_recv) * 1000
        limit = self.heartbeat_in * max(1.0, self.profile.heartbeat_tolerance)
        if elapsed > limit:
            self._hb_warned = True
            self._sys(
                f"No data from server for {elapsed / 1000:.1f}s "
                f"(expected a heart-beat every {self.heartbeat_in} ms)",
                level="warn",
            )
            if self.profile.close_on_missed_heartbeat:
                self._sys("Closing connection: server heart-beat missed", level="error")
                self.transport.abort()

    def _disconnect_timeout(self) -> None:
        if self.transport is not None:
            self._sys("No RECEIPT for DISCONNECT in time — closing transport", level="warn")
            self.transport.close()

    def _connected_timeout(self) -> None:
        if self.state == OPEN:
            self._sys("No CONNECTED frame received yet. Check accept-version, host, credentials "
                      "and framing options (NULL terminator!).", level="warn")
