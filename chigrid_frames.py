"""Extents ("frames") for the chiGRID: which Chicago the grid tiles.

The city extent clips cells to the city limit. The UN extents take a published urban-centre polygon
(``data/frames/un2025.geojson`` — UN WUP 2025, or ``jrc2023.geojson`` — JRC GHS-SMOD R2023A) as the
footprint, and decide each cell's membership by one of three edge rules:

  any    keep a cell whose square touches the frame (v1's city rule)
  whole  keep the whole cell when at least ``min_share`` of its land lies inside the frame
  clip   cut the cell at the frame boundary (UN edges are a staircase of 1-km squares, so clipped
         cells step)

Ids, origin and geometry are untouched by the choice of frame: a cell is the same cell in every
extent, and the frame only decides whether (and how much of) it is kept. Shares are area ratios in
degrees, which is exact enough at a cell's scale (cos-latitude varies <0.03% across a mile).
"""
from __future__ import annotations

import json
import math
from typing import Any

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON

EDGE_MODES = ("any", "whole", "clip")
_AREA_EPS = 1e-12  # deg²; slivers below this are float noise from overlays


def _geometry(obj: Any):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    if hasattr(obj, "geom_type"):
        return obj
    if obj.get("type") == "FeatureCollection":
        return unary_union([shape(f["geometry"]).buffer(0) for f in obj["features"] if f.get("geometry")])
    if obj.get("type") == "Feature":
        return shape(obj["geometry"]).buffer(0)
    return shape(obj).buffer(0)


def load_frame(path: str):
    """A frame GeoJSON -> (shapely geometry, provenance dict)."""
    with open(path) as fh:
        data = json.load(fh)
    props = data.get("properties") or {}
    if not props and data.get("features"):
        props = data["features"][0].get("properties") or {}
    return _geometry(data), props.get("provenance", props)


def load_land(path: str | None):
    """The land mask (state polygons cut at the shoreline), or None for no mask."""
    if not path:
        return None
    with open(path) as fh:
        return _geometry(json.load(fh))


def frame_index_range(bounds, *, pad_miles: float = 2.0) -> tuple[int, int, int, int]:
    """Signed mile-band ranges ``(s_lo, s_hi, w_lo, w_hi)`` covering ``bounds`` (lon/lat) plus
    ``pad_miles``. Band k spans k..k+1 miles south (negative = north) / west (negative = east) of
    State & Madison, so its cell has ordinate floor-index k."""
    min_lon, min_lat, max_lon, max_lat = bounds
    s_lo = math.floor((ORIGIN_LAT - max_lat) / MILE_LAT - pad_miles)
    s_hi = math.ceil((ORIGIN_LAT - min_lat) / MILE_LAT + pad_miles)
    w_lo = math.floor((ORIGIN_LON - max_lon) / MILE_LON - pad_miles)
    w_hi = math.ceil((ORIGIN_LON - min_lon) / MILE_LON + pad_miles)
    return s_lo, s_hi, w_lo, w_hi


def share(geom, region) -> float:
    """Fraction of ``geom``'s area inside ``region`` (0 for an empty/zero-area geom or no region)."""
    if region is None or geom.is_empty or geom.area <= _AREA_EPS:
        return 0.0
    return geom.intersection(region).area / geom.area


def apply_edges(drafts, frame, *, mode: str = "whole", min_share: float = 0.5, city=None,
                include_city: bool = False) -> list[dict[str, Any]]:
    """Decide membership for candidate cells and emit GeoJSON features.

    ``drafts``: iterable of ``{"geometry": shapely geom (the cell's full land part), "properties":
    dict}``. ``properties`` must carry ``chi_id`` and may carry ``edge``/``edge_kind`` from the build;
    ``frame_share``, ``in_city_share``, ``edge`` and ``edge_kind`` are (re)set here.
    ``include_city`` keeps city cells the frame excludes (the UN 2025 frame leaves out O'Hare and
    Lake Calumet), clipping in ``clip`` mode to frame ∪ city."""
    if mode not in EDGE_MODES:
        raise ValueError(f"edge mode must be one of {EDGE_MODES}, not {mode!r}")
    keep_region = frame.union(city) if (include_city and city is not None) else frame
    out = []
    for draft in drafts:
        geom, props = draft["geometry"], dict(draft["properties"])
        if geom.is_empty:
            continue
        f_share, c_share = share(geom, frame), share(geom, city)
        if mode == "any":
            keep = geom.intersects(keep_region)
        elif mode == "whole":
            keep = f_share >= min_share or (include_city and c_share > 0)
        else:
            geom_kept = geom.intersection(keep_region)
            keep = geom_kept.area > _AREA_EPS
        if not keep:
            continue
        kinds = set(props.get("edge_kind") or [])
        if mode == "clip":
            if geom_kept.area < geom.area - 1e-9 * max(geom.area, 1e-6):
                kinds.add("frame")
                if "area_sqmi" in props:  # a clipped cell is an edge cell: re-measure, never flag
                    props["area_sqmi"] = round(geom_kept.area / (MILE_LAT * MILE_LON), 3)
                if "flag" in props:
                    props["flag"] = False
            geom = _polygonal(geom_kept)
        elif f_share < 1.0 - 1e-9 and not (include_city and c_share >= 1.0 - 1e-9):
            kinds.add("frame")  # a whole cell straddling the frame line
        props["frame_share"] = round(f_share, 3)
        props["in_city_share"] = round(c_share, 3)
        props["edge_kind"] = sorted(kinds)
        props["edge"] = bool(props.get("edge")) or (mode == "clip" and "frame" in kinds)
        g = geom.__geo_interface__
        out.append({"type": "Feature", "geometry": {"type": g["type"], "coordinates": g["coordinates"]},
                    "properties": props})
    out.sort(key=lambda f: f["properties"]["chi_id"])
    return out


def _polygonal(geom):
    """Drop the points/lines an intersection can leave on a shared border; keep (Multi)Polygon."""
    from shapely.geometry import MultiPolygon

    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon" and g.area > _AREA_EPS]
    polys += [p for g in getattr(geom, "geoms", []) if g.geom_type == "MultiPolygon" for p in g.geoms]
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)
