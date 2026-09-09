from __future__ import annotations

from PIL import Image, ImageDraw, ImageEnhance

from app.core.color_hist import BINS, color_signature, histogram_similarity


def _scene(path, hue_shift=0):
    img = Image.new("RGB", (200, 150), (30, 60, 120))
    d = ImageDraw.Draw(img)
    for i in range(10):
        d.ellipse(
            [i * 18, i * 12, i * 18 + 50, i * 12 + 50], fill=((160 + hue_shift) % 255, 80, 200)
        )
    img.save(path)
    return path


def test_signature_shape(tmp_path):
    p = _scene(tmp_path / "a.png")
    sig = color_signature(str(p))
    assert sig and len(sig.split(",")) == BINS


def test_same_image_full_similarity(tmp_path):
    a = _scene(tmp_path / "a.png")
    b = tmp_path / "b.png"
    Image.open(a).save(b)
    assert histogram_similarity(color_signature(str(a)), color_signature(str(b))) >= 99.0


def test_brightness_change_stays_similar(tmp_path):
    a = _scene(tmp_path / "a.png")
    b = tmp_path / "b.png"
    ImageEnhance.Brightness(Image.open(a)).enhance(1.3).save(b)
    # V is ignored -> a brightness bump barely moves the signature
    sim = histogram_similarity(color_signature(str(a)), color_signature(str(b)))
    assert sim >= 80.0


def test_different_palette_low_similarity(tmp_path):
    a = _scene(tmp_path / "a.png", hue_shift=0)
    b = tmp_path / "b.png"
    Image.new("RGB", (200, 150), (200, 40, 20)).save(b)  # all red
    sim = histogram_similarity(color_signature(str(a)), color_signature(str(b)))
    assert sim is not None and sim < 60.0


def test_missing_or_bad_signature():
    assert histogram_similarity(None, "1,2,3") is None
    assert histogram_similarity("bad", "also bad") is None
    assert color_signature("/does/not/exist.png") is None
