#!/usr/bin/env python3
"""Generate the χGRID — Chicago's one-mile "chi" cells — as GeoJSON.

χGRID tiles Chicago into regular one-mile cells anchored to the city's address-grid
origin at State & Madison. Each cell is bounded by the mile roads and centered on the
half-mile road between them; it carries a formal chi-ordinate (``χ:S4W2``), a public
name (``Damen–43rd chi``), and a compact label (``Dam43``).

This script reproduces the cell GEOMETRY and ORDINATES exactly, and names each cell from
the canonical "secondary" (half-mile) road on its band. The published ``data/chigrid.geojson``
refines those names against Chicago's street-centerline geometry (so the lakefront avenue
reads Broadway/Sheridan where it really does), but the geometry and ordinates are identical.

Usage:
    python generate.py --boundary city_boundary.geojson > chigrid.geojson

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
    s, w = miles_south(center_lat), miles_west(center_lon)
    ns = f"{'S' if s >= 0 else 'N'}{int(math.floor(abs(s) + 1e-9))}"
    ew = f"{'W' if w >= 0 else 'E'}{int(math.floor(abs(w) + 1e-9))}"
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
        "properties": {"system": "χGRID", "origin": "State & Madison", "cell_miles": 1, "count": len(features)},
        "features": features,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the χGRID as GeoJSON.")
    parser.add_argument("--boundary", help="GeoJSON of the area to tile (e.g. the city outline)")
    args = parser.parse_args()
    boundary = json.load(open(args.boundary)) if args.boundary else None
    json.dump(build_chigrid(boundary), sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
