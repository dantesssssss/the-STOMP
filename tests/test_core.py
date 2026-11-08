from stomp_tester.core.frame import EncodeOptions, Frame, FrameParser, encode_frame, escape_header, unescape_header
from stomp_tester.core.models import LogEntry, Profile
from stomp_tester.core.templating import TemplateContext, render
from stomp_tester.core.textutil import hexdump, interpret_escapes, interpret_null_escapes, visualize
from stomp_tester.core.transport import resolve_kind


def test_encode_basic_frame():
    data = encode_frame(Frame("SEND", [("destination", "/app/x")], b"hi"))
    assert data == b"SEND\ndestination:/app/x\ncontent-length:2\n\nhi\x00"


def test_encode_options():
    opts = EncodeOptions(eol="\r\n", content_length=False, append_null=False, trailing_eol=True)
    data = encode_frame(Frame("SEND", [("destination", "/q")], b"x"), opts)
    assert data == b"SEND\r\ndestination:/q\r\n\r\nx\r\n"


def test_header_escaping_roundtrip():
    value = "a:b\nc\\d\re"
    escaped = escape_header(value, "1.2")
    assert escaped == "a\\cb\\nc\\\\d\\re"
    assert unescape_header(escaped, "1.2") == (value, None)
    assert escape_header(value, "1.0") == value
    decoded, warning = unescape_header("bad\\t", "1.2")
    assert decoded == "bad\\t" and warning


def test_connect_frames_are_not_escaped():
    data = encode_frame(Frame("CONNECT", [("passcode", "a:b")]), EncodeOptions())
    assert b"passcode:a:b" in data


def test_parser_handles_split_and_multiple_frames_and_heartbeats():
    wire = (
        b"\n\r\n"
        + encode_frame(Frame("MESSAGE", [("destination", "/t"), ("subscription", "s")], b"one"))
        + b"\n"
        + encode_frame(Frame("RECEIPT", [("receipt-id", "r1")]))
    )
    parser = FrameParser()
    events = []
    for i in range(len(wire)):  # byte by byte
        events += parser.feed(wire[i:i + 1])
    kinds = [e.kind for e in events]
    assert kinds.count("frame") == 2
    frames = [e.frame for e in events if e.kind == "frame"]
    assert frames[0].command == "MESSAGE" and frames[0].body == b"one"
    assert frames[1].get("receipt-id") == "r1"
    assert sum(e.count for e in events if e.kind == "heartbeat") == 3
    assert parser.pending == 0


def test_parser_content_length_allows_null_in_body():
    body = b"a\x00b"
    wire = encode_frame(Frame("MESSAGE", [("destination", "/t")], body))
    events = FrameParser().feed(wire)
    assert len(events) == 1 and events[0].frame.body == body and not events[0].frame.warnings


def test_parser_reports_missing_null_in_lenient_flush():
    parser = FrameParser()
    assert parser.feed(b"MESSAGE\ndestination:/t\n\nhello") == []
    assert parser.has_partial_frame()
    events = parser.flush()
    assert events[0].frame.body == b"hello"
    assert any("NULL" in w for w in events[0].frame.warnings)


def test_parser_frame_without_blank_line():
    events = FrameParser().feed(b"DISCONNECT\x00")
    assert events[0].frame.command == "DISCONNECT"
    assert events[0].frame.warnings


def test_parser_unescapes_headers():
    events = FrameParser("1.2").feed(b"MESSAGE\nkey:a\\cb\n\n\x00")
    assert events[0].frame.get("key") == "a:b"


def test_escape_interpretation():
    assert interpret_null_escapes("x\\u0000y^@z\\x00") == "x\x00y\x00z\x00"
    assert interpret_null_escapes('{"a":"\\n"}') == '{"a":"\\n"}'
    assert interpret_escapes("a\\nb\\tc\\u0041\\x42^@") == "a\nb\tcAB\x00"


def test_visualize_and_hexdump():
    assert visualize(b"A\r\n\x00") == "A␍␊\n␀"
    assert hexdump(b"AB").startswith("00000000  41 42")


def test_templating():
    ctx = TemplateContext()
    ctx.next_send()
    unknown: list[str] = []
    out = render("{{counter}}-{{name}}-{{nope}}-{{randint:5:5}}-{{null}}", ctx, {"name": "bob"}, unknown)
    assert out == "1-bob-{{nope}}-5-\x00"
    assert unknown == ["nope"]
    assert len(render("{{uuid}}", ctx)) == 36


def test_resolve_kind():
    assert resolve_kind("ws://h/ws", "auto") == "websocket"
    assert resolve_kind("https://h/ws", "auto") == "sockjs"
    assert resolve_kind("ssl://h:61614", "auto") == "tcp"
    assert resolve_kind("ws://h/ws", "tcp") == "tcp"


def test_models_roundtrip():
    p = Profile(name="x", ws_headers=[[True, "Authorization", "Bearer t"]])
    assert Profile.from_dict({**p.to_dict(), "unknown_future_key": 1}) == p
    e = LogEntry("in", "frame", command="MESSAGE", headers=[("a", "b")], body=b"\xff\x00", raw=b"MESSAGE\n\n\xff\x00\x00")
    e2 = LogEntry.from_dict(e.to_dict())
    assert (e2.body, e2.raw, e2.headers) == (e.body, e.raw, e.headers)
