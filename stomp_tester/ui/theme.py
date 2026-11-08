"""Design system: Apple-style semantic colours (light/dark), type scale, global stylesheet, app icon."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QFontInfo, QGuiApplication, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget


@dataclass(frozen=True)
class Tokens:
    dark: bool
    accent: str
    accent_hover: str
    accent_pressed: str
    bg: str  # content: conversation, editors, lists
    window: str  # chrome: toolbar, inspector, composer
    sidebar: str
    group: str  # grouped-form background
    group_border: str
    field: str
    field_border: str
    control: str
    control_hover: str
    control_pressed: str
    control_border: str
    fill: str  # quaternary fill: segmented track, hover
    fill_strong: str
    segment: str  # selected segment
    switch_off: str
    separator: str
    label: str
    secondary: str
    tertiary: str
    selection_inactive: str
    alt_row: str
    menu: str
    green: str
    orange: str
    red: str
    purple: str
    teal: str
    green_text: str
    orange_text: str
    red_text: str
    bubble_out: str
    bubble_out_text: str
    bubble_in: str
    bubble_in_text: str
    bubble_error: str
    bubble_success: str
    bubble_warn: str
    code_key: str
    code_string: str
    code_number: str
    code_keyword: str
    escape_bg: str


LIGHT = Tokens(
    dark=False,
    accent="#007AFF", accent_hover="#1F8BFF", accent_pressed="#0062CC",
    bg="#FFFFFF", window="#F6F6F8", sidebar="#ECECF0", group="#FFFFFF", group_border="#E2E2E7",
    field="#FFFFFF", field_border="#D1D1D6",
    control="#FFFFFF", control_hover="#F8F8FA", control_pressed="#E9E9EE", control_border="#D1D1D6",
    fill="#E8E8ED", fill_strong="#DCDCE1", segment="#FFFFFF", switch_off="#E2E2E7",
    separator="#E1E1E6", label="#1D1D1F", secondary="#6E6E73", tertiary="#AEAEB2",
    selection_inactive="#DADADF", alt_row="#FAFAFC", menu="#FFFFFF",
    green="#34C759", orange="#FF9500", red="#FF3B30", purple="#AF52DE", teal="#30B0C7",
    green_text="#248A3D", orange_text="#C93400", red_text="#D70015",
    bubble_out="#007AFF", bubble_out_text="#FFFFFF", bubble_in="#E9E9EB", bubble_in_text="#1D1D1F",
    bubble_error="#FFE2DF", bubble_success="#DCF4E2", bubble_warn="#FFEFD6",
    code_key="#0B4F9E", code_string="#C41A16", code_number="#1C00CF", code_keyword="#AD3DA4",
    escape_bg="#FFE7A0",
)

DARK = Tokens(
    dark=True,
    accent="#0A84FF", accent_hover="#3396FF", accent_pressed="#0064D2",
    bg="#1E1E1F", window="#29292B", sidebar="#252527", group="#323234", group_border="#3C3C3F",
    field="#1E1E1F", field_border="#48484C",
    control="#3A3A3D", control_hover="#444447", control_pressed="#505054", control_border="#4A4A4E",
    fill="#38383B", fill_strong="#47474B", segment="#5B5B60", switch_off="#3A3A3D",
    separator="#38383B", label="#F2F2F4", secondary="#A1A1A6", tertiary="#6C6C70",
    selection_inactive="#46464A", alt_row="#222224", menu="#2E2E30",
    green="#30D158", orange="#FF9F0A", red="#FF453A", purple="#BF5AF2", teal="#40C8E0",
    green_text="#32D74B", orange_text="#FFB340", red_text="#FF6961",
    bubble_out="#0A84FF", bubble_out_text="#FFFFFF", bubble_in="#3A3A3C", bubble_in_text="#F2F2F4",
    bubble_error="#4E2422", bubble_success="#1E3F28", bubble_warn="#47371A",
    code_key="#6BDFFF", code_string="#FC6A5D", code_number="#D9C97C", code_keyword="#FC5FA3",
    escape_bg="#5C4600",
)

_current: Tokens = LIGHT
_pref = "system"


def tokens() -> Tokens:
    return _current


def rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


def qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def base_pt() -> float:
    app = QApplication.instance()
    size = app.font().pointSizeF() if app is not None else -1
    return size if size > 0 else 13.0


def ui_font(delta: float = 0, weight: QFont.Weight | None = None) -> QFont:
    app = QApplication.instance()
    font = QFont(app.font()) if app is not None else QFont()
    font.setPointSizeF(max(7.0, base_pt() + delta))
    if weight is not None:
        font.setWeight(weight)
    return font


_MONO_FAMILY: str | None = None
_MONO_CANDIDATES = ["SF Mono", "Menlo", "Monaco", "Cascadia Mono", "Consolas", "JetBrains Mono",
                    "DejaVu Sans Mono", "Liberation Mono", "Ubuntu Mono", "Courier New"]


def mono_font(size: int = 0) -> QFont:
    global _MONO_FAMILY
    if _MONO_FAMILY is None:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if QFontInfo(font).fixedPitch():
            _MONO_FAMILY = QFontInfo(font).family()
        else:
            families = set(QFontDatabase.families())
            _MONO_FAMILY = next((f for f in _MONO_CANDIDATES if f in families), "Courier")
    if size <= 0:
        size = round(base_pt() - 1)
    font = QFont(_MONO_FAMILY)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(max(6, size))
    return font


def resolve_dark(pref: str) -> bool:
    if pref == "dark":
        return True
    if pref == "light":
        return False
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "colorScheme"):
        return hints.colorScheme() == Qt.ColorScheme.Dark
    return False


def apply_theme(app: QApplication, pref: str, _ui_style: str | None = None) -> None:
    """Apply the light/dark design system to the whole application."""
    global _current, _pref
    _pref = pref
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # also tints the native title bar on macOS
        if pref == "dark":
            hints.setColorScheme(Qt.ColorScheme.Dark)
        elif pref == "light":
            hints.setColorScheme(Qt.ColorScheme.Light)
        else:
            hints.unsetColorScheme()
    _current = DARK if resolve_dark(pref) else LIGHT
    if app.style().name().lower() != "fusion":
        app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(_palette(_current))
    app.setStyleSheet(build_stylesheet(_current))
    from .icons import refresh_icons

    refresh_icons()


def current_pref() -> str:
    return _pref


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _palette(t: Tokens) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.window, QPalette.ColorRole.WindowText: t.label,
        QPalette.ColorRole.Base: t.bg, QPalette.ColorRole.AlternateBase: t.alt_row,
        QPalette.ColorRole.Text: t.label, QPalette.ColorRole.Button: t.control,
        QPalette.ColorRole.ButtonText: t.label, QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.PlaceholderText: t.tertiary,
        QPalette.ColorRole.ToolTipBase: t.menu, QPalette.ColorRole.ToolTipText: t.label,
        QPalette.ColorRole.Link: t.accent, QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Light: t.control_hover, QPalette.ColorRole.Midlight: t.fill,
        QPalette.ColorRole.Mid: t.separator, QPalette.ColorRole.Dark: t.fill_strong,
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.tertiary))
    return pal


def build_stylesheet(t: Tokens) -> str:
    from .icons import icon_file

    pt = base_pt()
    small, caption = pt - 1, pt - 2
    updown = icon_file("chevron.updown", t.secondary, 12)
    down = icon_file("chevron.down", t.secondary, 12)
    up_small = icon_file("chevron.up", t.secondary, 10)
    down_small = icon_file("chevron.down", t.secondary, 10)
    right = icon_file("chevron.right", t.secondary, 12)
    check_white = icon_file("check", "#FFFFFF", 12, stroke=2.6)
    check_label = icon_file("check", t.label, 12, stroke=2.2)
    accent_soft = rgba(t.accent, 0.14)
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {t.label}; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QToolTip {{ background: {t.menu}; color: {t.label}; border: 1px solid {t.separator}; border-radius: 6px; padding: 4px 8px; }}

/* ---- surfaces ---- */
#Sidebar, #SidebarInner {{ background: {t.sidebar}; }}
#SidebarFooter {{ background: {t.sidebar}; border-top: 1px solid {t.separator}; }}
#InspectorPane, #InspectorInner {{ background: {t.window}; }}
#ConversationPane, #ChatView, #EmptyState {{ background: {t.bg}; }}
#Composer {{ background: {t.window}; }}
#HeaderBar {{ background: {t.window}; border-bottom: 1px solid {t.separator}; }}
QToolBar#MainToolbar {{ background: {t.window}; border: none; border-bottom: 1px solid {t.separator}; padding: 7px 10px; spacing: 8px; }}
QToolBar#MainToolbar::separator {{ width: 1px; background: {t.separator}; margin: 4px 4px; }}
QStatusBar {{ background: {t.window}; border-top: 1px solid {t.separator}; color: {t.secondary}; font-size: {caption}pt; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.secondary}; font-size: {caption}pt; padding: 0 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---- typography roles ---- */
QLabel[role="title"] {{ font-size: {pt + 4}pt; font-weight: 700; }}
QLabel[role="headline"] {{ font-size: {pt + 1}pt; font-weight: 600; }}
QLabel[role="section-title"] {{ font-size: {pt}pt; font-weight: 600; color: {t.label}; padding-left: 2px; }}
QLabel[role="secondary"] {{ color: {t.secondary}; }}
QLabel[role="caption"] {{ color: {t.secondary}; font-size: {caption}pt; }}
QLabel[role="caption-strong"] {{ color: {t.secondary}; font-size: {small}pt; font-weight: 600; }}
QLabel[role="sidebar-header"] {{ color: {t.secondary}; font-size: {caption}pt; font-weight: 700; }}
QLabel[role="badge"] {{ background: {t.fill}; color: {t.secondary}; border-radius: 8px; padding: 1px 7px; font-size: {caption}pt; font-weight: 600; }}
QLabel[role="pill-sent"] {{ background: {accent_soft}; color: {t.accent}; border-radius: 9px; padding: 1px 8px; font-size: {caption}pt; font-weight: 600; }}
QLabel[role="pill-received"] {{ background: {rgba(t.green, 0.16)}; color: {t.green_text}; border-radius: 9px; padding: 1px 8px; font-size: {caption}pt; font-weight: 600; }}
QLabel[role="pill-event"] {{ background: {t.fill}; color: {t.secondary}; border-radius: 9px; padding: 1px 8px; font-size: {caption}pt; font-weight: 600; }}
QFrame[role="separator"] {{ background: {t.separator}; border: none; min-height: 1px; max-height: 1px; }}
QFrame[role="vseparator"] {{ background: {t.separator}; border: none; min-width: 1px; max-width: 1px; }}

/* ---- grouped forms (System Settings style) ---- */
QFrame#FormGroup {{ background: {t.group}; border: 1px solid {t.group_border}; border-radius: 10px; }}
QWidget#FormRow {{ background: transparent; }}
QFrame#Banner {{ border-radius: 8px; }}
QFrame#Banner[level="warn"] {{ background: {rgba(t.orange, 0.13)}; border: 1px solid {rgba(t.orange, 0.35)}; }}
QFrame#Banner[level="error"] {{ background: {rgba(t.red, 0.12)}; border: 1px solid {rgba(t.red, 0.35)}; }}
QFrame#Banner QLabel {{ font-size: {small}pt; }}
QFrame#Card {{ background: {t.bg}; border: 1px solid {t.field_border}; border-radius: 8px; }}

/* ---- push buttons ---- */
QPushButton {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: 6px; padding: 4px 12px; min-height: 18px; color: {t.label}; }}
QPushButton:hover {{ background: {t.control_hover}; }}
QPushButton:pressed {{ background: {t.control_pressed}; }}
QPushButton:disabled {{ color: {t.tertiary}; border-color: {t.separator}; }}
QPushButton:focus {{ border-color: {t.accent}; }}
QPushButton[kind="primary"] {{ background: {t.accent}; border: 1px solid {t.accent}; color: #FFFFFF; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[kind="primary"]:pressed {{ background: {t.accent_pressed}; border-color: {t.accent_pressed}; }}
QPushButton[kind="primary"]:disabled {{ background: {rgba(t.accent, 0.35)}; border-color: transparent; color: rgba(255, 255, 255, 0.75); }}
QPushButton[kind="destructive"] {{ color: {t.red_text}; font-weight: 600; }}
QPushButton[kind="large"] {{ padding: 5px 16px; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {t.accent}; padding: 2px 4px; }}
QPushButton[kind="link"]:hover {{ color: {t.accent_hover}; }}
QPushButton[kind="sidebar"] {{ background: transparent; border: none; color: {t.secondary}; text-align: left; padding: 5px 8px; border-radius: 6px; }}
QPushButton[kind="sidebar"]:hover {{ background: {t.fill}; color: {t.label}; }}
QPushButton::menu-indicator {{ image: url({down}); subcontrol-origin: padding; subcontrol-position: right center; right: 6px; width: 10px; height: 10px; }}
QPushButton[menu="true"] {{ padding-right: 24px; }}

/* ---- tool buttons: icon buttons & chips ---- */
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; color: {t.secondary}; }}
QToolButton:hover {{ background: {t.fill}; color: {t.label}; }}
QToolButton:pressed {{ background: {t.fill_strong}; }}
QToolButton:checked {{ background: {accent_soft}; color: {t.accent}; }}
QToolButton:disabled {{ color: {t.tertiary}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton[kind="text"] {{ color: {t.accent}; padding: 3px 6px; font-size: {small}pt; }}
QToolButton[kind="text"]:hover {{ background: {accent_soft}; }}
QToolButton[kind="chip"] {{ background: transparent; border: 1px solid {t.field_border}; border-radius: 11px; padding: 2px 8px; color: {t.secondary}; font-size: {small}pt; }}
QToolButton[kind="chip"]:hover {{ background: {t.fill}; color: {t.label}; }}
QToolButton[kind="chip"]:checked {{ background: {accent_soft}; border-color: {rgba(t.accent, 0.5)}; color: {t.accent}; font-weight: 600; }}
QToolButton[kind="transport"] {{ background: {t.fill}; border-radius: 5px; padding: 2px 8px; color: {t.label}; font-size: {caption}pt; font-weight: 700; }}
QToolButton[kind="transport"]:hover {{ background: {t.fill_strong}; }}

/* ---- text fields ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.field}; border: 1px solid {t.field_border}; border-radius: 6px; padding: 3px 7px;
    selection-background-color: {rgba(t.accent, 0.32)}; selection-color: {t.label};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {rgba(t.accent, 0.75)}; padding: 2px 6px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {t.tertiary}; background: {t.window}; }}
QLineEdit[frameless="true"], QLineEdit[frameless="true"]:focus {{ background: transparent; border: none; padding: 0 2px; }}
QLineEdit[kind="search"] {{ border-radius: 7px; padding-left: 4px; background: {t.fill}; border-color: transparent; }}
QLineEdit[kind="search"]:focus {{ background: {t.field}; border: 2px solid {rgba(t.accent, 0.75)}; padding: 2px 6px 2px 3px; }}
QPlainTextEdit, QTextEdit {{ padding: 4px 6px; }}
QPlainTextEdit[kind="code"], QPlainTextEdit[kind="code"]:focus {{ background: {t.bg}; border: none; border-radius: 0; padding: 6px 8px; }}
QComboBox {{ padding-right: 22px; }}
QComboBox:focus {{ padding-right: 21px; }}
QComboBox::drop-down {{ border: none; width: 20px; subcontrol-origin: padding; subcontrol-position: right center; }}
QComboBox::down-arrow {{ image: url({updown}); width: 10px; height: 12px; }}
QComboBox[kind="command"] {{ font-weight: 700; color: {t.accent}; }}
QComboBox QAbstractItemView {{ background: {t.menu}; border: 1px solid {t.separator}; padding: 4px; selection-background-color: {t.accent}; selection-color: #FFFFFF; outline: 0; }}
QComboBox QAbstractItemView::item {{ min-height: 22px; padding: 0 6px; border-radius: 4px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 16px; border: none; background: transparent; margin-top: 2px; margin-right: 2px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 16px; border: none; background: transparent; margin-bottom: 2px; margin-right: 2px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {t.fill}; border-radius: 3px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up_small}); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down_small}); width: 9px; height: 9px; }}

/* ---- check boxes ---- */
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {t.control_border}; background: {t.field}; }}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({check_white}); }}
QCheckBox::indicator:disabled {{ background: {t.fill}; border-color: {t.separator}; }}

/* ---- item views ---- */
QAbstractItemView {{ background: {t.bg}; border: none; selection-background-color: {t.accent}; selection-color: #FFFFFF; alternate-background-color: {t.alt_row}; }}
QTableView {{ gridline-color: transparent; }}
QTableView::item {{ padding: 0 6px; border: none; }}
QTableView::item:selected:active {{ background: {t.accent}; color: #FFFFFF; }}
QTableView::item:selected:!active {{ background: {t.selection_inactive}; color: {t.label}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {t.bg}; color: {t.secondary}; border: none; border-bottom: 1px solid {t.separator}; padding: 4px 6px; font-size: {caption}pt; font-weight: 600; }}
QHeaderView::section:horizontal {{ border-right: 1px solid {t.separator}; }}
QHeaderView::section:horizontal:last {{ border-right: none; }}
QTableCornerButton::section {{ background: {t.bg}; border: none; }}
QTableWidget[kind="kv"] {{ background: {t.bg}; border: 1px solid {t.field_border}; border-radius: 7px; }}
QTableWidget[kind="kv"] QHeaderView::section {{ background: {t.bg}; border-top-left-radius: 7px; }}
QListView {{ outline: 0; }}

/* ---- scroll bars (overlay-like) ---- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {rgba(t.label, 0.22)}; border-radius: 3px; min-height: 32px; margin: 3px 3px 3px 2px; }}
QScrollBar::handle:horizontal {{ background: {rgba(t.label, 0.22)}; border-radius: 3px; min-width: 32px; margin: 2px 3px 3px 3px; }}
QScrollBar::handle:hover {{ background: {rgba(t.label, 0.38)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- menus ---- */
QMenu {{ background: {t.menu}; border: 1px solid {t.separator}; border-radius: 9px; padding: 5px; }}
QMenu::item {{ padding: 4px 24px 4px 24px; border-radius: 5px; color: {t.label}; }}
QMenu::item:selected {{ background: {t.accent}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: {t.tertiary}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {t.separator}; margin: 4px 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; left: 7px; }}
QMenu::indicator:checked {{ image: url({check_label}); }}
QMenu::right-arrow {{ image: url({right}); width: 10px; height: 10px; right: 8px; }}
QMenu::icon {{ left: 6px; }}
QMenuBar {{ background: {t.window}; border-bottom: 1px solid {t.separator}; }}
QMenuBar::item {{ padding: 4px 9px; border-radius: 5px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.fill}; }}

/* ---- misc ---- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {t.secondary}; padding: 5px 12px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.label}; border-bottom-color: {t.accent}; }}
QDialogButtonBox QPushButton {{ min-width: 72px; }}
QMessageBox QLabel {{ color: {t.label}; }}
"""


# --------------------------------------------------------------------------- semantic QColors
class Colors:
    """QColor view of the active tokens (used by highlighters, models and painters)."""

    def __init__(self) -> None:
        t = tokens()
        self.t = t
        self.dark = t.dark
        self.text = QColor(t.label)
        self.dim = QColor(t.secondary)
        self.tertiary = QColor(t.tertiary)
        self.accent = QColor(t.accent)
        self.success = QColor(t.green_text)
        self.warn = QColor(t.orange_text)
        self.error = QColor(t.red_text)
        self.purple = QColor(t.purple)
        self.string = QColor(t.code_string)
        self.number = QColor(t.code_number)
        self.key = QColor(t.code_key)
        self.keyword = QColor(t.code_keyword)
        self.escape_bg = QColor(t.escape_bg)

    def level(self, level: str) -> QColor:
        return {"error": self.error, "warn": self.warn, "success": self.success}.get(level, self.dim)


def is_dark() -> bool:
    return tokens().dark


# --------------------------------------------------------------------------- app icon
def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(render_icon(size))
    return icon


def render_icon(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256.0
    rect = QRectF(16 * s, 16 * s, 224 * s, 224 * s)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor("#3B82F6"))
    grad.setColorAt(1, QColor("#7C3AED"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(rect, 52 * s, 52 * s)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawRoundedRect(QRectF(46 * s, 62 * s, 118 * s, 56 * s), 22 * s, 22 * s)
    tail = QPainterPath()
    tail.moveTo(70 * s, 112 * s)
    tail.lineTo(60 * s, 136 * s)
    tail.lineTo(96 * s, 114 * s)
    p.drawPath(tail)
    p.setBrush(QColor(255, 255, 255, 150))
    p.drawRoundedRect(QRectF(92 * s, 140 * s, 118 * s, 56 * s), 22 * s, 22 * s)
    tail2 = QPainterPath()
    tail2.moveTo(186 * s, 190 * s)
    tail2.lineTo(196 * s, 214 * s)
    tail2.lineTo(160 * s, 192 * s)
    p.drawPath(tail2)
    pen = QPen(QColor("#4F46E5"), max(1.0, 9 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for x in (76, 105, 134):
        p.drawPoint(int(x * s), int(90 * s))
    p.end()
    return pm
