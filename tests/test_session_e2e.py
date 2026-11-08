"""End-to-end: StompSession against tools/mock_broker.py over WebSocket, SockJS and TCP."""

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import mock_broker  # noqa: E402
from stomp_tester.core import session as S  # noqa: E402
from stomp_tester.core.frame import Frame  # noqa: E402
from stomp_tester.core.models import Profile  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def broker():
    ws_port, tcp_port = _free_port(), _free_port()
    ready = threading.Event()

    def run():
        async def main():
            ev = asyncio.Event()
            task = asyncio.create_task(mock_broker.serve(mock_broker.Broker(heartbeat_ms=300), "127.0.0.1", ws_port, tcp_port, ev))
            await ev.wait()
            ready.set()
            await task
        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5)
    return ws_port, tcp_port


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


@pytest.mark.parametrize("kind", ["websocket", "sockjs", "tcp"])
def test_full_flow(app, broker, kind):
    ws_port, tcp_port = broker
    url = {
        "websocket": f"ws://127.0.0.1:{ws_port}/ws",
        "sockjs": f"http://127.0.0.1:{ws_port}/ws",
        "tcp": f"tcp://127.0.0.1:{tcp_port}",
    }[kind]
    session = S.StompSession()
    session.set_profile(Profile(url=url, transport="auto", heartbeat_out=200, heartbeat_in=200, auto_receipt=True))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.CONNECTED), [e.text or e.command for e in entries]
    assert session.version == "1.2"
    assert session.transport_kind == kind

    session.add_subscription("/topic/greet", ack="client")
    session.profile.auto_ack = True
    assert wait_until(lambda: any(e.command == "RECEIPT" for e in entries))

    session.send_frame(Frame("SEND", [("destination", "/app/greet"), ("content-type", "text/plain")], b"hello\x00world"))
    assert wait_until(lambda: any(e.command == "MESSAGE" for e in entries))
    message = next(e for e in entries if e.command == "MESSAGE")
    assert message.body == b"hello\x00world"
    assert session.subscriptions[0].count == 1
    assert wait_until(lambda: any(e.command == "ACK" and e.direction == "out" for e in entries))

    sent = next(e for e in entries if e.command == "SEND")
    assert wait_until(lambda: sent.rtt_ms is not None)

    # heart-beats flow both ways
    assert wait_until(lambda: session.stats["heartbeats_in"] > 0 and session.stats["heartbeats_out"] > 0, 3)

    # raw send with literal \u0000 handled by the caller (composer) — here bytes directly
    session.send_bytes(b"SEND\ndestination:/topic/greet\n\nraw\x00")
    assert wait_until(lambda: sum(1 for e in entries if e.command == "MESSAGE") == 2)

    session.disconnect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED)
    assert any(e.command == "DISCONNECT" for e in entries)
    assert not any(e.level == "error" for e in entries), [(e.text, e.warnings) for e in entries if e.level == "error"]


def test_server_error_and_reconnect(app, broker):
    ws_port, _ = broker
    session = S.StompSession()
    session.set_profile(Profile(url=f"ws://127.0.0.1:{ws_port}/ws", auto_reconnect=True, reconnect_delay=0.2,
                                reconnect_max=1, heartbeat_in=0, heartbeat_out=0))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.CONNECTED)
    session.send_frame(Frame("SEND", [("destination", "/app/error")], b"boom"))
    assert wait_until(lambda: any(e.command == "ERROR" for e in entries))
    assert wait_until(lambda: session.state == S.RECONNECTING)
    assert wait_until(lambda: session.state == S.CONNECTED)
    session.disconnect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED)


def test_connection_refused(app):
    session = S.StompSession()
    session.set_profile(Profile(url=f"ws://127.0.0.1:{_free_port()}/ws"))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED and len(entries) > 1)
    assert any(e.level == "error" for e in entries)


def test_lenient_mode_with_broker_omitting_null(app):
    ws_port, tcp_port = _free_port(), _free_port()
    ready = threading.Event()

    def run():
        async def main():
            ev = asyncio.Event()
            task = asyncio.create_task(mock_broker.serve(mock_broker.Broker(0, omit_null=True), "127.0.0.1", ws_port, tcp_port, ev))
            await ev.wait()
            ready.set()
            await task
        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5)
    session = S.StompSession()
    session.set_profile(Profile(url=f"ws://127.0.0.1:{ws_port}/ws", lenient_ws_frames=False))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: any("Partial frame" in e.text for e in entries))
    assert session.state == S.OPEN
    session.disconnect_(force=True)
    assert wait_until(lambda: session.state == S.DISCONNECTED)

    session.profile.lenient_ws_frames = True
    entries.clear()
    session.connect_()
    assert wait_until(lambda: session.state == S.CONNECTED)
    connected = next(e for e in entries if e.command == "CONNECTED")
    assert any("NULL" in w for w in connected.warnings)
    session.disconnect_(force=True)
    assert wait_until(lambda: session.state == S.DISCONNECTED)


def _start(broker_obj):
    ws_port, tcp_port = _free_port(), _free_port()
    ready = threading.Event()

    def run():
        async def main():
            ev = asyncio.Event()
            task = asyncio.create_task(mock_broker.serve(broker_obj, "127.0.0.1", ws_port, tcp_port, ev))
            await ev.wait()
            ready.set()
            await task
        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5)
    return ws_port


def test_http_url_falls_back_to_plain_websocket_when_no_sockjs(app):
    ws_port = _start(mock_broker.Broker(0, sockjs=False))
    session = S.StompSession()
    session.set_profile(Profile(url=f"http://127.0.0.1:{ws_port}/ws", transport="auto"))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.CONNECTED), [e.text or e.command for e in entries]
    assert any("no SockJS" in e.text for e in entries)
    session.add_subscription("/topic/x")
    session.send_frame(Frame("SEND", [("destination", "/topic/x")], b"hi"))
    assert wait_until(lambda: any(e.command == "MESSAGE" for e in entries))
    session.disconnect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED)


def test_explicit_sockjs_does_not_fall_back(app):
    ws_port = _start(mock_broker.Broker(0, sockjs=False))
    session = S.StompSession()
    session.set_profile(Profile(url=f"http://127.0.0.1:{ws_port}/ws", transport="sockjs"))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED and any("404" in e.text for e in entries))


def test_rejected_connect_explains_missing_login(app):
    ws_port = _start(mock_broker.Broker(0, require_login=True))
    session = S.StompSession()
    session.set_profile(Profile(url=f"ws://127.0.0.1:{ws_port}/ws"))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED and any(e.command == "ERROR" for e in entries))
    hint = next(e.text for e in entries if "CONNECT carried no login" in e.text)
    assert "ChannelInterceptor" in hint

    entries.clear()
    session.profile.login = "{{token}}"
    session.profile.variables = [[True, "token", "eyJ.test.jwt"]]
    session.connect_()
    assert wait_until(lambda: session.state == S.CONNECTED)
    connect = next(e for e in entries if e.command == "CONNECT")
    assert connect.header("login") == "eyJ.test.jwt"
    session.disconnect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED)


def test_plain_websocket_on_sockjs_only_endpoint_gets_a_hint(app, monkeypatch):
    """Spring's withSockJS() endpoint rejects a plain upgrade on /ws but answers /ws/info."""
    import websocket
    from stomp_tester.core import transport as T

    def reject(self, url, **opts):
        raise websocket.WebSocketBadStatusException("Handshake status 400", 400, "Bad Request", {}, b"")

    monkeypatch.setattr(websocket.WebSocket, "connect", reject)
    monkeypatch.setattr(T, "probe_sockjs", lambda *a, **k: True)
    session = S.StompSession()
    session.set_profile(Profile(url="ws://127.0.0.1:9/ws", transport="websocket"))
    entries = []
    session.entry_added.connect(entries.append)
    session.connect_()
    assert wait_until(lambda: session.state == S.DISCONNECTED and len(entries) >= 3)
    hint = next(e.text for e in entries if "SockJS-only" in e.text)
    assert "http://127.0.0.1:9/ws " in hint and "ws://127.0.0.1:9/ws/websocket" in hint
