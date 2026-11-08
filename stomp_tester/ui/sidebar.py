"""Source-list sidebar: Servers (profiles), Subscriptions and Templates."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QScrollArea,
    QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from ..core.models import Profile, Template
from ..core.session import CONNECTED, StompSession, Subscription
from .controls import icon_button, label, push_button
from .icons import icon, pixmap, set_icon
from .theme import qcolor, tokens, ui_font

OBJ = Qt.ItemDataRole.UserRole + 1
SUBTITLE = Qt.ItemDataRole.UserRole + 2
BADGE = Qt.ItemDataRole.UserRole + 3
DOT = Qt.ItemDataRole.UserRole + 4  # token name
ICON = Qt.ItemDataRole.UserRole + 5
SWITCH = Qt.ItemDataRole.UserRole + 6  # None | bool
ICON_COLOR = Qt.ItemDataRole.UserRole + 7


class SidebarDelegate(QStyledItemDelegate):
    switch_clicked = Signal(object)  # QModelIndex

    ROW1, ROW2 = 30, 44

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW2 if index.data(SUBTITLE) else self.ROW1)

    def _switch_rect(self, rect) -> QRectF:
        return QRectF(rect.right() - 14 - 28, rect.center().y() - 8, 28, 16)

    def editorEvent(self, event, model, option, index):
        if index.data(SWITCH) is None:
            return super().editorEvent(event, model, option, index)
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            if self._switch_rect(option.rect).adjusted(-4, -6, 4, 6).contains(event.position()):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self.switch_clicked.emit(index)
                return True
        return super().editorEvent(event, model, option, index)

    def paint(self, p: QPainter, option, index) -> None:
        t = tokens()
        view = option.widget
        r = QRectF(option.rect).adjusted(8, 1, -8, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focused = selected and view is not None and view.hasFocus()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if selected:
            p.setBrush(QColor(t.accent if focused else t.selection_inactive))
            p.drawRoundedRect(r, 6, 6)
        elif hover:
            p.setBrush(qcolor(t.label, 0.06))
            p.drawRoundedRect(r, 6, 6)

        text_color = "#FFFFFF" if focused else t.label
        sub_color = "rgba(255,255,255,0.8)" if focused else t.secondary
        x = r.left() + 8
        two_lines = bool(index.data(SUBTITLE))
        icon_name = index.data(ICON)
        if icon_name:
            icon_color = "#FFFFFF" if focused else (index.data(ICON_COLOR) or t.accent)
            pm = pixmap(icon_name, icon_color, 16)
            iy = r.top() + (9 if two_lines else (r.height() - 16) / 2)
            p.drawPixmap(QPointF(x, iy), pm)
            x += 24

        right = r.right() - 6
        if index.data(SWITCH) is not None:
            sw = self._switch_rect(option.rect)
            on = bool(index.data(SWITCH))
            p.setBrush(QColor("#FFFFFF" if (focused and on) else (t.accent if on else t.switch_off)))
            if focused and not on:
                p.setBrush(qcolor("#FFFFFF", 0.35))
            p.drawRoundedRect(sw, 8, 8)
            d = sw.height() - 4
            kx = sw.right() - 2 - d if on else sw.left() + 2
            p.setBrush(qcolor("#000000", 0.18))
            p.drawEllipse(QRectF(kx, sw.top() + 2.5, d, d))
            p.setBrush(QColor(t.accent) if (focused and on) else QColor("#FFFFFF"))
            p.drawEllipse(QRectF(kx, sw.top() + 2, d, d))
            right = sw.left() - 8
        badge = index.data(BADGE)
        if badge:
            font = ui_font(-2, QFont.Weight.DemiBold)
            fm = QFontMetrics(font)
            bw = max(20, fm.horizontalAdvance(badge) + 12)
            br = QRectF(right - bw, r.center().y() - 9, bw, 18)
            p.setBrush(qcolor("#FFFFFF", 0.25) if focused else QColor(t.fill_strong if t.dark else t.fill))
            p.drawRoundedRect(br, 9, 9)
            p.setPen(QColor("#FFFFFF" if focused else t.secondary))
            p.setFont(font)
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, badge)
            p.setPen(Qt.PenStyle.NoPen)
            right = br.left() - 6
        dot = index.data(DOT)
        if dot:
            p.setBrush(QColor("#FFFFFF" if focused else getattr(t, dot, dot)))
            p.drawEllipse(QRectF(right - 8, r.center().y() - 4, 8, 8))
            right -= 14

        width = max(10, right - x)
        title_font = ui_font(0, QFont.Weight.Medium if two_lines else QFont.Weight.Normal)
        p.setFont(title_font)
        fm = QFontMetrics(title_font)
        title = fm.elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight, int(width))
        p.setPen(QColor(text_color))
        if two_lines:
            p.drawText(QRectF(x, r.top() + 5, width, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
            sub_font = ui_font(-2)
            p.setFont(sub_font)
            sfm = QFontMetrics(sub_font)
            sub = sfm.elidedText(index.data(SUBTITLE), Qt.TextElideMode.ElideRight, int(width))
            p.setPen(QColor(sub_color) if not sub_color.startswith("rgba") else qcolor("#FFFFFF", 0.8))
            p.drawText(QRectF(x, r.top() + 5 + fm.height(), width, sfm.height() + 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)
        else:
            p.drawText(QRectF(x, r.top(), width, r.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.restore()


class SidebarList(QListWidget):
    """Height follows the content; the sidebar scroll area does the scrolling."""

    deselect_on_click = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.delegate = SidebarDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def fit(self) -> None:
        height = sum(self.sizeHintForRow(i) for i in range(self.count()))
        self.setFixedHeight(max(0, height) + 2)

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if self.deselect_on_click and item is not None and item.isSelected() and event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexFromItem(item)
            rect = self.visualRect(idx)
            if not self.delegate._switch_rect(rect).adjusted(-4, -6, 4, 6).contains(event.position()):
                self.clearSelection()
                self.setCurrentItem(None)
                return
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.viewport().update()


class SidebarSection(QWidget):
    def __init__(self, title: str, add_tooltip: str = "", on_add=None, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self.chevron = QToolButton()
        self.chevron.setAutoRaise(True)
        self.chevron.setFixedSize(16, 16)
        set_icon(self.chevron, "chevron.down", 11, color="tertiary")
        self.chevron.clicked.connect(self.toggle)
        self.title = label(title.upper(), "sidebar-header")
        self.title.mousePressEvent = lambda _e: self.toggle()
        head = QHBoxLayout()
        head.setContentsMargins(12, 0, 10, 0)
        head.setSpacing(2)
        head.addWidget(self.title)
        head.addWidget(self.chevron)
        head.addStretch(1)
        self.add_button = None
        if on_add is not None:
            self.add_button = icon_button("plus", add_tooltip, on_add, size=22, icon_size=14)
            head.addWidget(self.add_button)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 2, 0, 0)
        self.body_layout.setSpacing(4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(head)
        layout.addWidget(self.body)

    def add(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        set_icon(self.chevron, "chevron.right" if self._collapsed else "chevron.down", 11, color="tertiary")


class Sidebar(QWidget):
    profile_selected = Signal(int)
    profile_action = Signal(str, int)  # (new|duplicate|rename|delete|export|import, profile index or -1)
    subscription_filter = Signal(object)  # Subscription | None
    subscribe_requested = Signal(str)  # destination from the quick field
    subscription_edit = Signal(object)  # Subscription | None (new)
    template_load = Signal(object)
    template_send = Signal(object)
    template_action = Signal(str, object)  # rename | duplicate | delete
    template_save_current = Signal()
    history_requested = Signal()
    settings_requested = Signal()

    def __init__(self, session: StompSession, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.session = session
        self._profiles: list[Profile] = []
        self._templates: list[Template] = []

        inner = QWidget()
        inner.setObjectName("SidebarInner")
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 12, 0, 12)
        col.setSpacing(16)

        # servers
        self.servers = SidebarSection("Servers", "New server profile", lambda: self.profile_action.emit("new", -1))
        self.server_list = SidebarList()
        self.server_list.itemClicked.connect(self._server_clicked)
        self.server_list.customContextMenuRequested.connect(self._server_menu)
        self.servers.add(self.server_list)
        col.addWidget(self.servers)

        # subscriptions
        self.subs = SidebarSection("Subscriptions", "New subscription with options…", lambda: self.subscription_edit.emit(None))
        self.quick = QLineEdit()
        self.quick.setProperty("kind", "search")
        self.quick.setPlaceholderText("Subscribe to /topic/…")
        self.quick.setToolTip("Type a destination and press Return to subscribe")
        self.quick.addAction(icon("plus", "tertiary", 14), QLineEdit.ActionPosition.LeadingPosition)
        self.quick.returnPressed.connect(self._quick_subscribe)
        quick_holder = QWidget()
        qh = QHBoxLayout(quick_holder)
        qh.setContentsMargins(12, 0, 12, 2)
        qh.addWidget(self.quick)
        self.subs.add(quick_holder)
        self.sub_list = SidebarList()
        self.sub_list.deselect_on_click = True
        self.sub_list.delegate.switch_clicked.connect(self._sub_switch)
        self.sub_list.itemSelectionChanged.connect(self._sub_selection)
        self.sub_list.itemDoubleClicked.connect(lambda item: self.subscription_edit.emit(item.data(OBJ)))
        self.sub_list.customContextMenuRequested.connect(self._sub_menu)
        self.subs.add(self.sub_list)
        self.sub_empty = label("Nothing yet. Switched-on subscriptions are re-subscribed on every connect.", "caption", wrap=True)
        self.sub_empty.setContentsMargins(14, 0, 14, 0)
        self.subs.add(self.sub_empty)
        col.addWidget(self.subs)

        # templates
        self.tmpl = SidebarSection("Templates", "Save the composer as a template", self.template_save_current.emit)
        self.tmpl_list = SidebarList()
        self.tmpl_list.itemDoubleClicked.connect(lambda item: self.template_load.emit(item.data(OBJ)))
        self.tmpl_list.customContextMenuRequested.connect(self._tmpl_menu)
        self.tmpl_list.setToolTip("Double-click to load into the composer")
        self.tmpl.add(self.tmpl_list)
        self.tmpl_empty = label("Save frames you send often with ＋.", "caption", wrap=True)
        self.tmpl_empty.setContentsMargins(14, 0, 14, 0)
        self.tmpl.add(self.tmpl_empty)
        col.addWidget(self.tmpl)
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)

        footer = QWidget()
        footer.setObjectName("SidebarFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.setSpacing(2)
        self.btn_history = push_button("History", "sidebar", "clock", self.history_requested.emit, "Recorded sessions (⌘H)")
        self.btn_settings = push_button("Settings", "sidebar", "sliders", self.settings_requested.emit, "Settings (⌘,)")
        for b in (self.btn_history, self.btn_settings):
            set_icon(b, "clock" if b is self.btn_history else "sliders", 15, color="secondary", hover="label")
        fl.addWidget(self.btn_history)
        fl.addWidget(self.btn_settings)
        fl.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll, 1)
        layout.addWidget(footer)
        self.setMinimumWidth(210)
        session.subscriptions_changed.connect(self.refresh_subscriptions)
        session.state_changed.connect(lambda _s: self.refresh_subscriptions())

    # ------------------------------------------------------------------ servers
    def set_profiles(self, profiles: list[Profile], current: Profile, state: str) -> None:
        self._profiles = profiles
        self.server_list.blockSignals(True)
        self.server_list.clear()
        for i, p in enumerate(profiles):
            item = QListWidgetItem(p.name)
            item.setData(OBJ, i)
            item.setData(SUBTITLE, p.url or "no URL")
            item.setData(ICON, "server")
            if p is current and state != "disconnected":
                item.setData(DOT, "green" if state == CONNECTED else "orange")
            self.server_list.addItem(item)
            if p is current:
                item.setSelected(True)
                self.server_list.setCurrentItem(item)
        self.server_list.blockSignals(False)
        self.server_list.fit()

    def _server_clicked(self, item) -> None:
        self.profile_selected.emit(item.data(OBJ))

    def _server_menu(self, pos) -> None:
        item = self.server_list.itemAt(pos)
        index = item.data(OBJ) if item is not None else -1
        emit = self.profile_action.emit
        menu = QMenu(self)
        menu.addAction(icon("plus"), "New Server…", lambda: emit("new", -1))
        if item is not None:
            menu.addAction(icon("copy"), "Duplicate", lambda: emit("duplicate", index))
            menu.addAction(icon("pencil"), "Rename…", lambda: emit("rename", index))
            menu.addSeparator()
            menu.addAction(icon("trash"), "Delete…", lambda: emit("delete", index))
        menu.addSeparator()
        menu.addAction("Import Profiles…", lambda: emit("import", -1))
        menu.addAction("Export Profiles…", lambda: emit("export", -1))
        menu.exec(self.server_list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ subscriptions
    def refresh_subscriptions(self) -> None:
        selected = self.selected_subscription()
        connected = self.session.state == CONNECTED
        self.sub_list.blockSignals(True)
        self.sub_list.clear()
        for sub in self.session.subscriptions:
            spec = sub.spec
            item = QListWidgetItem(spec.destination)
            item.setData(OBJ, sub)
            if sub.active:
                state = "subscribed"
            elif spec.enabled:
                state = "subscribing…" if connected else "on connect"
            else:
                state = "off"
            item.setData(SUBTITLE, f"{spec.id} · {spec.ack}" + ("" if sub.active else f" · {state}"))
            item.setData(ICON, "antenna" if sub.active else "circle")
            item.setData(ICON_COLOR, tokens().green if sub.active else tokens().tertiary)
            item.setData(BADGE, str(sub.count) if sub.count else "")
            item.setData(SWITCH, spec.enabled)
            item.setToolTip(f"{spec.destination} — {state}\nid {spec.id}, ack {spec.ack}\n"
                            "Click to show only its messages · double-click to edit · switch to (un)subscribe")
            self.sub_list.addItem(item)
            if sub is selected:
                item.setSelected(True)
        self.sub_list.blockSignals(False)
        self.sub_list.fit()
        self.sub_empty.setVisible(not self.session.subscriptions)

    def selected_subscription(self) -> Subscription | None:
        items = self.sub_list.selectedItems()
        return items[0].data(OBJ) if items else None

    def _quick_subscribe(self) -> None:
        dest = self.quick.text().strip()
        if dest:
            self.subscribe_requested.emit(dest)
            self.quick.clear()

    def _sub_switch(self, index) -> None:
        sub = index.data(OBJ)
        if sub is not None:
            self.session.set_subscription_enabled(sub, not sub.spec.enabled)
            self.refresh_subscriptions()

    def _sub_selection(self) -> None:
        self.subscription_filter.emit(self.selected_subscription())

    def clear_subscription_selection(self) -> None:
        self.sub_list.blockSignals(True)
        self.sub_list.clearSelection()
        self.sub_list.blockSignals(False)

    def _sub_menu(self, pos) -> None:
        item = self.sub_list.itemAt(pos)
        if item is None:
            return
        sub: Subscription = item.data(OBJ)
        menu = QMenu(self)
        if self.session.state == CONNECTED:
            if sub.active:
                menu.addAction(icon("xmark"), "Unsubscribe", lambda: self.session.set_subscription_enabled(sub, False))
                menu.addAction(icon("arrow.clockwise"), "Send SUBSCRIBE Again", lambda: self.session.resubscribe(sub))
            else:
                menu.addAction(icon("antenna"), "Subscribe Now", lambda: self.session.set_subscription_enabled(sub, True))
        menu.addAction(icon("filter"), "Show Only Its Messages", lambda: (item.setSelected(True)))
        menu.addAction(icon("pencil"), "Edit…", lambda: self.subscription_edit.emit(sub))
        menu.addAction("Reset Counter", lambda: (setattr(sub, "count", 0), self.refresh_subscriptions()))
        menu.addSeparator()
        menu.addAction(icon("trash"), "Remove", lambda: self.session.remove_subscription(sub))
        menu.exec(self.sub_list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ templates
    def set_templates(self, templates: list[Template]) -> None:
        self._templates = templates
        self.tmpl_list.clear()
        for t in sorted(templates, key=lambda t: t.name.lower()):
            item = QListWidgetItem(t.name)
            item.setData(OBJ, t)
            item.setData(SUBTITLE, t.summary())
            item.setData(ICON, "code" if t.mode == "raw" else "doc")
            body = t.raw if t.mode == "raw" else t.body
            item.setToolTip((body[:600] or t.summary()) + "\n\nDouble-click to load into the composer")
            self.tmpl_list.addItem(item)
        self.tmpl_list.fit()
        self.tmpl_empty.setVisible(not templates)

    def _tmpl_menu(self, pos) -> None:
        item = self.tmpl_list.itemAt(pos)
        if item is None:
            return
        t: Template = item.data(OBJ)
        menu = QMenu(self)
        menu.addAction(icon("pencil"), "Load into Composer", lambda: self.template_load.emit(t))
        menu.addAction(icon("paperplane"), "Send Now", lambda: self.template_send.emit(t))
        menu.addSeparator()
        menu.addAction("Rename…", lambda: self.template_action.emit("rename", t))
        menu.addAction(icon("copy"), "Duplicate", lambda: self.template_action.emit("duplicate", t))
        menu.addSeparator()
        menu.addAction(icon("trash"), "Delete", lambda: self.template_action.emit("delete", t))
        menu.exec(self.tmpl_list.viewport().mapToGlobal(pos))

    def refresh_theme(self) -> None:
        for lst in (self.server_list, self.sub_list, self.tmpl_list):
            lst.viewport().update()
        self.refresh_subscriptions()
