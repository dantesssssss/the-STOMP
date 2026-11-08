# STOMP Tester

A desktop app for testing STOMP servers (Spring, RabbitMQ, ActiveMQ/Artemis, your own) over
**WebSocket**, **SockJS** and plain **TCP/TLS**. Written in Python with PySide6 (Qt).

## Run

```bash
uv sync
uv run main.py            # or: uv run python -m stomp_tester
```

Want something to talk to? Start the bundled mock broker in a second terminal:

```bash
uv run python tools/mock_broker.py          # ws://127.0.0.1:15674/ws · http://127.0.0.1:15674/ws (SockJS) · tcp://127.0.0.1:61613
uv run python tools/mock_broker.py --omit-null   # a broken server that forgets the NULL terminator
```

`SEND /app/x` is delivered to subscribers of `/topic/x`; `SEND /app/error` makes it reply with `ERROR`.

## What's in it

The window follows the macOS three-pane layout: a **sidebar** (servers, subscriptions, templates),
the **conversation** with the **composer** underneath, and an **inspector** on the right
(Message · Connection · Variables). Light and dark appearance follow the system or can be forced
in Settings.

- **Servers** (sidebar): one profile per server — URL, transport, CONNECT headers, framing and
  behaviour switches, subscriptions and variables are all stored per profile. The toolbar address
  bar holds the URL, a transport picker (Auto/WebSocket/SockJS/TCP) and the live connection status.
- **Connection inspector**: subprotocols, handshake HTTP headers (`Authorization: Bearer {{token}}`),
  TLS verification / CA file, CONNECT or STOMP frame (accept-version, host, login, passcode,
  extra headers), heart-beats with tolerance, auto-reconnect.
- **Composer**: *Structured* (command, destination, headers you can tick on/off, body with
  content-type, JSON format/minify) or *Raw* (type the whole frame). The eye button shows the exact
  bytes (`␊` LF, `␍` CR, `␀` NULL) and warns when the NULL terminator is missing.
- **Framing chips**: `NULL \0` (auto-append the terminator), `Receipt` (round-trip time under each
  message), `{{ }}` placeholders; *More* has content-length, raw-mode `\u0000` / `^@` → NULL,
  full escapes, line break after NULL, CRLF.
- **Placeholders**: `{{uuid}}`, `{{counter}}`, `{{timestamp}}`, `{{iso}}`, `{{randint:1:100}}`,
  `{{randstr:12}}`, `{{env:NAME}}`, `{{null}}` … plus your own variables.
- **Repeat** sending (×N every M ms), recent sends, saved templates (sidebar).
- **Subscriptions** (sidebar): type a destination and press Return; switch to (un)subscribe,
  message-count badges, click to show only that subscription's messages, double-click to edit
  (ack mode, extra headers). Switched-on subscriptions are re-subscribed on every connect.
- **Conversation**: Messages-style chat or a table; search (`destination:/topic/x`, `/regex/`)
  and filters in the toolbar; inspector with headers, pretty JSON, raw bytes and hex; ACK/NACK,
  resend, edit in composer, save as template.
- **History**: every session is recorded to a `.jsonl` file; browse, reopen, export to HTML,
  JSON, JSONL or text.

Everything is stored in `~/Library/Application Support/STOMP Tester` (macOS),
`%APPDATA%\STOMP Tester` (Windows) or `~/.local/share/stomp-tester` (Linux).
Set `STOMP_TESTER_HOME` to use another folder.

## Shortcuts

| | |
|---|---|
| Send | ⌘↩ (Ctrl+Enter) |
| Connect / disconnect | F5 or ⌘R |
| Byte preview | ⌘P |
| Save as template | ⌘S |
| Search | ⌘F |
| Chat / table | ⌘1 / ⌘2 |
| Structured / raw editor | ⇧⌘1 / ⇧⌘2 |
| Toggle sidebar / inspector / composer | ⌃⌘S / ⌥⌘I / ⇧⌘Y |
| Inspector tabs | ⌥⌘1 / ⌥⌘2 / ⌥⌘3 |
| Clear conversation | ⌘K |
| History | ⌘H |
| Settings | ⌘, |

## Tests

```bash
uv run pytest
```
