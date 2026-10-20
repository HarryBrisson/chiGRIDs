# Data licences

The code in this repository is MIT-licensed (see `../LICENSE`). Some files under `data/` are
derived from third-party data and carry the licence of their source. Each frame GeoJSON records its
exact source, archive sha256 and recipe in `features[0].properties.provenance`; `fetch_frames.py`
rebuilds them.

## GHSL (European Commission, Joint Research Centre) — CC BY 4.0

- `data/frames/un2025.geojson` is derived from **GHS_WUP_MTUC_GLOBE_R2025A v1.1**, the GHSL
  urban-centre polygons behind UN World Urbanization Prospects 2025 (Chicago: `ID_UC_G0=7747`).
- `data/frames/jrc2023.geojson` is derived from **GHS_SMOD_E2025_GLOBE_R2023A_54009_1000 V2.0**, the
  GHSL Settlement Model grid (class 30, "urban centre").

Licence: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
Attribution: © European Union, 1995–2026, European Commission Joint Research Centre, Global Human
Settlement Layer (GHSL). Source: https://human-settlement.emergency.copernicus.eu/. Changes made:
one polygon or one connected component selected, vectorized, holes filled (jrc2023), edges densified
and reprojected from Mollweide (ESRI:54009) to WGS 84 (EPSG:4326).

## US Census Bureau — public domain

- `data/frames/land.geojson` is derived from the 2020 cartographic boundary file of states at
  1:500,000 (`cb_2020_us_state_500k`): Illinois ∪ Indiana, clipped to the regional extent.

US federal government works are not subject to copyright in the United States (17 U.S.C. § 105).

## City of Chicago

- `data/frames/city.geojson` and the city-extent grids in `data/v1/` and `data/v2/` are derived from
  the City of Chicago's community-area boundaries, published on the City of Chicago Data Portal
  under its [terms of use](https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html).

## US Census Bureau TIGER/Line roads — public domain

- The regional street-aligned grids (`data/v2/un2025/`, `data/v2/jrc2023/`), the regional anchors
  (`data/anchors/`) and the local names of chis outside the city are fitted to the **2025 TIGER/Line
  roads** for Cook, DuPage, Kane, Kendall, Lake, McHenry and Will counties (IL) and Lake and Porter
  counties (IN). `data/tiger/snapshot.json` lists each county file and its sha256;
  `chigrid_tiger.py` rebuilds them.

US federal government works are not subject to copyright in the United States (17 U.S.C. § 105).

## OpenStreetMap — not used for anything in this repository

`chigrid_osm.py` and `fetch_osm.py` can build the regional grid from OpenStreetMap instead
(`--roads osm`), as a cross-check or for a separately published variant. Anything built that way is
derived from OpenStreetMap — © OpenStreetMap contributors, under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/) — and must be shared
under the ODbL with attribution. **No OSM-derived file is committed here** (`data/osm/` is ignored), so
everything under `data/` stays usable under the terms above and the code under MIT.
