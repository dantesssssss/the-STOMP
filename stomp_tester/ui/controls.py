"""macOS-style controls: switch, segmented control, spinner, status pill, address bar, forms, empty states."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Property, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QSplitter,
    QSplitterHandle, QToolButton, QVBoxLayout, QWidget,
)

from .icons import pixmap, set_icon
from .theme import qcolor, repolish, tokens, ui_font


# --------------------------------------------------------------------------- small helpers
def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def separator(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("role", "vseparator" if vertical else "separator")
    if vertical:
        line.setFixedWidth(1)
    else:
        line.setFixedHeight(1)
    return line


def icon_button(name: str, tooltip: str = "", slot=None, checkable: bool = False, size: int = 28,
                icon_size: int = 16, **icon_kwargs) -> QToolButton:
    btn = QToolButton()
    set_icon(btn, name, icon_size, **icon_kwargs)
    btn.setToolTip(tooltip)
    btn.setCheckable(checkable)
    btn.setAutoRaise(True)
    btn.setFixedSize(size, size)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot is not None:
        btn.clicked.connect(slot)
    return btn


def text_button(text: str, tooltip: str = "", slot=None, icon_name: str | None = None) -> QToolButton:
    btn = QToolButton()
    btn.setProperty("kind", "text")
    btn.setText(text)
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        set_icon(btn, icon_name, 13, color="accent", hover="accent")
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if slot is not None:
        btn.clicked.connect(slot)
    return btn


def chip(text: str, tooltip: str = "") -> QToolButton:
    btn = QToolButton()
    btn.setProperty("kind", "chip")
    btn.setText(text)
    btn.setToolTip(tooltip)
    btn.setCheckable(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def push_button(text: str, kind: str | None = None, icon_name: str | None = None, slot=None,
                tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if kind:
        btn.setProperty("kind", kind)
    if icon_name:
        white = kind == "primary"
        set_icon(btn, icon_name, 14, color="#FFFFFF" if white else "label", hover="#FFFFFF" if white else "label")
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot is not None:
        btn.clicked.connect(slot)
    return btn


def set_kind(widget: QWidget, kind: str | None) -> None:
    if widget.property("kind") != kind:
        widget.setProperty("kind", kind)
        repolish(widget)


# --------------------------------------------------------------------------- toggle switch
class ToggleSwitch(QAbstractButton):
    """The macOS switch: accent-coloured track with a sliding white knob."""

    def __init__(self, parent=None, small: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self._track = QSize(28, 16) if small else QSize(36, 21)
        self._offset = 0.0
        self._from_click = False
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return self._track + QSize(6, 6)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def nextCheckState(self) -> None:
        self._from_click = True
        super().nextCheckState()
        self._from_click = False

    def checkStateSet(self) -> None:
        target = 1.0 if self.isChecked() else 0.0
        self._anim.stop()
        if self._from_click and self.isVisible():
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._offset = target
            self.update()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self._track.width(), self._track.height()
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        track = QRectF(x, y, w, h)
        off, on = QColor(t.switch_off), QColor(t.accent)
        k = self._offset
        color = QColor(
            round(off.red() + (on.red() - off.red()) * k),
            round(off.green() + (on.green() - off.green()) * k),
            round(off.blue() + (on.blue() - off.blue()) * k),
        )
        if not self.isEnabled():
            color.setAlphaF(0.45)
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.accent, 0.45), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), h / 2 + 2, h / 2 + 2)
        p.setPen(QPen(qcolor("#000000", 0.06 if not t.dark else 0.0), 1))
        p.setBrush(color)
        p.drawRoundedRect(track, h / 2, h / 2)
        d = h - 4
        kx = x + 2 + k * (w - 4 - d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor("#000000", 0.18))
        p.drawEllipse(QRectF(kx, y + 2.6, d, d))
        p.setBrush(QColor("#FFFFFF") if self.isEnabled() else QColor("#F0F0F0"))
        p.drawEllipse(QRectF(kx, y + 2, d, d))


# --------------------------------------------------------------------------- segmented control
class SegmentedControl(QWidget):
    currentChanged = Signal(int)

    def __init__(self, items: list, parent=None, compact: bool = False, expanding: bool = False):
        """items: list of text, or (icon_name, text) tuples; text may be '' for icon-only segments."""
        super().__init__(parent)
        self._items: list[tuple[str | None, str]] = [
            (i[0], i[1]) if isinstance(i, tuple) else (None, i) for i in items
        ]
        self._tooltips: list[str] = [""] * len(self._items)
        self._index = 0
        self._hover = -1
        self._height = 22 if compact else 26
        self._pad = 16 if compact else 24
        self._expanding = expanding
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if expanding else QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

    def setToolTips(self, tips: list[str]) -> None:
        self._tooltips = tips + [""] * (len(self._items) - len(tips))

    def _font(self, selected: bool) -> QFont:
        return ui_font(-1, QFont.Weight.DemiBold if selected else QFont.Weight.Medium)

    def _segment_width(self) -> int:
        fm = QFontMetrics(self._font(True))
        widest = 0
        for icon_name, text in self._items:
            w = fm.horizontalAdvance(text) if text else 0
            if icon_name:
                w += 15 + (6 if text else 0)
            widest = max(widest, w)
        return widest + self._pad

    def sizeHint(self) -> QSize:
        return QSize(self._segment_width() * len(self._items) + 4, self._height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint() if not self._expanding else QSize(60 * len(self._items), self._height)

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, index: int, emit: bool = True) -> None:
        if 0 <= index < len(self._items) and index != self._index:
            self._index = index
            self.update()
            if emit:
                self.currentChanged.emit(index)

    def _rects(self) -> list[QRectF]:
        n = len(self._items)
        width = (self.width() - 4) / n
        return [QRectF(2 + i * width, 0, width, self.height()) for i in range(n)]

    def _index_at(self, x: float) -> int:
        for i, r in enumerate(self._rects()):
            if r.left() <= x < r.right():
                return i
        return -1

    def mousePressEvent(self, event) -> None:
        i = self._index_at(event.position().x())
        if i >= 0:
            self.setCurrentIndex(i)

    def mouseMoveEvent(self, event) -> None:
        i = self._index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.setToolTip(self._tooltips[i] if 0 <= i < len(self._tooltips) else "")
            self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = -1
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.setCurrentIndex(max(0, self._index - 1))
        elif event.key() == Qt.Key.Key_Right:
            self.setCurrentIndex(min(len(self._items) - 1, self._index + 1))
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0, (self.height() - self._height) / 2, 0, 0)
        outer.setHeight(self._height)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.fill))
        p.drawRoundedRect(outer, 7, 7)
        rects = [QRectF(r.left(), outer.top(), r.width(), outer.height()) for r in self._rects()]
        for i, r in enumerate(rects):
            if i == self._hover and i != self._index:
                p.setBrush(QColor(t.fill_strong))
                p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 5.5, 5.5)
        if 0 <= self._index < len(rects):
            sel = rects[self._index].adjusted(2, 2, -2, -2)
            p.setBrush(qcolor("#000000", 0.10 if not t.dark else 0.35))
            p.drawRoundedRect(sel.adjusted(0, 0.8, 0, 0.8), 5.5, 5.5)
            p.setBrush(QColor(t.segment))
            p.drawRoundedRect(sel, 5.5, 5.5)
        p.setPen(QPen(qcolor(t.tertiary, 0.5), 1))
        for i in range(1, len(rects)):
            if i != self._index and i - 1 != self._index:
                x = rects[i].left()
                p.drawLine(QPointF(x, outer.top() + 6), QPointF(x, outer.bottom() - 6))
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.accent, 0.45), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(outer.adjusted(1, 1, -1, -1), 7, 7)
        for i, (icon_name, text) in enumerate(self._items):
            r = rects[i]
            selected = i == self._index
            color = t.label if selected else t.secondary
            if not self.isEnabled():
                color = t.tertiary
            font = self._font(selected)
            p.setFont(font)
            fm = QFontMetrics(font)
            content_w = (fm.horizontalAdvance(text) if text else 0) + ((15 + (6 if text else 0)) if icon_name else 0)
            x = r.center().x() - content_w / 2
            if icon_name:
                pm = pixmap(icon_name, color, 15)
                p.drawPixmap(QPointF(x, r.center().y() - 7.5), pm)
                x += 15 + (6 if text else 0)
            if text:
                p.setPen(QColor(color))
                p.drawText(QRectF(x, r.top(), content_w + 2, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


# --------------------------------------------------------------------------- spinner & status pill
class Spinner(QWidget):
    """Activity indicator: twelve fading spokes."""

    def __init__(self, size: int = 14, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._step = 0
        self._timer = QTimer(self, interval=80, timeout=self._tick)
        self.hide()

    def _tick(self) -> None:
        self._step = (self._step + 1) % 12
        self.update()

    def start(self) -> None:
        self.show()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.rect().center()
        p.translate(c.x() + 0.5, c.y() + 0.5)
        r = self.width() / 2
        base = QColor(tokens().secondary)
        for i in range(12):
            color = QColor(base)
            color.setAlphaF(0.15 + 0.85 * (((i - self._step) % 12) / 11.0))
            p.setPen(QPen(color, max(1.4, r / 4.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.save()
            p.rotate(i * 30)
            p.drawLine(QPointF(0, -r * 0.45), QPointF(0, -r * 0.92))
            p.restore()


class StatusPill(QWidget):
    """Capsule showing the connection state: ● Connected, ◌ Connecting…"""

    STYLES = {
        "connected": "green", "open": "orange", "connecting": "orange", "reconnecting": "orange",
        "disconnecting": "orange", "disconnected": "tertiary", "error": "red",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "disconnected"
        self._text = "Disconnected"
        self.spinner = Spinner(11, self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 2, 10, 2)
        layout.setSpacing(5)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.text = QLabel()
        self.text.setFont(ui_font(-2, QFont.Weight.DemiBold))
        layout.addWidget(self.spinner)
        layout.addWidget(self.dot)
        layout.addWidget(self.text)
        self.setFixedHeight(22)
        self.set_state("disconnected", "Disconnected")

    def set_state(self, state: str, text: str) -> None:
        self._state, self._text = state, text
        busy = state in ("connecting", "reconnecting", "disconnecting")
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.dot.setVisible(not busy)
        self.refresh()

    def refresh(self) -> None:
        t = tokens()
        color = getattr(t, self.STYLES.get(self._state, "tertiary"))
        text_color = {"green": t.green_text, "orange": t.orange_text, "red": t.red_text}.get(
            self.STYLES.get(self._state, ""), t.secondary)
        self.dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        self.text.setStyleSheet(f"color:{text_color};")
        self.text.setText(self._text)
        self.update()

    def paintEvent(self, _event) -> None:
        t = tokens()
        color = getattr(t, self.STYLES.get(self._state, "tertiary"))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(color, 0.15 if self._state != "disconnected" else 0.25))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


# --------------------------------------------------------------------------- address bar
class AddressBar(QFrame):
    """Safari-style field: [transport ▾] url …………… [status]."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AddressBar")
        self.transport = QToolButton()
        self.transport.setProperty("kind", "transport")
        self.transport.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.transport.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transport.setToolTip("Transport — Auto picks it from the URL scheme")
        self.transport_menu = QMenu(self.transport)
        self.transport.setMenu(self.transport_menu)
        self.edit = QLineEdit()
        self.edit.setProperty("frameless", "true")
        self.edit.setClearButtonEnabled(False)
        self.status = StatusPill()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 4, 3)
        layout.setSpacing(6)
        layout.addWidget(self.transport)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.status)
        self.setFixedHeight(32)
        self.setMinimumWidth(380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.edit.installEventFilter(self)
        self._focused = False

    def sizeHint(self) -> QSize:
        return QSize(720, 32)

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._focused = event.type() == QEvent.Type.FocusIn
            self.update()
        return False

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor(t.field))
        if self._focused:
            p.setPen(QPen(qcolor(t.accent, 0.75), 2))
        else:
            p.setPen(QPen(QColor(t.field_border), 1))
        p.drawRoundedRect(r, 8, 8)


# --------------------------------------------------------------------------- grouped forms
class FormSection(QWidget):
    """A titled, rounded group of rows — the System Settings look."""

    def __init__(self, title: str = "", footer: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        if title:
            outer.addWidget(label(title, "section-title"))
        self.group = QFrame()
        self.group.setObjectName("FormGroup")
        self.rows = QVBoxLayout(self.group)
        self.rows.setContentsMargins(0, 2, 0, 2)
        self.rows.setSpacing(0)
        outer.addWidget(self.group)
        if footer:
            foot = label(footer, "caption", wrap=True)
            foot.setContentsMargins(4, 0, 4, 0)
            outer.addWidget(foot)
        self._count = 0

    def _add_separator(self) -> None:
        if self._count:
            holder = QWidget()
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(14, 0, 0, 0)
            lay.addWidget(separator())
            self.rows.addWidget(holder)

    def add_row(self, text: str, widget: QWidget | None = None, caption: str = "", stretch: bool = False) -> QWidget:
        self._add_separator()
        row = QWidget()
        row.setObjectName("FormRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 7, 12, 7)
        h.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = label(text, wrap=True)
        title.setMinimumWidth(72)
        texts.addWidget(title)
        if caption:
            texts.addWidget(label(caption, "caption", wrap=True))
        h.addLayout(texts, 0 if stretch else 1)
        if widget is not None:
            if stretch:
                h.addWidget(widget, 1)
            else:
                h.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.setMinimumHeight(38)
        self.rows.addWidget(row)
        self._count += 1
        return row

    def add_switch(self, text: str, caption: str = "") -> ToggleSwitch:
        switch = ToggleSwitch()
        self.add_row(text, switch, caption)
        return switch

    def add_widget(self, widget: QWidget, margins: tuple[int, int, int, int] = (10, 8, 10, 8)) -> None:
        self._add_separator()
        holder = QWidget()
        holder.setObjectName("FormRow")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(*margins)
        lay.addWidget(widget)
        self.rows.addWidget(holder)
        self._count += 1


class Banner(QFrame):
    def __init__(self, level: str = "warn", parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setProperty("level", level)
        self.icon = QLabel()
        self.text = label("", wrap=True, selectable=True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.text, 1)
        self.set_level(level)

    def set_level(self, level: str) -> None:
        self.setProperty("level", level)
        t = tokens()
        color = t.red_text if level == "error" else t.orange_text
        self.icon.setPixmap(pixmap("warning", color, 15))
        self.text.setStyleSheet(f"color:{color};")
        repolish(self)


class EmptyState(QWidget):
    action = Signal()

    def __init__(self, icon_name: str, title: str, message: str, button: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._icon_name = icon_name
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label(title, "headline")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message = label(message, "secondary", wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fit_message()
        self.button = push_button(button, "primary")
        self.button.setVisible(bool(button))
        self.button.clicked.connect(self.action.emit)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        layout.addWidget(self.title, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.message, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        self.refresh()

    def _fit_message(self) -> None:
        # Wrapped labels with an alignment flag don't get height-for-width from the layout,
        # so size the label explicitly.
        width = max(160, min(380, self.width() - 48))
        self.message.setFixedWidth(width)
        self.message.setFixedHeight(self.message.heightForWidth(width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_message()

    def set_content(self, icon_name: str, title: str, message: str, button: str = "") -> None:
        self._icon_name = icon_name
        self.title.setText(title)
        self.message.setText(message)
        self._fit_message()
        self.button.setText(button)
        self.button.setVisible(bool(button))
        self.refresh()

    def refresh(self) -> None:
        self.icon.setPixmap(pixmap(self._icon_name, "tertiary", 46, stroke=1.4))


# --------------------------------------------------------------------------- split view
class _ThinHandle(QSplitterHandle):
    """Hairline divider; the rest of the drag area is painted in the neighbours' colours."""

    def paintEvent(self, _event) -> None:
        splitter = self.splitter()
        t = tokens()
        index = next((i for i in range(splitter.count()) if splitter.handle(i) is self), -1)
        before, after = splitter.fills.get(index, ("window", "window"))
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        horizontal = self.orientation() == Qt.Orientation.Horizontal
        mid = (self.width() if horizontal else self.height()) // 2
        if horizontal:
            p.fillRect(0, 0, mid, self.height(), QColor(getattr(t, before)))
            p.fillRect(mid, 0, self.width() - mid, self.height(), QColor(getattr(t, after)))
            p.fillRect(mid, 0, 1, self.height(), QColor(t.separator))
        else:
            p.fillRect(0, 0, self.width(), mid, QColor(getattr(t, before)))
            p.fillRect(0, mid, self.width(), self.height() - mid, QColor(getattr(t, after)))
            p.fillRect(0, mid, self.width(), 1, QColor(t.separator))


class ThinSplitter(QSplitter):
    """Split view with hairline dividers that still have a comfortable drag area."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.fills: dict[int, tuple[str, str]] = {}
        self.setHandleWidth(5)
        self.setChildrenCollapsible(False)

    def set_fill(self, handle_index: int, before: str, after: str) -> None:
        """Token names for the surfaces on each side of handle `handle_index` (1-based like QSplitter)."""
        self.fills[handle_index] = (before, after)

    def createHandle(self):
        return _ThinHandle(self.orientation(), self)
