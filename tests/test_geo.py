from __future__ import annotations

from PIL import Image

from app.core.geo import (
    CountryResolver,
    GeoCoord,
    get_resolver,
    gps_from_exif,
    parse_iso6709,
)


def _jpeg_with_gps(path, lat_dms, lat_ref, lon_dms, lon_ref):
    img = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = img.getexif()
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = lat_ref, lat_dms
    gps[3], gps[4] = lon_ref, lon_dms
    img.save(path, exif=exif)
    return str(path)


# -- EXIF GPS ---------------------------------------------------------
def test_gps_from_exif_reads_dms(tmp_path):
    p = _jpeg_with_gps(tmp_path / "cr.jpg", (9.0, 55.0, 41.16), "N", (84.0, 5.0, 26.4), "W")
    coord = gps_from_exif(p)
    assert coord is not None
    assert abs(coord.lat - 9.9281) < 1e-3
    assert abs(coord.lon - (-84.0907)) < 1e-3


def test_gps_from_exif_absent(tmp_path):
    p = tmp_path / "plain.jpg"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(p)
    assert gps_from_exif(str(p)) is None


def test_gps_from_exif_null_island_rejected(tmp_path):
    p = _jpeg_with_gps(tmp_path / "z.jpg", (0.0, 0.0, 0.0), "N", (0.0, 0.0, 0.0), "E")
    assert gps_from_exif(p) is None


# -- ISO 6709 (video location tag) -----------------------------------
def test_parse_iso6709():
    assert parse_iso6709("+37.7857-122.4011+010.000/") == GeoCoord(37.7857, -122.4011)
    assert parse_iso6709("+40.6894-074.0447/") == GeoCoord(40.6894, -74.0447)
    assert parse_iso6709("-33.8568+151.2153/") == GeoCoord(-33.8568, 151.2153)
    assert parse_iso6709(None) is None
    assert parse_iso6709("not a coordinate") is None


# -- resolver --------------------------------------------------------
def test_country_for_known_points():
    r = CountryResolver()
    assert r.available
    cases = {
        (9.9281, -84.0907): "Costa Rica",
        (40.4168, -3.7038): "España",
        (40.7128, -74.0060): "Estados Unidos",  # coastal -> snap fallback
        (19.4326, -99.1332): "México",
        (-22.9068, -43.1729): "Brasil",
        (35.6820, 139.6900): "Japón",
    }
    for (lat, lon), expected in cases.items():
        assert r.country_for(GeoCoord(lat, lon)) == expected


def test_country_for_open_ocean_is_none():
    r = CountryResolver()
    assert r.country_for(GeoCoord(30.0, -40.0)) is None
    assert r.country_for(None) is None
    assert r.country_for(GeoCoord(999.0, 999.0)) is None


def test_resolver_missing_dataset_degrades(tmp_path):
    r = CountryResolver(tmp_path / "does-not-exist.json")
    assert not r.available
    assert r.country_for(GeoCoord(9.9281, -84.0907)) is None


def test_get_resolver_is_singleton():
    assert get_resolver() is get_resolver()
