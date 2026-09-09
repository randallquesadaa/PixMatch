from __future__ import annotations

from app.core.similarity import MatchCategory
from app.core.similarity_engine import (
    DEFAULT_WEIGHTS,
    Signals,
    SimilarityBands,
    classify_combined,
    combined_score,
    resolve_profile,
)


class _Cfg:
    def __init__(self, **kw):
        self.sensitivity = "medium"
        self.similarity_threshold = 78
        self.similarity_bands = {}
        self.similarity_weights = {}
        self.detect_crops = False
        self.detect_resized = True
        self.dhash_confirm_slack = 12
        self.__dict__.update(kw)


def _sig(base=0, w=1000, h=800):
    # a valid histogram signature sums to ~1000
    return Signals(
        phash=base,
        dhash=base,
        ahash=base,
        bhash=base,
        color_sig="1000" + ",0" * 63,
        width=w,
        height=h,
    )


def test_identical_signals_score_100():
    r = combined_score(_sig(0), _sig(0))
    assert r.score == 100.0
    assert set(r.per_signal) == {"phash", "dhash", "ahash", "color", "bhash"}
    assert r.reasons


def test_weights_are_normalised_and_respected():
    # phash differs a lot, everything else identical
    a = Signals(phash=0, dhash=0, ahash=0, bhash=0, color_sig="1", width=10, height=10)
    b = Signals(phash=(1 << 64) - 1, dhash=0, ahash=0, bhash=0, color_sig="1", width=10, height=10)
    default = combined_score(a, b).score
    phash_heavy = combined_score(
        a, b, weights={"phash": 1.0, "dhash": 0, "ahash": 0, "color": 0, "bhash": 0}
    ).score
    assert phash_heavy < default  # weighting phash pulls the score down


def test_resize_upgrade():
    r = combined_score(_sig(0, 4000, 3000), _sig(0, 1000, 750))
    assert r.upgrade is MatchCategory.RESIZED_DUPLICATE
    assert any("resoluci" in x for x in r.reasons)


def test_no_resize_upgrade_when_same_resolution():
    r = combined_score(_sig(0, 1000, 800), _sig(0, 1000, 800))
    assert r.upgrade is None


def test_classify_bands():
    bands = SimilarityBands(98, 90, 75)
    assert classify_combined(99.0, bands) is MatchCategory.VISUALLY_IDENTICAL
    assert classify_combined(93.0, bands) is MatchCategory.VERY_SIMILAR
    assert classify_combined(80.0, bands) is MatchCategory.SIMILAR
    assert classify_combined(50.0, bands) is MatchCategory.DIFFERENT


def test_sensitivity_presets():
    low = resolve_profile(_Cfg(sensitivity="low"))
    high = resolve_profile(_Cfg(sensitivity="high"))
    assert low.bands.similar > high.bands.similar  # low = stricter
    assert high.detect_crops and not low.detect_crops


def test_custom_profile_reads_config():
    cfg = _Cfg(
        sensitivity="custom",
        similarity_bands={"visually_identical": 95, "very_similar": 80, "similar": 60},
        similarity_weights={"phash": 0.5, "dhash": 0.5, "ahash": 0, "color": 0, "bhash": 0},
        detect_crops=True,
    )
    p = resolve_profile(cfg)
    assert p.bands.similar == 60.0
    assert p.detect_crops
    assert p.weights["phash"] == 0.5


def test_default_weights_sum_to_one():
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
