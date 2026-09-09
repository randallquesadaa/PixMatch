#!/usr/bin/env python3
"""Reproducible analysis benchmark.

Generates a synthetic photo library (with duplicates) into a temp folder and
times the analysis pipeline, reporting wall time and peak RSS.

    N=3000 python scripts/benchmark.py         # from the project root
    N=20000 python scripts/benchmark.py

Peak RSS should stay roughly flat as N grows - the pipeline streams hashes and
decodes one image at a time, and the thumbnail cache is LRU-bounded.
"""
from __future__ import annotations

import os
import random
import resource
import shutil
import tempfile
import time

from PIL import Image

from app.config import AppConfig
from app.core.analysis import run_analysis

N = int(os.environ.get("N", "3000"))
DUP_RATIO = float(os.environ.get("DUP_RATIO", "0.25"))


def _rss_mb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def _build_library(root: str) -> int:
    random.seed(1)
    bases = []
    for _ in range(40):
        img = Image.new("RGB", (800, 600),
                        (random.randrange(255), random.randrange(255), random.randrange(255)))
        for _ in range(30):
            x, y = random.randrange(800), random.randrange(600)
            img.paste((random.randrange(255),) * 3, (x, y, min(800, x + 40), min(600, y + 40)))
        bases.append(img)

    made = 0
    for i in range(N):
        sub = os.path.join(root, f"dir{i // 200}")
        os.makedirs(sub, exist_ok=True)
        existing = [f for f in os.listdir(sub) if f.endswith(".jpg")]
        if existing and random.random() < DUP_RATIO:
            shutil.copy2(os.path.join(sub, random.choice(existing)),
                         os.path.join(sub, f"img{i}_copy.jpg"))
        else:
            bases[i % 40].save(os.path.join(sub, f"img{i}.jpg"),
                               quality=random.choice([70, 85, 95]))
        made += 1
    return made


def main() -> None:
    root = tempfile.mkdtemp(prefix="pixmatch-bench-")
    try:
        t0 = time.time()
        made = _build_library(root)
        size_mb = sum(
            os.path.getsize(os.path.join(r, f))
            for r, _, fs in os.walk(root) for f in fs
        ) / 1e6
        print(f"generated {made} files ({size_mb:.0f} MB) in {time.time() - t0:.1f}s\n")

        cpu = os.cpu_count() or 4
        runs = [
            ("exact only",
             AppConfig(analyze_images=True, detect_pixel_identical=True,
                       analyze_similar_images=False, use_cache=False, workers=cpu)),
            ("+ perceptual",
             AppConfig(analyze_images=True, detect_pixel_identical=True,
                       analyze_similar_images=True, use_cache=False, workers=cpu)),
        ]
        for label, cfg in runs:
            t = time.time()
            r = run_analysis(root, cfg)
            print(f"[{label:13}] {time.time() - t:6.1f}s   "
                  f"groups={len(r.groups):5}  hashed={r.hashed_files:6}  "
                  f"decoded={r.decoded_files:6}  perceptual={r.perceptual_files:6}  "
                  f"peakRSS={_rss_mb()}MB")

        db = os.path.join(root, "cache.sqlite3")
        cfg = AppConfig(analyze_images=True, detect_pixel_identical=True,
                        analyze_similar_images=True, use_cache=True,
                        cache_db_path=db, workers=cpu)
        t = time.time(); run_analysis(root, cfg)
        print(f"\n[cache warm   ] {time.time() - t:6.1f}s")
        t = time.time(); r = run_analysis(root, cfg, incremental=True)
        print(f"[incremental  ] {time.time() - t:6.1f}s   cache_hits={r.cache_hits}  "
              f"decoded={r.decoded_files}  perceptual={r.perceptual_files}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
