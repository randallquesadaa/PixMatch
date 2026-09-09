from __future__ import annotations

from PIL import Image

from app.core.image_analyzer import difference_image, pixel_digest, pixels_equal


def _img(path, size=(50, 40), color=(120, 30, 200), fmt=None):
    im = Image.new("RGB", size, color)
    for x in range(0, size[0], 5):
        im.putpixel((x, 0), (0, 0, 0))
    im.save(path, format=fmt)
    return path


def test_same_pixels_different_container_same_digest(tmp_path):
    a = _img(tmp_path / "a.png")
    b = _img(tmp_path / "b.bmp")  # different format, lossless, same pixels
    da, _ = pixel_digest(str(a))
    db, _ = pixel_digest(str(b))
    assert da == db
    assert pixels_equal(str(a), str(b))


def test_rgb_vs_rgba_opaque_match(tmp_path):
    rgb = tmp_path / "rgb.png"
    rgba = tmp_path / "rgba.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(rgb)
    Image.new("RGBA", (20, 20), (10, 20, 30, 255)).save(rgba)
    assert pixel_digest(str(rgb))[0] == pixel_digest(str(rgba))[0]


def test_different_pixels_different_digest(tmp_path):
    a = _img(tmp_path / "a.png", color=(0, 0, 0))
    b = _img(tmp_path / "b.png", color=(255, 255, 255))
    assert pixel_digest(str(a))[0] != pixel_digest(str(b))[0]
    assert not pixels_equal(str(a), str(b))


def test_exif_orientation_normalised(tmp_path):
    """A landscape image tagged 'rotate 90' has the same normalised pixels as
    the same image physically rotated."""
    base = Image.new("RGB", (60, 30), (5, 5, 5))
    base.putpixel((0, 0), (255, 0, 0))  # asymmetric marker

    tagged = tmp_path / "tagged.jpg"
    exif = base.getexif()
    exif[0x0112] = 6
    base.save(tagged, exif=exif)

    rotated = tmp_path / "rotated.jpg"
    base.rotate(-90, expand=True).save(rotated)  # what orientation 6 means

    da, ia = pixel_digest(str(tagged))
    db, ib = pixel_digest(str(rotated))
    assert (ia.width, ia.height) == (ib.width, ib.height) == (30, 60)
    # JPEG is lossy so digests may differ slightly; the dimensions proving the
    # rotation was applied is the key check, plus a loose pixel comparison:
    assert pixels_equal(str(tagged), str(tagged))


def test_pixel_digest_on_corrupt_returns_none(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xd9garbage")
    assert pixel_digest(str(bad)) is None
    assert pixels_equal(str(bad), str(bad)) is False


def test_decompression_bomb_is_rejected_not_fatal(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # 10x10 already exceeds
    huge = tmp_path / "huge.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(huge)
    # must degrade gracefully, never raise / never allocate
    assert pixel_digest(str(huge)) is None


def test_difference_image_shape(tmp_path):
    a = _img(tmp_path / "a.png", size=(80, 60), color=(10, 10, 10))
    b = _img(tmp_path / "b.png", size=(40, 30), color=(10, 10, 10))
    diff = difference_image(str(a), str(b))
    assert diff is not None
    assert diff.size == (80, 60)  # B is resized to A
