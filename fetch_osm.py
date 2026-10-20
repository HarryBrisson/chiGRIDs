"""Download the OpenStreetMap roads that supply χGRID lines outside the city limits.

Two interchangeable sources; ``data/osm/snapshot.json`` (committed) records which one, and exactly
which bytes, the derived roads came from. ``chigrid_osm.load_osm_roads`` reads it and dispatches on
its ``source``.

* ``layercake`` (default) — OSM US's Layercake ``highways.parquet``: one planet-wide GeoParquet,
  spatially sorted, refreshed weekly in place. Only the row groups whose bbox statistics touch the
  region are range-read (about 110 MB of 29 GB; every request pinned to the file's ETag with
  ``If-Range``), then persisted raw-first as ``~/.cache/chigrids/osm/layercake-highways-<stamp>-<bbox>.parquet``
  with its sha256. Stamped with the upstream ``Last-Modified``.
* ``geofabrik`` — the Illinois + Indiana ``.osm.pbf`` extracts: ``<state>-latest`` is resolved to its
  dated file (e.g. ``illinois-261018.osm.pbf``), streamed (resuming dropped connections), checked
  against Geofabrik's ``.md5``, and stamped with the replication timestamp from the PBF header
  (cross-checked against ``<state>-updates/state.txt`` and ``Last-Modified``).

    python fetch_osm.py                         # layercake → data/osm/snapshot.json
    python fetch_osm.py --source geofabrik      # switch back to the state extracts
    python fetch_osm.py --source geofabrik --states illinois --snapshot /tmp/snap.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import sys
import time
import urllib.request
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_URL = "https://download.geofabrik.de/north-america/us/"
LAYERCAKE_URL = "https://data.openstreetmap.us/layercake/highways.parquet"
STATES = ("illinois", "indiana")
CACHE_DIR = Path.home() / ".cache" / "chigrids" / "osm"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "osm" / "snapshot.json"
USER_AGENT = "chiGRIDs-fetch_osm/1.0"  # Layercake's Cloudflare front 403s urllib's default UA


def _open(url: str, method: str = "GET", headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def _read_text(url: str) -> str:
    with _open(url) as resp:
        return resp.read().decode("utf-8")


def resolve_latest(state: str) -> tuple[str, str | None]:
    """Follow ``<state>-latest.osm.pbf`` to the dated file it points at. Returns (url, Last-Modified)."""
    with _open(f"{BASE_URL}{state}-latest.osm.pbf", method="HEAD") as resp:
        return resp.geturl(), resp.headers.get("Last-Modified")


def parse_state_txt(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().replace("\\:", ":")
    return out


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, value) for a protobuf message; length-delimited values come back as bytes."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            value, i = buf[i:i + n], i + n
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield field, value


def pbf_header(path: Path) -> dict[str, object]:
    """Read the OSMHeader block: replication timestamp (field 32), sequence number (33), base url (34)."""
    with open(path, "rb") as fh:
        (hlen,) = struct.unpack(">I", fh.read(4))
        blob_header = dict(_fields(fh.read(hlen)))
        if blob_header.get(1) != b"OSMHeader":
            raise ValueError(f"{path}: first block is {blob_header.get(1)!r}, not OSMHeader")
        blob = dict(_fields(fh.read(blob_header[3])))
    data = blob[1] if 1 in blob else zlib.decompress(blob[3])
    out: dict[str, object] = {}
    for field, value in _fields(data):
        if field == 32:
            out["replication_timestamp"] = datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif field == 33:
            out["replication_sequence_number"] = value
        elif field == 34:
            out["replication_base_url"] = value.decode("utf-8")
        elif field == 16:
            out["writingprogram"] = value.decode("utf-8")
    return out


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, retries: int = 8) -> str:
    """Stream ``url`` to ``dest`` (via ``.part``, resuming it with HTTP Range after a dropped connection),
    returning the md5 of the whole file."""
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries + 1):
        done = part.stat().st_size if part.exists() else 0
        try:
            with _open(url, headers={"Range": f"bytes={done}-"} if done else None) as resp:
                if done and resp.status != 206:  # server ignored the Range: start over
                    done = 0
                total = done + int(resp.headers.get("Content-Length") or 0)
                with open(part, "ab" if done else "wb") as out:
                    for chunk in iter(lambda: resp.read(1 << 20), b""):
                        out.write(chunk)
                        done += len(chunk)
                        if done % (50 << 20) < (1 << 20):
                            print(f"  {dest.name}: {done / 1e6:,.0f} / {total / 1e6:,.0f} MB", file=sys.stderr)
            break
        except (OSError, TimeoutError) as exc:  # URLError/socket timeouts/resets are all OSErrors
            if attempt == retries:
                raise
            print(f"  {dest.name}: {exc!r} at {done / 1e6:,.0f} MB; resuming", file=sys.stderr)
            time.sleep(min(60, 5 * 2 ** attempt))
    part.replace(dest)
    return md5_file(dest)


def fetch_state(state: str, cache_dir: Path = CACHE_DIR) -> dict[str, object]:
    url, last_modified = resolve_latest(state)
    filename = url.rsplit("/", 1)[-1]
    expected = _read_text(url + ".md5").split()[0].lower()
    dest = cache_dir / filename
    if dest.exists() and md5_file(dest) == expected:
        print(f"{filename}: cached, md5 ok", file=sys.stderr)
    else:
        print(f"{filename}: downloading {url}", file=sys.stderr)
        got = download(url, dest)
        if got != expected:
            dest.unlink()
            raise SystemExit(f"{filename}: md5 mismatch (got {got}, Geofabrik says {expected})")
    header = pbf_header(dest)
    try:
        state_txt = parse_state_txt(_read_text(f"{BASE_URL}{state}-updates/state.txt"))
    except Exception as exc:  # informational only; the PBF header is the authoritative stamp
        state_txt = {"error": str(exc)}
    timestamp = header.get("replication_timestamp") or state_txt.get("timestamp")
    if state_txt.get("timestamp") and state_txt["timestamp"] != timestamp:
        print(f"{filename}: note — updates/state.txt is at {state_txt['timestamp']}, file is {timestamp}", file=sys.stderr)
    return {
        "state": state,
        "file": filename,
        "url": url,
        "bytes": dest.stat().st_size,
        "md5": expected,
        "replication_timestamp": timestamp,
        "replication_sequence_number": header.get("replication_sequence_number"),
        "last_modified": parsedate_to_datetime(last_modified).strftime("%Y-%m-%dT%H:%M:%SZ") if last_modified else None,
        "writingprogram": header.get("writingprogram"),
    }


# --- layercake ------------------------------------------------------------------------------------

def _stamp(iso: str) -> str:
    return iso.replace("-", "").replace(":", "")


class RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file over HTTP Range requests, pinned to one ETag (a
    ``If-Range`` mismatch makes the server answer 200 with the whole file, which we refuse). Byte ranges
    can be prefetched in one request each; pyarrow's many small reads are then served from memory."""

    def __init__(self, url: str, retries: int = 6):
        super().__init__()
        self.url, self.retries, self.pos, self.fetched = url, retries, 0, 0
        self.chunks: list[tuple[int, bytes]] = []
        with _open(url, method="HEAD") as resp:
            self.size = int(resp.headers["Content-Length"])
            self.etag = resp.headers.get("ETag")
            self.last_modified = resp.headers.get("Last-Modified")

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def _get(self, start: int, end: int) -> bytes:
        """Bytes [start, end) of the pinned version."""
        headers = {"Range": f"bytes={start}-{end - 1}"}
        if self.etag:
            headers["If-Range"] = self.etag
        for attempt in range(self.retries + 1):
            try:
                with _open(self.url, headers=headers) as resp:
                    if resp.status != 206:
                        raise SystemExit(f"{self.url} changed upstream (ETag {self.etag} no longer current); re-run")
                    data = resp.read()
                if len(data) != end - start:
                    raise OSError(f"short read {len(data)} of {end - start}")
                self.fetched += len(data)
                return data
            except OSError as exc:
                if attempt == self.retries:
                    raise
                print(f"  range {start}-{end}: {exc!r}; retrying", file=sys.stderr)
                time.sleep(min(60, 3 * 2 ** attempt))
        raise AssertionError("unreachable")

    def prefetch(self, start: int, end: int) -> None:
        self.chunks.append((start, self._get(start, end)))

    def read(self, n: int = -1) -> bytes:
        n = self.size - self.pos if n is None or n < 0 else min(n, self.size - self.pos)
        if n <= 0:
            return b""
        for start, data in self.chunks:
            if start <= self.pos and self.pos + n <= start + len(data):
                out = data[self.pos - start: self.pos - start + n]
                break
        else:
            out = self._get(self.pos, self.pos + n)
        self.pos += len(out)
        return out

    def readinto(self, buf) -> int:
        data = self.read(len(buf))
        buf[: len(data)] = data
        return len(data)


def _row_group_span(rg) -> tuple[int, int]:
    starts, ends = [], []
    for i in range(rg.num_columns):
        col = rg.column(i)
        start = col.dictionary_page_offset if col.has_dictionary_page and col.dictionary_page_offset else col.data_page_offset
        starts.append(start)
        ends.append(start + col.total_compressed_size)
    return min(starts), max(ends)


def layercake_row_groups(metadata, bbox) -> list[int]:
    """Row groups whose ``bbox`` column statistics intersect ``bbox`` (lon_min, lat_min, lon_max, lat_max)."""
    names = [metadata.schema.column(i).path for i in range(metadata.num_columns)]
    col = {k: names.index(f"bbox.{k}") for k in ("xmin", "ymin", "xmax", "ymax")}
    hits = []
    for g in range(metadata.num_row_groups):
        rg = metadata.row_group(g)
        st = {k: rg.column(i).statistics for k, i in col.items()}
        if any(v is None or not v.has_min_max for v in st.values()):
            hits.append(g)  # no stats: can't rule it out
        elif (st["xmin"].min <= bbox[2] and st["xmax"].max >= bbox[0]
              and st["ymin"].min <= bbox[3] and st["ymax"].max >= bbox[1]):
            hits.append(g)
    return hits


def fetch_layercake(bbox, cache_dir: Path = CACHE_DIR, url: str = LAYERCAKE_URL) -> dict[str, object]:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    remote = RangeFile(url)
    last_modified = parsedate_to_datetime(remote.last_modified).strftime("%Y-%m-%dT%H:%M:%SZ")
    bbox_tag = hashlib.sha256(json.dumps([float(v) for v in bbox]).encode()).hexdigest()[:8]
    dest = cache_dir / f"layercake-highways-{_stamp(last_modified)}-{bbox_tag}.parquet"
    if dest.exists():
        print(f"{dest.name}: cached (upstream Last-Modified unchanged)", file=sys.stderr)
        pf = pq.ParquetFile(dest)
        row_groups = json.loads(pf.schema_arrow.metadata[b"chigrids"])["row_groups"]
    else:
        remote.prefetch(max(0, remote.size - (16 << 20)), remote.size)  # footer (~9 MB) in one request
        pf_remote = pq.ParquetFile(remote)
        md = pf_remote.metadata
        row_groups = layercake_row_groups(md, bbox)
        print(f"{url}: {md.num_rows:,} rows in {md.num_row_groups:,} row groups; "
              f"{len(row_groups)} touch the bbox", file=sys.stderr)
        for g in row_groups:
            remote.prefetch(*_row_group_span(md.row_group(g)))
        table = pf_remote.read_row_groups(row_groups)
        print(f"  fetched {remote.fetched / 1e6:,.0f} MB in range requests", file=sys.stderr)
        provenance = {"url": url, "etag": remote.etag, "last_modified": last_modified, "bbox": list(bbox),
                      "row_groups": row_groups, "upstream_rows": md.num_rows, "upstream_row_groups": md.num_row_groups}
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), b"chigrids": json.dumps(provenance).encode()})
        part = dest.with_name(dest.name + ".part")
        pq.write_table(table, part, compression="zstd", row_group_size=1 << 17)
        part.replace(dest)
    newest = pq.read_table(dest, columns=["timestamp"])["timestamp"]
    newest_edit = pc.max(newest).as_py()
    return {
        "file": dest.name,
        "url": url,
        "etag": remote.etag,
        "last_modified": last_modified,
        "bbox": [float(v) for v in bbox],
        "row_groups": row_groups,
        "rows": pq.ParquetFile(dest).metadata.num_rows,
        "bytes": dest.stat().st_size,
        "sha256": sha256_file(dest),
        "newest_element_timestamp": newest_edit.strftime("%Y-%m-%dT%H:%M:%SZ") if newest_edit else None,
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- snapshot --------------------------------------------------------------------------------------

def snapshot_id(source: str, files: list[dict[str, object]]) -> str:
    """Compact stamp for cache names, e.g. ``layercake-20261018T141813Z`` / ``geofabrik-20261018T202506Z``
    (the upstream Last-Modified for Layercake; the newest replication timestamp for Geofabrik)."""
    key = "last_modified" if source == "layercake" else "replication_timestamp"
    return f"{source}-{_stamp(max(str(f[key]) for f in files))}"


def main(argv: list[str] | None = None) -> int:
    from chigrid_osm import DEFAULT_BBOX

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=("layercake", "geofabrik"), default="layercake")
    parser.add_argument("--states", nargs="+", default=list(STATES), help="geofabrik only")
    parser.add_argument("--bbox", type=float, nargs=4, default=list(DEFAULT_BBOX), metavar=("LON0", "LAT0", "LON1", "LAT1"),
                        help="layercake only: region to range-read (the geofabrik extracts are whole states)")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    args = parser.parse_args(argv)

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    if args.source == "layercake":
        files = [fetch_layercake(tuple(args.bbox), args.cache_dir)]
        provider = "OpenStreetMap US Layercake highways.parquet (https://layercake.openstreetmap.us/)"
    else:
        files = [fetch_state(state, args.cache_dir) for state in args.states]
        provider = "Geofabrik OpenStreetMap extracts (https://download.geofabrik.de/)"
    snapshot = {
        "snapshot": snapshot_id(args.source, files),
        "source": args.source,
        "provider": provider,
        "license": "ODbL 1.0 — © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)",
        "cache_dir": "~/.cache/chigrids/osm",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"wrote {args.snapshot} (snapshot {snapshot['snapshot']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
