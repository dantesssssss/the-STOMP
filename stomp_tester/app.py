"""Application bootstrap."""

from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from . import APP_NAME, __version__
from .core.storage import Store
from .ui.main_window import MainWindow
from .ui.theme import app_icon, apply_theme


class _RoundedMenus(QObject):
    """Give every QMenu a translucent window so its rounded corners render cleanly."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Polish and isinstance(obj, QMenu) and not obj.property("_rounded"):
            obj.setProperty("_rounded", True)
            obj.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return False


def main(argv: list[str] | None = None) -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setWindowIcon(app_icon())
    menus = _RoundedMenus(app)
    app.installEventFilter(menus)

    try:
        store = Store()
    except OSError as exc:
        QMessageBox.critical(None, APP_NAME, f"Cannot create the data folder:\n{exc}")
        return 1
    settings = store.load_settings()
    apply_theme(app, settings.theme)

    window = MainWindow(store, settings)

    def excepthook(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        try:
            window.session.log_event(f"Internal error: {exc_type.__name__}: {exc}", level="error")
        except Exception:
            pass

    sys.excepthook = excepthook
    window.show()
    return app.exec()
