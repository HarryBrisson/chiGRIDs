import pytest

from chigrid import MILE_LAT, MILE_LON, ORIGIN_LAT, ORIGIN_LON, _base_road_name
from chigrid_osm import (
    DEFAULT_BBOX, bbox_miles, cluster_roads, highway_to_class, normalize_name, parse_other_tags, split_name,
)


@pytest.mark.parametrize("raw, expected", [
    ("West North Avenue", "NORTH"),
    ("North Avenue", "NORTH"),
    ("North Shore Ave", "NORTH SHORE"),
    ("West North Shore Avenue", "NORTH SHORE"),
    ("South Chicago Avenue", "SOUTH CHICAGO"),
    ("East Avenue", "EAST"),
    ("Northwest Highway", "NORTHWEST"),
    ("West Northwest Highway", "NORTHWEST"),
    ("Dempster Street", "DEMPSTER"),
    ("W Dempster St", "DEMPSTER"),
    ("Golf Road", "GOLF"),
    ("North Wolf Road", "WOLF"),
    ("S. Roselle Rd.", "ROSELLE"),
    ("West 159th Street", "159TH"),
    ("E 143rd St", "143RD"),
    ("North Lake Shore Drive", "LAKE SHORE"),
    ("Saint Charles Road", "SAINT CHARLES"),
    ("St. Charles Road", "ST CHARLES"),
    ("Lincoln Highway", "LINCOLN"),
    ("Illinois Prairie Trail", "ILLINOIS PRAIRIE"),
    ("Kennedy Expressway", "KENNEDY EXPRESSWAY"),
    ("Avenue O", "AVENUE O"),
    ("Broadway", "BROADWAY"),
    ("", ""),
    (None, ""),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_split_name_keeps_type_and_feeds_base_road_name():
    assert split_name("West Central Avenue") == ("CENTRAL", "AVE")
    assert split_name("Central Road") == ("CENTRAL", "RD")
    assert split_name("Mannheim Rd") == ("MANNHEIM", "RD")
    # STREET_NAME is already a base name: chigrid's own normalizer leaves it alone (modulo case)
    assert _base_road_name(normalize_name("West Oakton Street")) == "oakton"


@pytest.mark.parametrize("highway, cls", [
    ("motorway", "1"),
    ("motorway_link", "5"), ("trunk_link", "5"), ("primary_link", "5"), ("secondary_link", "5"), ("tertiary_link", "5"),
    ("trunk", "2"), ("primary", "2"),
    ("secondary", "3"), ("tertiary", "3"),
    ("residential", "4"), ("unclassified", "4"), ("living_street", "4"),
    ("service", "9"), ("track", "9"),
    ("footway", None), ("cycleway", None), ("", None), (None, None),
])
def test_highway_to_class(highway, cls):
    assert highway_to_class(highway) == cls


def test_parse_other_tags():
    tags = parse_other_tags('"ref"=>"IL 83;US 12","lanes"=>"4","note"=>"say \\"hi\\""')
    assert tags == {"ref": "IL 83;US 12", "lanes": "4", "note": 'say "hi"'}
    assert parse_other_tags(None) == {}


def test_default_bbox_covers_jrc_extent_plus_margin():
    reach = bbox_miles(DEFAULT_BBOX)
    # region R = jrc2023 bounds (W30.35 E11.87 S25.34 N22.07, data/frames provenance) + 2 mi, plus room
    # west/south to fit lines just beyond it
    assert reach["west"] >= 32.35 + 2 and reach["south"] >= 27.34 + 2
    assert reach["east"] >= 13.87 and reach["north"] >= 24.07


def _way(osm_id, name, typ, xy, highway="secondary"):
    """A feature from χ-mile coordinates (x east, y north of State & Madison)."""
    coords = [[ORIGIN_LON + x * MILE_LON, ORIGIN_LAT + y * MILE_LAT] for x, y in xy]
    return {"type": "Feature",
            "properties": {"STREET_NAME": name, "STREET_TYP": typ, "CLASS": highway_to_class(highway),
                           "highway": highway, "osm_id": osm_id, "ref": None},
            "geometry": {"type": "LineString", "coordinates": coords}}


def test_cluster_roads_synthetic():
    features = [
        # Dempster St at 10N: three ways, the middle one starting 0.2 mi past the first's end (a gap bridged)
        _way(1, "DEMPSTER", "ST", [(-20, 10), (-15, 10.05)]),
        _way(2, "DEMPSTER", "ST", [(-14.8, 10.05), (-10, 10.1)]),
        _way(3, "DEMPSTER", "ST", [(-10, 10.1), (-5, 10.1)], highway="primary"),
        # a different Dempster 0.6 mi past the end: too far to chain
        _way(4, "DEMPSTER", "ST", [(-4.4, 10.1), (-3, 10.1)], highway="residential"),
        # Central Ave (N/S) and Central Rd (E/W) touch end to end but must stay apart
        _way(5, "CENTRAL", "AVE", [(-6, 0), (-6, 12)]),
        _way(6, "CENTRAL", "RD", [(-6, 12), (-12, 12.2)]),
        # an untyped Central way continuing the avenue joins it
        _way(7, "CENTRAL", "", [(-6, 12.1), (-6, 14)]),
        # two Lake Sts far apart, and a T-junction spur whose endpoint lies on the first one's middle
        _way(8, "LAKE", "ST", [(-30, 0.5), (-20, 0.5)]),
        _way(9, "LAKE", "ST", [(5, 30), (6, 30)]),
        _way(10, "LAKE", "ST", [(-25, 0.5), (-25, 0)], highway="residential"),
        # unnamed way: ignored
        _way(11, "", "", [(0, 0), (1, 0)]),
    ]
    groups = cluster_roads({"type": "FeatureCollection", "features": features})
    by_ids = {tuple(sorted(g.osm_ids)): g for g in groups}
    assert set(by_ids) == {(1, 2, 3), (4,), (5, 7), (6,), (8, 10), (9,)}

    dempster = by_ids[(1, 2, 3)]
    assert dempster.name == "DEMPSTER" and dempster.street_type == "ST" and dempster.axis == "ew"
    assert dempster.x_span_mi == pytest.approx(15, abs=1e-6)
    assert dempster.y_span_mi == pytest.approx(0.1, abs=1e-6)
    assert dempster.length_mi == pytest.approx(5 + 4.8 + 5, abs=0.01)
    assert dempster.road_class == "3" and set(dempster.highways) == {"secondary", "primary"}
    assert dempster.med_lat == pytest.approx(ORIGIN_LAT + 10.075 * MILE_LAT)  # median vertex

    avenue = by_ids[(5, 7)]
    assert avenue.street_type == "AVE" and avenue.axis == "ns"
    assert avenue.lat_span[1] == pytest.approx(ORIGIN_LAT + 14 * MILE_LAT)
    assert len(avenue.lon) == 4

    assert [g.group_id for g in groups] == list(range(len(groups)))
    assert groups[0].length_mi == max(g.length_mi for g in groups)
    assert sorted(groups[0].summary()) == ["axis", "class", "group_id", "length_mi", "name", "refs", "type", "ways", "x_mi", "y_mi"]


def test_cluster_roads_split_types_off_chains_across_types():
    features = [
        _way(1, "YORK", "RD", [(-15.5, -5), (-15.5, -0.85)]),
        _way(2, "YORK", "ST", [(-15.5, -0.85), (-16, 3.4)]),
        _way(3, "YORK", "RD", [(-16, 3.4), (-16, 7.7)]),
    ]
    assert sorted(len(g.osm_ids) for g in cluster_roads(features)) == [1, 1, 1]
    (york,) = cluster_roads(features, split_types=False)
    assert sorted(york.osm_ids) == [1, 2, 3] and york.y_span_mi == pytest.approx(12.7)


def test_read_layercake_selects_road_ways_in_bbox(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    shapely = pytest.importorskip("shapely")
    from chigrid_osm import read_layercake

    rows = [  # (type, id, highway, name, ref, geometry)
        ("way", 1, "secondary", "West Dempster Street", "US 14", shapely.LineString([(-87.9, 42.0), (-87.8, 42.0)])),
        ("way", 2, "footway", "Dempster Path", None, shapely.LineString([(-87.9, 42.01), (-87.8, 42.01)])),
        ("node", 3, "residential", "A Node", None, shapely.Point(-87.85, 42.0)),
        ("way", 4, "residential", "Far Away Road", None, shapely.LineString([(-90.0, 40.0), (-89.9, 40.0)])),
        ("way", 5, "service", None, None, shapely.LineString([(-87.9, 42.02), (-87.8, 42.02)])),
        ("way", 6, "motorway", None, "I 294", shapely.LineString([(-87.88, 41.9), (-87.88, 42.1)])),
    ]
    bounds = [g.bounds for *_, g in rows]
    table = pa.table({
        "type": [r[0] for r in rows], "id": [r[1] for r in rows], "highway": [r[2] for r in rows],
        "name": [r[3] for r in rows], "ref": [r[4] for r in rows],
        "bbox": pa.array([{"xmin": b[0], "ymin": b[1], "xmax": b[2], "ymax": b[3]} for b in bounds],
                         type=pa.struct([(k, pa.float32()) for k in ("xmin", "ymin", "xmax", "ymax")])),
        "geometry": [shapely.to_wkb(r[5]) for r in rows],
    })
    path = tmp_path / "slice.parquet"
    pq.write_table(table, path)

    features = read_layercake(path, DEFAULT_BBOX)
    props = {f["properties"]["osm_id"]: f["properties"] for f in features}
    assert set(props) == {1, 6}  # footway, node, out-of-bbox and unnamed service way all dropped
    assert props[1]["STREET_NAME"] == "DEMPSTER" and props[1]["STREET_TYP"] == "ST" and props[1]["CLASS"] == "3"
    assert props[1]["ref"] == "US 14" and props[1]["SOURCE"] == "osm"
    assert props[6]["STREET_NAME"] == "" and props[6]["CLASS"] == "1" and props[6]["ref"] == "I 294"


def test_snapshot_id_is_source_prefixed():
    from fetch_osm import parse_state_txt, snapshot_id

    assert snapshot_id("layercake", [{"last_modified": "2026-10-18T14:18:13Z"}]) == "layercake-20261018T141813Z"
    assert snapshot_id("geofabrik", [{"replication_timestamp": "2026-10-18T20:25:06Z"},
                                     {"replication_timestamp": "2026-10-17T20:25:06Z"}]) == "geofabrik-20261018T202506Z"
    assert parse_state_txt("# c\ntimestamp=2026-10-18T20\\:25\\:06Z\nsequenceNumber=4944\n") == {
        "timestamp": "2026-10-18T20:25:06Z", "sequenceNumber": "4944"}
