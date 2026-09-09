"""End-to-end intelligent similarity (req. 49-63)."""
from __future__ import annotations

import shutil

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from app.config import AppConfig
from app.core.analysis import run_analysis
from app.core.embeddings import decode, encode
from app.core.similarity import MatchCategory


def _cfg(**kw):
    c = AppConfig(analyze_images=True, analyze_similar_images=True,
                  use_cache=False, workers=3, sensitivity="medium")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _photo(path, seed=0):
    img = Image.new("RGB", (1000, 750), (28, 58, 118))
    d = ImageDraw.Draw(img)
    for i in range(22):
        x = (i * 41 + seed * 17) % 1000
        y = (i * 29 + seed * 11) % 750
        d.ellipse([x, y, x + 90, y + 90], fill=(200 - i * 6, 40 + i * 7, 100 + seed))
    d.rectangle([160, 140, 460, 520], fill=(230, 205, 40))
    img.save(path)
    return path


# -- req. 57: DO NOT group different photos with a similar palette ----
def test_no_false_positive_similar_palette(tmp_path):
    a = Image.new("RGB", (1000, 750), (35, 55, 115))
    ImageDraw.Draw(a).polygon([(0, 750), (500, 180), (1000, 750)], fill=(215, 120, 60))
    ImageDraw.Draw(a).ellipse([760, 90, 900, 230], fill=(255, 240, 200))
    a.save(tmp_path / "sunset_a.jpg", quality=90)

    b = Image.new("RGB", (1000, 750), (40, 50, 110))
    ImageDraw.Draw(b).rectangle([0, 500, 1000, 750], fill=(210, 130, 70))
    ImageDraw.Draw(b).ellipse([120, 120, 280, 280], fill=(255, 235, 195))
    b.save(tmp_path / "sunset_b.jpg", quality=90)

    result = run_analysis(str(tmp_path), _cfg(sensitivity="high"))
    assert result.groups == []          # similar colours, different scenes


# -- req. 53: RESIZED DUPLICATE --------------------------------------
def test_resized_is_labelled(tmp_path):
    a = _photo(tmp_path / "original.png")
    Image.open(a).resize((400, 300)).save(tmp_path / "original_small.jpg", quality=82)
    groups = run_analysis(str(tmp_path), _cfg()).groups
    assert len(groups) == 1
    assert groups[0].category is MatchCategory.RESIZED_DUPLICATE
    assert any("resoluci" in r for r in groups[0].match_reasons)


# -- req. 52: CROPPED SIMILAR (high sensitivity) --------------------
def test_crop_is_labelled(tmp_path):
    a = _photo(tmp_path / "full.png")
    # a 500x500 crop of a 1000x750 photo -> clearly different aspect
    Image.open(a).crop((120, 90, 620, 590)).save(tmp_path / "framed.jpg", quality=90)
    groups = run_analysis(str(tmp_path), _cfg(sensitivity="high")).groups
    assert len(groups) == 1
    g = groups[0]
    assert g.category is MatchCategory.CROPPED_SIMILAR
    assert g.similarity_percent is not None
    assert any("recorte" in r for r in g.match_reasons)


def test_crop_not_run_at_medium(tmp_path):
    a = _photo(tmp_path / "full.png")
    Image.open(a).crop((120, 90, 620, 470)).save(tmp_path / "framed.jpg", quality=90)
    result = run_analysis(str(tmp_path), _cfg(sensitivity="medium"))
    assert result.crop_pairs_found == 0


# -- req. 54: light edits still detected --------------------------
def test_brightness_and_blur_still_similar(tmp_path):
    a = _photo(tmp_path / "a.png")
    ImageEnhance.Brightness(Image.open(a)).enhance(1.12).save(tmp_path / "a_bright.jpg", quality=88)
    Image.open(a).filter(ImageFilter.GaussianBlur(1.0)).save(tmp_path / "a_blur.jpg", quality=88)
    groups = run_analysis(str(tmp_path), _cfg(sensitivity="high")).groups
    assert len(groups) == 1
    assert groups[0].category in (
        MatchCategory.VISUALLY_IDENTICAL, MatchCategory.RESIZED_DUPLICATE,
        MatchCategory.VERY_SIMILAR, MatchCategory.SIMILAR,
    )
    assert groups[0].match_reasons


# -- req. 60: sensitivity changes the outcome ---------------------
def test_sensitivity_low_is_stricter(tmp_path):
    a = _photo(tmp_path / "a.png")
    # a heavier edit: resize + strong recompression + slight blur
    edited = Image.open(a).resize((520, 390)).filter(ImageFilter.GaussianBlur(1.4))
    ImageEnhance.Contrast(edited).enhance(1.25).save(tmp_path / "a_edited.jpg", quality=45)

    low = run_analysis(str(tmp_path), _cfg(sensitivity="low")).groups
    high = run_analysis(str(tmp_path), _cfg(sensitivity="high")).groups
    assert len(high) >= len(low)


# -- req. 62: every group carries a specific label + confidence ---
def test_groups_have_specific_labels_and_confidence(tmp_path):
    a = _photo(tmp_path / "a.png")
    shutil.copy2(a, tmp_path / "a_copy.png")            # FILE_IDENTICAL
    Image.open(a).resize((500, 375)).save(tmp_path / "a_small.jpg", quality=80)  # RESIZED
    result = run_analysis(str(tmp_path), _cfg())
    for g in result.groups:
        assert g.category is not MatchCategory.DIFFERENT
        assert g.category.style.short_es          # a real label, never just "DUPLICATE"
        assert 0 < g.confidence <= 100
    assert any(g.category is MatchCategory.FILE_IDENTICAL for g in result.groups)


# -- req. 58/59: embeddings are optional, storage round-trips -----
def test_embedding_storage_roundtrip():
    vec = [0.1, -0.25, 0.5, 0.0, 0.99]
    blob = encode(vec)
    back = decode(blob)
    assert back is not None and len(back) == len(vec)
    assert all(abs(x - y) < 1e-2 for x, y in zip(vec, back))
    assert encode(None) is None and decode(None) is None


def test_ai_flag_without_backend_is_harmless(tmp_path):
    a = _photo(tmp_path / "a.png")
    shutil.copy2(a, tmp_path / "a_copy.png")
    result = run_analysis(str(tmp_path), _cfg(use_ai_embeddings=True))
    assert result.groups        # still works via traditional methods
