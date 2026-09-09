from __future__ import annotations

from PIL import Image, ImageDraw

from app.core.crop_detect import detect_crop


def _scene(path, size=(800, 600)):
    img = Image.new("RGB", size, (25, 45, 90))
    d = ImageDraw.Draw(img)
    for i in range(20):
        d.ellipse([i * 30, i * 22, i * 30 + 90, i * 22 + 90], fill=(200 - i * 8, 40 + i * 9, 110))
    d.rectangle([120, 100, 360, 420], fill=(235, 210, 35))
    d.polygon([(500, 550), (650, 300), (780, 550)], fill=(90, 200, 130))
    img.save(path)
    return path


def test_detects_a_real_crop(tmp_path):
    full = _scene(tmp_path / "full.png")
    Image.open(full).crop((100, 80, 500, 400)).save(tmp_path / "crop.png")
    res = detect_crop(str(full), str(tmp_path / "crop.png"))
    assert res is not None
    assert res.score >= 82.0
    x0, y0, x1, y1 = res.region
    # matched region is roughly the (100,80)-(500,400) box of an 800x600 image
    assert 0.05 < x0 < 0.25 and 0.05 < y0 < 0.25
    assert 0.45 < x1 < 0.75


def test_unrelated_images_no_crop(tmp_path):
    a = _scene(tmp_path / "a.png")
    b = Image.new("RGB", (400, 300), (200, 60, 40))
    ImageDraw.Draw(b).ellipse([50, 50, 350, 250], fill=(20, 20, 20))
    b.save(tmp_path / "b.png")
    assert detect_crop(str(a), str(tmp_path / "b.png")) is None


def test_near_full_frame_is_not_a_crop(tmp_path):
    full = _scene(tmp_path / "full.png")
    Image.open(full).resize((400, 300)).save(tmp_path / "small.png")
    # same framing, just smaller -> resize, not crop
    assert detect_crop(str(full), str(tmp_path / "small.png")) is None


def test_corrupt_input(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    ok = _scene(tmp_path / "ok.png")
    assert detect_crop(str(ok), str(bad)) is None
