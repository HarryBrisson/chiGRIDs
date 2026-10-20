#!/usr/bin/env python3
"""Discover the suburban mile roads that continue the χGRID past the city limit.

For every grid line (N/S lines by miles west, E/W lines by miles south) and each side of the city, this
finds the OpenStreetMap road that runs where the grid says the next mile road should be, fits it to
straight pieces, and writes a PROPOSAL for a person to review:

    python discover_anchors.py --boundary community_areas.geojson --centerlines street_centerlines.json

writes ``data/anchors/candidates.json`` (the top candidates per slot, with their metrics) and
``data/anchors/anchors_regional.proposed.json``. Review it, then save it as ``anchors_regional.json``
(set ``"status": "verified"``) — the generators only read the reviewed file by default.

Search, per line k and side:
  prior    line k of a smooth model of the city grid (its ~1.006 mi spacing and ~1° rotation, fit on
           the city's own mile roads — the suburbs keep both), so an error never compounds outward
  window   the cross-axis miles on this side, inside the region and outside the city
  candidate every named through-road (US Census TIGER/Line by default — public domain — clustered by
           ``chigrid_osm.cluster_roads``) with vertices within
           ``--band`` miles of the prior, outside the city, on this side
  accept   within 0.35 mi of the prior, running ≥ 3 mi (or ``--min-coverage`` of the window), straight pieces (RMS ≤ 0.03 mi, max ≤ 0.1 mi,
           splitting at whole miles where one line won't do), spacing to the neighbours 0.75–1.25 mi
  rule     1 when it is the same road as the city anchor, 2 for an aligned substitute; slots with no
           acceptable road are left to the lattice's rule-3 imputation.

A city line closer than 0.75 mi to its neighbour at the city edge (Howard, a half-mile street that
bounds the city) is proposed ``"core": false`` so the real mile road beyond takes its index outside.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any

import numpy as np

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON, _base_road_name
from chigrid_region import Family, Piece, build_families
from chigrid_streets import ANCHOR_EW, ANCHOR_NS

RMS_MAX, MAX_ERR = 0.03, 0.10  # miles, per straight piece
MIN_PIECE_MI = 2.0
SPACING = (0.75, 1.25)
MAX_PRIOR_OFFSET = 0.35  # miles from the grid model's line
MAX_SLOPE_DIFF = 0.05  # mi/mi between a piece and the grid model's rotation (diagonals like Sheridan fail)
RUN_GAP_MI, MIN_RUN_MI = 1.5, 1.5  # a candidate's vertices split at gaps; runs shorter than this drop
STATE_LINE_LON = -87.5247  # Illinois–Indiana line
MAX_PIECES = 5  # a road needing more straight pieces than this isn't a straight mile road (88th Ave: 6)
OWN_ROAD_MIN_MILES = 1.5  # the city's own mile road continuing its line needs less to be believed
MIN_MILES = 3.0  # a road this long (inside the window) is enough, whatever share of the window it covers
SKIP_CLASSES = {"1", "5", "9"}  # expressways, ramps, service


def _mi(vertical: bool, lon, lat):
    """(t, across) in miles: t on the cross axis (miles south for an N/S line), across its position."""
    w = (ORIGIN_LON - np.asarray(lon)) / MILE_LON
    s = (ORIGIN_LAT - np.asarray(lat)) / MILE_LAT
    return (s, w) if vertical else (w, s)


def _position(fam: Family, mc, t: np.ndarray) -> np.ndarray:
    """Line (m, c) in degrees -> its across-position in miles at cross-axis miles ``t``."""
    m, c = mc[0], mc[1]
    if fam.vertical:
        y = ORIGIN_LAT - t * MILE_LAT
        return (ORIGIN_LON - (m * y + c)) / MILE_LON
    x = ORIGIN_LON - t * MILE_LON
    return (ORIGIN_LAT - (m * x + c)) / MILE_LAT


def _fit_pieces(fam: Family, t: np.ndarray, across: np.ndarray, side: str, *, depth=0):
    """Straight pieces through (t, across) miles: one fit, or split at the whole mile nearest the
    middle when it isn't straight enough. Returns [(lo, hi, m_deg, c_deg, rms, maxerr)] or None."""
    if len(t) < 6 or t.max() - t.min() < 0.5:
        return None
    # fit in degrees (the lattice's convention), measure error in miles
    if fam.vertical:
        y = ORIGIN_LAT - t * MILE_LAT
        x = ORIGIN_LON - across * MILE_LON
        m, c = np.polyfit(y, x, 1)
        err = (x - (m * y + c)) / MILE_LON
    else:
        x = ORIGIN_LON - t * MILE_LON
        y = ORIGIN_LAT - across * MILE_LAT
        m, c = np.polyfit(x, y, 1)
        err = (y - (m * x + c)) / MILE_LAT
    rms, mx = float(np.sqrt(np.mean(err ** 2))), float(np.max(np.abs(err)))
    if (rms <= RMS_MAX and mx <= MAX_ERR) or t.max() - t.min() < 2 * MIN_PIECE_MI or depth > 4:
        if rms > RMS_MAX * 2 or mx > MAX_ERR * 2.5:
            return None
        return [(float(t.min()), float(t.max()), float(m), float(c), rms, mx)]
    cut = float(round(np.median(t)))
    lo, hi = t <= cut, t >= cut
    left = _fit_pieces(fam, t[lo], across[lo], side, depth=depth + 1)
    right = _fit_pieces(fam, t[hi], across[hi], side, depth=depth + 1)
    if left is None or right is None:
        return None
    return left + right


def discover(ns_all, ew_all, city, region, groups, *, band=0.4, min_coverage=0.5, min_miles=MIN_MILES,
             pad_miles=1.0, log=print):
    import shapely

    from chigrid_frames import frame_index_range

    ns, ew = build_families(ns_all, ew_all, {}, city)
    anchors: dict[str, Any] = {"ns": {}, "ew": {}}
    candidates: dict[str, Any] = {"ns": {}, "ew": {}}

    # 1. core:false proposals: city lines squeezed against their neighbour at the city edge.
    for fam, key, table in ((ns, "ns", ANCHOR_NS), (ew, "ew", ANCHOR_EW)):
        ks = sorted(k for k in fam.core if k in table)
        for end, nb in ((ks[0], ks[0] + 1), (ks[-1], ks[-1] - 1)):
            gap = abs(float(_position(fam, fam.core[end], np.array([fam.mid]))[0]
                            - _position(fam, fam.core[nb], np.array([fam.mid]))[0]))
            if gap < SPACING[0]:
                anchors[key][str(end)] = {"core": False, "note": f"{table[end]} is {gap:.2f} mi from {table[nb]}: "
                                          "a half-mile street at the city edge, not a mile road", "pieces": []}
                log(f"{key} {end} ({table[end]}): core:false proposed, {gap:.2f} mi from its neighbour")
    # 2. The city's lines extrapolated past its last anchors (west of Mannheim at O'Hare, south of
    #    135th) are guesses that shape only the city's own edge cells; outside the city the regional
    #    grid takes those indices. Not east of Burley: the city meets Indiana there, where outside lines
    #    are the grid model anyway, and dropping them splits the far-SE cells across the state line.
    for fam, key, table, all_fits in ((ns, "ns", ANCHOR_NS, ns_all), (ew, "ew", ANCHOR_EW, ew_all)):
        lo, hi = min(table), max(table)
        ends = ((hi, hi + 1),) if key == "ns" else ((hi, hi + 1), (lo, lo - 1))
        for edge, beyond in ends:
            if beyond in all_fits and str(edge) not in anchors[key] and str(beyond) not in anchors[key]:
                anchors[key][str(beyond)] = {"core": False, "pieces": [],
                                             "note": f"extrapolated past {table[edge]}: city edge cells only"}
    ns, ew = build_families(ns_all, ew_all, anchors, city)

    s_lo, s_hi, w_lo, w_hi = frame_index_range(region.bounds, pad_miles=pad_miles)
    # Roads by axis, as (name, road_class, t, across) in miles — vertices outside the city only.
    by_axis = {True: [], False: []}
    for g in groups:
        base = _base_road_name(g.name)
        if not base or str(g.road_class) in SKIP_CLASSES:
            continue
        lon, lat = np.asarray(g.lon), np.asarray(g.lat)
        # Illinois only: Indiana is surveyed on its own grid, so its lines stay rule-3 (the grid model).
        outside = ~shapely.contains_xy(city, lon, lat) & (lon <= STATE_LINE_LON)
        if outside.sum() < 6:
            continue
        for vertical in (True, False):
            t, across = _mi(vertical, lon[outside], lat[outside])
            if t.max() - t.min() >= 1.0 and (t.max() - t.min()) >= (across.max() - across.min()):
                by_axis[vertical].append((base, str(g.road_class), t, across))

    region_buf = region.buffer(pad_miles * MILE_LAT)
    for fam, key, table, k_rng, t_rng in ((ns, "ns", ANCHOR_NS, (w_lo, w_hi), (s_lo, s_hi)),
                                          (ew, "ew", ANCHOR_EW, (s_lo, s_hi), (w_lo, w_hi))):
        sides = ("north", "south") if fam.vertical else ("east", "west")
        centre = 0
        order = sorted(range(k_rng[0], k_rng[1] + 1), key=lambda k: abs(k - centre))
        roads = by_axis[fam.vertical]
        for side in sides:
            used: dict[str, list[float]] = {}  # road -> median positions already anchoring a line here
            for k in order:
                ts = np.arange(t_rng[0], t_rng[1] + 1, 0.25)
                ts = ts[[fam.side(t) == side for t in ts]]
                if not len(ts):
                    continue
                prior = (*fam.model_line(k), 3, "model")
                pos = _position(fam, prior, ts)
                if fam.vertical:
                    lon_, lat_ = ORIGIN_LON - pos * MILE_LON, ORIGIN_LAT - ts * MILE_LAT
                else:
                    lon_, lat_ = ORIGIN_LON - ts * MILE_LON, ORIGIN_LAT - pos * MILE_LAT
                in_window = shapely.contains_xy(region_buf, lon_, lat_) & ~shapely.contains_xy(city, lon_, lat_)
                window = ts[in_window]
                if len(window) < 8:  # under 2 miles of this line lies in the region on this side
                    continue
                w_lo_t, w_hi_t = float(window.min()), float(window.max())
                by_name: dict[str, list] = {}
                for name, cls, t, across in roads:
                    sel = (t >= w_lo_t - 0.25) & (t <= w_hi_t + 0.25)
                    if not sel.any():
                        continue
                    near = sel & (np.abs(across - _position(fam, prior, t)) <= band)
                    if near.sum() < 6:
                        continue
                    entry = by_name.setdefault(name, [[], [], cls])
                    entry[0].append(t[near]); entry[1].append(across[near])
                scored = []
                window_bins = set(np.floor(window / 0.25).astype(int))
                own_road = _base_road_name(table.get(k, ""))
                city_roads = {_base_road_name(n) for n in table.values()}
                for name, (tl, al, cls) in by_name.items():
                    if name in city_roads and name != own_road:
                        continue  # a city mile road anchors only its own index (Irving Park bends NW past O'Hare)
                    t, across = np.concatenate(tl), np.concatenate(al)
                    order_t = np.argsort(t)
                    t, across = t[order_t], across[order_t]
                    # Same name, different road (Naperville's 95th St, 20 mi past Chicago's): split at
                    # gaps and drop short runs.
                    breaks = np.flatnonzero(np.diff(t) > RUN_GAP_MI) + 1
                    runs = [r for r in np.split(np.arange(len(t)), breaks) if t[r[-1]] - t[r[0]] >= MIN_RUN_MI]
                    if not runs:
                        continue
                    keep_idx = np.concatenate(runs)
                    t, across = t[keep_idx], across[keep_idx]
                    coverage = len(set(np.floor(t / 0.25).astype(int)) & window_bins) / len(window_bins)
                    pieces = _fit_pieces(fam, t, across, side)
                    covered_mi = len(set(np.floor(t / 0.25).astype(int)) & window_bins) * 0.25
                    metrics = {"road": name, "class": cls, "coverage": round(coverage, 2), "covered_mi": covered_mi,
                               "offset_from_prior_mi": round(float(np.median(across - _position(fam, prior, t))), 3),
                               "offset_from_nominal_mi": round(float(np.median(across)) - k, 3)}
                    model_slope = fam.model[2] + fam.model[3] * k
                    if pieces is None:
                        metrics["rejected"] = "not straight"
                    elif len(pieces) > MAX_PIECES:
                        metrics["rejected"] = f"needs {len(pieces)} straight pieces"
                    elif abs(float(np.polyfit(t, across, 1)[0]) - model_slope) > MAX_SLOPE_DIFF:
                        metrics["rejected"] = "runs diagonal to the grid"  # overall; township jogs still pass
                    elif any(abs(float(np.median(across)) - u) < SPACING[0] for u in used.get(name, [])):
                        metrics["rejected"] = "already anchors the neighbouring line"
                    else:
                        metrics["rms_mi"] = round(max(p[4] for p in pieces), 4)
                        metrics["max_mi"] = round(max(p[5] for p in pieces), 4)
                        metrics["n_pieces"] = len(pieces)
                        # spacing to the neighbouring lines (found roads or the grid model), checked along
                        # the candidate's whole run — roads can converge away from their middle
                        for ts_ in np.linspace(float(t.min()), float(t.max()), 5):
                            p = min(pieces, key=lambda q: 0 if q[0] <= ts_ <= q[1] else min(abs(ts_ - q[0]), abs(ts_ - q[1])))
                            here = float(_position(fam, (p[2], p[3]), np.array([ts_]))[0])
                            for nb in (k - 1, k + 1):
                                v = fam.resolve(nb, float(ts_), False)
                                gap = abs(here - float(_position(fam, v, np.array([ts_]))[0]))
                                if not (SPACING[0] <= gap <= SPACING[1]):
                                    metrics["rejected"] = f"{gap:.2f} mi from line {nb} at {ts_:.1f}"
                                    break
                            if "rejected" in metrics:
                                break
                        covered = metrics["covered_mi"]
                        if abs(metrics["offset_from_prior_mi"]) > MAX_PRIOR_OFFSET and "rejected" not in metrics:
                            metrics["rejected"] = f"{metrics['offset_from_prior_mi']:+.2f} mi off the grid model"
                        enough = OWN_ROAD_MIN_MILES if name == own_road else min_miles
                        if coverage < min_coverage and covered < enough and "rejected" not in metrics:
                            metrics["rejected"] = f"runs {covered:.1f} mi, {coverage:.0%} of the window"
                    score = coverage - 2 * abs(metrics["offset_from_prior_mi"]) - (0 if cls in ("2", "3") else 0.1) \
                        + (1.0 if name == own_road else 0.0)  # the city's own mile road continuing wins
                    scored.append((("rejected" not in metrics), score, metrics, pieces))
                scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
                slot = f"{k}:{side}"
                candidates[key][slot] = {"window_mi": [round(w_lo_t, 2), round(w_hi_t, 2)],
                                         "prior": {"source": prior[3], "rule": prior[2]},
                                         "top": [r[2] for r in scored[:3]]}
                if not scored or not scored[0][0]:
                    continue
                _, _, best, pieces = scored[0]
                used.setdefault(best["road"], []).append(k + best["offset_from_nominal_mi"])
                city_road = _base_road_name(table.get(k, ""))
                rule = 1 if best["road"] == city_road else 2
                entry = anchors[key].setdefault(str(k), {"pieces": []})
                new = [Piece(m=p[2], c=p[3], side=side, lo=p[0], hi=p[1], rule=rule, road=best["road"]) for p in pieces]
                entry["pieces"].extend({"side": side, "road": best["road"], "rule": rule, "from_mi": round(p[0], 3),
                                        "to_mi": round(p[1], 3), "slope": p[2], "intercept": p[3],
                                        "rms_mi": round(p[4], 4), "max_mi": round(p[5], 4), "status": "proposed"}
                                       for p in pieces)
                fam.pieces.setdefault(k, []).extend(new)
                fam.pieces[k].sort(key=lambda p: p.lo)
        found = sum(1 for e in anchors[key].values() if e.get("pieces"))
        log(f"{key}: roads found for {found} lines")
    return anchors, candidates


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    frames = os.path.join(here, "data", "frames")
    out_dir = os.path.join(here, "data", "anchors")
    ap = argparse.ArgumentParser(description="Propose regional anchors (suburban mile roads) from OSM.")
    ap.add_argument("--boundary", required=True)
    ap.add_argument("--centerlines", required=True)
    ap.add_argument("--region", default=os.path.join(frames, "jrc2023.geojson"))
    ap.add_argument("--roads", choices=("tiger", "osm"), default="tiger",
                    help="road source: tiger (public domain, default) or osm (ODbL: a cross-check only — "
                         "anchors derived from it must not be committed to this MIT repo)")
    ap.add_argument("--roads-file", help="a derived roads GeoJSON to use instead of --roads")
    ap.add_argument("--band", type=float, default=0.4, help="miles either side of the prior to search")
    ap.add_argument("--min-coverage", type=float, default=0.5)
    args = ap.parse_args()

    from chigrid_frames import load_frame
    from chigrid_osm import cluster_roads
    from chigrid_tiger import load_roads
    from chigrid_streets import city_polygon, fit_city_lines

    log = lambda m: print(m, file=sys.stderr)
    boundary = json.load(open(args.boundary))
    centerlines = json.load(open(args.centerlines))
    if "features" not in centerlines and "data" in centerlines:
        centerlines = centerlines["data"]
    ns_all, ew_all = fit_city_lines(centerlines)
    city = city_polygon(boundary)
    region, _ = load_frame(args.region)
    roads = json.load(open(args.roads_file)) if args.roads_file else load_roads(args.roads, log=log)
    groups = cluster_roads(roads)
    log(f"{len(groups)} road groups")
    anchors, candidates = discover(ns_all, ew_all, city, region, groups, band=args.band,
                                   min_coverage=args.min_coverage, log=log)
    meta = {"version": 1, "status": "proposed", "chi_spec": "χ ordinates count mile roads from State & Madison",
            "roads_snapshot": (roads.get("properties") or {}).get("snapshot"),
            "roads_license": (roads.get("properties") or {}).get("license"),
            "region": os.path.relpath(args.region, here), "band_mi": args.band, "min_coverage": args.min_coverage}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "anchors_regional.proposed.json"), "w") as fh:
        json.dump({**meta, **anchors}, fh, indent=1)
    with open(os.path.join(out_dir, "candidates.json"), "w") as fh:
        json.dump({**meta, **candidates}, fh, indent=1)
    log(f"wrote {out_dir}/anchors_regional.proposed.json and candidates.json — review, then save as anchors_regional.json")


if __name__ == "__main__":
    main()
