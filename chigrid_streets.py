"""Street-aligned chiGRID: cells bounded by the real mile-road centerlines.

Rule-based construction (Harry's design):
  1. Divide the city using established MILE roads.
  2. Where a mile road is missing, substitute another road broadly aligned with that grid line.
  3. Where no road is aligned, impute a line consistent with the grid (interpolate/extrapolate the
     neighboring anchors' position + slope), so every grid index gets a line and there are no gaps.
  4. Each interior cell should be 0.9-1.1 sq mi; out-of-tolerance interior cells are flagged.

Anchors are keyed by signed mile index (W/S positive, E/N negative). Each anchor mile road is fit to
a clean straight line; the present anchors define the grid's spacing and tilt, and missing indices are
filled by rule 2 then rule 3. Polygonizing the completed line set against the city boundary yields the
cells; edge cells are the (partial) pieces clipped to the city limit.
"""
from __future__ import annotations

import collections
from typing import Any

from chigrid import (
    MILE_LAT,
    MILE_LON,
    _base_road_name,
    _short,
    build_road_namer,
    chi_name_and_label,
    chi_ordinate,
    miles_south,
    miles_west,
)

# Established mile roads (cell LIMITS), by signed mile index. W/S positive, E/N negative.
ANCHOR_NS = {
    0: "state", 1: "halsted", 2: "ashland", 3: "western", 4: "kedzie", 5: "pulaski", 6: "cicero",
    7: "central", 8: "narragansett", 9: "harlem", 11: "east river", 13: "mannheim",
    -1: "cottage grove", -2: "stony island", -3: "yates", -4: "burley",  # far-SE avenues (Burley = long SE edge)
}
ANCHOR_EW = {
    0: "madison", -1: "chicago", -2: "north", -3: "fullerton", -4: "belmont", -5: "irving park",
    -6: "lawrence", -7: "bryn mawr", -8: "devon", -9: "touhy", -10: "howard",
    1: "roosevelt", 2: "cermak", 3: "31st", 4: "pershing", 5: "47th", 6: "garfield", 7: "63rd",
    8: "71st", 9: "79th", 10: "87th", 11: "95th", 12: "103rd", 13: "111th", 14: "119th",
    15: "127th", 16: "135th",
}
TOL_LO, TOL_HI = 0.9, 1.1  # interior-cell area tolerance (sq mi)

# Avenues that curve away from the grid: fit ONLY their straight run (a north..south mile window) and
# let the straight extrapolation cut a clean line past the bend, rather than letting the curve drag the
# whole line (which mispositions cells even well away from the bend). Harry's rule: "follow the road to
# its curve, then continue straight." Stony Island bends east at ~99th (fit to 12mi); Cottage Grove
# bends at ~95th AND wanders near its 35th-St north end, so fit just the 35th..95th run (4..11mi).
# name -> (north_mi | None, south_mi | None) bounds of the fit window.
ANCHOR_FIT_CLIP = {"stony island": (None, 12.0), "cottage grove": (4.0, 11.0)}

# E/W mile roads run on out into the suburbs (Irving Park, Belmont, Lawrence, Devon...), and those
# far-west fragments — near O'Hare especially — jog wildly and skew the straight fit's slope, so the
# boundary wobbles across the whole row. Fit every E/W road on its in-Chicago run only (the city's
# contiguous footprint ends ~W11-12; the O'Hare panhandle past that has its own region grid).
CITY_WEST_FIT_CAP = 12.0
# Per-road tighter west caps for E/W roads that curve before the city's west edge: Bryn Mawr runs
# straight to ~W9 then bends south at W10-11, dragging its fit 0.06mi south at Austin (W8) so the
# border slips past the real street. Fit just its straight run so the boundary sits on Bryn Mawr
# (south-of-Bryn-Mawr addresses land in the row below). name -> mile-west cap.
ANCHOR_FIT_CLIP_WEST = {"bryn mawr": 9.5}


def _road_points(street_centerlines, names):
    from shapely.geometry import shape

    keep = set(names)
    pts: dict[str, list] = collections.defaultdict(list)
    for feature in (street_centerlines or {}).get("features", []):
        base = _base_road_name((feature.get("properties") or {}).get("STREET_NAME"))
        if base not in keep or not feature.get("geometry"):
            continue
        try:
            geom = shape(feature["geometry"])
        except Exception:
            continue
        coords = list(geom.coords) if geom.geom_type == "LineString" else [
            p for line in getattr(geom, "geoms", []) for p in line.coords
        ]
        pts[base].extend(coords)
    return pts


def _fit_anchors(anchors, pts, vertical, clip=None, clip_west=None, clip_west_overrides=None):
    """Fit each present anchor to its OWN straight line (slope, intercept) so the grid line follows
    the real road: vertical avenues x=m*y+c, streets y=m*x+c. For anchors in ``clip`` (name ->
    (north_mi, south_mi)), fit only the points inside that mile-south window (the straight run, before
    the road curves); ``clip_west`` (a mile-west cap, per-road overrides in ``clip_west_overrides``)
    fits E/W roads only on their straight in-city run, dropping wild far-west fragments/curves."""
    import numpy as np

    fits = {}
    for index, name in anchors.items():
        coords = pts.get(name)
        if not coords or len(coords) < 6:
            continue
        arr = np.array(coords)
        if clip and name in clip:
            north, south = clip[name]
            ms = miles_south(arr[:, 1])
            mask = np.ones(len(arr), dtype=bool)
            if north is not None:
                mask &= ms >= north
            if south is not None:
                mask &= ms <= south
            if mask.sum() >= 6:  # keep just the straight run; ignore the curve (and any wandering end)
                arr = arr[mask]
        wcap = (clip_west_overrides or {}).get(name, clip_west)
        if wcap is not None:
            clipped = arr[miles_west(arr[:, 0]) <= wcap]
            if len(clipped) >= 6:  # in-city straight run only; drop far-west fragments/curves
                arr = clipped
        if vertical:
            fits[index] = tuple(float(v) for v in np.polyfit(arr[:, 1], arr[:, 0], 1))
        else:
            fits[index] = tuple(float(v) for v in np.polyfit(arr[:, 0], arr[:, 1], 1))
    return fits


def _complete(fits):
    """Fill every integer index lo..hi by interpolating the (slope, intercept) between anchors.
    Returns ({index: (slope, intercept)}, lo, hi); past the ends the caller extrapolates straight."""
    if not fits:
        return {}, None, None
    ks = sorted(fits)
    lo_k, hi_k = ks[0], ks[-1]
    out = dict(fits)
    for k in range(lo_k, hi_k + 1):
        if k in out:
            continue
        below = max(i for i in ks if i < k)
        above = min(i for i in ks if i > k)
        t = (k - below) / (above - below)
        out[k] = tuple(fits[below][d] + t * (fits[above][d] - fits[below][d]) for d in (0, 1))
    return out, lo_k, hi_k


def build_street_aligned_chigrid(boundary, street_centerlines, road_grid=None, *, extend=4) -> dict[str, Any]:
    import numpy as np
    from shapely.geometry import LineString, shape
    from shapely.ops import polygonize, unary_union

    feats = boundary["features"] if boundary.get("type") == "FeatureCollection" else [boundary]
    city = unary_union([shape(f.get("geometry", f)).buffer(0) for f in feats if f])
    # Close the tiny seams/gaps between the 77 community-area polygons (they leave ~150 spurious
    # internal holes), so only the true city limit clips the grid — otherwise CA seams fragment cells
    # and explode the "edge" count.
    city = city.buffer(0.0006).buffer(-0.0006)
    # Fill interior holes: the community-area polygons omit some Chicago land (cemeteries with city
    # addresses like Mount Greenwood, rail yards) which would otherwise punch donut carveouts into
    # cells. We care about the whole city, so treat fully-enclosed gaps as Chicago (outer limit kept).
    from shapely.geometry import MultiPolygon, Polygon
    def _fill(geom):
        if geom.geom_type == "Polygon":
            return Polygon(geom.exterior)
        return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
    city = _fill(city)
    min_lon, min_lat, max_lon, max_lat = city.bounds

    ns_fits = _fit_anchors(ANCHOR_NS, _road_points(street_centerlines, ANCHOR_NS.values()), vertical=True,
                           clip=ANCHOR_FIT_CLIP)
    ew_fits = _fit_anchors(ANCHOR_EW, _road_points(street_centerlines, ANCHOR_EW.values()), vertical=False,
                           clip_west=CITY_WEST_FIT_CAP, clip_west_overrides=ANCHOR_FIT_CLIP_WEST)
    # Each line follows its OWN road (its fitted slope+intercept), so a real mile road is a real cell
    # boundary (e.g. Irving Park bounds the Addison row exactly). Missing indices interpolate between
    # neighbors; a curving road is fit to a straight line so it still can't break the grid.
    ns_all, ns_lo, ns_hi = _complete(ns_fits)
    ew_all, ew_lo, ew_hi = _complete(ew_fits)

    # Rule 3: extrapolate (slope, intercept) past the outermost anchors at constant spacing (straight cut).
    # step = the outward per-index increment (from the 2nd-outermost anchor to the outermost); each
    # further index continues in that same direction. NOTE: it's `+ n*step`, not `+ d*n*step` — folding
    # d into the multiply inverts the low (east/north) end and plants stray lines back over the grid.
    def _extrapolate(allfits, lo, hi):
        for d, edge in ((-1, lo), (1, hi)):
            step = tuple(allfits[edge][i] - allfits[edge - d][i] for i in (0, 1))
            for n in range(1, extend + 1):
                allfits[edge + d * n] = tuple(allfits[edge][i] + n * step[i] for i in (0, 1))
    _extrapolate(ns_all, ns_lo, ns_hi)
    _extrapolate(ew_all, ew_lo, ew_hi)

    lines = [LineString([(m * y + c, y) for y in (min_lat - 0.06, max_lat + 0.06)]) for m, c in ns_all.values()]
    lines += [LineString([(x, m * x + c) for x in (min_lon - 0.06, max_lon + 0.06)]) for m, c in ew_all.values()]

    faces = [p for p in polygonize(unary_union(lines + [city.boundary])) if p.representative_point().within(city)]
    namer = build_road_namer(street_centerlines, road_grid)
    sqmi = MILE_LAT * MILE_LON

    # Every in-city face belongs to a cell — group by ordinate and MERGE (don't drop), so a cell split
    # into pieces by the lakefront/boundary stays one cell and the map tiles completely (no gaps).
    grouped: dict[str, list] = collections.defaultdict(list)
    for face in faces:
        c = face.representative_point()
        grouped[chi_ordinate(c.y, c.x)].append(face)
    by_ordinate = {o: unary_union(fs) for o, fs in grouped.items()}

    features = []
    for ordinate, face in by_ordinate.items():
        c = face.representative_point()
        # Two parallel names per cell:
        #  LOCAL  — the real arterials nearest this cell (Broadway/Sheridan on the lakefront, where the
        #           canonical Racine/Clark don't physically reach); honest about what actually bounds it.
        #  CANON  — the idealized mile-grid center roads by ordinate band (Racine–Peterson even where
        #           those streets don't run that far); predictable + consistent across the whole grid.
        avenue = street = None
        if namer is not None:
            avenue, street = namer(c.y, c.x)
        if avenue and street:
            name_local, label_local = f"{avenue}–{street} chi", f"{_short(avenue)}{_short(street)}"
        else:
            name_local, label_local = chi_name_and_label(c.y, c.x)  # no real road here: best available
        name_canon, label_canon = chi_name_and_label(c.y, c.x)
        area = round(face.area / sqmi, 3)
        is_edge = city.boundary.distance(face) <= 1e-5 or not face.buffer(-1e-6).within(city)
        geom = face.__geo_interface__
        features.append({
            "type": "Feature",
            "geometry": {"type": geom["type"], "coordinates": geom["coordinates"]},
            "properties": {
                "chi_id": ordinate.replace("χ:", ""),
                "chi_ordinate": ordinate,
                "name": name_local,
                "label": label_local,
                "name_canon": name_canon,
                "label_canon": label_canon,
                "center": [round(c.x, 6), round(c.y, 6)],
                "area_sqmi": area,
                "edge": is_edge,
                "flag": (not is_edge) and not (TOL_LO <= area <= TOL_HI),
            },
        })
    features.sort(key=lambda f: f["properties"]["chi_id"])
    return {
        "type": "FeatureCollection",
        "properties": {"system": "chiGRID", "origin": "State & Madison", "aligned": "mile_roads",
                       "edges": "straight_fit", "count": len(features)},
        "features": features,
    }
