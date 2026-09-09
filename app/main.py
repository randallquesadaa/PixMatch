"""Application entry point."""
from __future__ import annotations

import os
import sys

from app import APP_NAME, ORG_NAME, __version__
from app.utils.logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if any(a in ("-v", "--version") for a in argv[1:]):
        print(f"{APP_NAME} {__version__}")
        return 0
    if any(a in ("-h", "--help") for a in argv[1:]):
        print(
            f"{APP_NAME} {__version__}\n\n"
            f"Uso: {os.path.basename(argv[0])} [CARPETA]\n\n"
            "  CARPETA        carpeta a analizar al arrancar (opcional)\n"
            "  -v, --version  muestra la versión\n"
            "  -h, --help     muestra esta ayuda\n"
        )
        return 0

    configure_logging()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from app.config import AppConfig
    from app.i18n import set_language
    from app.ui.main_window import MainWindow
    from app.ui.theme import apply_theme

    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    _set_window_icon(app)

    config = AppConfig.load()
    set_language(config.language)
    apply_theme(app, config.theme)

    window = MainWindow(config)
    window.show()

    for arg in argv[1:]:
        if os.path.isdir(arg):
            window._set_folder(os.path.abspath(arg))
            break

    return app.exec()


def _set_window_icon(app) -> None:
    from PySide6.QtGui import QIcon

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), "packaging", "resources", "icon.png"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "resources", "icon.png"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            app.setWindowIcon(QIcon(path))
            return


if __name__ == "__main__":
    raise SystemExit(main())
