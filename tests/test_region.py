"""Regional lattice: corner solve, checks, ids, invariance (synthetic, no data needed)."""
import math

import pytest
from shapely.geometry import box, shape
from shapely.ops import unary_union

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON
from chigrid_frames import apply_edges
from chigrid_region import (
    Piece, build_regional_grid, build_regional_namer, check_lattice, lattice_ordinate, lattice_quads,
    solve_nodes, Family,
)
from chigrid_streets import city_cells


def lon(w):
    return ORIGIN_LON - w * MILE_LON


def lat(s):
    return ORIGIN_LAT - s * MILE_LAT


def _lines(lo, hi, tilt=0.0, spacing=1.0):
    """Straight lines at ``spacing`` miles, N/S as x = m*y + c, E/W as y = m*x + c, tilted slightly."""
    ns = {k: (tilt, lon(k * spacing) - tilt * ORIGIN_LAT) for k in range(lo, hi + 1)}
    ew = {k: (tilt, lat(k * spacing) - tilt * ORIGIN_LON) for k in range(lo, hi + 1)}
    return ns, ew


CITY = box(lon(3.4), lat(3.6), lon(-0.3), lat(-0.2))  # ~3.7 x 3.8 miles straddling the origin


def _grid(ns, ew, anchors=None, land=None, bounds=None):
    feats = city_cells(CITY, ns, ew, None)
    return feats, build_regional_grid(CITY, ns, ew, feats, region_bounds=bounds or box(lon(8), lat(8), lon(-4), lat(-4)).bounds,
                                      anchors=anchors, land=land, pad_miles=0)


def test_lattice_ordinate():
    assert lattice_ordinate(0, 0) == "χ:1S1W"
    assert lattice_ordinate(-1, -1) == "χ:1N1E"
    assert lattice_ordinate(8, 27) == "χ:28S9W"


def test_city_cells_verbatim_and_tiling():
    ns, ew = _lines(-2, 5)
    feats, g = _grid(ns, ew)
    assert g.problems == []
    drafts = {d["properties"]["chi_id"]: d for d in g.drafts}
    for f in feats:
        d = drafts[f["properties"]["chi_id"]]
        if "verbatim" in d:
            assert d["verbatim"] is f
    cells = [d["geometry"] for d in g.drafts]
    union = unary_union(cells)
    assert abs(sum(c.area for c in cells) - union.area) < 1e-9 * union.area  # no overlaps
    assert union.geom_type == "Polygon" and not union.interiors  # no gaps
    assert abs(union.area - union.envelope.area) < 1e-9 * union.area  # untilted lattice: a full rectangle
    assert union.buffer(1e-9).contains(box(lon(8), lat(8), lon(-4), lat(-4)))
    ids = [d["properties"]["chi_id"] for d in g.drafts]
    assert len(ids) == len(set(ids))


def test_straddling_cell_keeps_city_names_and_merges():
    ns, ew = _lines(-2, 5)
    feats, g = _grid(ns, ew)
    d = {x["properties"]["chi_id"]: x for x in g.drafts}["4S4W"]  # cut by the city's SW corner
    assert "city" in d["properties"]["edge_kind"] and d["geometry"].geom_type == "Polygon"
    assert abs(d["properties"]["area_sqmi"] - 1.0) < 0.01


def test_core_false_and_pieces_take_over_outside():
    ns, ew = _lines(-2, 5)
    # E/W line -1 is a half-mile street in the city (like Howard); outside, a road at 1.0 mi N takes it.
    ew[-1] = (0.0, lat(-0.5))
    oakton = {"side": "north", "slope": 0.0, "intercept": lat(-1.0), "rule": 1, "road": "oakton"}
    anchors = {"ew": {"-1": {"core": False, "pieces": [dict(oakton, side="east"), dict(oakton, side="west")]}}}
    feats, g = _grid(ns, ew, anchors=anchors)
    assert not [p for p in g.problems if "apart" in p]
    cell = {x["properties"]["chi_id"]: x for x in g.drafts}["1N6W"]  # well outside the city
    assert abs(cell["geometry"].bounds[3] - lat(-1.0)) < 1e-9


def test_check_lattice_catches_crossing_and_squeeze():
    ns, ew = _lines(-1, 3)
    fam_ns, fam_ew = Family(True, dict(ns)), Family(False, dict(ew))
    nodes = solve_nodes(fam_ns, fam_ew, (-1, 3), (-1, 3))
    assert check_lattice(nodes) == []
    squeezed = dict(ns)
    squeezed[2] = (0.0, lon(1.45))  # 0.45 mi from line 1
    nodes = solve_nodes(Family(True, squeezed), fam_ew, (-1, 3), (-1, 3))
    assert any("apart" in p for p in check_lattice(nodes))
    crossed = dict(ns)
    crossed[2] = (0.0, lon(0.8))  # east of line 1: lines cross
    nodes = solve_nodes(Family(True, crossed), fam_ew, (-1, 3), (-1, 3))
    assert any("wound" in p or "self-intersecting" in p for p in check_lattice(nodes))


def test_model_imputes_far_lines_on_the_rotated_grid():
    from chigrid_region import fit_model

    # city lines 0..5, spaced 1.006 mi and rotated (0.024 mi W per mi S); line 9 is never fitted
    def line(k):
        w0, tilt = 1.006 * k, -0.024
        # w = w0 + tilt*s  ->  x = ORIGIN_LON - MILE_LON*(w0 + tilt*(ORIGIN_LAT - y)/MILE_LAT)
        return (MILE_LON * tilt / MILE_LAT, ORIGIN_LON - MILE_LON * (w0 + tilt * ORIGIN_LAT / MILE_LAT))
    fam = Family(True, {k: line(k) for k in range(6)}, fitted=set(range(6)),
                 model=fit_model({k: line(k) for k in range(6)}, True, (-5, 5)))
    m, c, rule, src = fam.resolve(9, 20, False)
    assert (rule, src) == (3, "model")
    y = ORIGIN_LAT - 20 * MILE_LAT
    assert abs((ORIGIN_LON - (m * y + c)) / MILE_LON - (1.006 * 9 - 0.024 * 20)) < 1e-6
    # corners touching the city use the city's own fit
    assert fam.resolve(4, 20, True)[3] == "core"


def test_tilted_lattice_ids_count_roads():
    # A 1.5° rotation drifts 0.8 mi over 30 miles — ids still count mile roads, offset_mi records it.
    ns, ew = _lines(-2, 32, tilt=0.03)
    feats, g = _grid(ns, ew, bounds=box(lon(30), lat(30), lon(-1), lat(-1)).bounds)
    far = {x["properties"]["chi_id"]: x for x in g.drafts}["30S30W"]
    assert max(abs(v) for v in far["properties"]["offset_mi"]) > 0.3


def test_regional_namer_positions_roads_locally():
    import numpy as np

    class G:
        def __init__(self, name, lon, lat, cls="3"):
            self.name, self.lon, self.lat, self.road_class = name, np.array(lon), np.array(lat), cls

    s = np.linspace(-1, 30, 200)
    harlem = G("HARLEM", lon(9.2 - 0.025 * s), lat(s))      # drifts east going south
    oak_park = G("OAK PARK", lon(8.7 - 0.025 * s), lat(s))  # the half-mile road
    w = np.linspace(-1, 30, 200)
    st = G("159TH", lon(w), lat(19.5 + 0 * w))
    namer = build_regional_namer([harlem, oak_park, st], exclude={"Harlem"})
    # at 19.5 S, Oak Park sits at 8.21 W: the cell centred there names it, Harlem is a mile road
    assert namer(lat(19.4), lon(8.2)) == ("Oak Park", "159th")
    assert namer(lat(10.0), lon(20.0)) == (None, None)
