"""Escape-sequence interpretation and byte visualisation helpers."""

from __future__ import annotations

import json
import re

_HEX = set("0123456789abcdefABCDEF")
_NULL_PATTERNS = re.compile(r"\\u0000|\\x00|\\0(?![0-9])|\^@")


def interpret_null_escapes(text: str) -> str:
    """Turn the usual spellings of the NULL octet into a real NULL.

    Recognised: ``\\u0000``, ``\\x00``, ``\\0`` and ``^@``.
    """
    return _NULL_PATTERNS.sub("\x00", text)


def interpret_escapes(text: str) -> str:
    """Full C-like escape processing: \\n \\r \\t \\0 \\\\ \\xHH \\uHHHH and ^@."""
    out: list[str] = []
    i, n = 0, len(text)
    simple = {"n": "\n", "r": "\r", "t": "\t", "0": "\x00", "\\": "\\"}
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt == "x" and i + 4 <= n and set(text[i + 2:i + 4]) <= _HEX:
                out.append(chr(int(text[i + 2:i + 4], 16)))
                i += 4
                continue
            if nxt == "u" and i + 6 <= n and set(text[i + 2:i + 6]) <= _HEX:
                out.append(chr(int(text[i + 2:i + 6], 16)))
                i += 6
                continue
        if ch == "^" and i + 1 < n and text[i + 1] == "@":
            out.append("\x00")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def visualize(data: bytes, limit: int = 256 * 1024) -> str:
    """Make control characters visible: LF -> ␊, CR -> ␍, NULL -> ␀."""
    truncated = len(data) > limit
    text = data[:limit].decode("utf-8", errors="backslashreplace")
    out: list[str] = []
    for ch in text:
        if ch == "\n":
            out.append("␊\n")
        elif ch == "\r":
            out.append("␍")
        elif ch == "\x00":
            out.append("␀")
        elif ch == "\t":
            out.append("␉")
        elif ord(ch) < 32 or ch == "\x7f":
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    if truncated:
        out.append(f"\n… ({len(data) - limit} more bytes)")
    return "".join(out)


def hexdump(data: bytes, limit: int = 64 * 1024) -> str:
    lines = []
    chunk = data[:limit]
    for off in range(0, len(chunk), 16):
        row = chunk[off:off + 16]
        hexes = " ".join(f"{b:02x}" for b in row[:8])
        if len(row) > 8:
            hexes += "  " + " ".join(f"{b:02x}" for b in row[8:])
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"{off:08x}  {hexes:<49}  |{ascii_}|")
    if len(data) > limit:
        lines.append(f"… {len(data) - limit} more bytes not shown")
    return "\n".join(lines)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def try_pretty_json(text: str) -> str | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
    except (ValueError, RecursionError):
        return None


def one_line(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
