"""A tiny STOMP broker for trying STOMP Tester locally (and for the test-suite).

    uv run python tools/mock_broker.py                      # ws://localhost:15674/ws, tcp://localhost:61613
    uv run python tools/mock_broker.py --omit-null          # misbehave: frames without NULL terminator
    uv run python tools/mock_broker.py --spring-like        # no SockJS + CONNECT needs a login (like Remotie)

Routing: SEND to /app/<x> is delivered to subscribers of /topic/<x>; any other destination
is delivered to its own subscribers. SEND to /app/error makes the broker answer with ERROR.
SockJS clients connect to http://localhost:15674/ws (the /<server>/<session>/websocket path).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stomp_tester.core.frame import EncodeOptions, Frame, FrameParser, encode_frame, parse_heartbeat  # noqa: E402

log = logging.getLogger("mock-broker")
SOCKJS_PATH = re.compile(r"/[^/]+/[^/]+/websocket$")


class Broker:
    def __init__(self, heartbeat_ms: int = 4000, omit_null: bool = False, sockjs: bool = True,
                 require_login: bool = False):
        self.heartbeat_ms = heartbeat_ms
        self.omit_null = omit_null
        self.sockjs = sockjs
        self.require_login = require_login
        self.connections: set[Connection] = set()
        self.message_ids = itertools.count(1)
        self.sessions = itertools.count(1)


class Connection:
    def __init__(self, broker: Broker, send, close):
        self.broker = broker
        self._send = send
        self._close = close
        self.parser = FrameParser()
        self.version = "1.2"
        self.subs: dict[str, tuple[str, str]] = {}
        self.hb_task: asyncio.Task | None = None
        self.connected = False
        broker.connections.add(self)

    async def send_frame(self, frame: Frame) -> None:
        opts = EncodeOptions(version=self.version, append_null=not self.broker.omit_null)
        await self._send(encode_frame(frame, opts))

    async def on_data(self, data: bytes) -> None:
        for ev in self.parser.feed(data):
            if ev.kind == "frame":
                await self.handle(ev.frame)

    async def handle(self, f: Frame) -> None:
        cmd = f.command
        if not self.connected and cmd not in ("CONNECT", "STOMP"):
            await self.error("Not connected: send CONNECT first")
            return
        if cmd in ("CONNECT", "STOMP") and self.broker.require_login and not f.get("login"):
            # What Spring sends when a ChannelInterceptor throws on CONNECT.
            await self.error("Failed to send message to ExecutorSubscribableChannel[clientInboundChannel]")
            return
        if cmd in ("CONNECT", "STOMP"):
            accepted = [v.strip() for v in (f.get("accept-version") or "1.0").split(",")]
            self.version = max(v for v in accepted if v in ("1.0", "1.1", "1.2")) if accepted else "1.0"
            self.parser.version = self.version
            cx, cy = parse_heartbeat(f.get("heart-beat"))
            sx = self.broker.heartbeat_ms if cy else 0
            self.connected = True
            await self.send_frame(Frame("CONNECTED", [
                ("version", self.version),
                ("heart-beat", f"{sx},{self.broker.heartbeat_ms}"),
                ("server", "MockBroker/1.0"),
                ("session", f"session-{next(self.broker.sessions)}"),
            ]))
            if sx and cy:
                self.hb_task = asyncio.create_task(self.heartbeats(max(sx, cy)))
            return
        if cmd == "SUBSCRIBE":
            self.subs[f.get("id", "")] = (f.get("destination", ""), f.get("ack", "auto"))
        elif cmd == "UNSUBSCRIBE":
            self.subs.pop(f.get("id", ""), None)
        elif cmd == "SEND":
            dest = f.get("destination", "")
            if dest == "/app/error":
                await self.error("You asked for an error", f.body)
                return
            target = "/topic/" + dest[len("/app/"):] if dest.startswith("/app/") else dest
            await self.deliver(target, f)
        elif cmd == "DISCONNECT":
            if f.get("receipt"):
                await self.send_frame(Frame("RECEIPT", [("receipt-id", f.get("receipt"))]))
            await self._close()
            return
        elif cmd not in ("ACK", "NACK", "BEGIN", "COMMIT", "ABORT"):
            await self.error(f"Unknown command {cmd}")
            return
        if f.get("receipt"):
            await self.send_frame(Frame("RECEIPT", [("receipt-id", f.get("receipt"))]))

    async def deliver(self, destination: str, f: Frame) -> None:
        skip = {"destination", "receipt", "content-length", "transaction"}
        extra = [(k, v) for k, v in f.headers if k not in skip]
        for conn in list(self.broker.connections):
            for sub_id, (dest, ack) in conn.subs.items():
                if dest != destination:
                    continue
                mid = f"msg-{next(self.broker.message_ids)}"
                headers = [("destination", destination), ("subscription", sub_id), ("message-id", mid)]
                if ack != "auto":
                    headers.append(("ack", mid))
                await conn.send_frame(Frame("MESSAGE", headers + extra, f.body))

    async def error(self, message: str, body: bytes = b"") -> None:
        await self.send_frame(Frame("ERROR", [("message", message)], body))
        await self._close()

    async def heartbeats(self, interval_ms: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval_ms / 1000)
                await self._send(b"\n")
        except Exception:
            pass

    def closed(self) -> None:
        self.broker.connections.discard(self)
        if self.hb_task:
            self.hb_task.cancel()


async def serve(broker: Broker, host: str, ws_port: int, tcp_port: int, ready: asyncio.Event | None = None):
    from websockets.asyncio.server import serve as ws_serve

    async def ws_handler(ws):
        path = ws.request.path
        sockjs = bool(SOCKJS_PATH.search(path))

        async def send(data: bytes):
            text = data.decode("utf-8")
            await ws.send("a" + json.dumps([text]) if sockjs else text)

        async def close():
            await ws.close()

        conn = Connection(broker, send, close)
        try:
            if sockjs:
                await ws.send("o")
            async for message in ws:
                raw = message.encode("utf-8") if isinstance(message, str) else message
                if sockjs:
                    for item in json.loads(raw.decode("utf-8")):
                        await conn.on_data(item.encode("utf-8"))
                else:
                    await conn.on_data(raw)
        except Exception as exc:  # connection dropped
            log.debug("ws connection ended: %s", exc)
        finally:
            conn.closed()

    async def tcp_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        async def send(data: bytes):
            writer.write(data)
            await writer.drain()

        async def close():
            writer.close()

        conn = Connection(broker, send, close)
        try:
            while chunk := await reader.read(65536):
                await conn.on_data(chunk)
        except Exception as exc:
            log.debug("tcp connection ended: %s", exc)
        finally:
            conn.closed()
            writer.close()

    def process_request(connection, request):
        if not broker.sockjs and (request.path.rstrip("/").endswith("/info") or SOCKJS_PATH.search(request.path)):
            return connection.respond(404, "Not Found\n")
        return None

    ws_server = await ws_serve(ws_handler, host, ws_port, subprotocols=["v12.stomp", "v11.stomp", "v10.stomp"],
                               process_request=process_request)
    tcp_server = await asyncio.start_server(tcp_handler, host, tcp_port)
    if ready is not None:
        ready.set()
    async with ws_server, tcp_server:
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=15674)
    parser.add_argument("--tcp-port", type=int, default=61613)
    parser.add_argument("--heartbeat", type=int, default=4000, help="server heart-beat in ms (0 = off)")
    parser.add_argument("--omit-null", action="store_true", help="send frames without the NULL terminator")
    parser.add_argument("--spring-like", action="store_true",
                        help="no SockJS endpoint and CONNECT must carry a login, like a Spring server with an auth interceptor")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    broker = Broker(args.heartbeat, args.omit_null, sockjs=not args.spring_like, require_login=args.spring_like)
    print(f"Mock STOMP broker: ws://{args.host}:{args.ws_port}/ws  sockjs http://{args.host}:{args.ws_port}/ws  "
          f"tcp://{args.host}:{args.tcp_port}")
    try:
        asyncio.run(serve(broker, args.host, args.ws_port, args.tcp_port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
