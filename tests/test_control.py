"""Pause / resume / cancel primitive."""
from __future__ import annotations

import threading
import time

from app.utils.control import Cancelled, RunController


def test_checkpoint_passes_when_running():
    c = RunController()
    assert c.checkpoint() is True
    assert not c.is_paused
    assert not c.is_cancelled


def test_cancel_makes_checkpoint_false():
    c = RunController()
    c.cancel()
    assert c.checkpoint() is False
    assert c.is_cancelled


def test_pause_blocks_until_resume():
    c = RunController()
    c.pause()
    assert c.is_paused
    released_at: list[float] = []

    def worker():
        c.checkpoint()  # should block
        released_at.append(time.monotonic())

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.3)
    assert not released_at, "worker should still be blocked while paused"
    resume_at = time.monotonic()
    c.resume()
    t.join(timeout=2)
    assert released_at and released_at[0] >= resume_at


def test_cancel_wakes_a_paused_worker():
    c = RunController()
    c.pause()
    result: list[bool] = []

    def worker():
        result.append(c.checkpoint())

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.2)
    c.cancel()
    t.join(timeout=2)
    assert result == [False]


def test_raise_if_cancelled():
    c = RunController()
    c.cancel()
    try:
        c.raise_if_cancelled()
        assert False, "should have raised"
    except Cancelled:
        pass
