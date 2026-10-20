# χGRIDs — a square-mile geography for Chicago

**χGRIDs** (*Chicago Geographically Reductive Indexed Districts*) tile the city into roughly one-mile cells called **χGRIDs**, each anchored to Chicago's address-grid origin at **State & Madison**.

It's a consistent spatial layer *beneath* neighborhoods and community areas — not a replacement for them. Where neighborhood names are fuzzy and overlapping, a chi is exact, uniform, and addressable. This repo holds the **methodology, the polygons, and the generators**, MIT-licensed so anyone can use, cite, or build on them.

```
χ:5S3W  =  Damen–43rd chi  =  "Dam43"
```

## Versions

There's more than one reasonable way to draw a square-mile grid over a real city, so chiGRIDs ships as versions — same ordinate system and naming, different geometry. Pick the one that fits your use.

| Version | Geometry | Cells | Folder | Best for |
| --- | --- | --- | --- | --- |
| **v1 — mile-squares** | Perfect 1-mile squares on the address grid, centered on State & Madison | 302 | [`data/v1/`](data/v1/) | A clean, perfectly regular lattice — every cell is exactly 1 sq mi |
| **v2 — street-aligned** | Cells bounded by Chicago's **real mile-road centerlines**, clipped to the city limit | 288 | [`data/v2/`](data/v2/) | Cells that are real sets of blocks — every border falls on an actual mile street |
| **v3 — street-snapped** | *(planned)* edges snapped to each street's actual curving geometry | — | — | Pixel-faithful borders that follow the real wiggle of each street |

**v1** is the purest abstraction: a flawless square grid that ignores where the streets actually run. Some folks prefer it precisely because it's perfectly uniform. **v2** trades that uniformity for realism — its borders sit on Western, Halsted, Irving Park, and the rest, so a chi is a genuine cluster of city blocks. The two share ordinates and names, so you can switch between them freely.

The concepts below (the cell, ordinates, names) are common to every version.

## Extents: which Chicago

Every version can tile one of three Chicagos. They share the origin, the ordinates and the names, and
**a chi inside the city is the same cell in every extent** (same id, same names, and, unless it gained
land across the city limit, byte-identical geometry).

| Extent | Footprint | Source | Folder |
| --- | --- | --- | --- |
| **`city`** | The city limit, 599 km² | City of Chicago community areas | `data/<version>/` |
| **`un2025`** | The UN's 2025 urban centre, 932 km² | GHSL [GHS-WUP-MTUC R2025A v1.1](https://human-settlement.emergency.copernicus.eu/), polygon `ID_UC_G0=7747` | `data/<version>/un2025/` |
| **`jrc2023`** | The JRC's 2023 urban centre, 2,231 km² | GHSL [GHS-SMOD R2023A](https://human-settlement.emergency.copernicus.eu/), epoch 2025, class 30, the component holding the Loop | `data/<version>/jrc2023/` |

Both UN frames come from the same global rule (the Degree of Urbanisation): one-kilometre squares with
at least 1,500 people per km² of land, touching side to side, 50,000 people in all, small holes
filled, edges smoothed by majority vote. The **2025** rule, made for UN World Urbanization Prospects,
counts people only. The **2023** release added one clause: a square also counts if at least half of
it is built up. That clause admits airports, rail yards, factories and malls, which sit exactly in the
gaps between Chicago and its neighbours, so fourteen centres become one and Chicago more than doubles.
Neither is wrong; they answer different questions. (The UN 2025 frame leaves out O'Hare and Lake
Calumet, about 13% of the city; `--include-city` keeps those cells.)

Cells on a frame's edge are handled one of two ways:

- **`whole`** (`chigrid.geojson`): keep a chi when at least half of its land lies inside the frame.
  Borders stay on mile roads.
- **`clip`** (`chigrid_clip.geojson`): cut each chi at the frame. Areas add up to the frame exactly,
  but edges follow the frame's staircase of tilted one-kilometre squares.

Framed cells add `frame_share` (share of the chi's land inside the frame), `in_city_share`,
`edge_kind` (`city`: it straddles the city limit, `frame`: it crosses the frame, `shore`: water cuts
it), and in v2 `offset_mi` and `bounds_rule` (below).

**Outside the city, ids count mile roads.** Past the city limit the mile roads keep counting from State
& Madison — Dempster, Wolf, 159th — and `χ:20S9W` is the band between the 19th and 20th mile roads
south and the 8th and 9th west. Chicago's road grid is rotated about a degree from true north and the
suburbs keep that rotation (Harlem runs from 9.2 mi west of State at Touhy to 8.35 mi at 28 S), so far
out a road-bounded chi can sit up to ~0.85 mi off its geographic mile band; `offset_mi` (miles west,
miles south) records how far. Inside the city the two agree: every city chi's lattice id equals its
geographic id (two slivers excepted, 11S6W and 6N16W, whose published ids are kept).

**How v2 extends past the city.** The city grid is built exactly as before. Around it, each grid line
continues as a chain of straight pieces that bend only at grid corners outside the city: the city's own
fitted line wherever a corner touches the city, then a straight fit to the real suburban road
(rule 1 the same road, rule 2 an aligned substitute; `data/anchors/anchors_regional.json`), else rule 3:
interpolated between neighbouring roads, or the line of a smooth model of the city grid — its ~1.006 mi
spacing and ~1° rotation, fit on the city's own mile roads, which the suburbs keep. Indiana is surveyed
on its own grid, so its lines are always rule 3. `bounds_rule` is the highest rule among a chi's sides.
Suburban roads come from the US Census Bureau's TIGER/Line files (public domain, so the regional grids
stay MIT-friendly; an OpenStreetMap build is available with `--roads osm` but is ODbL and never committed
here); `discover_anchors.py` proposes them —
searching around the grid model, rejecting diagonals, short runs and roads that would squeeze a
neighbour — for a person to review. Where the suburban grid is genuinely irregular (around O'Hare,
where the city's own edge columns are narrow, and in DuPage County, which has its own road grid) more
chis fall outside 0.9–1.1 sq mi and carry `flag: true`.

## The cell

Every χGRID is **about one mile square**, with a simple rule:

- **Limits are mile roads.** A χGRID is bounded by the major mile-grid arterials.
- **Midpoints are half-mile roads.** Its center sits on the half-mile ("secondary") road between those mile roads, on both axes.

So the χ:5S3W χGRID is bounded east–west by **Ashland** (2 mi west) and **Western** (3 mi west) and north–south by **Pershing/39th** (4 mi south) and **47th** (5 mi south) — centered on **Damen** (2.5 mi west) and **43rd** (4.5 mi south). That's the **Damen–43rd chi**.

In v1 those limits are drawn as idealized straight lines exactly one mile apart; in v2 they're the real centerlines of those same mile roads, so cell sizes vary a little with the real grid.

## Chi-ordinates

Each cell has a formal coordinate relative to State & Madison, written **number-first** and **1-indexed**:

```
χ:5S3W   →   the cell in the 5th mile-band South and the 3rd mile-band West of State & Madison
```

The number is the count of mile-bands out from the origin (the cell spans miles 4–5 south, 2–3 west); `S`/`N` and `W`/`E` give the direction. The four cells touching the origin are **1S1W, 1S1E, 1N1W, 1N1E** — there is no "0" band, so the index never reads as a false zero distance.

Ordinates are unique and the grid tiles cleanly — every point in the city falls in exactly one chi.

## Names & labels

A χGRID is named **x before y** — the north–south *avenue* first, then the east–west *street*:

| Form | Example | Use |
| --- | --- | --- |
| **Ordinate** | `χ:5S3W` | formal / sortable index |
| **Name** | `Damen–43rd chi` | human-readable |
| **Label** | `Dam43` | compact, for maps and tables |

The center roads are always **half-mile (secondary) roads** — never the mile roads, which are the *limits*.

**v2 carries two names per cell.** The grid runs on idealized straight mile-lines, but real streets wander, so each cell gets:

- a **local** name from the real arterials nearest the cell — `name` / `label` (e.g. Broadway/Sheridan on the lakefront, `Mus99` for Muskegon–99th in the Calumet pocket);
- a **canonical** name from the idealized mile-grid — `name_canon` / `label_canon` (e.g. `Bur99` for Burnham–99th, `RivCon` for the State-&-Madison cell where the river crosses Congress) — predictable and consistent across the whole grid even where those streets don't physically run.

## Geographic, not address-number

χGRID uses **real miles on the ground**, not Chicago's address numbers (outside the city, see
*Extents* above for how ids count mile roads). The south-side street numbers overstate distance — 43rd Street is ~4.5 mi south, not 5.4 — so the grid reads geographically: 43rd lands in the **5S** band, Ashland on the **2W/3W** boundary. Chicago's physical arterials sit ~0.5 mi apart regardless of their numbering, so the cells line up with the real street grid.

## How v2 is built

The street-aligned grid is constructed by rule, from Chicago's street-centerline geometry:

1. **Divide the city by the established mile roads.** Each mile road (State, Halsted, Ashland, Western, …, Roosevelt, Cermak, 47th, Irving Park, …) is fit to a clean straight line; cells are the blocks between them.
2. **Substitute where a mile road is missing.** Where a mile band has no through arterial, the nearest road broadly aligned with that grid line stands in (e.g. far-SE avenues — Stony Island, Yates, Burley — carry the East Side).
3. **Impute where no road is aligned.** Where there's no road at all, a straight line consistent with the neighboring mile roads is drawn, so every grid index gets a boundary and the city tiles with no gaps.
4. **Aim for ~1 sq mi.** Interior cells outside 0.9–1.1 sq mi are flagged (`flag: true`); cells clipped by the city limit are marked `edge: true`.

A few refinements keep it honest:

- **Straight edges (this version).** Each mile road becomes a single straight line, so a curving road can't bend the grid. Roads that genuinely curve away — Stony Island bends east at ~99th toward South Chicago, Cottage Grove at ~95th — are fit only on their straight run and then cut straight to the city edge.
- **Filled interior holes.** Cemeteries, rail yards, and similar parcels are inside Chicago even when they're left out of community-area boundaries, so interior holes are filled — the grid covers the whole city.
- **Real city limit.** The outer boundary is the actual city limit (lakefront, the O'Hare corridor, the southern border), so edge cells are honest partial cells, not forced squares.

## The data

Each version ships the same files in its folder:

| File | What |
| --- | --- |
| `chigrid.geojson` | the chi polygons (WGS84), with `chi_id`, `chi_ordinate`, `name`, `label`, `center` |
| `chigrid.csv` | the same cells as a flat table |
| `chigrid_<version>_shapefile.zip` | the same polygons as an ESRI Shapefile (WGS84; field names shortened to 10 characters: `chi_ord`, `label_can`) for ArcGIS and QGIS |
| `chigrid_<version>_geopackage.zip` | the same polygons as a GeoPackage, one layer `chigrid_<version>`, full field names |

Direct downloads: [v1 shapefile](data/v1/chigrid_v1_shapefile.zip) · [v1 GeoPackage](data/v1/chigrid_v1_geopackage.zip) · [v2 shapefile](data/v2/chigrid_v2_shapefile.zip) · [v2 GeoPackage](data/v2/chigrid_v2_geopackage.zip). The zips are written from the GeoJSON with GeoPandas; the GeoJSON stays the source of truth.

A **v1** GeoJSON feature ([`data/v1/chigrid.geojson`](data/v1/chigrid.geojson)):

```json
{
  "type": "Feature",
  "geometry": { "type": "Polygon", "coordinates": [ [ [lon, lat], ... ] ] },
  "properties": {
    "chi_id": "5S3W",
    "chi_ordinate": "χ:5S3W",
    "name": "Damen–43rd chi",
    "label": "Dam43",
    "center": [-87.6763, 41.8168]
  }
}
```

A **v2** feature ([`data/v2/chigrid.geojson`](data/v2/chigrid.geojson)) adds the canonical name, the cell area, and edge/flag markers (and a geometry that may be a `MultiPolygon` where the lakefront splits a cell):

```json
{
  "type": "Feature",
  "geometry": { "type": "Polygon", "coordinates": [ ... ] },
  "properties": {
    "chi_id": "5S3W", "chi_ordinate": "χ:5S3W",
    "name": "Damen–43rd chi", "label": "Dam43",
    "name_canon": "Damen–43rd chi", "label_canon": "Dam43",
    "center": [-87.6763, 41.8168],
    "area_sqmi": 1.003, "edge": false, "flag": false
  }
}
```

### Assigning a point to its χGRID

The polygons are plain GeoJSON, so any GIS tool works. In Python:

```python
import json
from shapely.geometry import shape, Point

cells = json.load(open("data/v2/chigrid.geojson"))["features"]
def chi_for(lat, lon):
    p = Point(lon, lat)
    return next((c["properties"]["chi_ordinate"] for c in cells
                 if shape(c["geometry"]).covers(p)), None)

chi_for(41.8168, -87.6763)  # -> "χ:5S3W"
```

## Regenerating

**v1** — [`generate.py`](generate.py) reproduces the mile-square geometry and ordinates from a city boundary:

```bash
python generate.py --boundary city_boundary.geojson > data/v1/chigrid.geojson
```

**v2** — [`generate_v2.py`](generate_v2.py) builds the street-aligned grid from the city boundary plus Chicago's street centerlines:

```bash
python generate_v2.py --boundary city_boundary.geojson \
                      --centerlines street_centerlines.geojson > data/v2/chigrid.geojson
```

**Extents.** Fetch the frames once, then pass `--extent` and `--edges`:

```bash
python fetch_frames.py                       # data/frames/{un2025,jrc2023,land,city}.geojson
python generate.py --extent un2025 --edges whole --boundary city_boundary.geojson \
                   --names-from data/v1/chigrid.geojson > data/v1/un2025/chigrid.geojson
python chigrid_tiger.py                      # TIGER/Line county road files (v2 only)
python discover_anchors.py --boundary city_boundary.geojson --centerlines street_centerlines.geojson
#   review data/anchors/anchors_regional.proposed.json, save it as anchors_regional.json
python generate_v2.py --extent jrc2023 --edges clip --boundary city_boundary.geojson \
                      --centerlines street_centerlines.geojson > data/v2/jrc2023/chigrid_clip.geojson
python export.py data/v2/jrc2023/chigrid_clip.geojson   # CSV, Shapefile, GeoPackage
```

`generate_v2.py` refuses to write a grid whose corner lattice fails its checks (lines too close,
crossing, or drifting more than 1.25 mi); `--allow-problems` writes a draft anyway. Tests: `pytest`
(set `CHIGRID_BOUNDARY` and `CHIGRID_CENTERLINES` to also run the golden and invariance checks).

Both the boundary and the centerlines are public data (Chicago's [community-area boundaries](https://data.cityofchicago.org/) and [street-center lines](https://data.cityofchicago.org/) on the city data portal). The centerlines file is large (~100 MB), so it isn't bundled here — fetch it from the portal.

## Explore

The interactive map is published at **[harrybrisson.github.io/chiGRIDs/explorer.html](https://harrybrisson.github.io/chiGRIDs/explorer.html)** (GitHub Pages, from `main`). Or open [`explorer.html`](explorer.html) locally (served from this folder, e.g. `python -m http.server`). Use the **version toggle** to switch between the v1 mile-squares and the v2 street-aligned grid, and the **extent toggle** to switch between the city, UN 2025 and JRC 2023 Chicagos (whole or clipped cells); click or search any chi to see its name, ordinate, label, and center.

## License & attribution

**MIT** for the code — see [LICENSE](LICENSE). Use, reference, and build on chiGRIDs freely. The city-extent polygons are derived entirely from public data (Chicago's address grid, the city boundary, and street centerlines). The UN frames are © European Union, EC JRC GHSL (CC BY 4.0, attribution required), and the street-aligned regional grids are fitted to US Census TIGER/Line roads (public domain) — see [data/LICENSE-DATA.md](data/LICENSE-DATA.md).

If you use χGRIDs, a link back to this repo is appreciated but not required.
