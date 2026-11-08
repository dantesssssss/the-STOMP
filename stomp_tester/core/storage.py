"""JSON persistence for profiles, templates, settings and per-session history files."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import AppSettings, LogEntry, Profile, Template
from .textutil import try_pretty_json

APP_DIR_NAME = "STOMP Tester"


def default_data_dir() -> Path:
    override = os.environ.get("STOMP_TESTER_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "stomp-tester"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # Keep the broken file around for the user instead of silently overwriting it.
        try:
            path.replace(path.with_suffix(path.suffix + f".broken-{int(time.time())}"))
        except OSError:
            pass
        return default


class Store:
    def __init__(self, base: Path | None = None):
        self.base = base or default_data_dir()
        self.base.mkdir(parents=True, exist_ok=True)

    # profiles
    def load_profiles(self) -> tuple[list[Profile], str]:
        data = _read_json(self.base / "profiles.json", {})
        profiles = [Profile.from_dict(p) for p in data.get("profiles", [])]
        if not profiles:
            profiles = default_profiles()
        return profiles, data.get("last", profiles[0].name)

    def save_profiles(self, profiles: list[Profile], last: str) -> None:
        _write_json(self.base / "profiles.json", {"last": last, "profiles": [p.to_dict() for p in profiles]})

    # templates
    def load_templates(self) -> list[Template]:
        data = _read_json(self.base / "templates.json", {"templates": None})
        items = data.get("templates")
        if items is None:
            return default_templates()
        return [Template.from_dict(t) for t in items]

    def save_templates(self, templates: list[Template]) -> None:
        _write_json(self.base / "templates.json", {"templates": [t.to_dict() for t in templates]})

    # recent sends
    def load_recent(self) -> list[Template]:
        data = _read_json(self.base / "recent.json", {})
        return [Template.from_dict(t) for t in data.get("recent", [])]

    def save_recent(self, recent: list[Template]) -> None:
        _write_json(self.base / "recent.json", {"recent": [t.to_dict() for t in recent]})

    # composer draft
    def load_draft(self) -> Template | None:
        data = _read_json(self.base / "draft.json", None)
        return Template.from_dict(data) if isinstance(data, dict) and "name" in data else None

    def save_draft(self, draft: Template) -> None:
        _write_json(self.base / "draft.json", draft.to_dict())

    # settings
    def load_settings(self) -> AppSettings:
        return AppSettings.from_dict(_read_json(self.base / "settings.json", {}))

    def save_settings(self, settings: AppSettings) -> None:
        _write_json(self.base / "settings.json", settings.to_dict())

    def history_dir(self, settings: AppSettings) -> Path:
        path = Path(settings.history_dir).expanduser() if settings.history_dir else self.base / "history"
        path.mkdir(parents=True, exist_ok=True)
        return path


def default_profiles() -> list[Profile]:
    return [
        Profile(name="Local Spring (WebSocket)", url="ws://localhost:8080/ws"),
        Profile(name="Local SockJS", url="http://localhost:8080/ws", transport="sockjs"),
        Profile(name="Local TCP broker", url="tcp://localhost:61613", transport="tcp", vhost="/"),
    ]


def default_templates() -> list[Template]:
    return [
        Template(
            name="Send JSON",
            command="SEND",
            destination="/app/hello",
            content_type="application/json",
            body='{\n  "id": "{{uuid}}",\n  "n": {{counter}},\n  "sentAt": "{{iso}}"\n}',
        ),
        Template(
            name="Raw CONNECT (manual)",
            mode="raw",
            raw="CONNECT\naccept-version:1.2\nhost:localhost\nheart-beat:0,0\n\n\\u0000",
        ),
        Template(
            name="Raw SEND",
            mode="raw",
            raw="SEND\ndestination:/app/hello\ncontent-type:text/plain\n\nhello from raw mode^@",
        ),
    ]


# ---------------------------------------------------------------------------- history
def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name).strip("_")[:60] or "session"


class HistoryWriter:
    """Appends one JSON object per line. First line describes the session."""

    def __init__(self, directory: Path, profile: str, url: str):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{stamp}_{_safe(profile)}.jsonl"
        n = 1
        while self.path.exists():
            self.path = directory / f"{stamp}_{_safe(profile)}-{n}.jsonl"
            n += 1
        self._file = open(self.path, "a", encoding="utf-8")
        self._dirty = False
        self._write({"type": "session", "format": 1, "profile": profile, "url": url, "started": time.time()})

    def _write(self, obj: dict) -> None:
        if self._file.closed:
            return
        self._file.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._dirty = True

    def write(self, entry: LogEntry) -> None:
        self._write(entry.to_dict())

    def write_update(self, entry: LogEntry) -> None:
        self._write({"type": "update", "id": entry.id, "rtt_ms": entry.rtt_ms, "note": entry.note})

    def flush(self) -> None:
        if self._dirty and not self._file.closed:
            self._file.flush()
            self._dirty = False

    def close(self) -> None:
        if not self._file.closed:
            self._write({"type": "end", "ended": time.time()})
            self._file.close()


@dataclass
class HistoryInfo:
    path: Path
    profile: str
    url: str
    started: float
    size: int


def list_history(directory: Path) -> list[HistoryInfo]:
    items = []
    for path in directory.glob("*.jsonl"):
        try:
            with open(path, encoding="utf-8") as fh:
                meta = json.loads(fh.readline() or "{}")
            stat = path.stat()
        except (OSError, ValueError):
            continue
        items.append(HistoryInfo(
            path=path,
            profile=meta.get("profile", "?"),
            url=meta.get("url", ""),
            started=float(meta.get("started", 0) or 0) or path.stat().st_mtime,
            size=stat.st_size,
        ))
    items.sort(key=lambda i: i.started, reverse=True)
    return items


def read_history(path: Path) -> tuple[dict, list[LogEntry]]:
    meta: dict = {}
    entries: list[LogEntry] = []
    by_id: dict[int, LogEntry] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            kind = obj.get("type")
            if kind == "session":
                meta = obj
            elif kind == "update":
                entry = by_id.get(obj.get("id"))
                if entry is not None:
                    entry.rtt_ms = obj.get("rtt_ms")
                    entry.note = obj.get("note") or entry.note
            elif kind == "end":
                meta["ended"] = obj.get("ended")
            else:
                entry = LogEntry.from_dict(obj)
                entries.append(entry)
                by_id[entry.id] = entry
    return meta, entries


def prune_history(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------- export
def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def export_entries(path: Path, entries: list[LogEntry], meta: dict | None = None) -> None:
    suffix = path.suffix.lower()
    meta = meta or {}
    if suffix == ".jsonl":
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session", "format": 1, **meta}, ensure_ascii=False) + "\n")
            for e in entries:
                fh.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
    elif suffix == ".json":
        _write_json(path, {"session": meta, "entries": [e.to_dict() for e in entries]})
    elif suffix in (".html", ".htm"):
        path.write_text(_render_html(entries, meta), encoding="utf-8")
    else:
        path.write_text(_render_text(entries), encoding="utf-8")


def _render_text(entries: list[LogEntry]) -> str:
    arrows = {"out": ">>>", "in": "<<<", "sys": "---"}
    chunks = []
    for e in entries:
        head = f"[{_fmt_ts(e.ts)}] {arrows.get(e.direction, '---')} #{e.id}"
        if e.kind == "frame":
            extra = f"   (rtt {e.rtt_ms:.1f} ms)" if e.rtt_ms is not None else ""
            chunks.append(f"{head} {e.command}{extra}\n{e.frame_text().rstrip()}\n")
        elif e.kind == "heartbeat":
            chunks.append(f"{head} heart-beat\n")
        else:
            chunks.append(f"{head} {e.text}\n")
    return "\n".join(chunks)


def _render_html(entries: list[LogEntry], meta: dict) -> str:
    rows = []
    for e in entries:
        cls = {"out": "out", "in": "in"}.get(e.direction, "sys")
        if e.kind == "heartbeat":
            continue
        if e.kind == "event":
            rows.append(f'<div class="sys {e.level}">{html.escape(_fmt_ts(e.ts))} · {html.escape(e.text)}</div>')
            continue
        level = " error" if e.command == "ERROR" else ""
        headers = "".join(
            f"<div class='h'><b>{html.escape(k)}</b>: {html.escape(v)}</div>" for k, v in e.headers
        )
        body = e.body_text
        body = try_pretty_json(body) or body
        rtt = f" · rtt {e.rtt_ms:.1f} ms" if e.rtt_ms is not None else ""
        warn = "".join(f"<div class='w'>⚠ {html.escape(w)}</div>" for w in e.warnings)
        rows.append(
            f'<div class="row {cls}"><div class="bubble{level}"><div class="cmd">{html.escape(e.command or e.kind)}</div>'
            f"{headers}{'<pre>' + html.escape(body) + '</pre>' if body else ''}{warn}"
            f'<div class="meta">#{e.id} · {html.escape(_fmt_ts(e.ts))}{rtt}</div></div></div>'
        )
    title = html.escape(f"STOMP session · {meta.get('profile', '')} · {meta.get('url', '')}")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font:14px -apple-system,Segoe UI,Roboto,sans-serif;background:#f4f5f7;margin:0;padding:24px;color:#1d1f23}}
h1{{font-size:16px}} .row{{display:flex;margin:8px 0}} .row.out{{justify-content:flex-end}}
.bubble{{max-width:70%;background:#fff;border-radius:12px;padding:10px 14px;box-shadow:0 1px 2px #0002}}
.out .bubble{{background:#dcebff}} .bubble.error{{background:#ffe1e1}}
.cmd{{font-weight:700;margin-bottom:4px}} .h{{font:12px ui-monospace,Menlo,monospace;color:#555}}
pre{{font:12px ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-all;margin:6px 0 0}}
.meta{{font-size:11px;color:#888;margin-top:6px}} .w{{color:#b35c00;font-size:12px}}
.sys{{text-align:center;color:#777;font-size:12px;margin:10px 0}} .sys.error{{color:#c0392b}} .sys.warn{{color:#b35c00}}
@media (prefers-color-scheme:dark){{body{{background:#1b1c1f;color:#e6e6e6}} .bubble{{background:#2b2d31;box-shadow:none}}
.out .bubble{{background:#1e3a5f}} .bubble.error{{background:#5a2329}} .h{{color:#aaa}}}}
</style></head><body><h1>{title}</h1>{''.join(rows)}</body></html>"""
