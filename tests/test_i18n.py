from __future__ import annotations

import pytest

from app import i18n


@pytest.fixture(autouse=True)
def _reset_lang():
    i18n.set_language("es")
    yield
    i18n.set_language("es")


def test_spanish_is_identity():
    assert i18n.tr("Analizar de nuevo") == "Analizar de nuevo"
    assert i18n.tr("cadena inventada sin traducción") == "cadena inventada sin traducción"


def test_english_translates_known_and_passes_through_unknown():
    i18n.set_language("en")
    assert i18n.tr("▶  Analizar") == "▶  Analyze"
    assert i18n.tr("Ver duplicados") == "View duplicates"
    # unknown key falls back to the source string, never blank
    assert i18n.tr("una frase sin entrada") == "una frase sin entrada"


def test_every_english_value_is_nonempty():
    for key, value in i18n._EN.items():
        assert key and value, key


def test_current_language_roundtrip():
    i18n.set_language("EN")
    assert i18n.current_language() == "en"
    i18n.set_language("")
    assert i18n.current_language() == "es"
