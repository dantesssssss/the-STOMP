"""Line icons in the spirit of SF Symbols, drawn from tiny SVGs and tinted with theme colours."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import weakref
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import tokens

# 24×24 viewBox, stroked with the icon colour; "{c}" is replaced by the colour for fills.
PATHS: dict[str, str] = {
    "sidebar.left": '<rect x="3" y="4.5" width="18" height="15" rx="3"/><line x1="9.5" y1="4.5" x2="9.5" y2="19.5"/>'
                    '<line x1="5.6" y1="8.5" x2="7.4" y2="8.5"/><line x1="5.6" y1="11.5" x2="7.4" y2="11.5"/>',
    "sidebar.right": '<rect x="3" y="4.5" width="18" height="15" rx="3"/><line x1="14.5" y1="4.5" x2="14.5" y2="19.5"/>'
                     '<line x1="16.6" y1="8.5" x2="18.4" y2="8.5"/><line x1="16.6" y1="11.5" x2="18.4" y2="11.5"/>',
    "bolt": '<path d="M13.2 2.8 4.8 13.6h6.6l-1 7.6 8.4-10.8h-6.6z"/>',
    "power": '<path d="M7.6 6.6a7.2 7.2 0 1 0 8.8 0"/><line x1="12" y1="3" x2="12" y2="11"/>',
    "server": '<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/>'
              '<circle cx="7.5" cy="7.5" r="0.9" fill="{c}"/><circle cx="7.5" cy="16.5" r="0.9" fill="{c}"/>',
    "paperplane": '<path d="M21 3 10.6 13.4"/><path d="M21 3 14.4 21l-3.8-7.6L3 9.6z"/>',
    "eye": '<path d="M2.5 12c2.3-4.4 5.6-6.6 9.5-6.6s7.2 2.2 9.5 6.6c-2.3 4.4-5.6 6.6-9.5 6.6S4.8 16.4 2.5 12z"/>'
           '<circle cx="12" cy="12" r="2.8"/>',
    "bookmark": '<path d="M6.5 3.5h11v17l-5.5-4-5.5 4z"/>',
    "clock": '<circle cx="12" cy="12" r="8.8"/><polyline points="12 7 12 12 15.4 14"/>',
    "sliders": '<line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/>'
               '<circle cx="9" cy="7" r="2" fill="{c}"/><circle cx="15" cy="12" r="2" fill="{c}"/><circle cx="7.5" cy="17" r="2" fill="{c}"/>',
    "magnifier": '<circle cx="10.5" cy="10.5" r="6.3"/><line x1="15.2" y1="15.2" x2="20.5" y2="20.5"/>',
    "filter": '<line x1="4" y1="7" x2="20" y2="7"/><line x1="7" y1="12" x2="17" y2="12"/><line x1="10" y1="17" x2="14" y2="17"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "xmark": '<line x1="6.5" y1="6.5" x2="17.5" y2="17.5"/><line x1="17.5" y1="6.5" x2="6.5" y2="17.5"/>',
    "check": '<polyline points="5 12.5 10 17.5 19 7"/>',
    "message": '<path d="M5 4.5h14a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-7.5L7 20.5V17H5a2 2 0 0 1-2-2V6.5a2 2 0 0 1 2-2z"/>',
    "list": '<line x1="9" y1="6.5" x2="20" y2="6.5"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="17.5" x2="20" y2="17.5"/>'
            '<circle cx="4.8" cy="6.5" r="1" fill="{c}"/><circle cx="4.8" cy="12" r="1" fill="{c}"/><circle cx="4.8" cy="17.5" r="1" fill="{c}"/>',
    "trash": '<line x1="4" y1="6.5" x2="20" y2="6.5"/><path d="M9.5 6.5V4.5h5v2"/><path d="M6.2 6.5 7.1 20h9.8l.9-13.5"/>'
             '<line x1="10.2" y1="10" x2="10.2" y2="16.5"/><line x1="13.8" y1="10" x2="13.8" y2="16.5"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
    "export": '<path d="M8.5 9H7a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1.5"/>'
              '<line x1="12" y1="3" x2="12" y2="14"/><polyline points="8.6 6.4 12 3 15.4 6.4"/>',
    "repeat": '<polyline points="17 2.5 20.5 6 17 9.5"/><path d="M20.5 6H8a4.5 4.5 0 0 0-4.5 4.5V11"/>'
              '<polyline points="7 21.5 3.5 18 7 14.5"/><path d="M3.5 18H16a4.5 4.5 0 0 0 4.5-4.5V13"/>',
    "arrow.clockwise": '<path d="M19.8 12.5A7.8 7.8 0 1 1 17.5 6.4"/><polyline points="19 3 19 7.5 14.5 7.5"/>',
    "warning": '<path d="M12 3.8 21.2 19.8H2.8z"/><line x1="12" y1="10" x2="12" y2="14.2"/><circle cx="12" cy="17" r="0.8" fill="{c}"/>',
    "info": '<circle cx="12" cy="12" r="8.8"/><line x1="12" y1="11" x2="12" y2="16.5"/><circle cx="12" cy="7.8" r="0.9" fill="{c}"/>',
    "heart": '<path d="M12 19.8C5.6 15.5 3.2 12 3.2 8.8a4.4 4.4 0 0 1 8.8-1 4.4 4.4 0 0 1 8.8 1c0 3.2-2.4 6.7-8.8 11z"/>',
    "doc": '<path d="M6 3h8l4.5 4.5V21H6z"/><polyline points="14 3 14 7.5 18.5 7.5"/>',
    "code": '<polyline points="8 7 3 12 8 17"/><polyline points="16 7 21 12 16 17"/><line x1="13.6" y1="5" x2="10.4" y2="19"/>',
    "tray": '<path d="M3 13.5 6 5h12l3 8.5V19H3z"/><path d="M3 13.5h5l1.5 2.5h5l1.5-2.5h5"/>',
    "antenna": '<circle cx="12" cy="12" r="1.7" fill="{c}"/><path d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8"/><path d="M15.4 8.6a4.8 4.8 0 0 1 0 6.8"/>'
               '<path d="M5.6 5.6a9 9 0 0 0 0 12.8"/><path d="M18.4 5.6a9 9 0 0 1 0 12.8"/>',
    "chevron.down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron.up": '<polyline points="6 15 12 9 18 15"/>',
    "chevron.right": '<polyline points="9 6 15 12 9 18"/>',
    "chevron.updown": '<polyline points="7.5 9.5 12 5 16.5 9.5"/><polyline points="7.5 14.5 12 19 16.5 14.5"/>',
    "pencil": '<path d="M4 20l1.1-4.6L16.2 4.3a2.1 2.1 0 0 1 3 3L8.1 18.4z"/><line x1="14.2" y1="6.3" x2="17.2" y2="9.3"/>',
    "play": '<path d="M7.5 4.8 19 12 7.5 19.2z"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4.2l2 2.2H19a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "ellipsis": '<circle cx="6" cy="12" r="1.3" fill="{c}"/><circle cx="12" cy="12" r="1.3" fill="{c}"/><circle cx="18" cy="12" r="1.3" fill="{c}"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8.3 10.5V7.8a3.7 3.7 0 0 1 7.4 0v2.7"/>',
    "globe": '<circle cx="12" cy="12" r="8.8"/><ellipse cx="12" cy="12" rx="3.8" ry="8.8"/><line x1="3.2" y1="12" x2="20.8" y2="12"/>',
    "braces": '<path d="M9 4C7 4 6.5 5 6.5 6.5V9c0 1.5-1 2.5-2.5 3 1.5.5 2.5 1.5 2.5 3v2.5C6.5 19 7 20 9 20"/>'
              '<path d="M15 4c2 0 2.5 1 2.5 2.5V9c0 1.5 1 2.5 2.5 3-1.5.5-2.5 1.5-2.5 3v2.5c0 1.5-.5 2.5-2.5 2.5"/>',
    "arrow.down.line": '<line x1="12" y1="4" x2="12" y2="15.5"/><polyline points="7.5 11 12 15.5 16.5 11"/><line x1="5" y1="20" x2="19" y2="20"/>',
    "arrow.up": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="6.5 10.5 12 5 17.5 10.5"/>',
    "arrow.down": '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="6.5 13.5 12 19 17.5 13.5"/>',
    "dot": '<circle cx="12" cy="12" r="4.2" fill="{c}" stroke="none"/>',
    "circle": '<circle cx="12" cy="12" r="4.2"/>',
    "wand": '<line x1="4" y1="20" x2="15" y2="9"/><path d="M17 3v3M15.5 4.5h3M20 8v2M19 9h2M12 4v2M11 5h2"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><line x1="7" y1="14.5" x2="17" y2="14.5"/>'
                '<path d="M6 10h.01M9.3 10h.01M12.6 10h.01M15.9 10h.01M18 10h.01"/>',
}

_cache: dict[tuple, QPixmap] = {}
_tmpdir: Path | None = None
_registry: list[tuple[weakref.ref, str, dict]] = []


def _svg(name: str, color: str, stroke: float) -> bytes:
    body = PATHS[name].replace("{c}", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode("utf-8")


def _resolve(color: str | None, default: str) -> str:
    if not color:
        return default
    if color.startswith("#"):
        return color
    return getattr(tokens(), color, default)


def pixmap(name: str, color: str | None = None, size: int = 16, dpr: float = 2.0, stroke: float = 1.8) -> QPixmap:
    color = _resolve(color, tokens().secondary)
    key = (name, color, size, dpr, stroke)
    pm = _cache.get(key)
    if pm is None:
        renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
        image = QImage(int(size * dpr), int(size * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        pm = QPixmap.fromImage(image)
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return pm


def icon(name: str, color: str | None = None, size: int = 16, hover: str | None = None,
         checked: str | None = None, stroke: float = 1.8) -> QIcon:
    """Icon with proper normal / hover / disabled / checked tints."""
    t = tokens()
    normal = _resolve(color, t.secondary)
    ic = QIcon()
    ic.addPixmap(pixmap(name, normal, size, stroke=stroke), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, _resolve(hover, normal if color else t.label), size, stroke=stroke), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(pixmap(name, t.tertiary, size, stroke=stroke), QIcon.Mode.Disabled, QIcon.State.Off)
    ic.addPixmap(pixmap(name, t.tertiary, size, stroke=stroke), QIcon.Mode.Disabled, QIcon.State.On)
    on = _resolve(checked, t.accent)
    ic.addPixmap(pixmap(name, on, size, stroke=stroke), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, on, size, stroke=stroke), QIcon.Mode.Active, QIcon.State.On)
    ic.addPixmap(pixmap(name, "#FFFFFF", size, stroke=stroke), QIcon.Mode.Selected, QIcon.State.Off)
    return ic


def set_icon(widget, name: str, size: int = 16, **kwargs) -> None:
    """Set a themed icon on a button/action and re-tint it automatically when the theme changes."""
    widget.setIcon(icon(name, size=size, **kwargs))
    if hasattr(widget, "setIconSize"):
        widget.setIconSize(QSize(size, size))
    for i, (ref, _, _) in enumerate(_registry):
        if ref() is widget:
            _registry[i] = (ref, name, {"size": size, **kwargs})
            return
    _registry.append((weakref.ref(widget), name, {"size": size, **kwargs}))


def refresh_icons() -> None:
    alive = []
    for ref, name, kwargs in _registry:
        widget = ref()
        if widget is None:
            continue
        try:
            widget.setIcon(icon(name, **kwargs))
        except RuntimeError:  # C++ object already deleted
            continue
        alive.append((ref, name, kwargs))
    _registry[:] = alive


def icon_file(name: str, color: str, size: int = 16, stroke: float = 1.8) -> str:
    """PNG on disk for use in style sheets (url(...))."""
    global _tmpdir
    if _tmpdir is None:
        _tmpdir = Path(tempfile.mkdtemp(prefix="stomp-tester-icons-"))
        atexit.register(shutil.rmtree, _tmpdir, True)
    stem = f"{name}-{color.strip('#')}-{size}-{stroke}"
    path = _tmpdir / f"{stem}.png"
    if not path.exists():
        # 1x plus an @2x variant, which Qt picks up automatically on Retina screens.
        pixmap(name, color, size, dpr=1.0, stroke=stroke).toImage().save(str(path))
        pixmap(name, color, size, dpr=2.0, stroke=stroke).toImage().save(str(_tmpdir / f"{stem}@2x.png"))
    return path.as_posix()
