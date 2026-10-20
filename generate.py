#!/usr/bin/env python3
"""Generate the chiGRID — Chicago's one-mile "chi" cells — as GeoJSON.

chiGRID tiles Chicago into regular one-mile cells anchored to the city's address-grid
origin at State & Madison. Each cell is bounded by the mile roads and centered on the
half-mile road between them; it carries a formal chi-ordinate (``χ:5S3W``), a public
name (``Damen–43rd chi``), and a compact label (``Dam43``).

This script reproduces the cell GEOMETRY and ORDINATES exactly, and names each cell from
the canonical "secondary" (half-mile) road on its band. The published ``data/chigrid.geojson``
refines those names against Chicago's street-centerline geometry (so the lakefront avenue
reads Broadway/Sheridan where it really does), but the geometry and ordinates are identical.

Usage:
    python generate.py --boundary city_boundary.geojson > chigrid.geojson
    python generate.py --extent un2025 --edges whole --boundary city_boundary.geojson \
        --names-from data/v1/chigrid.geojson > data/v1/un2025/chigrid.geojson

``--boundary`` is any GeoJSON FeatureCollection/Feature/geometry of the area to tile (the
city outline). Requires ``shapely`` (pip install shapely). Without ``--boundary`` it emits
every cell whose center lies in the bounding box of the boundary — pass one for a clean clip.
"""
from __future__ import annotations

import argparse
import json
import math
import sys

# State & Madison — the origin of Chicago's street-address grid.
ORIGIN_LAT = 41.88197
ORIGIN_LON = -87.62768
MILE_LAT = 1.0 / 69.0
MILE_LON = MILE_LAT / math.cos(math.radians(ORIGIN_LAT))

# Canonical half-mile ("secondary") roads — the road that crosses State (E/W streets) or
# Madison (N/S avenues) on each band's mid-mile line, placed at its real geographic band.
# Index = geographic band (cell center at band + 0.5 miles from the origin). "" = no major
# secondary road on that line (e.g. the Loop) -> the cell is named by its ordinate.
AVENUES_W = ["", "Racine", "Damen", "California", "Central Park", "Kostner", "Laramie",
             "Austin", "Oak Park", "Oriole", "Cumberland"]
AVENUES_E = ["Martin Luther King", "Woodlawn", "Jeffery", "Burnham", "Avenue L"]
STREETS_S = ["", "16th", "26th", "35th", "43rd", "51st", "59th", "67th", "75th", "83rd",
             "91st", "99th", "107th", "115th", "123rd", "130th"]
STREETS_N = ["Kinzie", "Division", "Armitage", "Diversey", "Addison", "Montrose", "Foster",
             "Peterson", "Pratt", "Howard"]


def miles_south(lat: float) -> float:
    return (ORIGIN_LAT - lat) / MILE_LAT


def miles_west(lon: float) -> float:
    return (ORIGIN_LON - lon) / MILE_LON


def chi_ordinate(center_lat: float, center_lon: float) -> str:
    # 1-indexed, number-first: the cells touching the origin are 1S1W / 1S1E / 1N1W / 1N1E.
    s, w = miles_south(center_lat), miles_west(center_lon)
    ns = f"{int(math.floor(abs(s) + 1e-9)) + 1}{'S' if s >= 0 else 'N'}"
    ew = f"{int(math.floor(abs(w) + 1e-9)) + 1}{'W' if w >= 0 else 'E'}"
    return f"χ:{ns}{ew}"


def _short(name: str) -> str:
    head = name.split()[0]
    return "".join(c for c in head if c.isdigit()) if head[0].isdigit() else head[:3]


def _road(table: list, band: int) -> str:
    return table[band] if 0 <= band < len(table) else ""


def chi_name_and_label(center_lat: float, center_lon: float):
    s, w = miles_south(center_lat), miles_west(center_lon)
    avenue = _road(AVENUES_W if w >= 0 else AVENUES_E, int(math.floor(abs(w) + 1e-9)))
    street = _road(STREETS_S if s >= 0 else STREETS_N, int(math.floor(abs(s) + 1e-9)))
    ordinate = chi_ordinate(center_lat, center_lon)
    if avenue and street:
        return f"{avenue}–{street} chi", f"{_short(avenue)}{_short(street)}"
    return f"{ordinate} chi", ordinate


def _cell_ring(center_lat: float, center_lon: float):
    dlat, dlon = MILE_LAT / 2, MILE_LON / 2
    n, s, e, w = center_lat + dlat, center_lat - dlat, center_lon + dlon, center_lon - dlon
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


def build_chigrid(boundary) -> dict:
    geom = clip_geom = None
    if boundary is not None:
        from shapely.geometry import shape
        from shapely.ops import unary_union
        feats = boundary["features"] if boundary.get("type") == "FeatureCollection" else [boundary]
        clip_geom = unary_union([shape(f.get("geometry", f)).buffer(0) for f in feats if f])
        min_lon, min_lat, max_lon, max_lat = clip_geom.bounds
    else:  # whole Chicago bounding box if no boundary supplied
        min_lat, max_lat, min_lon, max_lon = 41.62, 42.06, -87.95, -87.50

    s_lo = math.floor(miles_south(max_lat) - 0.5) - 1
    s_hi = math.ceil(miles_south(min_lat) - 0.5) + 1
    w_lo = math.floor(miles_west(max_lon) - 0.5) - 1
    w_hi = math.ceil(miles_west(min_lon) - 0.5) + 1

    features, seen = [], set()
    Polygon = None
    if clip_geom is not None:
        from shapely.geometry import Polygon
    for ks in range(s_lo, s_hi + 1):
        clat = ORIGIN_LAT - (0.5 + ks) * MILE_LAT
        for kw in range(w_lo, w_hi + 1):
            clon = ORIGIN_LON - (0.5 + kw) * MILE_LON
            ring = _cell_ring(clat, clon)
            if clip_geom is not None and not Polygon(ring).intersects(clip_geom):
                continue
            ordinate = chi_ordinate(clat, clon)
            if ordinate in seen:
                continue
            seen.add(ordinate)
            name, label = chi_name_and_label(clat, clon)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "chi_id": ordinate.replace("χ:", ""),
                    "chi_ordinate": ordinate,
                    "name": name,
                    "label": label,
                    "center": [round(clon, 6), round(clat, 6)],
                },
            })
    features.sort(key=lambda f: f["properties"]["chi_id"])
    return {
        "type": "FeatureCollection",
        "properties": {"system": "chiGRID", "origin": "State & Madison", "cell_miles": 1, "count": len(features)},
        "features": features,
    }


def build_framed_chigrid(frame, *, edges="whole", min_share=0.5, city=None, land=None,
                         include_city=False, names=None, provenance=None, extent=None) -> dict:
    """v1 squares over a framed extent (the UN 2025 or JRC 2023 urban centre). Squares stay whole
    perfect miles in ``whole`` mode; ``clip`` cuts them at the frame. Shares are measured on each
    square's land part when ``land`` is given, so lakefront squares aren't penalised for water.
    ``names`` ({chi_id: (name, label)}) overrides the band-table names, e.g. the published city names."""
    from shapely.geometry import Polygon

    from chigrid_frames import apply_edges, frame_index_range

    bounds = frame.union(city).bounds if (include_city and city is not None) else frame.bounds
    s_lo, s_hi, w_lo, w_hi = frame_index_range(bounds, pad_miles=1)
    drafts = []
    for ks in range(s_lo, s_hi + 1):
        clat = ORIGIN_LAT - (0.5 + ks) * MILE_LAT
        for kw in range(w_lo, w_hi + 1):
            clon = ORIGIN_LON - (0.5 + kw) * MILE_LON
            square = Polygon(_cell_ring(clat, clon))
            part = square.intersection(land) if land is not None else square
            if part.is_empty:
                continue
            ordinate = chi_ordinate(clat, clon)
            chi_id = ordinate.replace("χ:", "")
            name, label = (names or {}).get(chi_id) or chi_name_and_label(clat, clon)
            drafts.append({"square": square, "geometry": part, "properties": {
                "chi_id": chi_id, "chi_ordinate": ordinate, "name": name, "label": label,
                "center": [round(clon, 6), round(clat, 6)]}})
    features = apply_edges(drafts, frame, mode=edges, min_share=min_share, city=city, include_city=include_city)
    if edges != "clip":  # whole/any keep the perfect square, not just its land part
        squares = {d["properties"]["chi_id"]: d["square"] for d in drafts}
        for f in features:
            f["geometry"] = {"type": "Polygon", "coordinates": [[list(p) for p in squares[f["properties"]["chi_id"]].exterior.coords]]}
    props = {"system": "chiGRID", "origin": "State & Madison", "cell_miles": 1, "extent": extent,
             "frame_version": (provenance or {}).get("release"), "frame_sha256": (provenance or {}).get("sha256"),
             "edge_mode": edges, "min_share": min_share if edges == "whole" else None,
             "include_city": include_city, "count": len(features)}
    return {"type": "FeatureCollection", "properties": props, "features": features}


EXTENTS = ("city", "un2025", "jrc2023")


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="Generate the chiGRID as GeoJSON.")
    parser.add_argument("--boundary", help="GeoJSON of the city outline (the city extent tiles it)")
    parser.add_argument("--extent", choices=EXTENTS, default="city",
                        help="city limits (default), or the UN 2025 / JRC 2023 urban centre")
    parser.add_argument("--frame", help="frame GeoJSON (default data/frames/<extent>.geojson)")
    parser.add_argument("--edges", choices=("whole", "clip"), help="framed extents: keep whole cells or clip at the frame")
    parser.add_argument("--min-share", type=float, default=0.5, help="whole mode: least land share inside the frame")
    parser.add_argument("--include-city", action="store_true", help="also keep city cells the frame leaves out")
    parser.add_argument("--land", help="land mask GeoJSON (default data/frames/land.geojson if present)")
    parser.add_argument("--names-from", help="published chigrid.geojson whose names/labels to keep by chi_id")
    args = parser.parse_args()
    boundary = json.load(open(args.boundary)) if args.boundary else None
    if args.extent == "city":
        if args.edges or args.frame or args.include_city:
            parser.error("--edges/--frame/--include-city apply to the framed extents (un2025, jrc2023)")
        json.dump(build_chigrid(boundary), sys.stdout, ensure_ascii=False, indent=1)
        return
    if not args.edges:
        parser.error(f"--extent {args.extent} needs --edges whole|clip")
    from chigrid_frames import _geometry, load_frame, load_land

    here = os.path.dirname(os.path.abspath(__file__))
    frame, provenance = load_frame(args.frame or os.path.join(here, "data", "frames", f"{args.extent}.geojson"))
    land_path = args.land or os.path.join(here, "data", "frames", "land.geojson")
    land = load_land(land_path if os.path.exists(land_path) else None)
    city = _geometry(boundary) if boundary else None
    names = None
    if args.names_from:
        names = {f["properties"]["chi_id"]: (f["properties"]["name"], f["properties"]["label"])
                 for f in json.load(open(args.names_from))["features"]}
    grid = build_framed_chigrid(frame, edges=args.edges, min_share=args.min_share, city=city, land=land,
                                include_city=args.include_city, names=names, provenance=provenance,
                                extent=args.extent)
    json.dump(grid, sys.stdout, ensure_ascii=False, indent=1)
    print(f"wrote {grid['properties']['count']} cells", file=sys.stderr)


if __name__ == "__main__":
    main()
