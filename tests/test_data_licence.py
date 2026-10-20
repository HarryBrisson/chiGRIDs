"""Everything under data/ must stay MIT-compatible: nothing derived from OpenStreetMap (ODbL) ships."""
import glob
import json

from conftest import REPO

OSM_MARKERS = ("openstreetmap", "layercake", "geofabrik", "osm_id")


def test_regional_grids_are_built_from_tiger():
    paths = glob.glob(str(REPO / "data" / "v2" / "*" / "chigrid*.geojson"))
    assert paths
    for path in paths:
        props = json.load(open(path))["properties"]
        assert str(props.get("roads_snapshot", "")).startswith("tiger"), path
        assert "OpenStreetMap" not in props.get("attribution", ""), path


def test_no_osm_derived_text_in_committed_data():
    for path in glob.glob(str(REPO / "data" / "**" / "*"), recursive=True):
        if "/data/osm/" in path or path.endswith((".zip", ".png", "LICENSE-DATA.md")) or not path.endswith((".json", ".geojson", ".csv")):
            continue
        text = open(path, encoding="utf-8").read().lower()
        assert not any(m in text for m in OSM_MARKERS), path
