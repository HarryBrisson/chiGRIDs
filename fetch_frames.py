"""Build the extent ("frame") polygons the chiGRID generators read, from their published sources.

    python fetch_frames.py              # fetch raw inputs (cached) and write data/frames/*.geojson
    python fetch_frames.py --keep-holes # leave the three enclosed non-urban holes in jrc2023

Frames written to ``data/frames/``:

  un2025.geojson   UN World Urbanization Prospects 2025 urban centre for Chicago — GHSL
                   GHS_WUP_MTUC_GLOBE_R2025A v1.1, polygon ``Year=2025 AND ID_UC_G0=7747`` (932 km²).
  jrc2023.geojson  JRC GHS-SMOD R2023A, epoch 2025: the 4-connected run of class-30 ("urban centre")
                   1-km cells containing the Loop (2,231 km² before holes are filled; reaches Indiana).
  land.geojson     Illinois ∪ Indiana (Census cartographic boundary, cut at the shoreline), clipped to
                   the jrc2023 bounds plus 2 miles — the land mask for regional extents.
  city.geojson     The city outline ``chigrid_streets.city_polygon`` clips to (77 community areas,
                   seams closed, holes filled).

Raw downloads go to ``~/.cache/chigrids/raw/`` first (override with ``CHIGRIDS_RAW``), each with a
sha256 in ``manifest.json``; a file whose hash still matches its manifest entry is not fetched again.
Copies already on disk (``--local-dir``, default ``~/.cache/what-is-chicago-data``) are used instead
of downloading. Each frame's ``properties.provenance`` records the release, URL, zip sha256, member
file, recipe, CRS and areas, so the frame can be rebuilt and checked.

Needs shapely, pyproj, pyogrio, numpy and rasterio (rasterio only for jrc2023; nothing else in the
repo uses it).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from collections import deque
from pathlib import Path

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON

REPO = Path(__file__).resolve().parent
FRAMES_DIR = REPO / "data" / "frames"
RAW_DIR = Path(os.environ.get("CHIGRIDS_RAW", Path.home() / ".cache" / "chigrids" / "raw"))
LOCAL_DIR = Path.home() / ".cache" / "what-is-chicago-data"
COMMUNITY_AREAS = REPO.parent / "ward-wise-civic-tech" / "shared" / "civic_data" / "data" / "community_areas.geojson"

GHSL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
GHSL_LICENCE = "CC BY 4.0, © European Union, EC JRC GHSL"
MOLLWEIDE = "ESRI:54009"
WGS84 = "EPSG:4326"
DENSIFY_M = 100.0
PRECISION_DEG = 1e-6  # output grid (~0.1 m); keeps the committed files small

# key -> release, url, raw filename, member read from the zip, name of a copy under --local-dir,
# sha256 of the archive as built (a mismatch means the release was re-uploaded: warn, rebuild, and
# let the area assertions decide whether it still matches)
SOURCES = {
    "mtuc": {
        "release": "GHS_WUP_MTUC_GLOBE_R2025A v1.1",
        "url": GHSL + "GHS_WUP_MTUC_GLOBE_R2025A/V1-1/GHS_WUP_MTUC_GLOBE_R2025A_V1_1_vector.zip",
        "file": "GHS_WUP_MTUC_GLOBE_R2025A_V1_1_vector.zip",
        "member": "GHS_WUP_MTUC_MT_GLOBE_R2025A_v1_1.shp",
        "local": "mtuc_vector.zip",
        "sha256": "7f927a52a1f4a833c7c65eb6fd0e1bee86f53e329a8453cb6e04c7459c49acfa",
    },
    "smod": {
        "release": "GHS_SMOD_E2025_GLOBE_R2023A_54009_1000 V2.0",
        "url": GHSL + "GHS_SMOD_GLOBE_R2023A/GHS_SMOD_E2025_GLOBE_R2023A_54009_1000/V2-0/"
                      "GHS_SMOD_E2025_GLOBE_R2023A_54009_1000_V2_0.zip",
        "file": "GHS_SMOD_E2025_GLOBE_R2023A_54009_1000_V2_0.zip",
        "member": "GHS_SMOD_E2025_GLOBE_R2023A_54009_1000_V2_0.tif",
        "local": "smod_e2025.zip",
        "sha256": "1804f2aa9e7bfeea18ce87dc66442c174e2ba32764a4f6d2320e1b9e1cf152f8",
    },
    "states": {
        "release": "US Census Bureau cartographic boundary file, states 1:500k, 2020",
        "url": "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_500k.zip",
        "file": "cb_2020_us_state_500k.zip",
        "member": "cb_2020_us_state_500k.shp",
        "local": "state.zip",
        "sha256": "dcc877984a3e4d59c1ee8d068b6681517c545e0c1827b7e78a7c2c7cbb167da6",
    },
}

UN2025 = {"where": "Year=2025 AND ID_UC_G0=7747", "area_km2": 932.0}
JRC2023 = {"smod_class": 30, "window_km": 120, "area_km2": 2231.0, "holes_km2": (17.0, 32.0, 35.0)}
AREA_TOL = 0.01


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------- raw fetch

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_path() -> Path:
    return RAW_DIR / "manifest.json"


def _load_manifest() -> dict:
    try:
        return json.loads(_manifest_path().read_text())
    except FileNotFoundError:
        return {}


def _download(url: str, dest: Path) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "chiGRIDs fetch_frames.py"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    part.replace(dest)


def fetch(key: str, *, local_dir: Path | None = LOCAL_DIR) -> tuple[Path, str]:
    """Return ``(path, sha256)`` of the raw archive for ``key``, fetching it into RAW_DIR first if
    it is missing or no longer matches its manifest hash."""
    src = SOURCES[key]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / src["file"]
    manifest = _load_manifest()
    entry = manifest.get(src["file"])
    if dest.exists():
        digest = sha256_file(dest)
        if entry and entry.get("sha256") == digest:
            log(f"  {src['file']}: cached, sha256 matches")
            return dest, digest
        if entry:
            log(f"  {src['file']}: sha256 differs from manifest, fetching again")
            dest.unlink()
    origin = src["url"]
    local = local_dir / src["local"] if local_dir else None
    if not dest.exists() and local and local.exists():
        log(f"  {src['file']}: copying local {local}")
        shutil.copy2(local, dest)
        origin = f"local copy of {src['url']} ({local})"
    elif not dest.exists():
        log(f"  {src['file']}: downloading {src['url']}")
        _download(src["url"], dest)
    digest = sha256_file(dest)
    if digest != src["sha256"]:
        log(f"  WARNING {src['file']}: sha256 {digest} != expected {src['sha256']} (re-published?)")
    manifest[src["file"]] = {"url": src["url"], "sha256": digest, "bytes": dest.stat().st_size,
                             "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                             "origin": origin}
    _manifest_path().write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return dest, digest


def vsizip(path: Path, member: str) -> str:
    return f"/vsizip/{path}/{member}"


# ---------------------------------------------------------------------------------------- geometry

def _transformer(src: str, dst: str):
    from pyproj import Transformer

    return Transformer.from_crs(src, dst, always_xy=True)


def reproject(geom, src: str, dst: str):
    import numpy as np
    import shapely

    tr = _transformer(src, dst)
    return shapely.transform(geom, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def to_wgs84(geom_moll):
    """Densify a Mollweide geometry at DENSIFY_M (so its straight edges stay where they are once
    curved in lon/lat), project to EPSG:4326 and snap to PRECISION_DEG."""
    import shapely

    geom = reproject(shapely.segmentize(geom_moll, DENSIFY_M), MOLLWEIDE, WGS84)
    return _snap(geom)


def _snap(geom):
    import shapely

    geom = shapely.set_precision(geom, PRECISION_DEG)
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    return geom


def geodesic_km2(geom_wgs84) -> float:
    from pyproj import Geod

    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(geom_wgs84)
    return abs(area) / 1e6


def fill_holes(geom):
    from shapely.geometry import MultiPolygon, Polygon

    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior)
    return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])


def chi_bounds(geom_wgs84) -> dict:
    """Bounds in χ-miles from State & Madison (each value is miles in that direction, so a frame
    that reaches 5.6 miles east has ``east: 5.6``)."""
    min_lon, min_lat, max_lon, max_lat = geom_wgs84.bounds
    return {"west": round((ORIGIN_LON - min_lon) / MILE_LON, 2),
            "east": round((max_lon - ORIGIN_LON) / MILE_LON, 2),
            "south": round((ORIGIN_LAT - min_lat) / MILE_LAT, 2),
            "north": round((max_lat - ORIGIN_LAT) / MILE_LAT, 2)}


def _check_area(name: str, got: float, want: float) -> None:
    if abs(got - want) > AREA_TOL * want:
        raise AssertionError(f"{name}: area {got:.1f} km² is not within {AREA_TOL:.0%} of {want}")


# ---------------------------------------------------------------------------------------- frames

def build_un2025(path: Path, digest: str) -> tuple[object, dict]:
    import pyogrio.raw
    import shapely

    src = SOURCES["mtuc"]
    meta, _, wkb, fields = pyogrio.raw.read(vsizip(path, src["member"]), where=UN2025["where"])
    if len(wkb) != 1:
        raise AssertionError(f"un2025: {UN2025['where']} matched {len(wkb)} rows, expected 1")
    attrs = {name: values[0].item() if hasattr(values[0], "item") else values[0]
             for name, values in zip(meta["fields"], fields)}
    geom_moll = shapely.from_wkb(wkb[0])
    if geom_moll.geom_type != "Polygon":
        raise AssertionError(f"un2025: expected one Polygon, got {geom_moll.geom_type}")
    area = geom_moll.area / 1e6
    _check_area("un2025", area, UN2025["area_km2"])
    geom = to_wgs84(geom_moll)
    return geom, {
        "release": src["release"], "url": src["url"], "zip_sha256": digest, "file": src["member"],
        "filter": UN2025["where"], "source_attributes": attrs,
        "recipe": "the single MTUC polygon matching the filter, densified then projected",
        "source_crs": MOLLWEIDE, "crs": WGS84, "densify_m": DENSIFY_M, "precision_deg": PRECISION_DEG,
        "area_km2_mollweide": round(area, 3), "area_km2_geodesic": round(geodesic_km2(geom), 3),
        "holes": len(geom_moll.interiors), "holes_filled": False,
        "licence": GHSL_LICENCE,
    }


def flood_fill_4(mask, seed: tuple[int, int]):
    """Boolean array of the 4-connected component of ``mask`` containing ``seed`` (BFS)."""
    import numpy as np

    rows, cols = mask.shape
    comp = np.zeros_like(mask, dtype=bool)
    if not mask[seed]:
        return comp
    comp[seed] = True
    queue = deque([seed])
    while queue:
        r, c = queue.popleft()
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= nr < rows and 0 <= nc < cols and mask[nr, nc] and not comp[nr, nc]:
                comp[nr, nc] = True
                queue.append((nr, nc))
    return comp


def build_jrc2023(path: Path, digest: str, *, fill: bool = True) -> tuple[object, dict]:
    import numpy as np
    import rasterio
    import rasterio.features
    import rasterio.transform
    import rasterio.windows
    from shapely.geometry import shape

    src = SOURCES["smod"]
    loop_x, loop_y = _transformer(WGS84, MOLLWEIDE).transform(ORIGIN_LON, ORIGIN_LAT)
    half = JRC2023["window_km"] * 1000.0
    with rasterio.open(vsizip(path, src["member"])) as ds:
        if ds.crs.to_string() != MOLLWEIDE:
            raise AssertionError(f"jrc2023: raster CRS {ds.crs} is not {MOLLWEIDE}")
        window = rasterio.windows.from_bounds(loop_x - half, loop_y - half, loop_x + half, loop_y + half,
                                              ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=window)
        wt = ds.window_transform(window)
    seed = tuple(int(v) for v in rasterio.transform.rowcol(wt, loop_x, loop_y))
    mask = arr == JRC2023["smod_class"]
    if not mask[seed]:
        raise AssertionError(f"jrc2023: the Loop cell is class {arr[seed]}, not {JRC2023['smod_class']}")
    comp = flood_fill_4(mask, seed)
    if comp[0, :].any() or comp[-1, :].any() or comp[:, 0].any() or comp[:, -1].any():
        raise AssertionError("jrc2023: component reaches the read window's edge; widen window_km")
    polys = [shape(g) for g, v in rasterio.features.shapes(comp.astype("uint8"), mask=comp, transform=wt,
                                                           connectivity=4) if v == 1]
    if len(polys) != 1:
        raise AssertionError(f"jrc2023: vectorized to {len(polys)} polygons, expected 1")
    geom_moll = polys[0]
    if not geom_moll.is_valid:
        raise AssertionError("jrc2023: vectorized polygon is invalid")
    cells = int(comp.sum())
    area_unfilled = geom_moll.area / 1e6
    _check_area("jrc2023", area_unfilled, JRC2023["area_km2"])
    holes = sorted(shape({"type": "Polygon", "coordinates": [list(r.coords)]}).area / 1e6
                   for r in geom_moll.interiors)
    want = JRC2023["holes_km2"]
    if len(holes) != len(want) or any(abs(h - w) > 1.5 for h, w in zip(holes, want)):
        raise AssertionError(f"jrc2023: holes {holes} km², expected about {list(want)}")
    filled_moll = fill_holes(geom_moll)
    area_filled = filled_moll.area / 1e6
    geom = to_wgs84(filled_moll if fill else geom_moll)
    return geom, {
        "release": src["release"], "url": src["url"], "zip_sha256": digest, "file": src["member"],
        "filter": f"SMOD class == {JRC2023['smod_class']} (urban centre)",
        "recipe": (f"read a ±{JRC2023['window_km']} km window around the Loop "
                   f"({ORIGIN_LON}, {ORIGIN_LAT}); keep the 4-connected component of class-"
                   f"{JRC2023['smod_class']} cells containing the Loop cell (BFS); vectorize with "
                   "rasterio.features.shapes (connectivity 4)"
                   + ("; fill enclosed holes" if fill else "") + "; densify then project"),
        "source_crs": MOLLWEIDE, "crs": WGS84, "densify_m": DENSIFY_M, "precision_deg": PRECISION_DEG,
        "window": {"col_off": int(window.col_off), "row_off": int(window.row_off),
                   "width": int(window.width), "height": int(window.height)},
        "cells": cells,
        "area_km2_mollweide": round(area_filled if fill else area_unfilled, 3),
        "area_km2_mollweide_unfilled": round(area_unfilled, 3),
        "area_km2_mollweide_filled": round(area_filled, 3),
        "area_km2_geodesic": round(geodesic_km2(geom), 3),
        "holes": len(holes), "holes_km2": [round(h, 3) for h in holes], "holes_filled": fill,
        "licence": GHSL_LICENCE,
    }


def build_land(path: Path, digest: str, frame_wgs84, *, pad_miles: float = 2.0) -> tuple[object, dict]:
    import pyogrio.raw
    import shapely
    from shapely.geometry import box
    from shapely.ops import unary_union

    src = SOURCES["states"]
    where = "STUSPS IN ('IL', 'IN')"
    _, _, wkb, _ = pyogrio.raw.read(vsizip(path, src["member"]), where=where)
    if len(wkb) != 2:
        raise AssertionError(f"land: {where} matched {len(wkb)} rows, expected 2")
    states = unary_union([g.buffer(0) for g in shapely.from_wkb(wkb)])  # NAD83 ≈ WGS84 at this scale
    min_lon, min_lat, max_lon, max_lat = frame_wgs84.bounds
    pad_lon, pad_lat = pad_miles * MILE_LON, pad_miles * MILE_LAT
    clip = (min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)
    geom = _snap(states.intersection(box(*clip)))
    return geom, {
        "release": src["release"], "url": src["url"], "zip_sha256": digest, "file": src["member"],
        "filter": where,
        "recipe": f"union of the two states, clipped to the jrc2023 frame bounds plus {pad_miles} miles",
        "clip_bounds": [round(v, 6) for v in clip],
        "source_crs": "EPSG:4269 (NAD83, treated as EPSG:4326)", "crs": WGS84,
        "precision_deg": PRECISION_DEG,
        "area_km2_geodesic": round(geodesic_km2(geom), 3),
        "licence": "Public domain (US Census Bureau, a US federal government work)",
    }


def build_city(path: Path) -> tuple[object, dict]:
    from chigrid_streets import city_polygon

    boundary = json.loads(path.read_text())
    geom = _snap(city_polygon(boundary))
    return geom, {
        "release": "City of Chicago community areas (77), via ward-wise-civic-tech civic_data",
        "file": path.name, "file_sha256": sha256_file(path),
        "recipe": "chigrid_streets.city_polygon: union, buffer(0.0006).buffer(-0.0006), holes filled",
        "crs": WGS84, "precision_deg": PRECISION_DEG,
        "area_km2_geodesic": round(geodesic_km2(geom), 3),
        "licence": "City of Chicago Data Portal terms of use",
    }


# ---------------------------------------------------------------------------------------- output

def write_frame(name: str, geom, provenance: dict) -> Path:
    from shapely.geometry import mapping

    provenance = {**provenance, "built": dt.date.today().isoformat(), "builder": "fetch_frames.py",
                  "bounds_lonlat": [round(v, 6) for v in geom.bounds], "bounds_chi_miles": chi_bounds(geom)}
    fc = {"type": "FeatureCollection", "name": name,
          "features": [{"type": "Feature", "properties": {"frame": name, "provenance": provenance},
                        "geometry": mapping(geom)}]}
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    out = FRAMES_DIR / f"{name}.geojson"
    out.write_text(json.dumps(fc, separators=(",", ":"), ensure_ascii=False) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep-holes", action="store_true", help="do not fill the holes in jrc2023")
    ap.add_argument("--local-dir", type=Path, default=LOCAL_DIR,
                    help="directory holding copies of the raw zips (mtuc_vector.zip, smod_e2025.zip, "
                         "state.zip); pass '' to always download")
    ap.add_argument("--community-areas", type=Path, default=COMMUNITY_AREAS)
    args = ap.parse_args(argv)
    local = args.local_dir if str(args.local_dir) else None

    log(f"raw inputs -> {RAW_DIR}")
    raw = {key: fetch(key, local_dir=local) for key in SOURCES}

    built = {}
    log("building un2025")
    built["un2025"] = build_un2025(*raw["mtuc"])
    log("building jrc2023")
    built["jrc2023"] = build_jrc2023(*raw["smod"], fill=not args.keep_holes)
    log("building land")
    built["land"] = build_land(*raw["states"], built["jrc2023"][0])
    log("building city")
    built["city"] = build_city(args.community_areas)

    for name, (geom, prov) in built.items():
        out = write_frame(name, geom, prov)
        areas = {k: v for k, v in prov.items() if k.startswith("area_km2")}
        log(f"  {out.relative_to(REPO)}  {out.stat().st_size / 1e3:,.0f} kB  {geom.geom_type}  "
            f"{areas}  χ-miles {chi_bounds(geom)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
