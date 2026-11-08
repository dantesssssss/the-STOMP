"""STOMP frame model, encoder and incremental parser (STOMP 1.0 / 1.1 / 1.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

CLIENT_COMMANDS = [
    "SEND", "SUBSCRIBE", "UNSUBSCRIBE", "ACK", "NACK",
    "BEGIN", "COMMIT", "ABORT", "DISCONNECT", "CONNECT", "STOMP",
]
SERVER_COMMANDS = ["CONNECTED", "MESSAGE", "RECEIPT", "ERROR"]
ALL_COMMANDS = CLIENT_COMMANDS + SERVER_COMMANDS

# Per spec, CONNECT and CONNECTED never escape header values (1.0 compatibility).
NO_ESCAPE_COMMANDS = {"CONNECT", "CONNECTED"}

REQUIRED_HEADERS = {
    "SEND": ["destination"],
    "SUBSCRIBE": ["destination", "id"],
    "UNSUBSCRIBE": ["id"],
    "ACK": ["id"],
    "NACK": ["id"],
    "BEGIN": ["transaction"],
    "COMMIT": ["transaction"],
    "ABORT": ["transaction"],
    "CONNECT": ["accept-version", "host"],
    "STOMP": ["accept-version", "host"],
}

COMMON_HEADERS = [
    "destination", "id", "ack", "receipt", "content-type", "content-length",
    "transaction", "subscription", "message-id", "accept-version", "host",
    "login", "passcode", "heart-beat", "persistent", "priority", "expires",
    "reply-to", "correlation-id", "selector", "durable", "auto-delete",
    "x-queue-name", "Authorization",
]

MAX_BUFFER = 64 * 1024 * 1024


@dataclass
class Frame:
    command: str
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    warnings: list[str] = field(default_factory=list)

    def get(self, name: str, default: str | None = None) -> str | None:
        """First occurrence wins, as the spec demands for repeated headers."""
        for key, value in self.headers:
            if key == name:
                return value
        return default

    def has(self, name: str) -> bool:
        return any(key == name for key, _ in self.headers)

    def set(self, name: str, value: str) -> None:
        for i, (key, _) in enumerate(self.headers):
            if key == name:
                self.headers[i] = (name, value)
                return
        self.headers.append((name, value))

    @property
    def body_text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class EncodeOptions:
    version: str = "1.2"
    eol: str = "\n"
    escape_headers: bool = True
    content_length: bool = True
    append_null: bool = True
    trailing_eol: bool = False


def escape_header(value: str, version: str) -> str:
    if version == "1.0":
        return value
    out = value.replace("\\", "\\\\").replace("\n", "\\n").replace(":", "\\c")
    if version >= "1.2":
        out = out.replace("\r", "\\r")
    return out


def unescape_header(value: str, version: str) -> tuple[str, str | None]:
    """Return (decoded, warning). Undefined escapes are kept literally and reported."""
    if version == "1.0" or "\\" not in value:
        return value, None
    out: list[str] = []
    warning = None
    i, n = 0, len(value)
    while i < n:
        ch = value[i]
        if ch == "\\":
            nxt = value[i + 1] if i + 1 < n else ""
            if nxt == "\\":
                out.append("\\")
            elif nxt == "n":
                out.append("\n")
            elif nxt == "c":
                out.append(":")
            elif nxt == "r" and version >= "1.2":
                out.append("\r")
            else:
                warning = f"undefined escape sequence '\\{nxt}' in header value"
                out.append("\\" + nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out), warning


def frame_head_text(frame: Frame, opts: EncodeOptions) -> str:
    """Command + header lines + blank line, exactly as they go on the wire."""
    escape = (
        opts.escape_headers
        and frame.command not in NO_ESCAPE_COMMANDS
        and opts.version != "1.0"
    )
    headers = list(frame.headers)
    if opts.content_length and frame.body and not frame.has("content-length"):
        headers.append(("content-length", str(len(frame.body))))
    lines = [frame.command]
    for key, value in headers:
        if escape:
            key, value = escape_header(key, opts.version), escape_header(value, opts.version)
        lines.append(f"{key}:{value}")
    return opts.eol.join(lines) + opts.eol + opts.eol


def encode_frame(frame: Frame, opts: EncodeOptions | None = None) -> bytes:
    opts = opts or EncodeOptions()
    data = frame_head_text(frame, opts).encode("utf-8") + frame.body
    if opts.append_null:
        data += b"\x00"
    if opts.trailing_eol:
        data += opts.eol.encode("ascii")
    return data


def parse_heartbeat(value: str | None) -> tuple[int, int]:
    try:
        a, b = (value or "0,0").split(",", 1)
        return max(0, int(a.strip())), max(0, int(b.strip()))
    except ValueError:
        return 0, 0


@dataclass
class ParseEvent:
    kind: str  # "frame" | "heartbeat" | "error"
    frame: Frame | None = None
    raw: bytes = b""
    message: str = ""
    count: int = 0


class _Incomplete(Exception):
    pass


class FrameParser:
    """Incremental STOMP parser. Feed it bytes; it yields complete frames and heart-beats.

    It never raises on malformed input: problems are attached to the frame as warnings,
    or reported as "error" events, because showing the user what the server actually
    sent is the whole point of a testing tool.
    """

    def __init__(self, version: str = "1.2"):
        self.buffer = bytearray()
        self.version = version

    def reset(self) -> None:
        self.buffer.clear()

    @property
    def pending(self) -> int:
        return len(self.buffer)

    def has_partial_frame(self) -> bool:
        return bool(self.buffer.strip(b"\r\n"))

    def feed(self, data: bytes) -> list[ParseEvent]:
        if data:
            self.buffer += data
        events: list[ParseEvent] = []
        while self.buffer:
            try:
                event = self._parse_one(final=False)
            except _Incomplete:
                break
            if event is None:
                break
            events.append(event)
        if len(self.buffer) > MAX_BUFFER:
            events.append(ParseEvent(
                "error", raw=bytes(self.buffer[:4096]),
                message=f"Discarded {len(self.buffer)} buffered bytes: no frame terminator found",
            ))
            self.buffer.clear()
        return events

    def flush(self) -> list[ParseEvent]:
        """Treat everything buffered as complete (lenient mode / end of stream)."""
        events = self.feed(b"")
        while self.buffer:
            if not self.buffer.strip(b"\r\n"):
                # Only line breaks left: report them as heart-beats.
                count = self.buffer.count(b"\n") or 1
                events.append(ParseEvent("heartbeat", raw=bytes(self.buffer), count=count))
                self.buffer.clear()
                break
            event = self._parse_one(final=True)
            if event is None:
                break
            events.append(event)
        return events

    # ------------------------------------------------------------------ internals
    def _parse_one(self, final: bool) -> ParseEvent | None:
        buf = self.buffer

        # Heart-beats: bare EOLs between frames.
        pos, beats = 0, 0
        while pos < len(buf):
            if buf[pos] == 0x0A:
                beats, pos = beats + 1, pos + 1
            elif buf[pos] == 0x0D and pos + 1 < len(buf) and buf[pos + 1] == 0x0A:
                beats, pos = beats + 1, pos + 2
            elif buf[pos] == 0x0D and pos + 1 == len(buf) and not final:
                break  # might be the first half of CRLF
            else:
                break
        if beats:
            raw = bytes(buf[:pos])
            del buf[:pos]
            return ParseEvent("heartbeat", raw=raw, count=beats)
        if not buf:
            return None
        if buf[0] == 0x00:
            del buf[:1]
            return ParseEvent("error", raw=b"\x00", message="Stray NULL byte between frames")

        # Command + header lines.
        lines: list[bytes] = []
        warnings: list[str] = []
        pos = 0
        terminated_early = False
        while True:
            nl = buf.find(b"\n", pos)
            nul = buf.find(b"\x00", pos)
            if nul >= 0 and (nl < 0 or nul < nl):
                line = bytes(buf[pos:nul])
                if line.endswith(b"\r"):
                    line = line[:-1]
                if line:
                    lines.append(line)
                warnings.append("frame terminated before the blank line that ends the headers")
                pos = nul + 1
                terminated_early = True
                break
            if nl < 0:
                if not final:
                    raise _Incomplete
                line = bytes(buf[pos:])
                if line.endswith(b"\r"):
                    line = line[:-1]
                if line:
                    lines.append(line)
                warnings.append("incomplete frame: headers not terminated and no NULL byte")
                pos = len(buf)
                terminated_early = True
                break
            line = bytes(buf[pos:nl])
            if line.endswith(b"\r"):
                line = line[:-1]
            pos = nl + 1
            if not line and lines:
                break
            lines.append(line)

        command = lines[0].decode("utf-8", errors="replace") if lines else ""
        if command not in ALL_COMMANDS:
            warnings.append(f"unknown command '{command}'")
        headers: list[tuple[str, str]] = []
        unescape = command not in NO_ESCAPE_COMMANDS
        for line in lines[1:]:
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError:
                text = line.decode("utf-8", errors="replace")
                warnings.append("header line is not valid UTF-8")
            if ":" not in text:
                warnings.append(f"header line without ':' -> {text!r}")
                key, value = text, ""
            else:
                key, value = text.split(":", 1)
            if unescape:
                key, w1 = unescape_header(key, self.version)
                value, w2 = unescape_header(value, self.version)
                for w in (w1, w2):
                    if w and w not in warnings:
                        warnings.append(w)
            headers.append((key, value))

        if terminated_early:
            raw = bytes(buf[:pos])
            del buf[:pos]
            return ParseEvent("frame", Frame(command, headers, b"", warnings), raw=raw)

        # Body.
        body_start = pos
        length_value = next((v for k, v in headers if k == "content-length"), None)
        length = None
        if length_value is not None:
            try:
                length = int(length_value.strip())
                if length < 0:
                    raise ValueError
            except ValueError:
                warnings.append(f"invalid content-length '{length_value}'")
                length = None

        end = None
        if length is not None:
            if len(buf) < body_start + length + 1:
                if not final:
                    raise _Incomplete
                warnings.append(
                    f"content-length is {length} but only {len(buf) - body_start} body bytes arrived"
                )
                end, frame_end = len(buf), len(buf)
            elif buf[body_start + length] == 0x00:
                end, frame_end = body_start + length, body_start + length + 1
            else:
                warnings.append(f"content-length {length} does not match: no NULL after the body")
        if end is None:
            nul = buf.find(b"\x00", body_start)
            if nul < 0:
                if not final:
                    raise _Incomplete
                warnings.append("missing NULL terminator (\\0) at end of frame")
                end, frame_end = len(buf), len(buf)
            else:
                end, frame_end = nul, nul + 1

        body = bytes(buf[body_start:end])
        raw = bytes(buf[:frame_end])
        del buf[:frame_end]
        return ParseEvent("frame", Frame(command, headers, body, warnings), raw=raw)
