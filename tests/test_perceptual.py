from __future__ import annotations

from PIL import Image, ImageDraw

from app.core.perceptual import (
    BKTree,
    average_hash,
    difference_hash,
    hamming,
    perceptual_hash,
    similarity_percent,
)
from app.core.similarity import MatchCategory, classify_similarity, distance_cutoff


def _photo(path, size=(400, 300), seed=0):
    img = Image.new("RGB", size, (30, 40, 60))
    d = ImageDraw.Draw(img)
    for i in range(12):
        x = (i * 37 + seed * 13) % size[0]
        y = (i * 53 + seed * 7) % size[1]
        d.ellipse([x, y, x + 60, y + 60], fill=(200 - i * 10, 50 + i * 8, 90 + seed))
    d.rectangle([40, 40, 180, 160], fill=(220, 210, 30))
    img.save(path)
    return path


def test_hashes_are_64_bit_or_none(tmp_path):
    p = _photo(tmp_path / "a.png")
    for fn in (perceptual_hash, difference_hash, average_hash):
        h = fn(str(p))
        assert h is not None and 0 <= h < (1 << 64)


def test_identical_images_same_phash(tmp_path):
    a = _photo(tmp_path / "a.png")
    b = tmp_path / "b.png"
    Image.open(a).save(b)  # re-saved, same pixels
    assert perceptual_hash(str(a)) == perceptual_hash(str(b))


def test_resized_and_recompressed_is_close(tmp_path):
    a = _photo(tmp_path / "a.png", size=(600, 450))
    small = tmp_path / "a_small.jpg"
    Image.open(a).resize((240, 180)).save(small, quality=70)
    d = hamming(perceptual_hash(str(a)), perceptual_hash(str(small)))
    assert d <= 10
    assert similarity_percent(d) >= 84.0


def test_different_images_are_far(tmp_path):
    a = _photo(tmp_path / "a.png", seed=1)
    b = tmp_path / "b.png"
    img = Image.new("RGB", (400, 300), (250, 250, 250))
    d = ImageDraw.Draw(img)
    for i in range(-300, 400, 18):
        d.line([(i, 0), (i + 300, 300)], fill=(0, 0, 0), width=6)
    img.save(b)
    dist = hamming(perceptual_hash(str(a)), perceptual_hash(str(b)))
    assert dist >= 12


def test_corrupt_image_returns_none(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    assert perceptual_hash(str(bad)) is None
    assert difference_hash(str(bad)) is None
    assert average_hash(str(bad)) is None


def test_similarity_helpers():
    assert similarity_percent(0) == 100.0
    assert similarity_percent(64) == 0.0
    assert similarity_percent(6) == 90.6
    assert distance_cutoff(90) == 6
    assert distance_cutoff(100) == 0


def test_classify_similarity_keeps_categories_separate():
    # a perfect perceptual match is "visually identical", never "pixel identical"
    assert classify_similarity(100.0, 90) is MatchCategory.VISUALLY_IDENTICAL
    assert classify_similarity(97.0, 90) is MatchCategory.VERY_SIMILAR
    assert classify_similarity(91.0, 90) is MatchCategory.SIMILAR
    assert classify_similarity(80.0, 90) is MatchCategory.DIFFERENT


def test_bktree_radius_query():
    tree = BKTree()
    values = [0b0000, 0b0001, 0b0011, 0b1111, 0b1110]
    for v in values:
        tree.add(v, v)
    near = set(tree.query(0b0000, 1))
    assert near == {0b0000, 0b0001}
    assert set(tree.query(0b1111, 1)) == {0b1111, 0b1110}
    assert set(tree.query(0b0000, 4)) == set(values)
