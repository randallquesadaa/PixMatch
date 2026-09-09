from __future__ import annotations

import csv
import json

from app.config import AppConfig
from app.core.analysis import run_analysis
from app.core.duplicate_groups import Decision
from app.core.export import export_csv, export_html, export_json


def _result(tmp_path, make_image):
    import shutil

    # "&" is filesystem-legal on every OS and still has to be HTML-escaped
    src = make_image(tmp_path / "carpeta" / "IMG &1.jpg", size=(60, 40))
    shutil.copy2(src, tmp_path / "carpeta" / "IMG copia.jpg")
    make_image(tmp_path / "otra.jpg", size=(10, 10), color=(0, 255, 0))
    return run_analysis(str(tmp_path), AppConfig(workers=2, use_cache=False))


def test_csv_has_expected_columns_and_rows(tmp_path, make_image):
    result = _result(tmp_path, make_image)
    result.groups[0].records[0].decision = Decision.KEEP
    out = tmp_path / "r.csv"
    export_csv(result, out)

    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {"group_id", "category", "decision", "path", "sha256", "phash"} <= set(rows[0])
    assert len(rows) == sum(g.count for g in result.groups)
    assert any(r["decision"] == "conservar" for r in rows)


def test_json_round_trips(tmp_path, make_image):
    result = _result(tmp_path, make_image)
    out = tmp_path / "r.json"
    export_json(result, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["root"] == str(tmp_path)
    assert data["summary"]["groups"] == len(result.groups)
    assert len(data["groups"][0]["files"]) == result.groups[0].count
    assert "generated" in data


def test_html_is_self_contained_and_escaped(tmp_path, make_image):
    result = _result(tmp_path, make_image)
    result.groups[0].records[1].decision = Decision.DELETE
    out = tmp_path / "r.html"
    export_html(result, out)
    text = out.read_text(encoding="utf-8")

    assert text.startswith("<!doctype html>")
    assert "MARCADO PARA ELIMINAR" in text
    assert "IMG &amp;1.jpg" in text  # ampersand escaped
    assert "IMG &1.jpg" not in text  # never raw
    assert "data:image/png;base64," in text  # thumbnails embedded


def test_html_skips_thumbnails_when_huge(tmp_path, make_image, monkeypatch):
    import app.core.export as exp

    monkeypatch.setattr(exp, "_THUMB_LIMIT", 1)
    result = _result(tmp_path, make_image)
    out = tmp_path / "r.html"
    export_html(result, out)
    text = out.read_text(encoding="utf-8")
    assert "se omiten las miniaturas" in text
    assert "data:image/png;base64," not in text
