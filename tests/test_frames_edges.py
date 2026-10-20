"""Edge rules for framed extents (synthetic lattice, no data needed)."""
import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON
from chigrid_frames import apply_edges, frame_index_range


def _lattice(n=4):
    drafts = []
    for i in range(n):
        for j in range(n):
            drafts.append({"geometry": box(i, j, i + 1, j + 1), "properties": {"chi_id": f"{i}-{j}"}})
    return drafts


FRAME = box(0.5, 0.0, 2.6, 4.0)  # column 0 half inside, columns 1 full, column 2 at 0.6, column 3 out


def test_whole_keeps_exactly_cells_at_or_above_min_share():
    kept = {f["properties"]["chi_id"]: f for f in apply_edges(_lattice(), FRAME, mode="whole")}
    assert {k.split("-")[0] for k in kept} == {"0", "1", "2"}  # column 0 sits exactly at 0.5: kept
    assert kept["0-0"]["properties"]["frame_share"] == 0.5 and "frame" in kept["0-0"]["properties"]["edge_kind"]
    assert kept["1-0"]["properties"]["edge_kind"] == []
    assert kept["2-0"]["geometry"]["coordinates"][0]  # whole cells keep their full geometry
    assert apply_edges(_lattice(), FRAME, mode="whole", min_share=0.55).__len__() == 8


def test_clip_tiles_the_frame_exactly_with_no_overlap():
    from shapely.geometry import shape

    cells = [shape(f["geometry"]) for f in apply_edges(_lattice(), FRAME, mode="clip")]
    union = unary_union(cells)
    assert union.symmetric_difference(FRAME).area < 1e-12
    assert abs(sum(c.area for c in cells) - union.area) < 1e-12


def test_include_city_keeps_city_cells_outside_the_frame():
    city = box(3, 0, 4, 1)  # one cell the frame excludes
    whole = {f["properties"]["chi_id"] for f in apply_edges(_lattice(), FRAME, mode="whole", city=city,
                                                             include_city=True)}
    assert "3-0" in whole and "3-1" not in whole
    clip = {f["properties"]["chi_id"]: f for f in apply_edges(_lattice(), FRAME, mode="clip", city=city,
                                                               include_city=True)}
    assert clip["3-0"]["properties"]["in_city_share"] == 1.0 and clip["3-0"]["properties"]["frame_share"] == 0.0


def test_any_is_the_touch_rule():
    assert len(apply_edges(_lattice(), box(0.9, 0.9, 1.1, 1.1), mode="any")) == 4


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        apply_edges(_lattice(), FRAME, mode="majority")


def test_frame_index_range_covers_bounds_with_pad():
    lat = lambda s: ORIGIN_LAT - s * MILE_LAT
    lon = lambda w: ORIGIN_LON - w * MILE_LON
    # a frame from 3.2N..16.6S and 5.6E..15.0W
    s_lo, s_hi, w_lo, w_hi = frame_index_range((lon(15.0), lat(16.6), lon(-5.6), lat(-3.2)), pad_miles=0)
    assert (s_lo, s_hi, w_lo, w_hi) == (-4, 17, -6, 15)
    assert frame_index_range((lon(15.0), lat(16.6), lon(-5.6), lat(-3.2)), pad_miles=2) == (-6, 19, -8, 17)


def test_v1_framed_extent_keeps_city_ids_and_squares():
    from shapely.geometry import shape

    from generate import build_chigrid, build_framed_chigrid

    lat = lambda s: ORIGIN_LAT - s * MILE_LAT
    lon = lambda w: ORIGIN_LON - w * MILE_LON
    city = box(lon(3), lat(3), lon(0), lat(0))           # 3x3 miles SW of the origin
    frame = box(lon(5.5), lat(4), lon(-1), lat(-1))      # bigger, cuts column 5W in half
    city_ids = {f["properties"]["chi_id"]: f for f in build_chigrid({"type": "Polygon",
                "coordinates": [list(city.exterior.coords)]})["features"]}
    whole = {f["properties"]["chi_id"]: f for f in build_framed_chigrid(frame, edges="whole", city=city)["features"]}
    clip = {f["properties"]["chi_id"]: f for f in build_framed_chigrid(frame, edges="clip", city=city)["features"]}
    inner = [k for k, f in city_ids.items() if shape(f["geometry"]).within(city)]
    assert inner and all(whole[k]["geometry"] == city_ids[k]["geometry"] for k in inner)
    assert "1S6W" in whole and whole["1S6W"]["properties"]["frame_share"] == 0.5
    assert abs(shape(clip["1S6W"]["geometry"]).area - MILE_LAT * MILE_LON / 2) < 1e-12
    assert "1S7W" not in whole and "1N1E" in whole and "1N2E" not in whole and "2N1E" not in whole
    assert whole["2S2W"]["properties"]["in_city_share"] == 1.0
