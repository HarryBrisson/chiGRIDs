"""chiGRID ordinates, canonical naming and the published-data goldens.

The golden tests need the city inputs (they are large, so not bundled):
    CHIGRID_BOUNDARY=…/community_areas.geojson CHIGRID_CENTERLINES=…/street_centerlines.json pytest
"""
import json

import pytest

from chigrid import (
    MILE_LAT,
    MILE_LON,
    MILE_ROADS_EW,
    MILE_ROADS_NS,
    ORIGIN_LAT,
    ORIGIN_LON,
    chi_name_and_label,
    chi_ordinate,
)
from conftest import REPO


def _center(miles_south, miles_west):
    return ORIGIN_LAT - miles_south * MILE_LAT, ORIGIN_LON - miles_west * MILE_LON


def test_origin_cell_is_1s1w():
    assert chi_ordinate(ORIGIN_LAT, ORIGIN_LON) == "χ:1S1W"


def test_quadrants_around_the_origin():
    assert chi_ordinate(*_center(0.5, -0.5)) == "χ:1S1E"
    assert chi_ordinate(*_center(-0.5, 0.5)) == "χ:1N1W"
    assert chi_ordinate(*_center(-0.5, -0.5)) == "χ:1N1E"


def test_damen_43rd_is_5s3w():
    lat, lon = _center(4.5, 2.5)
    assert chi_ordinate(lat, lon) == "χ:5S3W"
    assert chi_name_and_label(lat, lon) == ("Damen–43rd chi", "Dam43")


def test_no_mile_road_is_ever_a_center_name():
    for s in range(-11, 17):
        for w in range(-6, 12):
            name, _ = chi_name_and_label(*_center(s + 0.5, w + 0.5))
            if name.startswith("χ:"):
                continue
            avenue, street = name.removesuffix(" chi").split("–")
            assert avenue not in MILE_ROADS_NS and street not in MILE_ROADS_EW, name


def test_v1_golden_geometry_and_ids(city_boundary):
    from generate import build_chigrid

    built = {f["properties"]["chi_id"]: f for f in build_chigrid(city_boundary)["features"]}
    published = {f["properties"]["chi_id"]: f for f in json.load(open(REPO / "data/v1/chigrid.geojson"))["features"]}
    assert set(built) == set(published)
    assert all(built[k]["geometry"] == published[k]["geometry"] for k in published)


def test_v2_golden_bytes(city_boundary, street_centerlines):
    from chigrid_streets import build_street_aligned_chigrid
    from generate_v2 import V2_CITY_PROPS

    grid = build_street_aligned_chigrid(city_boundary, street_centerlines, collection_props=V2_CITY_PROPS)
    assert json.dumps(grid, ensure_ascii=False).encode() == (REPO / "data/v2/chigrid.geojson").read_bytes()


def test_v2_empty_centerlines_give_an_empty_grid(city_boundary):
    from chigrid_streets import build_street_aligned_chigrid

    grid = build_street_aligned_chigrid(city_boundary, {"type": "FeatureCollection", "features": []})
    assert grid["features"] == [] and grid["properties"]["count"] == 0


@pytest.mark.parametrize("extent,edges", [("un2025", "whole"), ("un2025", "clip"), ("jrc2023", "whole")])
def test_framed_v2_keeps_city_cells(city_boundary, street_centerlines, extent, edges):
    """Every published city cell that the frame keeps comes through with the same id and names, and
    with identical geometry unless it gained a part outside the city (or was clipped at the frame)."""
    from generate_v2 import build_framed_v2

    frames = REPO / "data" / "frames"
    if not (frames / f"{extent}.geojson").exists():
        pytest.skip("run fetch_frames.py first")
    grid, problems, stats = build_framed_v2(
        city_boundary, street_centerlines, extent=extent, frame_path=str(frames / f"{extent}.geojson"),
        region_path=str(frames / "jrc2023.geojson"), land_path=str(frames / "land.geojson"), edges=edges)
    assert not [p for p in problems if p.startswith(("quad", "N/S", "E/W")) and ("wound" in p or "self-int" in p)]
    published = {f["properties"]["chi_id"]: f for f in json.load(open(REPO / "data/v2/chigrid.geojson"))["features"]}
    built = {f["properties"]["chi_id"]: json.loads(json.dumps(f, ensure_ascii=False)) for f in grid["features"]}
    ids = [f["properties"]["chi_id"] for f in grid["features"]]
    assert len(ids) == len(set(ids))
    kept = [k for k in published if k in built]
    assert len(kept) > 200
    for k in kept:
        b, p = built[k]["properties"], published[k]["properties"]
        assert (b["name"], b["label"], b["name_canon"], b["label_canon"]) == (p["name"], p["label"], p["name_canon"], p["label_canon"])
        if "city" not in b["edge_kind"] and not (edges == "clip" and "frame" in b["edge_kind"]):
            assert built[k]["geometry"] == published[k]["geometry"], k
