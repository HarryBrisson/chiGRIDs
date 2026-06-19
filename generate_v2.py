#!/usr/bin/env python3
"""Generate the **v2 (street-aligned)** chiGRID — cells bounded by Chicago's real mile-road
centerlines, clipped to the city limit.

    python generate_v2.py --boundary city_boundary.geojson \
                          --centerlines street_centerlines.geojson > data/v2/chigrid.geojson

Inputs (both public data from the Chicago data portal, https://data.cityofchicago.org/):
  --boundary      a GeoJSON FeatureCollection of the city / community-area polygons (the cells are
                  clipped to this; interior holes such as cemeteries and rail yards are filled).
  --centerlines   the street-center-lines GeoJSON, features carrying STREET_NAME and CLASS. This file
                  is large (~100 MB) so it is not bundled in this repo — fetch it from the portal.

The geometry, ordinates, and naming rules live in chigrid_streets.py / chigrid.py (vendored here so
this script is self-contained). See the README for the methodology.
"""
import argparse
import json
import sys

from chigrid_streets import build_street_aligned_chigrid


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the v2 street-aligned chiGRID.")
    ap.add_argument("--boundary", required=True, help="city/community-area boundary GeoJSON")
    ap.add_argument("--centerlines", required=True, help="street-centerline GeoJSON (FeatureCollection)")
    args = ap.parse_args()

    with open(args.boundary) as fh:
        boundary = json.load(fh)
    with open(args.centerlines) as fh:
        centerlines = json.load(fh)
        if isinstance(centerlines, dict) and "data" in centerlines and "features" not in centerlines:
            centerlines = centerlines["data"]  # some portal exports wrap the FeatureCollection in {"data": ...}

    grid = build_street_aligned_chigrid(boundary, centerlines)
    json.dump(grid, sys.stdout, ensure_ascii=False)
    print(f"wrote {grid['properties']['count']} cells", file=sys.stderr)


if __name__ == "__main__":
    main()
