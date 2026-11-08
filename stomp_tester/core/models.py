"""Plain data models: connection profiles, app settings, saved messages and log entries."""

from __future__ import annotations

import base64
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .textutil import one_line


def _from_dict(cls, data: dict):
    """Build a dataclass from a dict, ignoring unknown keys and keeping defaults for missing ones."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class Profile:
    name: str = "Local"
    url: str = "ws://localhost:8080/ws"
    transport: str = "auto"

    # Transport
    ws_subprotocols: str = "v12.stomp, v11.stomp, v10.stomp"
    ws_headers: list = field(default_factory=list)  # [[enabled, key, value]]
    ws_send_binary: bool = False
    tls_verify: bool = True
    tls_ca_file: str = ""
    connect_timeout: float = 10.0

    # STOMP CONNECT
    auto_connect_frame: bool = True
    connect_command: str = "CONNECT"
    accept_version: str = "1.2,1.1,1.0"
    vhost: str = ""
    login: str = ""
    passcode: str = ""
    heartbeat_out: int = 10000
    heartbeat_in: int = 10000
    connect_headers: list = field(default_factory=list)  # [[enabled, key, value]]

    # Framing
    append_null: bool = True
    trailing_eol: bool = False
    eol: str = "LF"
    escape_headers: bool = True
    auto_content_length: bool = True
    auto_receipt: bool = False
    placeholders: bool = True
    raw_null_escapes: bool = True
    raw_all_escapes: bool = False
    lenient_ws_frames: bool = False

    # Behaviour
    auto_subscribe: bool = True
    auto_ack: bool = False
    disconnect_receipt: bool = True
    auto_reconnect: bool = False
    reconnect_delay: float = 3.0
    reconnect_max: int = 0
    heartbeat_tolerance: float = 2.0
    close_on_missed_heartbeat: bool = False
    log_raw_chunks: bool = False

    # Data
    subscriptions: list = field(default_factory=list)  # SubscriptionSpec dicts
    variables: list = field(default_factory=list)  # [[enabled, key, value]]

    @property
    def eol_str(self) -> str:
        return "\r\n" if self.eol == "CRLF" else "\n"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        return _from_dict(cls, data)

    def copy(self, name: str | None = None) -> "Profile":
        clone = Profile.from_dict(self.to_dict())
        if name:
            clone.name = name
        return clone

    def variables_dict(self) -> dict[str, str]:
        return {k: v for enabled, k, v in _rows(self.variables) if enabled and k}


def _rows(rows: list) -> list[tuple[bool, str, str]]:
    """Normalise header rows: accepts [k, v] or [enabled, k, v]."""
    out = []
    for row in rows or []:
        if len(row) == 3:
            out.append((bool(row[0]), str(row[1]), str(row[2])))
        elif len(row) == 2:
            out.append((True, str(row[0]), str(row[1])))
    return out


def enabled_pairs(rows: list) -> list[tuple[str, str]]:
    return [(k, v) for enabled, k, v in _rows(rows) if enabled and k.strip()]


@dataclass
class AppSettings:
    theme: str = "system"  # system | light | dark
    ui_style: str = "native"  # native | fusion
    font_size: int = 0  # monospace size in pt, 0 = follow the UI font
    timestamp_format: str = "time_ms"  # time_ms | time | datetime | relative
    max_view_entries: int = 20000
    chat_preview_lines: int = 14
    chat_show_headers: bool = True
    pretty_json: bool = True
    wrap_lines: bool = True
    autosave_history: bool = True
    history_dir: str = ""
    history_keep: int = 300
    confirm_exit_connected: bool = True
    default_view: str = "chat"
    show_heartbeats: bool = False
    show_raw_chunks: bool = True
    autoscroll: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return _from_dict(cls, data)


@dataclass
class Template:
    name: str
    mode: str = "structured"  # structured | raw
    command: str = "SEND"
    destination: str = ""
    headers: list = field(default_factory=list)  # [[enabled, key, value]]
    content_type: str = ""
    body: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        return _from_dict(cls, data)

    def summary(self) -> str:
        if self.mode == "raw":
            first = self.raw.strip().splitlines()[0] if self.raw.strip() else "(empty)"
            return f"raw · {one_line(first, 60)}"
        return f"{self.command} {self.destination}".strip()


@dataclass
class SubscriptionSpec:
    destination: str
    id: str
    ack: str = "auto"
    headers: list = field(default_factory=list)  # [[enabled, key, value]]
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SubscriptionSpec":
        return _from_dict(cls, data)


def _encode_bytes(data: bytes, key: str, out: dict) -> None:
    if not data:
        return
    try:
        out[key] = data.decode("utf-8")
    except UnicodeDecodeError:
        out[key + "_b64"] = base64.b64encode(data).decode("ascii")


def _decode_bytes(data: dict, key: str) -> bytes:
    if key + "_b64" in data:
        return base64.b64decode(data[key + "_b64"])
    value = data.get(key)
    return value.encode("utf-8") if isinstance(value, str) else b""


@dataclass(eq=False)
class LogEntry:
    direction: str  # in | out | sys
    kind: str  # frame | heartbeat | raw | event
    ts: float = field(default_factory=time.time)
    command: str = ""
    headers: list = field(default_factory=list)  # [(key, value)]
    body: bytes = b""
    raw: bytes = b""
    text: str = ""
    level: str = "info"  # info | success | warn | error
    warnings: list = field(default_factory=list)
    binary: bool = False
    receipt: str = ""
    rtt_ms: float | None = None
    note: str = ""
    id: int = 0
    rev: int = 0
    _search: str | None = field(default=None, repr=False)
    _body_text: str | None = field(default=None, repr=False)

    def header(self, name: str, default: str = "") -> str:
        for key, value in self.headers:
            if key == name:
                return value
        return default

    @property
    def body_text(self) -> str:
        if self._body_text is None:
            self._body_text = self.body.decode("utf-8", errors="replace")
        return self._body_text

    @property
    def size(self) -> int:
        return len(self.raw) if self.raw else len(self.body)

    @property
    def destination(self) -> str:
        return self.header("destination")

    def target(self) -> str:
        """The most informative routing value for the list views."""
        if self.kind != "frame":
            return ""
        for name in ("destination", "receipt-id", "id", "subscription", "transaction", "message"):
            value = self.header(name)
            if value:
                return value if name == "destination" else f"{name}: {value}"
        if self.command == "CONNECTED":
            return f"version {self.header('version', '?')}"
        return ""

    def summary(self) -> str:
        if self.kind == "event":
            return one_line(self.text, 300)
        if self.kind == "heartbeat":
            return self.text or "heart-beat"
        parts = []
        if self.note:
            parts.append(self.note)
        if self.body:
            parts.append(one_line(self.body_text, 300))
        if self.warnings:
            parts.append("⚠ " + "; ".join(self.warnings))
        return " · ".join(parts)

    def search_text(self) -> str:
        if self._search is None:
            lines = [self.direction, self.kind, self.command, self.text, self.note]
            lines += [f"{k}:{v}" for k, v in self.headers]
            lines.append(self.body_text[:200_000])
            lines += self.warnings
            self._search = "\n".join(lines).lower()
        return self._search

    def touch(self) -> None:
        self.rev += 1
        self._search = None

    def frame_text(self) -> str:
        if self.kind != "frame":
            return self.text
        lines = [self.command] + [f"{k}:{v}" for k, v in self.headers]
        return "\n".join(lines) + "\n\n" + self.body_text

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "ts": round(self.ts, 6),
            "dir": self.direction,
            "kind": self.kind,
        }
        if self.command:
            out["command"] = self.command
        if self.headers:
            out["headers"] = [list(h) for h in self.headers]
        _encode_bytes(self.body, "body", out)
        if self.raw and self.raw != self.body:
            _encode_bytes(self.raw, "raw", out)
        for key in ("text", "note", "receipt"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.level != "info":
            out["level"] = self.level
        if self.warnings:
            out["warnings"] = list(self.warnings)
        if self.binary:
            out["binary"] = True
        if self.rtt_ms is not None:
            out["rtt_ms"] = round(self.rtt_ms, 3)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "LogEntry":
        body = _decode_bytes(data, "body")
        raw = _decode_bytes(data, "raw") or body
        return cls(
            direction=data.get("dir", "sys"),
            kind=data.get("kind", "event"),
            ts=float(data.get("ts", 0.0)),
            command=data.get("command", ""),
            headers=[tuple(h) for h in data.get("headers", [])],
            body=body,
            raw=raw,
            text=data.get("text", ""),
            level=data.get("level", "info"),
            warnings=list(data.get("warnings", [])),
            binary=bool(data.get("binary", False)),
            receipt=data.get("receipt", ""),
            rtt_ms=data.get("rtt_ms"),
            note=data.get("note", ""),
            id=int(data.get("id", 0)),
        )
