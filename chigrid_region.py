"""Regional chiGRID lattice: v2 street-aligned cells beyond the city limit.

The city grid (Pass A, :func:`chigrid_streets.city_cells`) is built exactly as before, so in-city
cells never change. This module builds everything around it (Pass B) as a lattice of grid corners:

* Every grid line is a sequence of straight pieces, chosen per crossing (per corner):

  1. **core** — the city's own fitted line (``fit_city_lines``), used at every corner that touches
     a quad overlapping the city, so a quad's sides lie on the city lines wherever the city is;
  2. **piece** — a straight fit to a real suburban road from ``anchors_regional.json`` (rule 1 the
     same road continues, rule 2 an aligned substitute);
  3. **imputed** — rule 3: interpolated between neighbouring lines that have a road on this side,
     else the line of a smooth model of the city grid (its ~1.006 mi spacing and ~1° rotation, which
     the suburbs keep).

* Each corner is the closed-form intersection of the two pieces chosen for it; each cell is the quad
  of its four corners. Lines bend only at corners, so checking spacing and winding at the corners is
  exact (:func:`check_lattice`).

* A cell's part outside the city is ``quad ∩ land − city``; it merges with the city cell of the same
  id (cells that straddle the city limit).

* Ids COUNT MILE ROADS (Harry, 2026-10-20): ``9W`` is the band between the 8th and 9th mile road west
  of State, wherever those roads run. Chicago's road grid is rotated ~1° from true north and the
  suburbs keep that rotation (Harlem drifts from 9.20 W at 8 N to 8.35 W at 28 S), so far from the
  city a road-bounded cell sits up to ~0.85 mi off its geographic band; ``offset_mi`` records how far.
  Inside the city the two agree — every city cell's geographic id must equal its lattice id, or the
  build fails.

Index conventions match ``chigrid_streets``: N/S lines by signed miles WEST (E negative), x = m*y + c;
E/W lines by signed miles SOUTH (N negative), y = m*x + c. A quad (a, b) lies between N/S lines a, a+1
and E/W lines b, b+1, i.e. in band a west and band b south.
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from typing import Any

from chigrid import (MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON, _short, chi_name_and_label, chi_ordinate,
                     miles_south, miles_west)
from chigrid_streets import ANCHOR_EW, ANCHOR_NS, TOL_HI, TOL_LO

LATTICE_LIMITS = {"min_gap": 0.5, "max_gap": 2.0, "max_offset": 1.25}  # miles: a broken lattice, not a
# merely odd cell (cells outside 0.9–1.1 sq mi are flagged instead); offset = sanity bound
_EPS = 1e-12


@dataclass(frozen=True)
class Piece:
    """A straight stretch of one grid line. ``lo``/``hi`` bound it on the CROSS axis in miles (miles
    south for an N/S line, miles west for an E/W line); outside them it continues straight."""
    m: float
    c: float
    side: str  # north/south (N/S lines), east/west (E/W lines)
    lo: float = float("-inf")
    hi: float = float("inf")
    rule: int = 1
    road: str = ""


PIECE_REACH = 1.5  # miles: a fitted road piece stands in for its line this far past its own ends


@dataclass
class Family:
    """One family of grid lines (N/S or E/W) and how to resolve each line at each crossing.

    ``model`` (a, b, c, d) is the city grid as one smooth surface — line k sits at
    ``(a + b*k) + (c + d*k) * t`` miles, fit on the city's own mile roads. It captures the grid's
    spacing (~1.006 mi) and its ~1° rotation, which the suburbs keep, so it is both the search prior
    for suburban roads and the rule-3 line where no road is found."""
    vertical: bool  # True: N/S lines (x = m*y + c)
    core: dict[int, tuple[float, float]]
    pieces: dict[int, list[Piece]] = field(default_factory=dict)
    mid: float = 0.0  # cross-axis miles splitting the two sides (the city's middle)
    fitted: set = field(default_factory=set)  # core lines fit to real city roads (not extrapolated)
    model: tuple = (0.0, 1.0, 0.0, 0.0)

    def side(self, t: float) -> str:
        if self.vertical:
            return "north" if t < self.mid else "south"
        return "east" if t < self.mid else "west"

    def _own_piece(self, k: int, t: float):
        cands = [p for p in self.pieces.get(k, []) if p.side == self.side(t)]
        if not cands:
            return None
        inside = [p for p in cands if p.lo <= t < p.hi]
        if inside:
            return inside[0]
        gap = lambda p: min(abs(t - p.lo), abs(t - p.hi))
        nearest = min(cands, key=gap)
        return nearest if gap(nearest) <= PIECE_REACH else None

    def model_line(self, k: int) -> tuple[float, float]:
        """Line k of the model surface, as (m, c) in degrees."""
        a, b, c, d = self.model
        A, B = a + b * k, c + d * k  # across = A + B * t, in miles
        if self.vertical:  # w = A + B*s;  x = ORIGIN_LON - w*MILE_LON;  s = (ORIGIN_LAT - y)/MILE_LAT
            return (MILE_LON * B / MILE_LAT, ORIGIN_LON - MILE_LON * (A + B * ORIGIN_LAT / MILE_LAT))
        return (MILE_LAT * B / MILE_LON, ORIGIN_LAT - MILE_LAT * (A + B * ORIGIN_LON / MILE_LON))

    def resolve(self, k: int, t: float, adjacent: bool):
        """The straight line ``(m, c, rule, source)`` used for line ``k`` at the crossing ``t``."""
        if adjacent and k in self.core:
            return (*self.core[k], 1 if k in self.fitted and self._anchored(k) else 3, "core")
        p = self._own_piece(k, t)
        if p is not None:
            return (p.m, p.c, p.rule, f"piece:{p.road}")
        # rule 3a: between the nearest lines with a road on this side
        with_piece = sorted(i for i in self.pieces if self._own_piece(i, t) is not None)
        below = [i for i in with_piece if i < k]
        above = [i for i in with_piece if i > k]
        if below and above and (above[0] - below[-1]) <= 4:
            a, b = below[-1], above[0]
            pa, pb = self._own_piece(a, t), self._own_piece(b, t)
            w = (k - a) / (b - a)
            return (pa.m + w * (pb.m - pa.m), pa.c + w * (pb.c - pa.c), 3, f"interp:{a}..{b}")
        # rule 3b: the grid model's line. (Not the city fit continued straight: an edge line like
        # Mannheim's is over-tilted and drifts 1+ mi within 20 miles; corners touching the city use
        # the city fit anyway.)
        return (*self.model_line(k), 3, "model")

    def _anchored(self, k: int) -> bool:
        table = ANCHOR_NS if self.vertical else ANCHOR_EW
        return k in table


def fit_model(lines: dict[int, tuple[float, float]], vertical: bool, t_range, *, max_rms: float = 0.08) -> tuple:
    """Least-squares (a, b, c, d) with line k at (a + b*k) + (c + d*k)*t miles, sampled over t_range.
    Robust: lines the surface misses by more than ``max_rms`` miles (Mannheim's over-tilted fit, a
    curving edge road) are dropped and the surface refit, so they can't skew the rotation."""
    import numpy as np

    samples = {}
    for k, (m, c) in lines.items():
        ts = np.linspace(t_range[0], t_range[1], 9)
        if vertical:
            across = (ORIGIN_LON - (m * (ORIGIN_LAT - ts * MILE_LAT) + c)) / MILE_LON
        else:
            across = (ORIGIN_LAT - (m * (ORIGIN_LON - ts * MILE_LON) + c)) / MILE_LAT
        samples[k] = (ts, across)
    keep = set(samples)
    for _ in range(5):
        rows = np.array([[1.0, k, t, k * t] for k in keep for t in samples[k][0]])
        vals = np.concatenate([samples[k][1] for k in keep])
        coef, *_ = np.linalg.lstsq(rows, vals, rcond=None)
        rms = {k: float(np.sqrt(np.mean((samples[k][1] - (coef[0] + coef[1] * k + (coef[2] + coef[3] * k) * samples[k][0])) ** 2)))
               for k in keep}
        worst = max(rms, key=rms.get)
        if rms[worst] <= max_rms or len(keep) <= 4:
            break
        keep.discard(worst)
    return tuple(float(v) for v in coef)


def load_anchors(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path) as fh:
        return json.load(fh)


def build_families(ns_all, ew_all, anchors: dict[str, Any], city) -> tuple[Family, Family]:
    """Assemble both line families from the city fits and the regional anchors file.

    Core lines are all of the city's fitted lines (including the ones extrapolated past the last city
    anchor — they bound real city cells at O'Hare and the far SE). An anchor marked ``"core": false`` — e.g. Howard, a
    half-mile street that bounds the city — and every city line beyond it on that side drop out of
    the core, so the regional road (Oakton) takes that index outside the city."""
    from chigrid import miles_south, miles_west

    def _family(all_fits, table, key, vertical, mid, t_range):
        core = dict(all_fits)
        spec = (anchors or {}).get(key, {})
        for k_str, entry in spec.items():
            k = int(k_str)
            if entry.get("core") is False:
                for i in list(core):
                    if (k > 0 and i >= k) or (k < 0 and i <= k) or i == k:
                        core.pop(i, None)
        fitted = {k for k in core if min(table) <= k <= max(table)}
        pieces = {}
        for k_str, entry in spec.items():
            ps = [Piece(m=p["slope"], c=p["intercept"], side=p["side"], lo=p.get("from_mi", float("-inf")),
                        hi=p.get("to_mi", float("inf")), rule=p.get("rule", 1), road=p.get("road", ""))
                  for p in entry.get("pieces", [])]
            if ps:
                pieces[int(k_str)] = sorted(ps, key=lambda p: p.lo)
        model = fit_model({k: core[k] for k in fitted}, vertical, t_range) if len(fitted) >= 3 else (0.0, 1.0, 0.0, 0.0)
        return Family(vertical=vertical, core=core, pieces=pieces, mid=mid, fitted=fitted, model=model)

    min_lon, min_lat, max_lon, max_lat = city.bounds
    mid_s = (miles_south(max_lat) + miles_south(min_lat)) / 2
    mid_w = (miles_west(max_lon) + miles_west(min_lon)) / 2
    s_rng = (miles_south(max_lat), miles_south(min_lat))
    w_rng = (miles_west(max_lon), miles_west(min_lon))
    return (_family(ns_all, ANCHOR_NS, "ns", True, mid_s, s_rng),
            _family(ew_all, ANCHOR_EW, "ew", False, mid_w, w_rng))


def _intersect(ns_mc, ew_mc):
    m1, c1 = ns_mc[:2]
    m2, c2 = ew_mc[:2]
    x = (m1 * c2 + c1) / (1 - m1 * m2)
    return x, m2 * x + c2


def solve_nodes(ns: Family, ew: Family, i_rng, j_rng, adjacent=None):
    """Corners {(i, j): (x, y, ns_rule, ew_rule)} for N/S lines i and E/W lines j. ``adjacent`` is a
    set of corners that touch a city quad (core pieces apply there)."""
    nodes = {}
    for i in range(i_rng[0], i_rng[1] + 1):
        for j in range(j_rng[0], j_rng[1] + 1):
            adj = adjacent is None or (i, j) in adjacent
            a = ns.resolve(i, j, adj)
            b = ew.resolve(j, i, adj)
            if a is None or b is None:
                continue
            x, y = _intersect(a, b)
            nodes[(i, j)] = (x, y, a[2], b[2])
    return nodes


def lattice_quads(nodes) -> dict[tuple[int, int], Any]:
    from shapely.geometry import Polygon

    quads = {}
    for (i, j) in nodes:
        corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
        if all(c in nodes for c in corners):
            quads[(i, j)] = Polygon([nodes[c][:2] for c in corners])
    return quads


def check_lattice(nodes, *, min_gap=None, max_gap=None, max_offset=None, quads=None) -> list[str]:
    """Problems with the corner lattice (empty = it tiles cleanly). Lines are straight between corners,
    so spacing, offset and winding checked at the corners is exact. ``quads`` restricts the check to
    the corners of those quads (e.g. the ones holding land), so open water can't fail a build."""
    if quads is not None:
        keep = {(a + di, b + dj) for a, b in quads for di in (0, 1) for dj in (0, 1)}
        nodes = {k: v for k, v in nodes.items() if k in keep}
    lim = dict(LATTICE_LIMITS)
    lim.update({k: v for k, v in (("min_gap", min_gap), ("max_gap", max_gap), ("max_offset", max_offset)) if v is not None})
    problems = []
    for (i, j), (x, y, *_) in nodes.items():
        w = (ORIGIN_LON - x) / MILE_LON
        s = (ORIGIN_LAT - y) / MILE_LAT
        if abs(w - i) > lim["max_offset"] or abs(s - j) > lim["max_offset"]:
            problems.append(f"corner {i},{j} is {w - i:+.2f} mi W / {s - j:+.2f} mi S off its nominal position")
        for di, dj in ((1, 0), (0, 1)):
            nb = nodes.get((i + di, j + dj))
            if nb is None:
                continue
            gap = (((nb[0] - x) / MILE_LON) ** 2 + ((nb[1] - y) / MILE_LAT) ** 2) ** 0.5
            if not (lim["min_gap"] <= gap <= lim["max_gap"]):
                line = f"E/W line {j}" if di else f"N/S line {i}"
                problems.append(f"{line}: corners {i},{j}→{i + di},{j + dj} are {gap:.2f} mi apart")
    for (i, j), quad in lattice_quads(nodes).items():
        if quads is not None and (i, j) not in quads:
            continue
        if not quad.is_valid:
            problems.append(f"quad {i},{j} is self-intersecting (lines cross)")
        elif not quad.exterior.is_ccw:  # corners run NE→NW→SW→SE (i grows west, j south): CCW
            problems.append(f"quad {i},{j} is wound the wrong way (lines cross)")
    return problems


def _clean(geom):
    """Snap to a 1e-9° grid (~0.1 mm) so a city part and the outside part it continues — computed by
    different overlays — fuse into one polygon instead of touching along a float hairline."""
    import shapely
    from shapely.geometry import MultiPolygon

    geom = shapely.set_precision(geom, 1e-9)
    polys = [p for p in getattr(geom, "geoms", [geom]) if p.geom_type == "Polygon" and p.area > _EPS]
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def lattice_ordinate(a: int, b: int) -> str:
    """The chi_ordinate a quad (a, b) must have: band a west / band b south, 1-indexed."""
    ns = f"{b + 1}S" if b >= 0 else f"{-b}N"
    ew = f"{a + 1}W" if a >= 0 else f"{-a}E"
    return f"χ:{ns}{ew}"


@dataclass
class RegionalGrid:
    drafts: list[dict[str, Any]]  # {"geometry", "properties"} for apply_edges, sorted by chi_id
    problems: list[str]
    stats: dict[str, Any]


def build_regional_grid(city, ns_all, ew_all, city_features, *, region_bounds, anchors=None, land=None,
                        namer=None, pad_miles: float = 2.0) -> RegionalGrid:
    """City cells (verbatim) plus lattice cells over ``region_bounds`` outside the city.

    ``city_features`` are Pass A's features; ``land`` (optional) masks out water; ``namer`` is a
    ``(lat, lon) -> (avenue|None, street|None)`` local namer for cells outside the city."""
    from shapely.ops import unary_union

    from chigrid_frames import frame_index_range

    ns, ew = build_families(ns_all, ew_all, anchors or {}, city)
    s_lo, s_hi, w_lo, w_hi = frame_index_range(region_bounds, pad_miles=pad_miles)
    i_rng, j_rng = (w_lo, w_hi + 1), (s_lo, s_hi + 1)

    # Which quads overlap the city — measured on the core lattice (core pieces wherever they exist).
    prelim = lattice_quads(solve_nodes(ns, ew, i_rng, j_rng, adjacent=None))
    city_quads = {q for q, poly in prelim.items() if poly.intersection(city).area > _EPS}
    adjacent = {(a + di, b + dj) for a, b in city_quads for di in (0, 1) for dj in (0, 1)}
    nodes = solve_nodes(ns, ew, i_rng, j_rng, adjacent=adjacent)
    quads = lattice_quads(nodes)
    outside_land = land.difference(city) if land is not None else None
    # Only quads holding land (outside the city) or touching the city are checked and emitted.
    def _holds_land(poly):
        if outside_land is None:
            return True
        if not poly.is_valid:  # crossed lines: keep it in view so check_lattice reports it
            return poly.envelope.intersects(outside_land)
        return poly.intersection(outside_land).area > _EPS * 10

    relevant = {q for q, poly in quads.items() if q in city_quads or _holds_land(poly)}
    problems = check_lattice(nodes, quads=relevant)

    by_id_city = {f["properties"]["chi_id"]: f for f in city_features}
    # Inside the city, geographic ids (Pass A) must equal lattice ids: check every city face's quad.
    from shapely.geometry import shape
    city_quad_polys = {q: quads[q] for q in city_quads if q in quads}
    city_exceptions: list[str] = []
    for chi_id, feature in by_id_city.items():
        rp = shape(feature["geometry"]).representative_point()
        hits = [q for q, poly in city_quad_polys.items() if poly.covers(rp)]
        if hits and lattice_ordinate(*hits[0]) != feature["properties"]["chi_ordinate"]:
            # The published city grid named a few slivers by geography where the tilted road lattice
            # disagrees (11S6W: 0.004 sq mi west of Cicero). City cells never change, so note it.
            city_exceptions.append(f"{chi_id} (lattice quad {lattice_ordinate(*hits[0]).replace('χ:', '')})")
    outside: dict[str, list] = collections.defaultdict(list)
    quad_of: dict[str, tuple[int, int]] = {}
    rules: dict[str, int] = {}
    for (a, b), quad in quads.items():
        if (a, b) not in relevant:
            continue
        chi_id = lattice_ordinate(a, b).replace("χ:", "")
        quad_of[chi_id] = (a, b)
        if not quad.is_valid:
            continue  # reported by check_lattice; never emit a self-crossing cell
        part = quad.difference(city) if outside_land is None else quad.intersection(outside_land)
        if part.area <= _EPS * 10:
            continue
        outside[chi_id].append(part)
        corner_rules = [r for corner in ((a, b), (a + 1, b), (a + 1, b + 1), (a, b + 1)) for r in nodes[corner][2:]]
        rules[chi_id] = max(corner_rules)

    sqmi = MILE_LAT * MILE_LON
    drafts, merged, noncontiguous = [], 0, []
    for chi_id in sorted(set(by_id_city) | set(outside)):
        city_feature = by_id_city.get(chi_id)
        parts = outside.get(chi_id)
        if city_feature is not None and not parts:
            from shapely.geometry import shape
            drafts.append({"geometry": shape(city_feature["geometry"]), "properties": dict(city_feature["properties"]),
                           "verbatim": city_feature})
            continue
        from shapely.geometry import shape
        out_geom = unary_union(parts)
        geom = out_geom if city_feature is None else unary_union([shape(city_feature["geometry"]), out_geom])
        geom = _clean(geom)
        if city_feature is not None:
            merged += 1
            if geom.geom_type == "MultiPolygon" and len(geom.geoms) > len(getattr(shape(city_feature["geometry"]), "geoms", [0])):
                noncontiguous.append(chi_id)
        quad = quads[quad_of[chi_id]]
        kinds = set()
        if land is not None and quad.difference(land).area > 1e-3 * quad.area:
            kinds.add("shore")
        if city_feature is not None:
            kinds.add("city")
            props = dict(city_feature["properties"])  # keep the city names
        else:
            rp = geom.representative_point()
            name_canon, label_canon = chi_name_and_label(rp.y, rp.x)
            avenue, street = namer(rp.y, rp.x) if namer is not None else (None, None)
            if avenue and street:
                name, label = f"{avenue}–{street} chi", f"{_short(avenue)}{_short(street)}"
            else:
                name, label = name_canon, label_canon
            props = {"chi_id": chi_id, "chi_ordinate": f"χ:{chi_id}", "name": name, "label": label,
                     "name_canon": name_canon, "label_canon": label_canon}
        rp = geom.representative_point()
        area = round(geom.area / sqmi, 3)
        is_edge = "shore" in kinds
        a, b = quad_of[chi_id]
        qc = quad.centroid
        offset = [round(miles_west(qc.x) - (a + 0.5), 2), round(miles_south(qc.y) - (b + 0.5), 2)]
        props.update({"center": [round(rp.x, 6), round(rp.y, 6)], "area_sqmi": area, "edge": is_edge,
                      "flag": (not is_edge) and not (TOL_LO <= area <= TOL_HI),
                      "edge_kind": sorted(kinds), "bounds_rule": rules.get(chi_id, 1), "offset_mi": offset})
        drafts.append({"geometry": geom, "properties": props})
    stats = {"quads": len(quads), "city_quads": len(city_quads), "outside_cells": len(outside),
             "straddling_city": merged, "noncontiguous_merges": noncontiguous, "city_id_exceptions": city_exceptions,
             "index_range": {"ns": list(i_rng), "ew": list(j_rng)}}
    return RegionalGrid(drafts=drafts, problems=problems, stats=stats)


def build_regional_namer(groups, *, exclude=(), name_miles: float = 0.6, min_span_miles: float = 1.0,
                         bin_miles: float = 0.25, skip_classes=("1", "5", "9")):
    """Local namer for cells outside the city, from OSM ``RoadGroup``s (``chigrid_osm.cluster_roads``).

    Returns ``(lat, lon) -> (avenue|None, street|None)``: the nearest N/S and E/W through-roads (span
    ≥ ``min_span_miles``) to the cell centre, within ``name_miles``, never a mile road (``exclude`` —
    base names, e.g. every road in the regional anchors) and never an expressway/ramp/service road.
    Suburban roads drift with the grid's ~1° tilt, so a road is positioned LOCALLY — the median of its
    vertices within a ``bin_miles`` band of the cell's latitude (or longitude) — not by one median."""
    import numpy as np

    from chigrid import _base_road_name, _pretty_road_name

    excluded = {_base_road_name(n) for n in exclude}
    ns_roads, ew_roads = [], []  # (pretty name, {bin: position in miles})
    for g in groups:
        base = _base_road_name(g.name)
        if not base or base in excluded or str(g.road_class) in skip_classes:
            continue
        w = (ORIGIN_LON - np.asarray(g.lon)) / MILE_LON
        s = (ORIGIN_LAT - np.asarray(g.lat)) / MILE_LAT
        vertical = (s.max() - s.min()) >= (w.max() - w.min())
        along, across = (s, w) if vertical else (w, s)
        if along.max() - along.min() < min_span_miles:
            continue
        bins = np.floor(along / bin_miles).astype(int)
        positions = {}
        for b in np.unique(bins):
            positions[int(b)] = float(np.median(across[bins == b]))
        (ns_roads if vertical else ew_roads).append((_pretty_road_name(base), positions))

    def _nearest(roads, along_mi, across_mi):
        b = int(np.floor(along_mi / bin_miles))
        best, found = name_miles, None
        for name, positions in roads:
            for bb in (b, b - 1, b + 1):
                pos = positions.get(bb)
                if pos is not None:
                    if abs(pos - across_mi) < best:
                        best, found = abs(pos - across_mi), name
                    break
        return found

    def namer(lat: float, lon: float):
        w, s = miles_west(lon), miles_south(lat)
        return _nearest(ns_roads, s, w), _nearest(ew_roads, w, s)

    return namer
