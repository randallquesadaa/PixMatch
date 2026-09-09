"""Theme switching must actually flip text colours, including on nested and
already-created widgets (regression: text stayed white going dark -> light)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from app.ui.theme import apply_theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _win_text(w) -> str:
    return w.palette().color(QPalette.WindowText).name()


def _window(w) -> str:
    return w.palette().color(QPalette.Window).name()


def test_toggle_flips_colours_on_existing_nested_widget(qapp):
    root = QWidget()
    layout = QVBoxLayout(root)
    child = QLabel("hola")
    nested = QLabel("nested")
    inner = QWidget()
    QVBoxLayout(inner).addWidget(nested)
    layout.addWidget(child)
    layout.addWidget(inner)
    root.show()

    apply_theme(qapp, "dark")
    dark_text = _win_text(child)
    dark_bg = _window(child)

    apply_theme(qapp, "light")
    light_text = _win_text(child)
    light_bg = _window(child)

    assert dark_text != light_text
    assert dark_bg != light_bg
    # light theme: dark text on light background
    assert light_text.lower() < "#888888"
    assert light_bg.lower() > "#cccccc"
    # nested widget flipped too
    assert _win_text(nested) == light_text

    apply_theme(qapp, "dark")
    assert _win_text(child) == dark_text

    root.close()


def test_unknown_theme_falls_back_to_light(qapp):
    w = QLabel("x")
    w.show()
    apply_theme(qapp, "banana")
    assert _win_text(w).lower() < "#888888"
    w.close()


# -- WCAG contrast --------------------------------------------------
def _luminance(qcolor) -> float:
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b, _ = qcolor.getRgb()
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a, b) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_text_colours_meet_wcag_contrast(qapp):
    from PySide6.QtGui import QPalette

    for theme in ("light", "dark"):
        apply_theme(qapp, theme)
        pal = qapp.palette()
        window = pal.color(QPalette.Window)
        base = pal.color(QPalette.Base)

        body = pal.color(QPalette.WindowText)
        muted = pal.color(QPalette.PlaceholderText)          # #Hint / captions
        disabled = pal.color(QPalette.Disabled, QPalette.WindowText)

        assert _contrast(body, window) >= 7.0, (theme, "body text")
        # muted / hint text must still clear AA (4.5:1) on both backgrounds
        assert _contrast(muted, window) >= 4.5, (theme, "muted on window")
        assert _contrast(muted, base) >= 4.5, (theme, "muted on base")
        # disabled text only needs to be perceivable (3:1)
        assert _contrast(disabled, window) >= 3.0, (theme, "disabled")
