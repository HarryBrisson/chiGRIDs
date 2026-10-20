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

# Top-level properties of the published data/v2/chigrid.geojson (city extent).
V2_CITY_PROPS = {"system": "chiGRID", "version": "v2", "variant": "street-aligned", "origin": "State & Madison",
                 "boundary": "real mile-road centerlines", "edges": "straight_fit"}
EXTENTS = ("city", "un2025", "jrc2023")


def build_framed_v2(boundary, centerlines, *, extent, frame_path, region_path, land_path=None, edges="whole",
                    min_share=0.5, include_city=False, anchors_path=None, roads=None):
    """The v2 grid over a framed extent: the unchanged city grid plus the regional lattice, with
    membership decided by ``edges`` against the frame. Returns (FeatureCollection, problems, stats)."""
    import hashlib
    import os

    from chigrid import build_road_namer
    from chigrid_frames import apply_edges, load_frame, load_land
    from chigrid_region import build_regional_grid, build_regional_namer, load_anchors
    from chigrid_streets import ANCHOR_EW, ANCHOR_NS, city_cells, city_polygon, fit_city_lines

    fitted = fit_city_lines(centerlines)
    if fitted is None:
        raise SystemExit("no mile-road anchors in the centerlines")
    city = city_polygon(boundary)
    city_features = city_cells(city, *fitted, build_road_namer(centerlines, None))
    frame, provenance = load_frame(frame_path)
    region, _ = load_frame(region_path)
    land = load_land(land_path)
    anchors = load_anchors(anchors_path)
    namer = None
    if roads is not None:
        from chigrid_osm import cluster_roads
        mile_roads = set(ANCHOR_NS.values()) | set(ANCHOR_EW.values())
        for family in ("ns", "ew"):
            for entry in (anchors.get(family) or {}).values():
                mile_roads |= {p.get("road", "") for p in entry.get("pieces", [])}
        namer = build_regional_namer(cluster_roads(roads), exclude=mile_roads)
    grid = build_regional_grid(city, *fitted, city_features, region_bounds=region.bounds, anchors=anchors,
                               land=land, namer=namer)
    features = apply_edges(grid.drafts, frame, mode=edges, min_share=min_share, city=city, include_city=include_city)
    # Untouched city cells keep their published geometry exactly (apply_edges re-serializes it).
    verbatim = {d["properties"]["chi_id"]: d["verbatim"] for d in grid.drafts if "verbatim" in d}
    for f in features:
        v = verbatim.get(f["properties"]["chi_id"])
        clipped = edges == "clip" and "frame" in f["properties"]["edge_kind"]
        if v is not None and not clipped:
            f["geometry"] = v["geometry"]

    def _sha(path):
        if not path or not os.path.exists(path):
            return None
        return hashlib.sha256(open(path, "rb").read()).hexdigest()

    props_snapshot = ((roads or {}).get("properties") or {}).get("snapshot") or ""
    props = dict(V2_CITY_PROPS)
    props.update({
        "extent": extent,
        "frame_version": provenance.get("release"),
        "frame_filter": provenance.get("filter"),
        "frame_sha256": _sha(frame_path),
        "edge_mode": edges,
        "min_share": min_share if edges == "whole" else None,
        "include_city": include_city,
        "ids": "mile-road lattice (offset_mi = distance from the geographic band)",
        "anchors_sha256": _sha(anchors_path),
        "anchors_status": anchors.get("status") if anchors else None,  # "proposed" until reviewed
        "roads_snapshot": ((roads or {}).get("properties") or {}).get("snapshot"),
        "attribution": "Frames: © European Union, EC JRC GHSL (CC BY 4.0). Regional roads: "
                       + ("© OpenStreetMap contributors (ODbL)" if str(props_snapshot).startswith(("layercake", "geofabrik", "osm"))
                          else "US Census Bureau TIGER/Line (public domain)")
                       + ". City: City of Chicago data portal.",
        "count": len(features),
    })
    return {"type": "FeatureCollection", "properties": props, "features": features}, grid.problems, grid.stats


def main() -> None:
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    frames = os.path.join(here, "data", "frames")
    ap = argparse.ArgumentParser(description="Generate the v2 street-aligned chiGRID.")
    ap.add_argument("--boundary", required=True, help="city/community-area boundary GeoJSON")
    ap.add_argument("--centerlines", required=True, help="street-centerline GeoJSON (FeatureCollection)")
    ap.add_argument("--extent", choices=EXTENTS, default="city",
                    help="which Chicago to tile: city limits (default), the UN 2025 urban centre, or JRC 2023's")
    ap.add_argument("--edges", choices=("whole", "clip"), help="framed extents: keep whole cells or clip at the frame")
    ap.add_argument("--min-share", type=float, default=0.5, help="whole mode: least land share inside the frame")
    ap.add_argument("--include-city", action="store_true", help="also keep city cells the frame leaves out")
    ap.add_argument("--frame", help="frame GeoJSON (default data/frames/<extent>.geojson)")
    ap.add_argument("--region", default=os.path.join(frames, "jrc2023.geojson"),
                    help="the lattice is always built over this frame's bounds (so cells match across extents)")
    ap.add_argument("--land", default=os.path.join(frames, "land.geojson"), help="land mask GeoJSON")
    ap.add_argument("--anchors", default=os.path.join(here, "data", "anchors", "anchors_regional.json"),
                    help="reviewed regional anchors (suburban mile roads)")
    ap.add_argument("--roads", choices=("tiger", "osm", "none"), default="tiger",
                    help="road source for local names outside the city: tiger (public domain, default), osm "
                         "(ODbL: a cross-check only, never commit its output), or none")
    ap.add_argument("--allow-problems", action="store_true", help="write the grid even if the lattice check fails")
    args = ap.parse_args()

    with open(args.boundary) as fh:
        boundary = json.load(fh)
    with open(args.centerlines) as fh:
        centerlines = json.load(fh)
        if isinstance(centerlines, dict) and "data" in centerlines and "features" not in centerlines:
            centerlines = centerlines["data"]  # some portal exports wrap the FeatureCollection in {"data": ...}

    if args.extent == "city":
        if args.edges or args.frame or args.include_city:
            ap.error("--edges/--frame/--include-city apply to the framed extents (un2025, jrc2023)")
        grid = build_street_aligned_chigrid(boundary, centerlines, collection_props=V2_CITY_PROPS)
        json.dump(grid, sys.stdout, ensure_ascii=False)
        print(f"wrote {grid['properties']['count']} cells", file=sys.stderr)
        return
    if not args.edges:
        ap.error(f"--extent {args.extent} needs --edges whole|clip")
    roads = None
    if args.roads != "none":
        from chigrid_tiger import load_roads
        roads = load_roads(args.roads, log=lambda m: print(m, file=sys.stderr))
    anchors = args.anchors if os.path.exists(args.anchors) else None
    if anchors is None:
        print("no regional anchors: every line outside the city is imputed (rule 3)", file=sys.stderr)
    grid, problems, stats = build_framed_v2(
        boundary, centerlines, extent=args.extent, frame_path=args.frame or os.path.join(frames, f"{args.extent}.geojson"),
        region_path=args.region, land_path=args.land if os.path.exists(args.land) else None, edges=args.edges,
        min_share=args.min_share, include_city=args.include_city, anchors_path=anchors, roads=roads)
    print(json.dumps(stats), file=sys.stderr)
    if problems:
        print(f"{len(problems)} lattice problems, e.g.:\n  " + "\n  ".join(problems[:15]), file=sys.stderr)
        if not args.allow_problems:
            raise SystemExit("lattice check failed (fix the anchors, or pass --allow-problems for a draft)")
    json.dump(grid, sys.stdout, ensure_ascii=False)
    flags = sum(f["properties"]["flag"] for f in grid["features"])
    print(f"wrote {grid['properties']['count']} cells ({flags} flagged)", file=sys.stderr)


if __name__ == "__main__":
    main()
