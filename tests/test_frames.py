"""The committed frame polygons (data/frames, built by fetch_frames.py) match their recipe."""
import json
from pathlib import Path

import pytest

FRAMES = Path(__file__).resolve().parent.parent / "data" / "frames"
if not FRAMES.is_dir():
    pytest.skip("data/frames missing; run fetch_frames.py", allow_module_level=True)

import fetch_frames  # noqa: E402
from chigrid import ORIGIN_LAT, ORIGIN_LON  # noqa: E402

# frame -> (source key, expected Mollweide area in km² before holes are filled)
GHSL_FRAMES = {"un2025": ("mtuc", 932.0), "jrc2023": ("smod", 2231.0)}


def _load(name):
    data = json.loads((FRAMES / f"{name}.geojson").read_text())
    assert data["type"] == "FeatureCollection" and len(data["features"]) == 1
    feat = data["features"][0]
    return feat["geometry"], feat["properties"]["provenance"]


def _shape(geometry):
    from shapely.geometry import shape

    return shape(geometry)


@pytest.mark.parametrize("name", sorted(GHSL_FRAMES))
def test_frame_is_one_valid_polygon(name):
    geometry, prov = _load(name)
    geom = _shape(geometry)
    assert geom.geom_type == "Polygon" and geom.is_valid
    if prov["holes_filled"]:
        assert len(geom.interiors) == 0


@pytest.mark.parametrize("name", sorted(GHSL_FRAMES))
def test_areas_within_one_percent(name):
    geometry, prov = _load(name)
    _, want = GHSL_FRAMES[name]
    unfilled = prov.get("area_km2_mollweide_unfilled", prov["area_km2_mollweide"])
    assert abs(unfilled - want) <= 0.01 * want
    # the committed lon/lat polygon, projected back, still has the recorded area
    moll = fetch_frames.reproject(_shape(geometry), fetch_frames.WGS84, fetch_frames.MOLLWEIDE)
    assert abs(moll.area / 1e6 - prov["area_km2_mollweide"]) <= 0.01 * prov["area_km2_mollweide"]
    assert abs(prov["area_km2_geodesic"] - prov["area_km2_mollweide"]) <= 0.01 * prov["area_km2_mollweide"]


def test_jrc2023_holes_recorded():
    _, prov = _load("jrc2023")
    assert prov["holes"] == 3 and [round(h) for h in prov["holes_km2"]] == [17, 32, 35]
    assert prov["area_km2_mollweide_filled"] == pytest.approx(prov["area_km2_mollweide_unfilled"]
                                                              + sum(prov["holes_km2"]))


@pytest.mark.parametrize("name,key", [("un2025", "mtuc"), ("jrc2023", "smod"), ("land", "states")])
def test_sha256_matches_provenance(name, key):
    _, prov = _load(name)
    src = fetch_frames.SOURCES[key]
    assert prov["zip_sha256"] == src["sha256"] and prov["url"] == src["url"] and prov["file"] == src["member"]
    raw = fetch_frames.RAW_DIR / src["file"]
    if raw.exists():
        assert fetch_frames.sha256_file(raw) == prov["zip_sha256"]


@pytest.mark.parametrize("name", ["un2025", "jrc2023", "land", "city"])
def test_loop_inside(name):
    from shapely.geometry import Point

    geometry, _ = _load(name)
    assert _shape(geometry).contains(Point(ORIGIN_LON, ORIGIN_LAT))


def test_frames_nest():
    """The UN centre sits almost wholly inside the JRC component (12 of its 932 km² fall outside:
    they are different GHSL products), and the land mask's box covers the JRC component."""
    un, jrc = _shape(_load("un2025")[0]), _shape(_load("jrc2023")[0])
    assert un.difference(jrc).area / un.area < 0.02
    lx0, ly0, lx1, ly1 = _shape(_load("land")[0]).bounds
    jx0, jy0, jx1, jy1 = jrc.bounds
    assert lx0 < jx0 and ly0 < jy0 and lx1 > jx1 and ly1 > jy1


@pytest.mark.parametrize("name", ["un2025", "jrc2023", "land", "city"])
def test_committed_size(name):
    assert (FRAMES / f"{name}.geojson").stat().st_size < 1_000_000
