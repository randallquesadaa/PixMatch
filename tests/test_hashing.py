from __future__ import annotations

import hashlib

import pytest

from app.core.hashing import HashCancelled, hash_file, partial_signature


def test_sha256_matches_hashlib(tmp_path):
    p = tmp_path / "a.bin"
    data = b"pixmatch" * 5000
    p.write_bytes(data)
    assert hash_file(p) == hashlib.sha256(data).hexdigest()


def test_identical_content_same_hash_different_names(tmp_path):
    a = tmp_path / "one.bin"
    b = tmp_path / "sub" / "two-renamed.bin"
    b.parent.mkdir()
    payload = b"\x00\x01\x02\x03" * 12345
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert hash_file(a) == hash_file(b)


def test_different_content_different_hash(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"x" * 1000)
    b.write_bytes(b"x" * 999 + b"y")
    assert hash_file(a) != hash_file(b)


def test_empty_file(tmp_path):
    p = tmp_path / "empty"
    p.touch()
    assert hash_file(p) == hashlib.sha256(b"").hexdigest()


def test_unicode_path(tmp_path):
    p = tmp_path / "café ☕ 日本語" / "imagén-01.bin"
    p.parent.mkdir()
    p.write_bytes(b"unicode-payload")
    assert hash_file(p) == hashlib.sha256(b"unicode-payload").hexdigest()


def test_cancellation_raises(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"0" * (4 << 20))
    with pytest.raises(HashCancelled):
        hash_file(p, chunk_size=1024, check=lambda: False)


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        hash_file(tmp_path / "nope.bin")


def test_partial_signature_zero_bytes(tmp_path):
    a = tmp_path / "empty1"
    b = tmp_path / "empty2"
    a.touch()
    b.touch()
    assert partial_signature(a) == partial_signature(b)
    assert partial_signature(a).startswith("0:")


def test_partial_signature_distinguishes_and_matches(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"A" * 200_000)
    b.write_bytes(b"A" * 200_000)
    c.write_bytes(b"A" * 199_999 + b"B")
    assert partial_signature(a) == partial_signature(b)
    assert partial_signature(a) != partial_signature(c)
