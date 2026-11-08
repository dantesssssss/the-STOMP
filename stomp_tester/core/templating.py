"""{{placeholder}} expansion for headers and bodies."""

from __future__ import annotations

import os
import random
import re
import string
import time
import uuid
from datetime import datetime, timezone

PATTERN = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*(?::[^{}]*)?)\s*\}\}")

BUILTINS_HELP = [
    ("{{uuid}}", "random UUID v4"),
    ("{{counter}}", "send counter (increments once per send)"),
    ("{{timestamp}}", "epoch milliseconds"),
    ("{{unix}}", "epoch seconds"),
    ("{{iso}}", "ISO-8601 UTC time"),
    ("{{date}} / {{time}}", "local date / time"),
    ("{{randint:1:100}}", "random integer in range"),
    ("{{randstr:12}}", "random alphanumeric string"),
    ("{{random}}", "random float 0..1"),
    ("{{env:NAME}}", "environment variable"),
    ("{{null}} {{lf}} {{cr}} {{crlf}} {{tab}}", "control characters"),
    ("{{myVar}}", "any variable from the Variables panel"),
]


class TemplateContext:
    def __init__(self) -> None:
        self.counter = 0

    def next_send(self) -> int:
        self.counter += 1
        return self.counter


def render(
    text: str,
    ctx: TemplateContext,
    variables: dict[str, str] | None = None,
    unknown: list[str] | None = None,
    counter: int | None = None,
) -> str:
    if "{{" not in text:
        return text
    variables = variables or {}
    count = ctx.counter if counter is None else counter

    def repl(match: re.Match) -> str:
        expr = match.group(1)
        name, _, arg = expr.partition(":")
        if expr in variables:
            return variables[expr]
        if name in variables and not arg:
            return variables[name]
        key = name.lower()
        if key == "uuid":
            return str(uuid.uuid4())
        if key in ("counter", "seq"):
            return str(count)
        if key in ("timestamp", "ts", "now_ms"):
            return str(int(time.time() * 1000))
        if key == "unix":
            return str(int(time.time()))
        if key == "iso":
            return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        if key == "date":
            return datetime.now().strftime("%Y-%m-%d")
        if key == "time":
            return datetime.now().strftime("%H:%M:%S")
        if key == "random":
            return str(random.random())
        if key == "randint":
            lo, hi = 0, 1_000_000
            parts = [p for p in arg.split(":") if p.strip()]
            try:
                if len(parts) == 1:
                    hi = int(parts[0])
                elif len(parts) >= 2:
                    lo, hi = int(parts[0]), int(parts[1])
            except ValueError:
                pass
            return str(random.randint(min(lo, hi), max(lo, hi)))
        if key == "randstr":
            try:
                length = max(1, min(4096, int(arg or 8)))
            except ValueError:
                length = 8
            return "".join(random.choices(string.ascii_letters + string.digits, k=length))
        if key == "env":
            return os.environ.get(arg, "")
        controls = {"null": "\x00", "lf": "\n", "cr": "\r", "crlf": "\r\n", "tab": "\t"}
        if key in controls:
            return controls[key]
        if unknown is not None:
            unknown.append(expr)
        return match.group(0)

    return PATTERN.sub(repl, text)
