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

χGRID uses **real miles on the ground**, not Chicago's address numbers. The south-side street numbers overstate distance — 43rd Street is ~4.5 mi south, not 5.4 — so the grid reads geographically: 43rd lands in the **5S** band, Ashland on the **2W/3W** boundary. Chicago's physical arterials sit ~0.5 mi apart regardless of their numbering, so the cells line up with the real street grid.

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

Each version ships the same two files in its folder:

| File | What |
| --- | --- |
| `chigrid.geojson` | the chi polygons (WGS84), with `chi_id`, `chi_ordinate`, `name`, `label`, `center` |
| `chigrid.csv` | the same cells as a flat table |

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

Both the boundary and the centerlines are public data (Chicago's [community-area boundaries](https://data.cityofchicago.org/) and [street-center lines](https://data.cityofchicago.org/) on the city data portal). The centerlines file is large (~100 MB), so it isn't bundled here — fetch it from the portal.

## Explore

Open [`explorer.html`](explorer.html) in a browser (served from this folder, e.g. `python -m http.server`) for an interactive map. Use the **version toggle** to switch between the v1 mile-squares and the v2 street-aligned grid; click or search any chi to see its name, ordinate, label, and center.

## License & attribution

**MIT** — see [LICENSE](LICENSE). Use, reference, and build on chiGRIDs freely. The polygons are derived entirely from public data (Chicago's address grid, the city boundary, and street centerlines).

If you use χGRIDs, a link back to this repo is appreciated but not required.
