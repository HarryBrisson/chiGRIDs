"""OpenStreetMap roads for the χGRID region outside the city limits.

Inside the city the Chicago portal centerlines stay the source; outside it, the OSM snapshot named in
``data/osm/snapshot.json`` (see ``fetch_osm.py``) supplies the roads. Its ``source`` picks the reader:
``layercake`` (default; OSM US's GeoParquet ``highways`` slice, read with pyarrow) or ``geofabrik``
(state ``.osm.pbf`` extracts, read with GDAL's OSM driver via ``pyogrio.raw.read`` — no
osmium/pyrosm). Either way this module normalizes each way to the
``{STREET_NAME, CLASS}`` feature schema that ``chigrid._base_road_name``/``build_road_namer`` expect,
and clusters same-named ways into ``RoadGroup``s so that distinct roads sharing a name (Central Ave vs
Central Rd, the many Lake Sts) stay apart.

The derived FeatureCollection is too big to commit; ``load_osm_roads`` caches it under
``~/.cache/chigrids/derived/osm_roads_<snapshot>.geojson`` keyed by the snapshot id
(``<source>-<timestamp>``).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON, _ROAD_TYPE_SUFFIX

REPO = Path(__file__).resolve().parent
SNAPSHOT_PATH = REPO / "data" / "osm" / "snapshot.json"
OSM_CACHE = Path.home() / ".cache" / "chigrids" / "osm"
DERIVED_CACHE = Path.home() / ".cache" / "chigrids" / "derived"
SCHEMA_VERSION = 1

# (lon_min, lat_min, lon_max, lat_max): 34.55W..19.4E and 29.81S..25.39N χ-miles of State & Madison
# (see bbox_miles). Covers the region R = jrc2023 bounds (W30.35 E11.87 S25.34 N22.07) + 2 mi with
# ≥ 2 mi to spare west — room to fit a line past 32W (−88.25 would stop at 31.97W, short of R).
DEFAULT_BBOX = (-88.30, 41.45, -87.25, 42.25)

HIGHWAY_CLASS = {
    "motorway": "1",
    "trunk": "2", "primary": "2",
    "secondary": "3", "tertiary": "3",
    "residential": "4", "unclassified": "4", "living_street": "4",
    "service": "9", "track": "9",
}
HIGHWAY_LINKS = ("motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link")
HIGHWAYS = tuple(HIGHWAY_CLASS) + HIGHWAY_LINKS

_DIRECTIONS = {
    "N", "S", "E", "W", "NE", "NW", "SE", "SW",
    "NORTH", "SOUTH", "EAST", "WEST", "NORTHEAST", "NORTHWEST", "SOUTHEAST", "SOUTHWEST",
}
# Names whose leading directional word IS the name (Chicago portal STREET_NAME "NORTH SHORE", "SOUTH
# CHICAGO", ...), so an OSM "North Shore Avenue" must not lose it. "West North Shore Avenue" still
# drops the WEST.
DIRECTIONAL_NAMES = {
    "NORTH SHORE", "SOUTH SHORE", "NORTH BRANCH", "SOUTH BRANCH", "NORTH PARK", "SOUTH PARK",
    "SOUTH CHICAGO", "EAST END", "WEST END", "EAST RIVER", "WEST RIVER", "EAST PRAIRIE",
}
# chigrid's suffixes, plus the extra types suburban roads carry.
_TYPE_SUFFIX = re.compile(
    rf"(?:{_ROAD_TYPE_SUFFIX.pattern})|(?:\s+(highway|hwy|trail|trl|pike|turnpike|tpke)$)", re.IGNORECASE
)
_TYPE_ABBREV = {
    "AVENUE": "AVE", "STREET": "ST", "BOULEVARD": "BLVD", "ROAD": "RD", "DRIVE": "DR", "PLACE": "PL",
    "PARKWAY": "PKWY", "COURT": "CT", "TERRACE": "TER", "LANE": "LN", "HIGHWAY": "HWY", "TRAIL": "TRL",
    "TURNPIKE": "TPKE",
}
_OTHER_TAG = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


# --- normalization ---------------------------------------------------------------------------------

def highway_to_class(highway: Any) -> str | None:
    """OSM ``highway`` → the portal CLASS codes chigrid filters on (1 expressway, 2/3 arterials, 4 local,
    5 ramp, 9 service). None for highway values we don't read."""
    hw = str(highway or "").strip().lower()
    if hw.endswith("_link"):
        return "5"
    return HIGHWAY_CLASS.get(hw)


def split_name(name: Any) -> tuple[str, str]:
    """``"West North Avenue"`` → ``("NORTH", "AVE")``. Uppercases, drops the trailing street type, then
    drops one leading directional word — only when a real name remains (``"North Avenue"`` → NORTH) and
    the directional isn't part of the name (``"North Shore Ave"`` → NORTH SHORE)."""
    s = re.sub(r"\s+", " ", str(name or "").upper().replace(".", "")).strip()
    street_type = ""
    m = _TYPE_SUFFIX.search(s)
    if m and m.start() > 0:
        street_type = m.group(0).strip()
        s = s[: m.start()].strip()
        street_type = _TYPE_ABBREV.get(street_type, street_type)
    tokens = s.split(" ")
    if len(tokens) > 1 and tokens[0] in _DIRECTIONS and s not in DIRECTIONAL_NAMES:
        s = " ".join(tokens[1:])
    return s, street_type


def normalize_name(name: Any) -> str:
    return split_name(name)[0]


def parse_other_tags(other_tags: Any) -> dict[str, str]:
    """GDAL's ``other_tags`` HSTORE string (``"ref"=>"IL 83","lanes"=>"4"``) → dict."""
    if not other_tags:
        return {}
    unescape = lambda v: v.replace('\\"', '"').replace("\\\\", "\\")
    return {unescape(k): unescape(v) for k, v in _OTHER_TAG.findall(str(other_tags))}


def to_miles(lon, lat):
    """lon/lat → χ-miles (x east, y north of State & Madison), with chigrid's flat MILE_LON/MILE_LAT."""
    return (np.asarray(lon, dtype=float) - ORIGIN_LON) / MILE_LON, (np.asarray(lat, dtype=float) - ORIGIN_LAT) / MILE_LAT


def bbox_miles(bbox=DEFAULT_BBOX) -> dict[str, float]:
    """How far a lon/lat bbox reaches from State & Madison in χ-miles: {west, east, south, north}."""
    (x0, x1), (y0, y1) = to_miles([bbox[0], bbox[2]], [bbox[1], bbox[3]])
    return {"west": round(-float(x0), 2), "east": round(float(x1), 2), "south": round(-float(y0), 2), "north": round(float(y1), 2)}


# --- reading ---------------------------------------------------------------------------------------

def _road_feature(osm_id, name, highway, ref, geom) -> dict[str, Any] | None:
    """One way → a ``{STREET_NAME, CLASS, ...}`` feature, or None (unread highway type, no geometry, or
    neither name nor ref — unnamed driveways, sidewalks-as-service, etc.)."""
    cls = highway_to_class(highway)
    if cls is None or geom is None or geom.is_empty or geom.geom_type not in ("LineString", "MultiLineString"):
        return None
    street_name, street_type = split_name(name)
    if not street_name and not ref:
        return None
    if geom.geom_type == "LineString":
        geometry = {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in geom.coords]}
    else:
        geometry = {"type": "MultiLineString",
                    "coordinates": [[[round(x, 6), round(y, 6)] for x, y in g.coords] for g in geom.geoms]}
    return {
        "type": "Feature",
        "properties": {
            "STREET_NAME": street_name,
            "STREET_TYP": street_type,
            "CLASS": cls,
            "NAME_RAW": name or None,
            "ref": ref or None,
            "osm_id": int(osm_id) if str(osm_id).isdigit() else osm_id,
            "highway": highway,
            "SOURCE": "osm",
        },
        "geometry": geometry,
    }


def read_osm_lines(pbf: str | Path, bbox=DEFAULT_BBOX) -> list[dict[str, Any]]:
    """Read one Geofabrik ``.osm.pbf`` (GDAL's OSM driver, ``lines`` layer) into road features."""
    import pyogrio
    import shapely
    from pyogrio.raw import read

    # Keep GDAL's node index in RAM rather than spilling to a temp file (much faster on state extracts).
    pyogrio.set_gdal_config_options({"OSM_MAX_TMPFILE_SIZE": "4096"})
    where = "highway IN ({})".format(", ".join(f"'{h}'" for h in HIGHWAYS))
    meta, _fids, geoms, cols = read(
        str(pbf), layer="lines", bbox=tuple(bbox), where=where, columns=["osm_id", "name", "highway", "other_tags"]
    )
    data = dict(zip(meta["fields"], cols))
    features = []
    for osm_id, name, highway, other_tags, geom in zip(
        data["osm_id"], data["name"], data["highway"], data["other_tags"], shapely.from_wkb(geoms)
    ):
        feature = _road_feature(osm_id, name, highway, parse_other_tags(other_tags).get("ref"), geom)
        if feature:
            features.append(feature)
    return features


def read_layercake(parquet: str | Path, bbox=DEFAULT_BBOX) -> list[dict[str, Any]]:
    """Read a persisted Layercake ``highways`` slice (see ``fetch_osm.fetch_layercake``) into road
    features: OSM ways (not nodes/relations) of the read highway types whose bbox meets ``bbox`` — the
    same selection GDAL's ``lines`` layer + spatial filter makes from a ``.pbf``."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    import shapely

    lon0, lat0, lon1, lat1 = bbox
    b = {k: pc.field(("bbox", k)) for k in ("xmin", "ymin", "xmax", "ymax")}
    flt = ((pc.field("type") == "way") & pc.field("highway").isin(list(HIGHWAYS))
           & (b["xmin"] <= lon1) & (b["xmax"] >= lon0) & (b["ymin"] <= lat1) & (b["ymax"] >= lat0))
    table = pq.read_table(parquet, columns=["id", "name", "highway", "ref", "geometry"], filters=flt)
    features = []
    for osm_id, name, highway, ref, geom in zip(
        table["id"].to_pylist(), table["name"].to_pylist(), table["highway"].to_pylist(), table["ref"].to_pylist(),
        shapely.from_wkb(table["geometry"].to_numpy(zero_copy_only=False)),
    ):
        feature = _road_feature(osm_id, name, highway, ref, geom)
        if feature:
            features.append(feature)
    return features


def _check_bbox_within(bbox, fetched) -> None:
    if not (fetched[0] <= bbox[0] and fetched[1] <= bbox[1] and bbox[2] <= fetched[2] and bbox[3] <= fetched[3]):
        raise ValueError(f"bbox {list(bbox)} reaches past the fetched Layercake slice {list(fetched)}; "
                         f"re-run fetch_osm.py --bbox ...")


# --- clustering ------------------------------------------------------------------------------------

@dataclass
class RoadGroup:
    """One physical road: same-named ways that chain end-to-end (gaps ≤ the clustering tolerance)."""
    group_id: int
    name: str                      # base STREET_NAME, e.g. "DEMPSTER"
    street_type: str               # most common type among its ways, e.g. "ST"
    road_class: str                # CLASS carrying the most length
    feature_index: list[int]       # indices into the features passed to cluster_roads
    osm_ids: list[Any]
    refs: list[str]
    highways: dict[str, float]     # highway → length in miles
    length_mi: float
    lon: np.ndarray = field(repr=False)   # all vertices
    lat: np.ndarray = field(repr=False)

    @property
    def med_lon(self) -> float:
        return float(np.median(self.lon))

    @property
    def med_lat(self) -> float:
        return float(np.median(self.lat))

    @property
    def lon_span(self) -> tuple[float, float]:
        return float(self.lon.min()), float(self.lon.max())

    @property
    def lat_span(self) -> tuple[float, float]:
        return float(self.lat.min()), float(self.lat.max())

    @property
    def x_span_mi(self) -> float:
        return (self.lon.max() - self.lon.min()) / MILE_LON

    @property
    def y_span_mi(self) -> float:
        return (self.lat.max() - self.lat.min()) / MILE_LAT

    @property
    def axis(self) -> str:
        return "ns" if self.y_span_mi >= self.x_span_mi else "ew"

    def summary(self) -> dict[str, Any]:
        x, y = to_miles(self.lon, self.lat)
        return {
            "group_id": self.group_id, "name": self.name, "type": self.street_type, "class": self.road_class,
            "ways": len(self.osm_ids), "refs": self.refs, "length_mi": round(self.length_mi, 2), "axis": self.axis,
            "x_mi": [round(float(x.min()), 2), round(float(np.median(x)), 2), round(float(x.max()), 2)],
            "y_mi": [round(float(y.min()), 2), round(float(np.median(y)), 2), round(float(y.max()), 2)],
        }


def _feature_parts(geometry: dict[str, Any]) -> list[list[list[float]]]:
    if not geometry:
        return []
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return list(geometry["coordinates"])
    return []


class _TypedUnionFind:
    """Union-find whose components each hold at most one non-empty street type, so an untyped way can
    join an Ave or a Rd but never bridge the two."""

    def __init__(self, types: list[str]):
        self.parent = list(range(len(types)))
        self.type = list(types)

    def find(self, i: int) -> int:
        p = self.parent
        root = i
        while p[root] != root:
            root = p[root]
        while p[i] != root:
            p[i], i = root, p[i]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        ta, tb = self.type[ra], self.type[rb]
        if ra == rb or (ta and tb and ta != tb):
            return
        keep, drop = min(ra, rb), max(ra, rb)
        self.parent[drop] = keep
        self.type[keep] = ta or tb


def cluster_roads(
    features: dict[str, Any] | Iterable[dict[str, Any]], *, gap_miles: float = 0.25, split_types: bool = True
) -> list[RoadGroup]:
    """Group ways into physical roads: same base STREET_NAME, compatible street type (equal, or one
    missing — so Central Ave and Central Rd never merge), and chained together by an endpoint lying
    within ``gap_miles`` of another way. ``split_types=False`` ignores the street type (then York Rd →
    York St → York Rd in Elmhurst chain into one road, but so would a Lake Ave ending on a Lake St).
    Unnamed (ref-only) ways are skipped. Groups are returned longest-first; ``group_id`` is the
    position in that order."""
    import shapely

    if isinstance(features, dict):
        features = features.get("features", [])
    features = list(features)

    by_name: dict[str, list[int]] = defaultdict(list)
    parts_xy: dict[int, list[np.ndarray]] = {}
    for i, feature in enumerate(features):
        props = feature.get("properties") or {}
        name = str(props.get("STREET_NAME") or "").strip().upper()
        parts = [np.asarray(p, dtype=float) for p in _feature_parts(feature.get("geometry")) if len(p) >= 2]
        if not name or not parts:
            continue
        by_name[name].append(i)
        parts_xy[i] = [np.column_stack(to_miles(p[:, 0], p[:, 1])) for p in parts]

    groups: list[RoadGroup] = []
    for name, idxs in by_name.items():
        types = [str((features[i].get("properties") or {}).get("STREET_TYP") or "") for i in idxs]
        uf = _TypedUnionFind(types if split_types else [""] * len(idxs))
        if len(idxs) > 1:
            lines = [shapely.multilinestrings([shapely.linestrings(p) for p in parts_xy[i]]) for i in idxs]
            ends = shapely.points(np.array([[parts_xy[i][0][0], parts_xy[i][-1][-1]] for i in idxs]).reshape(-1, 2))
            tree = shapely.STRtree(lines)
            end_idx, way_idx = tree.query(ends, predicate="dwithin", distance=gap_miles)
            for e, w in zip(end_idx // 2, way_idx):
                uf.union(int(e), int(w))
        members: dict[int, list[int]] = defaultdict(list)
        for k in range(len(idxs)):
            members[uf.find(k)].append(k)
        for ks in members.values():
            fidx = [idxs[k] for k in ks]
            lon = np.concatenate([np.asarray(p)[:, 0] for i in fidx for p in _feature_parts(features[i]["geometry"])])
            lat = np.concatenate([np.asarray(p)[:, 1] for i in fidx for p in _feature_parts(features[i]["geometry"])])
            hw_len: Counter = Counter()
            cls_len: Counter = Counter()
            type_count: Counter = Counter()
            refs: list[str] = []
            for i in fidx:
                props = features[i].get("properties") or {}
                length = sum(float(np.hypot(*np.diff(p, axis=0).T).sum()) for p in parts_xy[i])
                hw_len[str(props.get("highway") or "")] += length
                cls_len[str(props.get("CLASS") or "")] += length
                if props.get("STREET_TYP"):
                    type_count[props["STREET_TYP"]] += 1
                for r in str(props.get("ref") or "").split(";"):
                    if r.strip() and r.strip() not in refs:
                        refs.append(r.strip())
            groups.append(RoadGroup(
                group_id=-1, name=name,
                street_type=type_count.most_common(1)[0][0] if type_count else "",
                road_class=cls_len.most_common(1)[0][0] if cls_len else "",
                feature_index=fidx,
                osm_ids=[(features[i].get("properties") or {}).get("osm_id") for i in fidx],
                refs=refs, highways={k: round(v, 3) for k, v in hw_len.most_common()},
                length_mi=float(sum(hw_len.values())), lon=lon, lat=lat,
            ))
    groups.sort(key=lambda g: (-g.length_mi, g.name))
    for gid, g in enumerate(groups):
        g.group_id = gid
    return groups


def groups_named(groups: Iterable[RoadGroup], name: str) -> list[RoadGroup]:
    want = normalize_name(name)
    return [g for g in groups if g.name == want]


# --- snapshot + cache ------------------------------------------------------------------------------

def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    with open(path) as fh:
        return json.load(fh)


def derived_path(snapshot: dict[str, Any], bbox=DEFAULT_BBOX, cache_dir: Path = DERIVED_CACHE) -> Path:
    stem = f"osm_roads_{snapshot['snapshot']}"
    if tuple(bbox) != DEFAULT_BBOX:
        stem += "_" + hashlib.sha256(json.dumps(list(bbox)).encode()).hexdigest()[:8]
    return cache_dir / f"{stem}.geojson"


def build_osm_roads(snapshot: dict[str, Any], bbox=DEFAULT_BBOX, osm_dir: Path = OSM_CACHE, *, log=print) -> dict[str, Any]:
    """Read every extract in the snapshot, de-duplicate ways present in both states, cluster, and return
    the FeatureCollection (each feature tagged with its ``ROAD_GROUP``)."""
    features: list[dict[str, Any]] = []
    seen: set = set()
    timings = {}
    source = snapshot.get("source", "geofabrik")
    for entry in snapshot["files"]:
        t0 = time.time()
        if source == "layercake":
            _check_bbox_within(bbox, entry["bbox"])
            part = read_layercake(osm_dir / entry["file"], bbox)
        else:
            part = read_osm_lines(osm_dir / entry["file"], bbox)
        fresh = [f for f in part if f["properties"]["osm_id"] not in seen]  # ways crossing the IL/IN line
        seen.update(f["properties"]["osm_id"] for f in fresh)
        if "state" in entry:
            for f in fresh:
                f["properties"]["state"] = entry["state"]
        features.extend(fresh)
        timings[entry["file"]] = round(time.time() - t0, 1)
        log(f"{entry['file']}: {len(part):,} ways in bbox, {len(fresh):,} new ({timings[entry['file']]}s)")
    t0 = time.time()
    groups = cluster_roads(features)
    for g in groups:
        for i in g.feature_index:
            features[i]["properties"]["ROAD_GROUP"] = g.group_id
    timings["cluster"] = round(time.time() - t0, 1)
    log(f"clustered {sum(len(g.feature_index) for g in groups):,} named ways into {len(groups):,} road groups ({timings['cluster']}s)")
    return {
        "type": "FeatureCollection",
        "properties": {
            "schema_version": SCHEMA_VERSION,
            "source": f"OpenStreetMap via {snapshot.get('provider', source)}; © OpenStreetMap contributors, ODbL 1.0",
            "snapshot": snapshot["snapshot"],
            "osm_snapshot": snapshot["snapshot"],
            "osm_source": source,
            "files": [{k: e[k] for k in ("file", "md5", "sha256", "etag", "last_modified", "replication_timestamp") if k in e}
                      for e in snapshot["files"]],
            "bbox": [float(v) for v in bbox],
            "bbox_miles": bbox_miles(bbox),
            "highways": list(HIGHWAYS),
            "n_features": len(features),
            "n_groups": len(groups),
            "timings_s": timings,
        },
        "features": features,
    }


def load_osm_roads(
    snapshot: dict[str, Any] | str | Path | None = None,
    bbox=DEFAULT_BBOX,
    *,
    osm_dir: Path = OSM_CACHE,
    cache_dir: Path = DERIVED_CACHE,
    refresh: bool = False,
    log=print,
) -> dict[str, Any]:
    """The derived OSM roads FeatureCollection for ``snapshot`` (default: ``data/osm/snapshot.json``),
    built once and cached at ``cache_dir/osm_roads_<snapshot>.geojson``."""
    if snapshot is None or isinstance(snapshot, (str, Path)):
        snapshot = load_snapshot(Path(snapshot) if snapshot else SNAPSHOT_PATH)
    path = derived_path(snapshot, bbox, cache_dir)
    if path.exists() and not refresh:
        with open(path) as fh:
            fc = json.load(fh)
        props = fc.get("properties") or {}
        if props.get("schema_version") == SCHEMA_VERSION and props.get("bbox") == [float(v) for v in bbox]:
            return fc
        log(f"{path.name}: built with another schema or bbox, rebuilding")
    fc = build_osm_roads(snapshot, bbox, osm_dir, log=log)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "w") as fh:
        json.dump(fc, fh, separators=(",", ":"))
    tmp.replace(path)
    log(f"wrote {path} ({path.stat().st_size / 1e6:,.0f} MB)")
    return fc


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build (or load) the derived OSM roads for the snapshot.")
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    ap.add_argument("--bbox", type=float, nargs=4, default=list(DEFAULT_BBOX), metavar=("LON0", "LAT0", "LON1", "LAT1"))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    fc = load_osm_roads(args.snapshot, tuple(args.bbox), refresh=args.refresh, log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(fc["properties"], indent=2))
