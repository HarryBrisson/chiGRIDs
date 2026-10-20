"""Anchor discovery on a synthetic city + suburbs (no data needed)."""
import numpy as np
from shapely.geometry import box

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON
from discover_anchors import discover


def lon(w):
    return ORIGIN_LON - w * MILE_LON


def lat(s):
    return ORIGIN_LAT - s * MILE_LAT


class G:
    def __init__(self, name, lon, lat, road_class="3"):
        self.name, self.lon, self.lat, self.road_class = name, np.asarray(lon), np.asarray(lat), road_class


def test_discovers_suburban_roads_and_flags_half_mile_edge(monkeypatch):
    import chigrid_region
    import discover_anchors

    table_ns = {k: f"ave{k}" for k in range(0, 4)}
    table_ew = {0: "st0", 1: "st1", 2: "st2", 3: "st3", 4: "edge"}  # 'edge' is a half-mile street
    for mod in (chigrid_region, discover_anchors):
        monkeypatch.setattr(mod, "ANCHOR_NS", table_ns)
        monkeypatch.setattr(mod, "ANCHOR_EW", table_ew)
    ns_all = {k: (0.0, lon(k)) for k in range(0, 4)}
    ew_all = {k: (0.0, lat(k)) for k in range(0, 4)}
    ew_all[4] = (0.0, lat(3.5))
    city = box(lon(3.5), lat(3.5), lon(0), lat(0))
    region = box(lon(12), lat(9), lon(0), lat(0))
    s = np.linspace(-0.5, 9.5, 400)
    w = np.linspace(-0.5, 12.5, 400)
    groups = [G(f"ave{k}", lon(np.full_like(s, k + 0.02)), lat(s)) for k in range(0, 4)]
    groups += [G(f"suburb{k}", lon(np.full_like(s, k - 0.05)), lat(s)) for k in range(4, 13)]
    groups += [G(f"st{k}", lon(w), lat(np.full_like(w, k + 0.03))) for k in range(0, 4)]
    groups += [G(f"road{k}", lon(w), lat(np.full_like(w, k))) for k in range(4, 10)]  # 4 = the real mile road
    groups += [G("halfway", lon(w), lat(np.full_like(w, 5.5)))]                           # never a mile road
    groups += [G("ave1 expressway", lon(np.full_like(s, 1.0)), lat(s), road_class="1")]  # skipped class
    anchors, candidates = discover(ns_all, ew_all, city, region, groups, pad_miles=0, log=lambda m: None)
    assert anchors["ew"]["4"]["core"] is False
    roads = {k: {p["road"] for p in v["pieces"]} for k, v in anchors["ew"].items()}
    assert roads["4"] == {"road4"} and roads["6"] == {"road6"}
    assert "halfway" not in set().union(*roads.values())
    ns_roads = {k: {p["road"] for p in v["pieces"]} for k, v in anchors["ns"].items()}
    assert ns_roads["8"] == {"suburb8"}
    assert {p["rule"] for p in anchors["ns"]["2"]["pieces"]} == {1}  # the city's own avenue continues
    assert all(p["rms_mi"] <= 0.03 for v in anchors["ns"].values() for p in v["pieces"])
