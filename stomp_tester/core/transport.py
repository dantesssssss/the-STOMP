"""Byte transports for STOMP: WebSocket, SockJS-over-WebSocket and raw TCP (optionally TLS).

Each transport owns one background reader thread. Callbacks are invoked from that thread;
the session marshals them onto the Qt main thread.
"""

from __future__ import annotations

import json
import random
import socket
import ssl
import string
import struct
import threading
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

import websocket
from websocket import ABNF

AUTO, WEBSOCKET, SOCKJS, TCP = "auto", "websocket", "sockjs", "tcp"
TRANSPORT_LABELS = {
    AUTO: "Auto (from URL)",
    WEBSOCKET: "WebSocket",
    SOCKJS: "SockJS (WebSocket)",
    TCP: "TCP / TLS",
}
TLS_SCHEMES = {"wss", "https", "ssl", "tls", "stomp+ssl", "stomps", "tcps"}


def resolve_kind(url: str, kind: str) -> str:
    if kind != AUTO:
        return kind
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme in ("ws", "wss"):
        return WEBSOCKET
    if scheme in ("http", "https"):
        return SOCKJS
    return TCP


def url_host(url: str) -> str:
    target = url if "://" in url else "tcp://" + url
    try:
        return urlsplit(target).hostname or ""
    except ValueError:
        return ""


def sockjs_info_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme.lower(), parts.scheme.lower() or "http")
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/info", parts.query, ""))


def probe_sockjs(url: str, headers: list[tuple[str, str]], timeout: float, tls_verify: bool) -> bool | None:
    """GET <endpoint>/info. True: SockJS endpoint, False: 404 (no SockJS), None: unknown."""
    import urllib.error
    import urllib.request

    info = sockjs_info_url(url)
    request = urllib.request.Request(info, headers=dict(headers))
    context = ssl._create_unverified_context() if info.startswith("https") and not tls_verify else None
    try:
        with urllib.request.urlopen(request, timeout=timeout or 10, context=context) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except Exception:
        return None


@dataclass
class TransportConfig:
    url: str
    kind: str = AUTO
    headers: list[tuple[str, str]] = field(default_factory=list)
    subprotocols: list[str] = field(default_factory=list)
    tls_verify: bool = True
    ca_file: str = ""
    timeout: float = 10.0
    send_binary: bool = False
    auto: bool = False  # transport was picked from the URL scheme (enables SockJS → WebSocket fallback)


@dataclass
class TransportCallbacks:
    on_open: Callable[[dict], None]
    on_data: Callable[[bytes, bool], None]
    on_control: Callable[[str], None]
    on_close: Callable[[str, bool], None]
    on_log: Callable[[str, str, list], None]  # level, text, headers


class Transport:
    kind = "base"

    def __init__(self, cfg: TransportConfig, cb: TransportCallbacks):
        self.cfg = cfg
        self.cb = cb
        self._closing = False
        self._closed_reported = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_safe, name=f"{self.kind}-reader", daemon=True)
        self._thread.start()

    def describe(self) -> str:
        return self.cfg.url

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def abort(self) -> None:
        self.close()

    def _report_close(self, reason: str, error: bool) -> None:
        with self._lock:
            if self._closed_reported:
                return
            self._closed_reported = True
        self.cb.on_close(reason, error)

    def _run_safe(self) -> None:
        try:
            self._run()
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._report_close(f"Transport failure: {exc!r}", True)

    def _run(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------- WebSocket
class WebSocketTransport(Transport):
    kind = WEBSOCKET

    def __init__(self, cfg: TransportConfig, cb: TransportCallbacks):
        super().__init__(cfg, cb)
        self._ws: websocket.WebSocket | None = None
        self._send_lock = threading.Lock()

    def connect_url(self) -> str:
        return self.cfg.url

    def describe(self) -> str:
        return self.connect_url()

    def _connect_options(self) -> dict:
        opts: dict = {
            "timeout": self.cfg.timeout or None,
            "subprotocols": [p for p in self.cfg.subprotocols if p] or None,
            "suppress_origin": True,
        }
        extra: list[str] = []
        cookies: list[str] = []
        for key, value in self.cfg.headers:
            low = key.strip().lower()
            if not low:
                continue
            if low == "origin":
                opts["origin"] = value
                opts["suppress_origin"] = False
            elif low == "host":
                opts["host"] = value
            elif low == "cookie":
                cookies.append(value)
            else:
                extra.append(f"{key.strip()}: {value}")
        if extra:
            opts["header"] = extra
        if cookies:
            opts["cookie"] = "; ".join(cookies)
        return opts

    def _sslopt(self) -> dict:
        if not self.cfg.tls_verify:
            return {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
        if self.cfg.ca_file:
            return {"ca_certs": self.cfg.ca_file}
        return {}

    def _run(self) -> None:
        url = self.connect_url()
        ws = websocket.WebSocket(sslopt=self._sslopt(), enable_multithread=True, skip_utf8_validation=True)
        self._ws = ws
        try:
            ws.connect(url, **self._connect_options())
        except websocket.WebSocketBadStatusException as exc:
            headers = list((getattr(exc, "resp_headers", None) or {}).items())
            body = getattr(exc, "resp_body", None) or b""
            status_msg = getattr(exc, "status_message", "") or ""
            text = f"WebSocket handshake rejected: HTTP {exc.status_code} {status_msg}".strip()
            if body:
                text += "\n" + body[:2000].decode("utf-8", errors="replace")
            self.cb.on_log("error", text, headers)
            if self.kind == WEBSOCKET and exc.status_code in (400, 404) and \
                    probe_sockjs(url, self.cfg.headers, self.cfg.timeout, self.cfg.tls_verify):
                parts = urlsplit(url)
                http_url = urlunsplit(({"wss": "https"}.get(parts.scheme, "http"), parts.netloc, parts.path, parts.query, ""))
                ws_url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/websocket", parts.query, ""))
                self.cb.on_log("warn", "This endpoint is SockJS-only (registered with withSockJS()), so it doesn't accept "
                                       f"plain WebSocket here. Use {http_url} (SockJS) or {ws_url} (plain WebSocket).", [])
            self._report_close(f"Handshake failed (HTTP {exc.status_code})", True)
            return
        except Exception as exc:
            reason = "Cancelled" if self._closing else f"Connection failed: {exc}"
            self._report_close(reason, not self._closing)
            return
        if self._closing:
            ws.shutdown()
            self._report_close("Cancelled", False)
            return
        ws.settimeout(None)
        info = {
            "transport": self.kind,
            "url": url,
            "status": ws.getstatus(),
            "headers": list((ws.getheaders() or {}).items()),
            "subprotocol": ws.getsubprotocol() or "",
        }
        self._handshake_done(info)

        reason, error = "Connection closed", False
        while True:
            try:
                opcode, frame = ws.recv_data_frame(control_frame=True)
            except websocket.WebSocketConnectionClosedException:
                reason = "Connection closed" if self._closing else "Connection closed by server (no close frame)"
                error = not self._closing
                break
            except Exception as exc:
                if self._closing:
                    reason = "Connection closed"
                else:
                    reason, error = f"Connection error: {exc}", True
                break
            if opcode == ABNF.OPCODE_TEXT:
                self._on_message(bytes(frame.data), False)
            elif opcode == ABNF.OPCODE_BINARY:
                self._on_message(bytes(frame.data), True)
            elif opcode == ABNF.OPCODE_CLOSE:
                code, text = None, ""
                if len(frame.data) >= 2:
                    code = struct.unpack("!H", frame.data[:2])[0]
                    text = frame.data[2:].decode("utf-8", errors="replace")
                who = "client" if self._closing else "server"
                reason = f"WebSocket closed by {who}"
                if code is not None:
                    reason += f" (code {code}{', ' + text if text else ''})"
                error = not self._closing and code not in (None, 1000, 1001)
                break
            elif opcode == ABNF.OPCODE_PING:
                self.cb.on_control("WebSocket ping (pong sent)")
            elif opcode == ABNF.OPCODE_PONG:
                self.cb.on_control("WebSocket pong")
        try:
            ws.shutdown()
        except Exception:
            pass
        self._report_close(reason, error)

    def _handshake_done(self, info: dict) -> None:
        self.cb.on_open(info)

    def _on_message(self, data: bytes, binary: bool) -> None:
        self.cb.on_data(data, binary)

    def _send_ws(self, payload: bytes, binary: bool) -> None:
        ws = self._ws
        if ws is None or not ws.connected:
            raise ConnectionError("WebSocket is not open")
        with self._send_lock:
            ws.send(payload, ABNF.OPCODE_BINARY if binary else ABNF.OPCODE_TEXT)

    def send(self, data: bytes) -> None:
        self._send_ws(data, self.cfg.send_binary)

    def close(self) -> None:
        self._closing = True
        ws = self._ws
        if ws is not None and ws.connected:
            try:
                with self._send_lock:
                    ws.send_close(1000, b"")
            except Exception:
                pass
            timer = threading.Timer(2.0, self.abort)
            timer.daemon = True
            timer.start()
        else:
            self.abort()

    def abort(self) -> None:
        self._closing = True
        ws = self._ws
        sock = getattr(ws, "sock", None) if ws else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class SockJSTransport(WebSocketTransport):
    """SockJS framing over its raw WebSocket transport (what Spring's withSockJS() exposes)."""

    kind = SOCKJS

    def __init__(self, cfg: TransportConfig, cb: TransportCallbacks):
        super().__init__(cfg, cb)
        self._server_id = f"{random.randint(0, 999):03d}"
        self._session_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self._info: dict = {}
        self._opened = False
        self.plain = False  # True after falling back to a plain WebSocket endpoint

    def connect_url(self) -> str:
        parts = urlsplit(self.cfg.url)
        scheme = {"http": "ws", "https": "wss"}.get(parts.scheme.lower(), parts.scheme.lower() or "ws")
        path = parts.path if self.plain else parts.path.rstrip("/") + f"/{self._server_id}/{self._session_id}/websocket"
        return urlunsplit((scheme, parts.netloc, path, parts.query, ""))

    def _run(self) -> None:
        if self.cfg.auto and probe_sockjs(self.cfg.url, self.cfg.headers, self.cfg.timeout, self.cfg.tls_verify) is False:
            self.plain = True
            self.cb.on_log("info", f"{sockjs_info_url(self.cfg.url)} returned 404, so this endpoint has no SockJS — "
                                   f"using plain WebSocket at {self.connect_url()}", [])
        super()._run()

    def _handshake_done(self, info: dict) -> None:
        if self.plain:
            self._opened = True
            self.cb.on_open(info)
            return
        self._info = info
        self.cb.on_log("info", "WebSocket open, waiting for SockJS 'o' frame", info.get("headers", []))

    def _on_message(self, data: bytes, binary: bool) -> None:
        if self.plain:
            self.cb.on_data(data, binary)
            return
        text = data.decode("utf-8", errors="replace")
        if not text:
            return
        kind, payload = text[0], text[1:]
        try:
            if kind == "o":
                if not self._opened:
                    self._opened = True
                    self.cb.on_open(self._info)
            elif kind == "h":
                self.cb.on_control("SockJS heartbeat 'h'")
            elif kind == "a":
                for message in json.loads(payload):
                    self.cb.on_data(str(message).encode("utf-8"), False)
            elif kind == "m":
                self.cb.on_data(str(json.loads(payload)).encode("utf-8"), False)
            elif kind == "c":
                code, reason = (json.loads(payload) + [None, ""])[:2]
                self.cb.on_log("warn", f"SockJS close frame: {code} {reason}", [])
            else:
                self.cb.on_log("warn", f"Unknown SockJS frame: {text[:200]}", [])
        except ValueError as exc:
            self.cb.on_log("error", f"Malformed SockJS frame ({exc}): {text[:200]}", [])

    def send(self, data: bytes) -> None:
        if self.plain:
            self._send_ws(data, self.cfg.send_binary)
            return
        if not self._opened:
            raise ConnectionError("SockJS session is not open yet")
        payload = json.dumps([data.decode("utf-8", errors="replace")])
        self._send_ws(payload.encode("utf-8"), False)


# ---------------------------------------------------------------------------- TCP
class TcpTransport(Transport):
    kind = TCP

    def __init__(self, cfg: TransportConfig, cb: TransportCallbacks):
        super().__init__(cfg, cb)
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        target = cfg.url if "://" in cfg.url else "tcp://" + cfg.url
        parts = urlsplit(target)
        self.tls = parts.scheme.lower() in TLS_SCHEMES
        self.host = parts.hostname or "localhost"
        try:
            port = parts.port
        except ValueError:
            port = None
        self.port = port or (61614 if self.tls else 61613)

    def describe(self) -> str:
        return f"{'tls' if self.tls else 'tcp'}://{self.host}:{self.port}"

    def _run(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.cfg.timeout or None)
            if self.tls:
                ctx = ssl.create_default_context(cafile=self.cfg.ca_file or None)
                if not self.cfg.tls_verify:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=self.host)
            sock.settimeout(None)
        except Exception as exc:
            reason = "Cancelled" if self._closing else f"Connection failed: {exc}"
            self._report_close(reason, not self._closing)
            return
        self._sock = sock
        if self._closing:
            sock.close()
            self._report_close("Cancelled", False)
            return
        info: dict = {"transport": self.kind, "url": self.describe(), "peer": f"{self.host}:{self.port}"}
        if self.tls and isinstance(sock, ssl.SSLSocket):
            cipher = sock.cipher()
            info["headers"] = [("tls-version", sock.version() or ""), ("cipher", cipher[0] if cipher else "")]
        self.cb.on_open(info)

        reason, error = "Connection closed", False
        while True:
            try:
                chunk = sock.recv(65536)
            except Exception as exc:
                if not self._closing:
                    reason, error = f"Connection error: {exc}", True
                break
            if not chunk:
                if not self._closing:
                    reason = "Connection closed by server"
                break
            self.cb.on_data(chunk, False)
        try:
            sock.close()
        except OSError:
            pass
        self._report_close(reason, error)

    def send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise ConnectionError("TCP socket is not open")
        with self._send_lock:
            sock.sendall(data)

    def close(self) -> None:
        self._closing = True
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def create_transport(cfg: TransportConfig, cb: TransportCallbacks) -> Transport:
    kind = resolve_kind(cfg.url, cfg.kind)
    cfg.auto = cfg.kind == AUTO
    cfg.kind = kind
    if kind == SOCKJS:
        return SockJSTransport(cfg, cb)
    if kind == WEBSOCKET:
        return WebSocketTransport(cfg, cb)
    return TcpTransport(cfg, cb)
