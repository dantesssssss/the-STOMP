"""Syntax highlighting for raw STOMP frames and JSON bodies."""

from __future__ import annotations

import re

from PySide6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat

from .theme import Colors

_ESCAPES = re.compile(r"\\u0000|\\x00|\\0(?![0-9])|\^@|\\[nrt]")
_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")
_JSON_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_JSON_KEY = re.compile(r'"(?:\\.|[^"\\])*"(?=\s*:)')
_JSON_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")
_JSON_LITERAL = re.compile(r"\b(?:true|false|null)\b")
_NULL_IN_BLOCK = re.compile(r"\\u0000|\\x00|\\0(?![0-9])|\^@|\x00")


def _fmt(color, bold=False, bg=None, italic=False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(color)
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if bg is not None:
        f.setBackground(bg)
    if italic:
        f.setFontItalic(True)
    return f


class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.refresh_colors()

    def refresh_colors(self) -> None:
        c = Colors()
        self.f_key = _fmt(c.key)
        self.f_string = _fmt(c.string)
        self.f_number = _fmt(c.number)
        self.f_literal = _fmt(c.purple, bold=True)
        self.f_placeholder = _fmt(c.purple, italic=True)
        self.f_escape = _fmt(c.warn, bold=True, bg=c.escape_bg)
        self.rehighlight()

    def highlight_json(self, text: str, offset: int = 0) -> None:
        for m in _JSON_STRING.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.f_string)
        for m in _JSON_KEY.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.f_key)
        for m in _JSON_NUMBER.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.f_number)
        for m in _JSON_LITERAL.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.f_literal)
        for m in _PLACEHOLDER.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.f_placeholder)

    def highlightBlock(self, text: str) -> None:
        self.highlight_json(text)


class FrameHighlighter(JsonHighlighter):
    """Block states: 0 = expecting command, 1 = headers, 2 = body."""

    def refresh_colors(self) -> None:
        c = Colors()
        self.f_command = _fmt(c.accent, bold=True)
        self.f_header_key = _fmt(c.key, bold=True)
        self.f_header_value = _fmt(c.text)
        super().refresh_colors()

    def highlightBlock(self, text: str) -> None:
        state = self.previousBlockState()
        if state < 0:
            state = 0
        if state == 0:
            if text.strip() == "":
                self.setCurrentBlockState(0)
            else:
                self.setFormat(0, len(text), self.f_command)
                self.setCurrentBlockState(1)
        elif state == 1:
            if text.strip("\r") == "":
                self.setCurrentBlockState(2)
            else:
                colon = text.find(":")
                if colon >= 0:
                    self.setFormat(0, colon, self.f_header_key)
                    self.setFormat(colon + 1, len(text) - colon - 1, self.f_header_value)
                for m in _PLACEHOLDER.finditer(text):
                    self.setFormat(m.start(), m.end() - m.start(), self.f_placeholder)
                self.setCurrentBlockState(1)
        else:
            self.highlight_json(text)
            self.setCurrentBlockState(2)
        if state != 0 or text.strip():
            if _NULL_IN_BLOCK.search(text):
                self.setCurrentBlockState(0)  # NULL ends the frame: next line starts a new one
        for m in _ESCAPES.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_escape)
