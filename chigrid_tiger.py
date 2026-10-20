"""US Census TIGER/Line roads for the χGRID region outside the city limits.

TIGER/Line is a US federal work — public domain — so everything derived from it (the regional anchor
fits, the street-aligned cells, the local names) can ship under this repo's MIT licence. It is the
default road source for the regional grid; OpenStreetMap (``chigrid_osm``) stays available as an
optional cross-check, but nothing derived from it is committed.

    python chigrid_tiger.py            # download the county road files, build the derived cache

Raw county zips are kept under ``~/.cache/chigrids/tiger/`` (sha256 recorded in
``data/tiger/snapshot.json``); the derived FeatureCollection — the ``{STREET_NAME, STREET_TYP, CLASS}``
schema ``chigrid_osm.cluster_roads`` and the namers read — is cached under
``~/.cache/chigrids/derived/tiger_roads_<vintage>.geojson``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from chigrid_osm import DEFAULT_BBOX, split_name

REPO = Path(__file__).resolve().parent
VINTAGE = "2025"
BASE_URL = f"https://www2.census.gov/geo/tiger/TIGER{VINTAGE}/ROADS/"
# Every county the region (DEFAULT_BBOX: the JRC 2023 frame plus ~2 mi) touches.
COUNTIES = {
    "17031": "Cook, IL", "17043": "DuPage, IL", "17089": "Kane, IL", "17093": "Kendall, IL",
    "17097": "Lake, IL", "17111": "McHenry, IL", "17197": "Will, IL",
    "18089": "Lake, IN", "18127": "Porter, IN",
}
RAW_DIR = Path.home() / ".cache" / "chigrids" / "tiger"
DERIVED_DIR = Path.home() / ".cache" / "chigrids" / "derived"
SNAPSHOT_PATH = REPO / "data" / "tiger" / "snapshot.json"
SCHEMA_VERSION = 1

# MAF/TIGER feature class -> the CLASS codes the fitters and namers already understand
# (1 expressway, 2 primary, 4 local street, 5 ramp, 9 service). TIGER doesn't separate arterials from
# residential streets (both S1400), so the regional search leans on geometry, not class.
MTFCC_CLASS = {"S1100": "1", "S1200": "2", "S1400": "4", "S1630": "5", "S1640": "9"}


def _download(url: str, dest: Path) -> str:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": "chiGRIDs/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
            while chunk := r.read(1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def fetch(raw_dir: Path = RAW_DIR, *, log=print) -> dict[str, Any]:
    """Download (once) every county road file; write and return the snapshot."""
    files = []
    for fips, county in COUNTIES.items():
        name = f"tl_{VINTAGE}_{fips}_roads.zip"
        sha = _download(BASE_URL + name, raw_dir / name)
        files.append({"file": name, "county": county, "url": BASE_URL + name, "sha256": sha})
        log(f"{county:12} {name} {sha[:12]}")
    snapshot = {"snapshot": f"tiger{VINTAGE}", "source": "US Census Bureau TIGER/Line Shapefiles, roads",
                "license": "Public domain (US federal government work, 17 U.S.C. § 105)",
                "vintage": VINTAGE, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": files}
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=1) + "\n")
    return snapshot


def build(snapshot: dict[str, Any], bbox=DEFAULT_BBOX, raw_dir: Path = RAW_DIR, *, log=print) -> dict[str, Any]:
    """County road files -> one FeatureCollection in the regional road schema (deduplicated by
    LINEARID: a road on a county line appears in both files)."""
    import pyogrio

    features, seen = [], set()
    for entry in snapshot["files"]:
        path = f"/vsizip/{raw_dir / entry['file']}"
        meta, _, geoms, fields = pyogrio.raw.read(path, bbox=bbox, return_fids=False, encoding="UTF-8")
        cols = {name: fields[i] for i, name in enumerate(meta["fields"])}
        import shapely
        for linearid, fullname, mtfcc, wkb in zip(cols["LINEARID"], cols["FULLNAME"], cols["MTFCC"], geoms):
            cls = MTFCC_CLASS.get(mtfcc)
            if cls is None or not fullname or linearid in seen:
                continue
            seen.add(linearid)
            base, street_type = split_name(fullname)
            if not base:
                continue
            geom = shapely.from_wkb(wkb)
            features.append({"type": "Feature",
                             "geometry": shapely.geometry.mapping(geom),
                             "properties": {"STREET_NAME": base, "STREET_TYP": street_type, "CLASS": cls,
                                            "highway": mtfcc, "name": fullname, "LINEARID": linearid,
                                            "SOURCE": "tiger"}})
        log(f"{entry['county']:12} {len(features):7} roads so far")
    return {"type": "FeatureCollection",
            "properties": {"snapshot": snapshot["snapshot"], "schema_version": SCHEMA_VERSION, "bbox": list(bbox),
                           "license": snapshot["license"]},
            "features": features}


def load_tiger_roads(snapshot_path: Path = SNAPSHOT_PATH, *, refresh: bool = False, log=print) -> dict[str, Any]:
    """The derived roads FeatureCollection, built once per snapshot and cached."""
    snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else fetch(log=log)
    path = DERIVED_DIR / f"tiger_roads_{snapshot['snapshot']}.geojson"
    if path.exists() and not refresh:
        fc = json.loads(path.read_text())
        if (fc.get("properties") or {}).get("schema_version") == SCHEMA_VERSION:
            return fc
    fc = build(snapshot, log=log)
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(fc, separators=(",", ":")))
    tmp.replace(path)
    return fc


def load_roads(source: str = "tiger", *, log=print) -> dict[str, Any]:
    """Regional roads by source: ``tiger`` (default, public domain) or ``osm`` (ODbL — a cross-check
    only; never commit anything derived from it)."""
    if source == "tiger":
        return load_tiger_roads(log=log)
    if source == "osm":
        from chigrid_osm import load_osm_roads
        return load_osm_roads(log=log)
    raise ValueError(f"unknown road source {source!r}")


if __name__ == "__main__":
    fc = load_tiger_roads(refresh="--refresh" in sys.argv, log=lambda m: print(m, file=sys.stderr))
    print(f"{len(fc['features'])} TIGER road segments", file=sys.stderr)
