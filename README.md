# χGRIDs — simple square polygons for Chicago's square-mile geography

**χGRIDs** (*Chicago Geographically Reductive Indexed Districts*) are a regular **square-mile geography for Chicago**. It tiles the city into one-mile cells called **χGRIDs**, each anchored to Chicago's address-grid origin at **State & Madison**.

It's a consistent spatial layer *beneath* neighborhoods and community areas — not a replacement for them. Where neighborhood names are fuzzy and overlapping, a chi is exact, uniform, and addressable. This repo holds the **methodology, the polygons, and a generator**, MIT-licensed so anyone can use, cite, or build on them.

```
χ:5S3W  =  Damen–43rd chi  =  "Dam43"
```

## The cell

Every χGRID is **one mile square**, with a simple rule:

- **Limits are mile roads.** A χGRID is bounded by the major mile-grid arterials.
- **Midpoints are half-mile roads.** Its center sits on the half-mile ("secondary") road between those mile roads, on both axes.

So the χ:5S3W χGRID is bounded east–west by **Ashland** (2 mi west) and **Western** (3 mi west) and north–south by **Pershing/39th** (4 mi south) and **47th** (5 mi south) — centered on **Damen** (2.5 mi west) and **43rd** (4.5 mi south). That's the **Damen–43rd chi**.

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
| **Name** | `Damen–43rd χ` | human-readable |
| **Label** | `Dam43` | compact, for maps and tables |

The center roads are always **half-mile (secondary) roads** — never the mile roads, which are the *limits*. Cells with no major secondary road on either band (the Loop core, the lakefront, the far edges) fall back to their canonic halfmile avenue for the name.

## Geographic, not address-number

χGRID uses **real miles on the ground**, not Chicago's address numbers. The south-side street numbers overstate distance — 43rd Street is ~4.5 mi south, not 5.4 — so the grid reads geographically: 43rd lands in the **5S** band, Ashland on the **2W/3W** boundary. Chicago's physical arterials sit ~0.5 mi apart regardless of their numbering, so the cells line up with the real street grid.

## The data

| File | What |
| --- | --- |
| [`data/chigrid.geojson`](data/chigrid.geojson) | the 302 chi polygons (WGS84), with `chi_id`, `chi_ordinate`, `name`, `label`, `center` |
| [`data/chigrid.csv`](data/chigrid.csv) | the same cells as a flat table (ordinate, id, name, label, center lat/lon) |

Each GeoJSON feature:

```json
{
  "type": "Feature",
  "geometry": { "type": "Polygon", "coordinates": [ [ [lon, lat], ... ] ] },
  "properties": {
    "chi_id": "5S3W",
    "chi_ordinate": "χ:5S3W",
    "name": "Damen–43rd χ",
    "label": "Dam43",
    "center": [-87.6763, 41.8168]
  }
}
```

### Assigning a point to its χGRID

The polygons are plain GeoJSON, so any GIS tool works. In Python:

```python
import json
from shapely.geometry import shape, Point

cells = json.load(open("data/chigrid.geojson"))["features"]
def chi_for(lat, lon):
    p = Point(lon, lat)
    return next((c["properties"]["chi_ordinate"] for c in cells
                 if shape(c["geometry"]).covers(p)), None)

chi_for(41.8168, -87.6763)  # -> "χ:5S3W"
```

## Regenerating

[`generate.py`](generate.py) reproduces the cell geometry and ordinates exactly:

```bash
python generate.py --boundary city_boundary.geojson > data/chigrid.geojson
```

It names each cell from the canonical secondary road on its band. The published `data/chigrid.geojson` refines those names against Chicago's street-centerline geometry (so the lakefront avenue reads Broadway/Sheridan where it actually does, and downtown cells pick up local streets), but the **geometry and ordinates are identical**.

## Explore

Open [`explorer.html`](explorer.html) in a browser (served from this folder, e.g. `python -m http.server`) for an interactive map: click or search any chi to see its name, ordinate, label, and center.

## License & attribution

**MIT** — see [LICENSE](LICENSE). Use, reference, and build on chiGRID freely. The polygons are derived entirely from public data (Chicago's address grid, the city boundary, and street
centerlines).

If you use χGRIDs, a link back to this repo is appreciated but not required.
