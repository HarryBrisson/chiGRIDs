"""chiGRID — Chicago Geographically Reductive Indexed Districts.

A proposed regular square-mile geography for Chicago. The city is tiled into one-mile cells
called *chis*, each anchored to Chicago's address grid origin at **State & Madison**. A chi is
named x-before-y for its center half-mile roads (``Damen–43rd chi``), carries a compact label
(``Dam43``), and a formal *chi-ordinate* indexing its position (``χ:5S3W`` = the cell in the 5th
mile-band south and 3rd mile-band west of State & Madison).

chiGRID does not replace neighborhood or community-area names; it is a consistent spatial layer
*beneath* them — which is exactly why it slots in as a third ``area_type`` alongside ward and
community_area now that the allocation backbone is geography-generic.

The grid is **geographic** (real miles on the ground), not address-number based — Chicago's
south-side street numbers overstate distance (43rd St is ~4.5 mi south, not 5.4), so 43rd reads
into the 5S band and Ashland on the 2W/3W boundary.

Calibration (Harry's rule): cell **limits are mile roads**, cell **midpoints are half-mile roads**,
on both axes. So χ:5S3W is bounded E/W by Ashland (2 mi) and Western (3 mi) and centered on Damen
(2.5 mi), bounded N/S by 39th (4 mi) and 47th (5 mi) and centered on 43rd (4.5 mi) — i.e. the
Damen–43rd chi (label ``Dam43``, x/avenue before y/street). Ordinates are 1-indexed and number-first
(the cells touching the origin are 1S1W / 1S1E / 1N1W / 1N1E, never a confusing 0).
"""

from __future__ import annotations

import math
import re
from typing import Any

# State & Madison: the origin of Chicago's street-address grid.
ORIGIN_LAT = 41.88197
ORIGIN_LON = -87.62768
MILE_LAT = 1.0 / 69.0  # degrees latitude per mile
MILE_LON = MILE_LAT / math.cos(math.radians(ORIGIN_LAT))  # degrees longitude per mile here

# A chi's center sits on the HALF-mile road between the mile roads that bound it, so a cell is named
# for that center road — never a mile road (Ashland/Kedzie/Madison are limits, not centers). These
# tables map each geographic band index (cell center at band+0.5 miles from State & Madison) to its
# center arterial. Bands are GEOGRAPHIC, not address-number: Chicago's south-side numbered streets
# sit ~0.5 mi apart on the ground even though their address numbers don't (so the S4 center is 43rd,
# not 36th). "" = no major through arterial on that half-mile line (e.g. the Loop) -> name falls back
# to the chi-ordinate. The west avenues here are independently confirmed by the street-centerline grid.
# Canonical half-mile ("secondary") roads — the road that crosses State (E/W streets) or Madison
# (N/S avenues) on each band's mid-mile line. Sourced from Wikipedia's Chicago secondary-streets list
# and placed at their real geographic band; "" = no major secondary road on that line (e.g. the Loop).
# Index = geographic band (cell center at band+0.5 mi from State & Madison).
AVENUES_W: list[str] = [  # N/S avenues, miles-west band
    "River", "Racine", "Damen", "California", "Central Park", "Kostner", "Laramie", "Austin", "Oak Park",
    "Oriole", "Cumberland",
]
AVENUES_E: list[str] = [  # N/S avenues, miles-east band
    "Martin Luther King", "Woodlawn", "Jeffery", "Burnham", "Avenue L",
]
STREETS_S: list[str] = [  # E/W streets, miles-south band
    "Congress", "16th", "26th", "35th", "43rd", "51st", "59th", "67th", "75th", "83rd", "91st", "99th",
    "107th", "115th", "123rd", "130th",
]
STREETS_N: list[str] = [  # E/W streets, miles-north band
    "Kinzie", "Division", "Armitage", "Diversey", "Addison", "Montrose", "Foster", "Peterson",
    "Pratt", "Howard",
]

# Reference gazetteer of MILE roads (cell limits) — used only to validate that no mile road is ever
# emitted as a center name. Geographic latitude/longitude.
MILE_ROADS_NS = {"State", "Halsted", "Ashland", "Western", "Kedzie", "Pulaski", "Cicero", "Central", "Harlem"}
MILE_ROADS_EW = {"Madison", "Roosevelt", "Cermak", "31st", "Pershing", "47th", "Garfield", "63rd",
                 "North", "Fullerton", "Belmont", "Irving Park", "Lawrence", "Bryn Mawr", "Devon", "Chicago"}


def miles_south(lat: float) -> float:
    return (ORIGIN_LAT - lat) / MILE_LAT


def miles_west(lon: float) -> float:
    return (ORIGIN_LON - lon) / MILE_LON


def chi_ordinate(center_lat: float, center_lon: float) -> str:
    """Formal chi-ordinate (e.g. ``χ:5S3W``) for a cell center, relative to State & Madison.

    1-indexed and number-first: the four cells touching the origin are 1S1W / 1S1E / 1N1W / 1N1E
    (no confusing 0s), and the number is the count of mile-bands out (Damen–43rd, ~4.5 mi S / ~2.5 mi
    W, is 5S3W — the 5th band south, 3rd band west)."""
    s = miles_south(center_lat)
    w = miles_west(center_lon)
    # Centers sit on whole-half-mile lines; guard floor() against float undershoot. +1 -> 1-indexed.
    ns = f"{int(math.floor(abs(s) + 1e-9)) + 1}{'S' if s >= 0 else 'N'}"
    ew = f"{int(math.floor(abs(w) + 1e-9)) + 1}{'W' if w >= 0 else 'E'}"
    return f"χ:{ns}{ew}"


# Disambiguated short tokens where the default first-3 collides (Division/Diversey both -> "Div").
_SHORT_OVERRIDES = {"Division": "Divi", "Diversey": "Dive"}


def _short(name: str) -> str:
    """Compact token for the label: '43rd' -> '43', 'Ashland' -> 'Ash', 'Avenue O' -> 'O'."""
    if name in _SHORT_OVERRIDES:
        return _SHORT_OVERRIDES[name]
    if name.startswith("Avenue ") and len(name.split()) == 2:  # the East Side lettered avenues
        return name.split()[1]
    stripped = name.split()[0]
    if stripped[0].isdigit():
        return "".join(ch for ch in stripped if ch.isdigit())
    return stripped[:3]


def _center_road(table: list[str], band: int) -> str:
    return table[band] if 0 <= band < len(table) else ""


def chi_name_and_label(center_lat: float, center_lon: float, *, name_radius: float = 0.6) -> tuple[str, str]:
    """Public name (``Damen–43rd chi``) and compact label (``Dam43``), x/avenue (N/S road) before
    y/street (E/W road). The name is the cell's CENTER half-mile road on each axis, looked up by the
    cell's geographic band — so a mile road (a cell limit) can never be a center name. Falls back to
    the chi-ordinate when a band has no major center arterial (e.g. the Loop)."""
    s = miles_south(center_lat)
    w = miles_west(center_lon)
    ns_band = int(math.floor(abs(s) + 1e-9))  # latitude band  -> E/W street (y)
    ew_band = int(math.floor(abs(w) + 1e-9))  # longitude band -> N/S avenue (x)
    avenue = _center_road(AVENUES_W if w >= 0 else AVENUES_E, ew_band)
    street = _center_road(STREETS_S if s >= 0 else STREETS_N, ns_band)
    ordinate = chi_ordinate(center_lat, center_lon)
    if avenue and street:
        return f"{avenue}–{street} chi", f"{_short(avenue)}{_short(street)}"
    return f"{ordinate} chi", ordinate


# Major arterials that road_grid omits because they're diagonal (not grid-aligned). name -> run.
DIAGONAL_ARTERIALS: dict[str, str] = {
    "broadway": "north_south", "sheridan": "north_south", "clark": "north_south",
    "lincoln": "north_south", "milwaukee": "north_south", "clybourn": "north_south",
    "elston": "north_south", "ridge": "north_south", "blue island": "north_south",
    "indianapolis": "north_south", "brainard": "north_south", "avenue o": "north_south",
    "archer": "east_west", "ogden": "east_west", "grand": "east_west",
}
_ROAD_TYPE_SUFFIX = re.compile(
    r"\s+(avenue|street|boulevard|road|drive|place|parkway|court|terrace|lane|way|ave|st|blvd|rd|dr|pl)$",
    re.IGNORECASE,
)


def _base_road_name(name: Any) -> str:
    return _ROAD_TYPE_SUFFIX.sub("", str(name or "").strip().lower()).strip()


def _pretty_road_name(base: str) -> str:
    """Title-case a base road name, keeping ordinal suffixes lower (43rd) and Mc names (McClurg)."""
    s = base.title()
    s = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), s)
    s = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), s)
    return s


def build_road_namer(
    street_centerlines: dict[str, Any] | None,
    road_grid: list[dict[str, Any]] | None,
    *,
    name_miles: float = 0.6,
    span_buffer_miles: float = 0.2,
    broad_min_miles: float = 1.0,
    wide_miles: float = 1.6,
):
    """Build a center-road namer from real street geometry. Returns ``(center_lat, center_lon) ->
    (avenue_name|None, street_name|None)``, or None if inputs are unavailable.

    Each chi is named for the major arterial whose grid line is nearest its exact center on each axis
    — by the road's representative latitude (E/W streets) / longitude (N/S avenues), NOT winding
    geometry, so the canonical mid-mile road wins (26th over 24th, Peterson over Thorndale, never a
    mile road 0.5 mi off). A road must also span the cell on the other axis, so the avenue follows the
    real road as it changes name going out (Racine → Broadway → Sheridan). The arterial set =
    road_grid mile/half-mile/major roads + curated diagonals (Broadway, Sheridan, …) + numbered E/W
    streets (the grid streets road_grid tags residential)."""
    if not street_centerlines or not street_centerlines.get("features"):
        return None
    try:
        from shapely.geometry import shape
    except Exception:
        return None

    # Two tiers of candidates. PRIMARY = the CANONICAL mid-mile centers — the curated band tables (the
    # secondary roads crossing State/Madison; they know 43rd/Peterson are centers, not residential
    # 42nd/44th, and exclude the mile roads that are cell LIMITS) plus the diagonals (Broadway/Sheridan/
    # …) so the avenue can change name going out. FALLBACK = any prominent through-street (runs ≥
    # broad_min_miles on its axis), used ONLY for cells the canonical set can't reach (the downtown 0.5-mi
    # lines, the far edges) so they read as a real street, never a bare ordinate. Real geometry positions
    # everything by its representative lon/lat.
    avenue_names = (
        {_base_road_name(n) for table in (AVENUES_W, AVENUES_E) for n in table if n}
        | set(DIAGONAL_ARTERIALS)
    )
    street_names = {_base_road_name(n) for table in (STREETS_S, STREETS_N) for n in table if n}
    # Mile roads are cell LIMITS, never centers — keep them out of the broad fallback too.
    mile_av = {_base_road_name(m) for m in MILE_ROADS_NS}
    mile_st = {_base_road_name(m) for m in MILE_ROADS_EW}
    skip_classes = {"1", "5", "9", "99", "RIV", "E", "S", "", " "}  # expressways, ramps, rivers, junk

    def _coords(geom):
        if geom.geom_type == "LineString":
            return list(geom.coords)
        if geom.geom_type == "MultiLineString":
            return [pt for line in geom.geoms for pt in line.coords]
        return []

    road_pts: dict[str, list] = {}
    for feature in street_centerlines.get("features", []):
        props = feature.get("properties") or {}
        base = _base_road_name(props.get("STREET_NAME"))
        if not base or str(props.get("CLASS") or "").strip() in skip_classes or not feature.get("geometry"):
            continue
        try:
            pts = _coords(shape(feature["geometry"]))
        except Exception:
            continue
        if pts:
            road_pts.setdefault(base, []).extend(pts)

    av_recs, st_recs, broad_av, broad_st = [], [], [], []  # (name, med, span_lo, span_hi)
    broad_min = broad_min_miles
    for base, pts in road_pts.items():
        xs = sorted(p[0] for p in pts)
        ys = sorted(p[1] for p in pts)
        med_lon, med_lat = xs[len(xs) // 2], ys[len(ys) // 2]
        lat_lo, lat_hi, lon_lo, lon_hi = ys[0], ys[-1], xs[0], xs[-1]
        name = _pretty_road_name(base)
        lat_span = (lat_hi - lat_lo) / MILE_LAT
        lon_span = (lon_hi - lon_lo) / MILE_LON
        if base in avenue_names:
            av_recs.append((name, med_lon, lat_lo, lat_hi))
        elif base not in mile_av and lat_span >= lon_span and lat_span >= broad_min:  # N/S through-street
            broad_av.append((name, med_lon, lat_lo, lat_hi))
        if base in street_names:
            st_recs.append((name, med_lat, lon_lo, lon_hi))
        elif base not in mile_st and lon_span > lat_span and lon_span >= broad_min:  # E/W through-street
            broad_st.append((name, med_lat, lon_lo, lon_hi))
    if not av_recs or not st_recs:
        return None
    name_lon, name_lat = name_miles * MILE_LON, name_miles * MILE_LAT
    wide_lon, wide_lat = wide_miles * MILE_LON, wide_miles * MILE_LAT
    span_lat, span_lon = span_buffer_miles * MILE_LAT, span_buffer_miles * MILE_LON

    def _nearest_avenue(recs, center_lat, center_lon, reach):
        best, found = reach, None
        for name, med_lon, lat_lo, lat_hi in recs:  # nearest avenue actually spanning this latitude
            if lat_lo - span_lat <= center_lat <= lat_hi + span_lat and abs(med_lon - center_lon) < best:
                best, found = abs(med_lon - center_lon), name
        return found

    def _nearest_street(recs, center_lat, center_lon, reach):
        best, found = reach, None
        for name, med_lat, lon_lo, lon_hi in recs:  # nearest street actually spanning this longitude
            if lon_lo - span_lon <= center_lon <= lon_hi + span_lon and abs(med_lat - center_lat) < best:
                best, found = abs(med_lat - center_lat), name
        return found

    def namer(center_lat: float, center_lon: float):
        avenue = _nearest_avenue(av_recs, center_lat, center_lon, name_lon)
        if avenue is None:  # nearest prominent through-avenue, not an ordinate
            avenue = _nearest_avenue(broad_av, center_lat, center_lon, name_lon)
        if avenue is None:  # edge cells (state line / lake): reach wider for any real road
            avenue = _nearest_avenue(av_recs + broad_av, center_lat, center_lon, wide_lon)
        street = _nearest_street(st_recs, center_lat, center_lon, name_lat)
        if street is None:
            street = _nearest_street(broad_st, center_lat, center_lon, name_lat)
        if street is None:
            street = _nearest_street(st_recs + broad_st, center_lat, center_lon, wide_lat)
        return avenue, street

    return namer


def _cell_polygon(center_lat: float, center_lon: float) -> list[list[float]]:
    """Square ring (GeoJSON [lon, lat] order) one mile on a side, centered on the point."""
    dlat, dlon = MILE_LAT / 2, MILE_LON / 2
    n, s = center_lat + dlat, center_lat - dlat
    e, w = center_lon + dlon, center_lon - dlon
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


def _boundary_geometry(boundary: Any):
    """Coerce a GeoJSON FeatureCollection / geometry / shapely geom into one shapely geometry."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    if hasattr(boundary, "geom_type"):
        return boundary
    if isinstance(boundary, dict) and boundary.get("type") == "FeatureCollection":
        # buffer(0) repairs the self-intersections common in published civic geojson before union.
        geoms = [shape(f["geometry"]).buffer(0) for f in boundary.get("features", []) if f.get("geometry")]
        return unary_union(geoms)
    if isinstance(boundary, dict) and boundary.get("type") == "Feature":
        return shape(boundary["geometry"])
    return shape(boundary)


def build_chigrid(
    boundary: Any,
    *,
    ns_center_phase: float = 0.5,
    ew_center_phase: float = 0.5,
    name_radius: float = 0.6,
    road_namer=None,
    region_grids=None,
) -> dict[str, Any]:
    """Generate the χGRID as a GeoJSON FeatureCollection of one-mile cells covering ``boundary``.

    A cell is kept when its square intersects the city boundary. ``ns_center_phase`` /
    ``ew_center_phase`` set where cell centers fall (in miles from State & Madison); the defaults
    (0.5 on both axes) put cell limits on mile roads and midpoints on half-mile roads, so χ:S4W2 is
    the Damen–43rd chi. When ``road_namer`` (from :func:`build_road_namer`) is given, cells are named
    from real street geometry; otherwise the geographic band tables are used as a fallback.
    """
    from shapely.geometry import Polygon

    geom = _boundary_geometry(boundary)
    if geom.is_empty:
        return {"type": "FeatureCollection",
                "properties": {"system": "chiGRID", "origin": "State & Madison", "cell_miles": 1, "count": 0},
                "features": []}
    min_lon, min_lat, max_lon, max_lat = geom.bounds

    # Range of integer cell steps (k) whose centers can cover the bbox, padded by one cell.
    s_lo = math.floor(miles_south(max_lat) - ns_center_phase) - 1
    s_hi = math.ceil(miles_south(min_lat) - ns_center_phase) + 1
    w_lo = math.floor(miles_west(max_lon) - ew_center_phase) - 1
    w_hi = math.ceil(miles_west(min_lon) - ew_center_phase) + 1

    features = []
    seen: set[str] = set()
    for ks in range(s_lo, s_hi + 1):
        center_lat = ORIGIN_LAT - (ns_center_phase + ks) * MILE_LAT
        for kw in range(w_lo, w_hi + 1):
            center_lon = ORIGIN_LON - (ew_center_phase + kw) * MILE_LON
            ring = _cell_polygon(center_lat, center_lon)
            if not Polygon(ring).intersects(geom):
                continue
            ordinate = chi_ordinate(center_lat, center_lon)
            if ordinate in seen:
                continue
            seen.add(ordinate)
            avenue = street = None
            if road_namer is not None:
                avenue, street = road_namer(center_lat, center_lon)
            # Fall back per axis to the canonical mid-mile road (the secondary road crossing
            # State/Madison on this band) when real geometry didn't name that axis.
            s, w = miles_south(center_lat), miles_west(center_lon)
            if not avenue:
                avenue = _center_road(AVENUES_W if w >= 0 else AVENUES_E, int(math.floor(abs(w) + 1e-9)))
            if not street:
                street = _center_road(STREETS_S if s >= 0 else STREETS_N, int(math.floor(abs(s) + 1e-9)))
            if avenue and street:
                name = f"{avenue}–{street} chi"
                label = f"{_short(avenue)}{_short(street)}"  # brief shortform, e.g. "Dam43"
            else:
                name, label = f"{ordinate} chi", ordinate  # unnamed: keep the χ:ordinate as the label
            chi_id = ordinate.replace("χ:", "")
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "chi_id": chi_id,
                    "chi_ordinate": ordinate,
                    "name": name,
                    "label": label,
                    "center": [round(center_lon, 6), round(center_lat, 6)],
                },
            })
    _apply_region_grids(features, region_grids)
    features.sort(key=lambda f: f["properties"]["chi_id"])
    return {
        "type": "FeatureCollection",
        "properties": {
            "system": "chiGRID",
            "origin": "State & Madison",
            "cell_miles": 1,
            "count": len(features),
        },
        "features": features,
    }


def _apply_region_grids(features: list[dict[str, Any]], region_grids) -> None:
    """Give still-unnamed cells inside a named region a local grid label (e.g. O'Hare's runway
    interior -> ``OHare-1A``). Each region: {name, polygon, n_origin (north band = row 1), w_origin
    (west band = col A)}. Only cells that fell back to an ordinate are relabeled."""
    if not region_grids:
        return
    from shapely.geometry import shape
    for feature in features:
        if not feature["properties"]["name"].startswith("χ:"):
            continue  # already has a real street name
        cell = shape(feature["geometry"])
        for region in region_grids:
            if not region["polygon"].intersects(cell):
                continue
            lon, lat = feature["properties"]["center"]
            # n_ord / w_ord are the 1-indexed ordinate band numbers (match n_origin/w_origin).
            n_ord = int(math.floor(abs(miles_south(lat)) + 1e-9)) + 1
            w_ord = int(math.floor(abs(miles_west(lon)) + 1e-9)) + 1
            row = region["n_origin"] - n_ord + 1
            col = chr(ord("A") + region["w_origin"] - w_ord)
            label = f"{region['name']}-{row}{col}"
            feature["properties"]["name"] = label
            feature["properties"]["label"] = label
            break


def ohare_region_grid(community_area_geojson: dict[str, Any] | None):
    """Build the O'Hare region-grid spec (community area #76) — its airport interior has no street
    grid, so those cells get an ``OHare-1A`` local grid instead of a bare ordinate. None if absent."""
    if not community_area_geojson:
        return None
    try:
        from shapely.geometry import shape
        from shapely.ops import unary_union
    except Exception:
        return None
    polys = []
    for feature in community_area_geojson.get("features", []):
        props = feature.get("properties", {})
        ident = str(props.get("community_area_id") or props.get("community_area_number") or "")
        if (ident == "76" or "hare" in str(props.get("name", "")).lower()) and feature.get("geometry"):
            polys.append(shape(feature["geometry"]).buffer(0))
    if not polys:
        return None
    return {"name": "OHare", "polygon": unary_union(polys), "n_origin": 9, "w_origin": 17}


def build_chi_profiles(chi_grid: dict[str, Any]) -> list[dict[str, Any]]:
    """Minimal per-chi area profiles for the metric pipeline (POI counts come from chi_places)."""
    return [
        {
            "chi_id": f["properties"]["chi_id"],
            "name": f["properties"]["name"],
            "chi_ordinate": f["properties"]["chi_ordinate"],
            "label": f["properties"]["label"],
        }
        for f in chi_grid.get("features", [])
    ]


def build_chi_places(chi_grid: dict[str, Any], places: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Assign places of interest to chis by point-in-polygon, with per-category counts —
    the chi analogue of build_community_area_places."""
    from shapely.geometry import Point, shape

    cells = []
    for f in chi_grid.get("features", []):
        if not f.get("geometry"):
            continue
        cells.append({
            "chi_id": f["properties"]["chi_id"],
            "name": f["properties"]["name"],
            "geometry": shape(f["geometry"]),
            "places": [],
        })
    for place in places or []:
        lng, lat = place.get("lng"), place.get("lat")
        if lng is None or lat is None:
            continue
        point = Point(lng, lat)
        for cell in cells:
            if cell["geometry"].covers(point):
                cell["places"].append(place)
                break

    results = []
    for cell in cells:
        cell_places = sorted(
            cell["places"], key=lambda p: (p.get("category") or "", p.get("name") or "", p.get("poi_id") or "")
        )
        counts: dict[str, int] = {}
        for place in cell_places:
            category = place.get("category")
            if category:
                counts[category] = counts.get(category, 0) + 1
        results.append({
            "chi_id": cell["chi_id"],
            "name": cell["name"],
            "place_ids": [p["poi_id"] for p in cell_places],
            "featured_place_ids": [p["poi_id"] for p in cell_places[:8]],
            "counts_by_category": counts,
        })
    return results
