#!/usr/bin/env python3
"""CSV, Shapefile and GeoPackage exports for a chiGRID GeoJSON.

    python export.py data/v2/un2025/chigrid.geojson data/v2/un2025/chigrid_clip.geojson

writes, next to each input, ``<stem>.csv``, ``chigrid_<version>_<extent>[_clip]_shapefile.zip`` and
``…_geopackage.zip``. Needs geopandas + pyogrio (only this script does). Shapefile field names are cut
to 10 characters: chi_ord, label_can, frame_shr (share of the cell's land inside the frame), city_shr
(share inside the city limit), off_w / off_s (miles from the cell's geographic band), bounds_rul.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import zipfile

COLUMNS = [  # (GeoJSON / GeoPackage name, Shapefile name)
    ("chi_ordinate", "chi_ord"), ("chi_id", "chi_id"), ("name", "name"), ("label", "label"),
    ("name_canon", "name_canon"), ("label_canon", "label_can"), ("center_lat", "center_lat"),
    ("center_lon", "center_lon"), ("area_sqmi", "area_sqmi"), ("edge", "edge"), ("flag", "flag"),
    ("edge_kind", "edge_kind"), ("frame_share", "frame_shr"), ("in_city_share", "city_shr"),
    ("offset_w_mi", "off_w"), ("offset_s_mi", "off_s"), ("bounds_rule", "bounds_rul"),
]


def rows(grid: dict) -> list[dict]:
    out = []
    for f in grid["features"]:
        p = dict(f["properties"])
        lon, lat = p.pop("center")
        offset = p.pop("offset_mi", None) or [None, None]
        p.update({"center_lat": lat, "center_lon": lon, "offset_w_mi": offset[0], "offset_s_mi": offset[1]})
        if isinstance(p.get("edge_kind"), list):
            p["edge_kind"] = ",".join(p["edge_kind"])
        out.append({k: p.get(k) for k, _ in COLUMNS if k in p})
    return out


def export(path: str) -> list[str]:
    import geopandas as gpd
    from shapely.geometry import shape

    grid = json.load(open(path))
    props = grid.get("properties", {})
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    version = props.get("version") or ("v2" if "name_canon" in grid["features"][0]["properties"] else "v1")
    tag = "_".join(x for x in (f"chigrid_{version}", props.get("extent"), "clip" if stem.endswith("_clip") else "") if x)
    table = rows(grid)
    cols = [k for k, _ in COLUMNS if any(r.get(k) not in (None, "") for r in table)]
    written = []
    csv_path = os.path.join(folder, f"{stem}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(table)
    written.append(csv_path)

    gdf = gpd.GeoDataFrame(table, geometry=[shape(f["geometry"]) for f in grid["features"]], crs="EPSG:4326")[cols + ["geometry"]]
    with tempfile.TemporaryDirectory() as tmp:
        gpkg = os.path.join(tmp, f"{tag}.gpkg")
        gdf.to_file(gpkg, driver="GPKG", layer=tag)
        shp = os.path.join(tmp, f"{tag}.shp")
        gdf.rename(columns=dict(COLUMNS)).to_file(shp, driver="ESRI Shapefile", encoding="utf-8")
        for kind, files in (("geopackage", [gpkg]),
                            ("shapefile", [os.path.join(tmp, f"{tag}.{ext}") for ext in ("cpg", "dbf", "prj", "shp", "shx")])):
            zpath = os.path.join(folder, f"{tag}_{kind}.zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for f in files:
                    if os.path.exists(f):
                        z.write(f, os.path.basename(f))
            written.append(zpath)
    return written


if __name__ == "__main__":
    for p in sys.argv[1:]:
        for out in export(p):
            print(out, file=sys.stderr)
