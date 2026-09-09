"""Light and dark themes.

Base colours are driven through a real :class:`QPalette` (not a
``QWidget { color: ... }`` stylesheet rule, which is sticky and does not clear
cleanly when switching themes). The stylesheet carries structure and
interaction states (hover / pressed / disabled) and resolves its colours from
the palette so it works unchanged in both themes.

Contrast: body text, muted ("hint") text and disabled text are all chosen to
clear WCAG AA (>= 4.5:1 for body, >= 3:1 for the large/secondary cases)
against their background. Muted text uses a dedicated colour, *not* the border
colour, so borders can stay subtle without making captions unreadable.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_ACCENT = "#2563eb"
_ACCENT_HOVER = "#1d4ed8"
_DANGER = "#dc2626"
_DANGER_HOVER = "#b91c1c"

_QSS = """
* { font-size: 13px; }

QToolBar { border: 0; padding: 6px; spacing: 4px; background: palette(window); }
QToolButton {
    padding: 6px 10px; border-radius: 6px; color: palette(window-text);
    border: 1px solid transparent;
}
QToolButton:hover { background: palette(alternate-base); border-color: palette(mid); }
QToolButton:pressed { background: palette(mid); }
QToolButton:disabled { color: palette(placeholder-text); background: transparent; }

QMenuBar { background: palette(window); }
QMenuBar::item { padding: 4px 10px; }
QMenuBar::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenu { border: 1px solid palette(mid); }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenu::item:disabled { color: palette(placeholder-text); }

QPushButton {
    padding: 7px 14px; border-radius: 6px;
    border: 1px solid palette(mid);
    background: palette(button); color: palette(button-text);
}
QPushButton:hover { background: palette(alternate-base); border-color: %(accent)s; }
QPushButton:pressed { background: palette(mid); }
QPushButton:focus { border-color: %(accent)s; }
QPushButton:disabled {
    color: palette(placeholder-text);
    background: palette(window);
    border-color: palette(mid);
}
QPushButton#primary { background: %(accent)s; color: #ffffff; border: 0; font-weight: 600; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:pressed { background: #1e40af; }
QPushButton#primary:disabled { background: palette(mid); color: palette(window); }
QPushButton#danger { border: 1px solid %(danger)s; color: %(danger)s; background: palette(button); }
QPushButton#danger:hover { background: %(danger)s; color: #ffffff; }
QPushButton#danger:pressed { background: %(danger_hover)s; color: #ffffff; }
QPushButton#danger:disabled { color: palette(placeholder-text); border-color: palette(mid); background: palette(window); }

QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled { color: palette(placeholder-text); }
QCheckBox::indicator { width: 16px; height: 16px; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    padding: 5px 8px; border-radius: 6px; border: 1px solid palette(mid);
    background: palette(base); color: palette(text);
    selection-background-color: %(accent)s; selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: %(accent)s; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: palette(window-text); }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: palette(placeholder-text); background: palette(window);
}
QComboBox QAbstractItemView {
    background: palette(base); color: palette(text);
    selection-background-color: %(accent)s; selection-color: #ffffff;
}

QListWidget, QTreeWidget, QTableWidget {
    border: 1px solid palette(mid); border-radius: 6px;
    background: palette(base); color: palette(text);
}
QListWidget::item { padding: 3px 4px; }
QListWidget::item:hover { background: palette(alternate-base); }
QListWidget::item:selected, QTableWidget::item:selected {
    background: %(accent)s; color: #ffffff;
}
QHeaderView::section {
    background: palette(alternate-base); color: palette(window-text);
    padding: 4px 6px; border: 0; border-right: 1px solid palette(mid);
}

QProgressBar {
    border: 1px solid palette(mid); border-radius: 6px; text-align: center;
    height: 20px; background: palette(base); color: palette(text);
}
QProgressBar::chunk { background: %(accent)s; border-radius: 5px; }

QGroupBox {
    border: 1px solid palette(mid); border-radius: 8px; margin-top: 10px; padding: 10px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTabBar::tab {
    padding: 6px 14px; border: 1px solid palette(mid); border-bottom: 0;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    background: palette(window); color: palette(window-text);
}
QTabBar::tab:selected { background: palette(base); }
QTabBar::tab:hover { background: palette(alternate-base); }

QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: palette(mid); border-radius: 6px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: palette(placeholder-text); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }

#Card { border: 1px solid palette(mid); border-radius: 10px; background: palette(base); }
#Badge { border-radius: 9px; padding: 2px 10px; color: #ffffff; font-weight: 600; }
#Hint { color: palette(placeholder-text); }
#DropZone {
    border: 2px dashed palette(mid); border-radius: 16px;
    color: palette(placeholder-text); font-size: 16px;
}
""" % {
    "accent": _ACCENT,
    "accent_hover": _ACCENT_HOVER,
    "danger": _DANGER,
    "danger_hover": _DANGER_HOVER,
}

# Semantic text colours, per theme, all AA-contrast against that theme's
# window / card background. Used via objectName (#StatusKeep, #StatusDelete,
# #Warn, #Muted).
_SEMANTIC = {
    "light": (
        "#StatusKeep { color: #15803d; font-weight: 600; }"
        "#StatusDelete { color: #b42318; font-weight: 600; }"
        "#Warn { color: #b45309; font-weight: 600; }"
        "#Muted { color: palette(placeholder-text); }"
    ),
    "dark": (
        "#StatusKeep { color: #4ade80; font-weight: 600; }"
        "#StatusDelete { color: #f87171; font-weight: 600; }"
        "#Warn { color: #fbbf24; font-weight: 600; }"
        "#Muted { color: palette(placeholder-text); }"
    ),
}


def _palette(theme: str) -> QPalette:
    if theme == "dark":
        window = QColor("#1e1f22")
        base = QColor("#2b2d31")
        alt = QColor("#34373e")
        text = QColor("#e8e9ec")            # ~13:1 on window
        button = QColor("#34373e")
        border = QColor("#4b4f57")          # subtle border, NOT used for text
        muted = QColor("#a9afb8")           # ~6.5:1 on window  (hint / placeholder)
        disabled = QColor("#7f858e")        # ~4.6:1 on window
        link = QColor("#7cb4ff")
    else:
        window = QColor("#f4f5f7")
        base = QColor("#ffffff")
        alt = QColor("#e9ebef")
        text = QColor("#1c1f24")            # ~14:1 on window
        button = QColor("#ffffff")
        border = QColor("#cdd2da")          # subtle border, NOT used for text
        muted = QColor("#586069")           # ~5.7:1 on window, ~6.4:1 on white
        disabled = QColor("#727984")        # ~4.5:1 on window
        link = QColor("#1a56db")

    p = QPalette()
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, button)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    # PlaceholderText is our "muted but readable" role (used by #Hint too).
    p.setColor(QPalette.PlaceholderText, muted)
    p.setColor(QPalette.Mid, border)
    p.setColor(QPalette.Dark, border)
    p.setColor(QPalette.Highlight, QColor(_ACCENT))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, link)

    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText,
                 QPalette.PlaceholderText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def apply_theme(app: QApplication, theme: str) -> None:
    theme = "dark" if theme == "dark" else "light"
    app.setStyle("Fusion")  # consistent, palette-driven base across platforms
    app.setPalette(_palette(theme))
    app.setStyleSheet(_QSS + _SEMANTIC[theme])

    # Re-polish every existing widget so a live theme switch fully takes effect
    # (Qt does not always cascade a new app stylesheet/palette to descendants).
    for widget in app.allWidgets():
        widget.setPalette(app.palette())
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
