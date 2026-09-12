#!/usr/bin/env python3
"""Build ``app/resources/country_borders.json`` from Natural Earth data.

The result is a compact, offline dataset used by :mod:`app.core.geo` to turn a
GPS coordinate into a country name (in Spanish) without any network access and
without numpy / scipy (kept out of the PixMatch builds on purpose).

Usage::

    python scripts/build_country_borders.py            # downloads NE 1:50m
    python scripts/build_country_borders.py path.geojson

Source: Natural Earth (public domain), ``ne_50m_admin_0_countries``:
https://github.com/nvkelso/natural-earth-vector

The generated JSON is committed to the repo, so this script only needs to run
when the borders should be refreshed.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path

NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_50m_admin_0_countries.geojson"
)
OUT = Path(__file__).resolve().parent.parent / "app" / "resources" / "country_borders.json"

# How aggressively to thin the border polylines (degrees). ~0.006 deg ~= 600 m;
# CountryResolver snaps a near-miss to the closest border, so this only needs to
# be fine enough to keep distinct countries apart. Keeps the file ~1.5 MB.
SIMPLIFY_EPS = 0.006
COORD_DECIMALS = 4


def _load_geojson(arg: str | None) -> dict:
    if arg:
        return json.loads(Path(arg).read_text(encoding="utf-8"))
    print(f"Downloading {NE_URL} ...")
    with urllib.request.urlopen(NE_URL, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _perp_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    if a == b:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _simplify(points: list[list[float]], eps: float) -> list[list[float]]:
    """Iterative Douglas-Peucker (no recursion-limit surprises on big rings)."""
    if len(points) < 4:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        a, b = tuple(points[lo]), tuple(points[hi])
        dmax, idx = 0.0, -1
        for i in range(lo + 1, hi):
            d = _perp_dist(tuple(points[i]), a, b)
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [points[i] for i in range(len(points)) if keep[i]]


def _round_ring(ring: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for lon, lat in ring:
        pt = [round(lon, COORD_DECIMALS), round(lat, COORD_DECIMALS)]
        if not out or out[-1] != pt:
            out.append(pt)
    return out


def _polygons(geometry: dict) -> list[list[list[list[float]]]]:
    if not geometry:
        return []
    gtype = geometry["type"]
    if gtype == "Polygon":
        return [geometry["coordinates"]]
    if gtype == "MultiPolygon":
        return list(geometry["coordinates"])
    return []


def _bbox(polys: list[list[list[list[float]]]]) -> list[float]:
    xs = [pt[0] for poly in polys for ring in poly for pt in ring]
    ys = [pt[1] for poly in polys for ring in poly for pt in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def main() -> None:
    raw = _load_geojson(sys.argv[1] if len(sys.argv) > 1 else None)
    countries = []
    for feat in raw.get("features", []):
        props = feat.get("properties", {})
        name = props.get("NAME_ES") or props.get("NAME_EN") or props.get("ADMIN")
        iso = props.get("ISO_A2")
        if not iso or iso == "-99":
            iso = props.get("ISO_A2_EH") or props.get("ADM0_A3") or ""
        if not name:
            continue

        polys_out: list[list[list[list[float]]]] = []
        for poly in _polygons(feat.get("geometry")):
            rings_out = []
            for ring in poly:
                simplified = _round_ring(_simplify(ring, SIMPLIFY_EPS))
                if len(simplified) >= 4:
                    rings_out.append(simplified)
            if rings_out:
                polys_out.append(rings_out)
        if not polys_out:
            continue

        countries.append(
            {
                "name": name,
                "iso": iso,
                "bbox": [round(v, COORD_DECIMALS) for v in _bbox(polys_out)],
                "polys": polys_out,
            }
        )

    countries.sort(key=lambda c: c["name"])
    payload = {
        "source": "Natural Earth 1:50m admin_0 (public domain)",
        "simplify_eps_deg": SIMPLIFY_EPS,
        "count": len(countries),
        "countries": countries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT}  ({len(countries)} countries, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
