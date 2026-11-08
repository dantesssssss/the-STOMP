"""Messages-style rendering: sent frames are blue bubbles on the right, received ones grey on the left,
connection events are quiet centred captions."""

from __future__ import annotations

import html
import re
from collections import OrderedDict

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QColor, QPainter, QPalette, QPen, QTextDocument, QTextOption
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from ..core.models import LogEntry
from ..core.textutil import human_size, try_pretty_json, visualize
from .log_model import ENTRY_ROLE, format_ts
from .theme import base_pt, mono_font, qcolor, tokens

SIDE = 18  # distance of bubbles from the view edge
PAD_X, PAD_Y = 13, 9
RADIUS = 17
GAP_TOP, GAP_BOTTOM = 3, 7
CAPTION_GAP = 3
SKIP_HEADERS = {"destination", "content-length"}


def _pre(text: str) -> str:
    """Escape text for Qt rich text, keeping line breaks and indentation."""
    lines = []
    for line in text.split("\n"):
        line = html.escape(line.replace("\t", "    "))
        line = re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group(0)), line)
        if line.startswith(" "):
            line = "&nbsp;" + line[1:]
        lines.append(line)
    return "<br>".join(lines)


class _Layout:
    __slots__ = ("bubble", "caption", "bubble_w", "bubble_h", "caption_h", "kind")

    def __init__(self, bubble, caption, bubble_w, bubble_h, caption_h, kind):
        self.bubble, self.caption = bubble, caption
        self.bubble_w, self.bubble_h, self.caption_h = bubble_w, bubble_h, caption_h
        self.kind = kind  # bubble | compact | centered

    @property
    def height(self) -> int:
        h = GAP_TOP + self.bubble_h + GAP_BOTTOM
        if self.caption is not None:
            h += CAPTION_GAP + self.caption_h
        return int(h + 0.999)


class ChatDelegate(QStyledItemDelegate):
    def __init__(self, view: QListView):
        super().__init__(view)
        self.view = view
        self.preview_lines = 14
        self.show_headers = True
        self.pretty_json = True
        self.ts_format = "time_ms"
        self.start_ts: float | None = None
        self.mono_size = 0
        self._layouts: OrderedDict[tuple, _Layout] = OrderedDict()
        self._heights: dict[tuple, int] = {}

    def invalidate(self) -> None:
        self._layouts.clear()
        self._heights.clear()

    def entry_changed(self, proxy_index) -> None:
        self.sizeHintChanged.emit(proxy_index)

    # ------------------------------------------------------------------ styling
    def _style(self, e: LogEntry) -> tuple[str, str, str, str]:
        """(background, text, secondary text, title colour) for a frame bubble."""
        t = tokens()
        if e.command == "ERROR" or (e.kind == "frame" and e.level == "error"):
            return t.bubble_error, t.label, t.secondary, t.red_text
        if e.direction == "out":
            return t.bubble_out, t.bubble_out_text, "rgba(255,255,255,0.78)", t.bubble_out_text
        if e.warnings or e.level == "warn":
            return t.bubble_warn, t.label, t.secondary, t.orange_text
        if e.command == "CONNECTED":
            return t.bubble_success, t.label, t.secondary, t.green_text
        return t.bubble_in, t.bubble_in_text, t.secondary, t.label

    def _doc(self, html_text: str, width: float, center: bool = False, font_delta: float = 0) -> QTextDocument:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        font = self.view.font()
        font.setPointSizeF(max(7.0, base_pt() + font_delta))
        doc.setDefaultFont(font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        if center:
            option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        doc.setDefaultTextOption(option)
        doc.setHtml(html_text)
        doc.setTextWidth(width)
        if not center:
            doc.setTextWidth(min(width, doc.idealWidth() + 1))
        return doc

    def _bubble_html(self, e: LogEntry, compact: bool) -> str:
        _bg, _text, sub, title = self._style(e)
        esc = html.escape
        mf = mono_font(self.mono_size)
        mono = f'font-family:"{mf.family()}"; font-size:{mf.pointSize()}pt;'
        mono_small = f'font-family:"{mf.family()}"; font-size:{max(7, mf.pointSize() - 1)}pt;'
        command = e.command or ("Raw data" if e.kind == "raw" else e.kind.title())
        target = e.target()
        if compact:  # RECEIPT: one quiet line
            note = f" · {esc(e.note)}" if e.note else ""
            return (f"<span style='color:{title}; font-weight:600'>✓ Receipt</span>"
                    f"<span style='color:{sub}'> {esc(e.header('receipt-id'))}{note}</span>")
        parts = [
            f"<div><span style='color:{title}; font-weight:700'>{esc(command)}</span>"
            + (f"&nbsp;&nbsp;<span style='color:{sub}'>{esc(target)}</span>" if target else "")
            + "</div>"
        ]
        if e.command == "CONNECTED" and e.note:
            parts.append(f"<div style='color:{sub}'>{esc(e.note)}</div>")
        if self.show_headers and e.kind == "frame":
            shown = [(k, v) for k, v in e.headers if k not in SKIP_HEADERS]
            if shown:
                lines = [
                    f"<span style='color:{sub}'>{esc(k)}:</span>{esc(v if len(v) < 240 else v[:240] + '…')}"
                    for k, v in shown[:8]
                ]
                if len(shown) > 8:
                    lines.append(f"<span style='color:{sub}'>+{len(shown) - 8} more</span>")
                parts.append(f"<div style='{mono_small} margin-top:3px'>{'<br>'.join(lines)}</div>")
        body = visualize(e.raw, 6000) if e.kind == "raw" else e.body_text
        if self.pretty_json and body and e.kind != "raw":
            body = try_pretty_json(body) or body
        if body:
            lines = body.splitlines() or [body]
            limit = max(1, self.preview_lines)
            clipped = len(lines) > limit or len(body) > 6000
            parts.append(f"<div style='{mono} margin-top:5px'>{_pre(chr(10).join(lines[:limit])[:6000])}</div>")
            if clipped:
                parts.append(f"<div style='color:{sub}; margin-top:2px'>… {len(lines)} lines — see Inspector</div>")
        return "".join(parts)

    def _caption_html(self, e: LogEntry) -> str:
        t = tokens()
        bits = [format_ts(e.ts, self.ts_format, self.start_ts)]
        if e.size:
            bits.append(human_size(e.size))
        if e.rtt_ms is not None:
            bits.append(f"<span style='color:{t.green_text}'>✓ Receipt {e.rtt_ms:.1f} ms</span>")
        elif e.receipt:
            bits.append("Awaiting receipt…")
        if e.note and e.command not in ("CONNECTED", "RECEIPT"):
            bits.append(html.escape(e.note))
        if e.binary:
            bits.append("binary")
        caption = " · ".join(bits)
        for w in e.warnings:
            caption += f"<br><span style='color:{t.orange_text}'>⚠ {html.escape(w)}</span>"
        return caption

    def _event_html(self, e: LogEntry) -> str:
        t = tokens()
        ts = format_ts(e.ts, self.ts_format, self.start_ts)
        if e.kind == "heartbeat":
            return f"<span style='color:{t.tertiary}'>♥ {html.escape(e.text or 'heart-beat')} · {ts}</span>"
        color = {"error": t.red_text, "warn": t.orange_text, "success": t.green_text}.get(e.level, t.secondary)
        weight = "600" if e.level in ("error", "warn", "success") else "400"
        extra = f" · {len(e.headers)} headers" if e.headers else ""
        text = html.escape(e.text).replace("\n", "<br>")
        return (f"<span style='color:{color}; font-weight:{weight}'>{text}</span>"
                f"<br><span style='color:{t.tertiary}'>{ts}{extra}</span>")

    # ------------------------------------------------------------------ layout
    def _layout(self, e: LogEntry, width: int) -> _Layout:
        key = (e.id, id(e), e.rev, width)
        layout = self._layouts.get(key)
        if layout is not None:
            self._layouts.move_to_end(key)
            return layout
        if e.direction == "sys" or e.kind == "heartbeat":
            delta = -2.5 if e.kind == "heartbeat" else -1.5
            doc = self._doc(self._event_html(e), max(160, width * 0.8), center=True, font_delta=delta)
            doc.setTextWidth(min(doc.textWidth(), doc.idealWidth() + 1))
            layout = _Layout(doc, None, doc.textWidth(), doc.size().height(), 0, "centered")
        else:
            compact = e.command == "RECEIPT" and not e.warnings
            max_w = max(180, min(width * 0.74, 980)) - 2 * PAD_X
            doc = self._doc(self._bubble_html(e, compact), max_w, font_delta=-1.5 if compact else 0)
            pad_y = 5 if compact else PAD_Y
            bubble_w = doc.textWidth() + 2 * PAD_X
            bubble_h = doc.size().height() + 2 * pad_y
            caption = None
            cap_h = 0.0
            if not compact:
                caption = self._doc(self._caption_html(e), max(160, width * 0.74), font_delta=-2.5)
                cap_h = caption.size().height()
            layout = _Layout(doc, caption, bubble_w, bubble_h, cap_h, "compact" if compact else "bubble")
        self._layouts[key] = layout
        if len(self._layouts) > 500:
            self._layouts.popitem(last=False)
        return layout

    def _viewport_width(self) -> int:
        return max(240, self.view.viewport().width())

    def sizeHint(self, option, index) -> QSize:
        e: LogEntry = index.data(ENTRY_ROLE)
        width = self._viewport_width()
        key = (e.id, id(e), e.rev, width)
        height = self._heights.get(key)
        if height is None:
            height = self._layout(e, width).height
            if len(self._heights) > 60000:
                self._heights.clear()
            self._heights[key] = height
        return QSize(width, height)

    # ------------------------------------------------------------------ painting
    def paint(self, painter: QPainter, option, index) -> None:
        e: LogEntry = index.data(ENTRY_ROLE)
        width = self._viewport_width()
        lay = self._layout(e, width)
        t = tokens()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if lay.kind == "centered":
            x = rect.left() + (rect.width() - lay.bubble_w) / 2
            y = rect.top() + GAP_TOP
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(t.accent, 0.13))
                painter.drawRoundedRect(QRectF(x - 10, y - 3, lay.bubble_w + 20, lay.bubble_h + 6), 8, 8)
            self._draw_doc(painter, lay.bubble, x, y, t.secondary)
            painter.restore()
            return

        bg, text, _sub, _title = self._style(e)
        outgoing = e.direction == "out"
        x = rect.right() - SIDE - lay.bubble_w if outgoing else rect.left() + SIDE
        y = rect.top() + GAP_TOP
        bubble = QRectF(x, y, lay.bubble_w, lay.bubble_h)
        radius = min(RADIUS, lay.bubble_h / 2) if lay.kind == "compact" else RADIUS
        if selected:
            painter.setPen(QPen(qcolor(t.accent, 0.55), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bubble.adjusted(-3.5, -3.5, 3.5, 3.5), radius + 3.5, radius + 3.5)
        painter.setPen(QPen(QColor(t.orange), 2) if (e.warnings and outgoing) else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(bubble, radius, radius)
        pad_y = 5 if lay.kind == "compact" else PAD_Y
        self._draw_doc(painter, lay.bubble, x + PAD_X, y + pad_y, text)
        if lay.caption is not None:
            cap_w = min(lay.caption.textWidth(), lay.caption.idealWidth() + 1)
            cx = rect.right() - SIDE - cap_w - 6 if outgoing else rect.left() + SIDE + 6
            self._draw_doc(painter, lay.caption, cx, y + lay.bubble_h + CAPTION_GAP, t.secondary)
        painter.restore()

    @staticmethod
    def _draw_doc(painter: QPainter, doc: QTextDocument, x: float, y: float, color: str) -> None:
        painter.save()
        painter.translate(x, y)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor(color))
        doc.documentLayout().draw(painter, ctx)
        painter.restore()


class ChatView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatView")
        self.delegate = ChatDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setUniformItemSizes(False)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(300)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSpacing(0)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContentsMargins(0, 6, 0, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scheduleDelayedItemsLayout()
