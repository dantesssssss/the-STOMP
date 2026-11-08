"""Two-way binding between widgets and the active Profile.

Several widgets can bind to the same attribute (e.g. the "NULL" chip in the composer and the
switch in the Connection inspector); they all stay in sync.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from ..core.models import Profile
from .controls import SegmentedControl
from .widgets import KeyValueTable


class ProfileHub(QObject):
    changed = Signal(str)  # attribute name
    switched = Signal(object)  # new Profile

    def __init__(self, profile: Profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._refreshers: dict[str, list] = {}

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        for refreshers in self._refreshers.values():
            for refresh in refreshers:
                refresh()
        self.switched.emit(profile)

    def get(self, attr: str):
        return getattr(self.profile, attr)

    def set(self, attr: str, value) -> None:
        if getattr(self.profile, attr) == value:
            return
        setattr(self.profile, attr, value)
        for refresh in self._refreshers.get(attr, []):
            refresh()
        self.changed.emit(attr)

    def _register(self, attr: str, refresh) -> None:
        self._refreshers.setdefault(attr, []).append(refresh)
        refresh()

    # binders -------------------------------------------------------------
    def bind_check(self, widget: QAbstractButton, attr: str):
        """Any checkable button: QCheckBox, ToggleSwitch, chip QToolButton."""

        def refresh():
            value = bool(self.get(attr))
            if widget.isChecked() != value:
                widget.blockSignals(True)
                widget.setChecked(value)
                widget.blockSignals(False)

        widget.toggled.connect(lambda checked: self.set(attr, bool(checked)))
        self._register(attr, refresh)
        return widget

    def bind_action(self, action: QAction, attr: str, on_value=True, off_value=False) -> QAction:
        action.setCheckable(True)

        def refresh():
            value = self.get(attr) == on_value
            if action.isChecked() != value:
                action.blockSignals(True)
                action.setChecked(value)
                action.blockSignals(False)

        action.toggled.connect(lambda checked: self.set(attr, on_value if checked else off_value))
        self._register(attr, refresh)
        return action

    def bind_segment(self, widget: SegmentedControl, attr: str, values: list) -> SegmentedControl:
        def refresh():
            value = self.get(attr)
            if value in values and widget.currentIndex() != values.index(value):
                widget.setCurrentIndex(values.index(value), emit=False)

        widget.currentChanged.connect(lambda i: self.set(attr, values[i]))
        self._register(attr, refresh)
        return widget

    def bind_line(self, widget: QLineEdit, attr: str) -> QLineEdit:
        def refresh():
            value = str(self.get(attr))
            if widget.text() != value:
                widget.blockSignals(True)
                widget.setText(value)
                widget.blockSignals(False)

        widget.textChanged.connect(lambda text: self.set(attr, text))
        self._register(attr, refresh)
        return widget

    def bind_spin(self, widget: QSpinBox | QDoubleSpinBox, attr: str):
        cast = float if isinstance(widget, QDoubleSpinBox) else int

        def refresh():
            value = cast(self.get(attr))
            if widget.value() != value:
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)

        widget.valueChanged.connect(lambda v: self.set(attr, cast(v)))
        self._register(attr, refresh)
        return widget

    def bind_combo(self, widget: QComboBox, attr: str) -> QComboBox:
        """Combo items must carry the stored value as userData."""

        def refresh():
            idx = widget.findData(self.get(attr))
            if idx >= 0 and widget.currentIndex() != idx:
                widget.blockSignals(True)
                widget.setCurrentIndex(idx)
                widget.blockSignals(False)

        widget.currentIndexChanged.connect(lambda i: self.set(attr, widget.itemData(i)))
        self._register(attr, refresh)
        return widget

    def bind_table(self, widget: KeyValueTable, attr: str) -> KeyValueTable:
        def refresh():
            value = [list(r) for r in self.get(attr)]
            if widget.rows() != value:
                widget.set_rows(value)

        widget.changed.connect(lambda: self.set(attr, widget.rows()))
        self._register(attr, refresh)
        return widget
