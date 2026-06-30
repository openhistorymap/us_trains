# HGTFS reconstruction of the US rail network (1840–1870)

This folder reconstructs the **general structure of the United States railroad
network** as a feed conforming to the **Historical GTFS (HGTFS)** specification
— https://hgtfs.github.io/ (reference page: `src/pages/Reference.tsx`).

```bash
python3 build_hgtfs.py        # writes ./hgtfs/ and ./hgtfs.zip   (needs ogr2ogr)
```

## What HGTFS adds to GTFS (and how this feed uses it)

HGTFS keeps GTFS's files and adds a **temporal validity model**: "every entity
is valid over a span of time." Instead of weekly calendar snapshots, entities
carry `date_opened` / `date_closed`, with `date_opened_min` / `date_opened_max`
and `date_precision` to express *honest historical uncertainty*. It also adds
historical `route_type`s (1400–1499) and four new files.

| HGTFS feature | How this feed uses it |
|---|---|
| `route_type` **1405 = Steam railway** | every route (vs. plain GTFS `2`) |
| `date_opened` + uncertainty brackets | a line first **observed** in snapshot year *Y* is "open by `Y0101`". If it was absent in the previous snapshot *P*, opening is bracketed `(P0101, Y0101]` → `date_opened_min=P0101`, `date_opened_max=Y0101`, `date_precision=window_P_Y`. First-snapshot (1840) entities get `open_by_1840`. Applied to agencies, routes, stops, and edges. |
| `date_closed` | **left empty** — HGTFS says assert closure only with positive evidence; a line's absence from a later snapshot is *not* evidence it closed. |
| **`network_edges.txt`** (the network as a *dated graph*) | the temporal heart of this feed: one edge per railroad segment, `date_opened` = earliest snapshot in which its endpoints are connected. This is where the 1840→1870 **growth** lives (see table below). |
| **`historical_sources.txt`** | cites the RRMMA line GIS and the depot layers (`source_type=5`, secondary source). `trips.source_id` references it. |
| **`route_operators.txt`** | period operator tenure per line (`valid_from = date_opened`; `confidence=high` when the name came from the chartered `NAME`, else `low`). |
| **`events.txt`** | six dated events framing the era (B&O charter 1827 → Golden Spike 1869). |
| `location_accuracy` | `0` for depot points, `1` for nodes inferred from line geometry. |
| `time_accuracy` | `1` (estimated) on every `stop_times` row — these are reconstructed, not from a timetable. |

## Source data (this folder)

The **RRMMA** dataset — *Railroads and the Making of Modern America*,
University of Nebraska-Lincoln (https://railroads.unl.edu/).

| File | What | Used for |
|---|---|---|
| `USrailshps.zip` | US railroad **line** geometry for 1840/45/50/55/61/70, with `NAME` (chartered line = period operator), `RROWNER1‑3` (modern successor), `MARK`, `GAUGE`, `STATE`, `FNODE_/TNODE_` | agency, routes, shapes, trips, **network_edges**, stops (nodes), calendar |
| `South_Depots.kml` | Southern points — `Depot`/`Junction`/`Endpoint` + 1520 named labels | stops (depots), stop_times |
| `1861_Railroad.kml`, `RRMMA_KML.zip` | styled / per-state renderings of the same lines | not used (redundant) |
| `Burlington_Land_Sales.xls` | 3688 land-grant sale records, no geometry | out of scope |

## File-by-file mapping

- **agency.txt** — one period operator per chartered `NAME`. `agency_note` carries the 2012 successor (`RROWNER1`) and states. `date_opened` = first observed year.
- **routes.txt** — one route per line, `route_type=1405`. `route_desc` = gauge + states + observed years. `route_short_name` = reporting `MARK`.
- **stops.txt** — two kinds: **graph nodes** (snapped line endpoints/junctions, `location_accuracy=1`) and **depots** (`location_accuracy=0`), named from the nearest label point (≤6 km). Each carries the full `date_opened*` uncertainty bracket.
- **network_edges.txt** — every segment as a dated edge `(from_stop_id, to_stop_id, route_id, date_opened, evidence=dated_line, line_name)`.
- **shapes.txt / trips.txt / stop_times.txt** — one **representative trip per line** (latest-observed extent), split into connected components. Stops = terminals + on-line Southern depots, ordered by along-track distance. Times = `08:00` + distance ÷ 24 km/h, tagged `time_accuracy=1`.
- **calendar.txt** — a single observation-window service `rrmma_1840_1870` (1840-01-01 … 1870-12-31, daily). Real validity is per-entity via `date_opened`.
- **historical_sources.txt / route_operators.txt / events.txt / feed_info.txt** — as above.

## The dated network graph (the "general structure")

`network_edges.txt`, counted cumulatively, *is* the reconstructed structure of
US railroads over the era:

| by year | edges existing | 
|---|---|
| 1840 | 475 |
| 1845 | 675 |
| 1850 | 1,488 |
| 1855 | 1,922 |
| 1861 | 2,566 |
| 1870 | 3,142 |

## Honest caveats

- **Schedules are reconstructed, not archival** (RRMMA has no timetables). A trip's
  total duration tracks the connected network's track length, not a journey time;
  big networks (e.g. Union Pacific 1870) yield multi-day nominal trips by design.
  All `stop_times` are `time_accuracy=1`.
- **Depots are Southern-only**; elsewhere a trip has just its terminal nodes.
- **`RROWNER1` is the 2012 successor**, never the period operator — kept only in
  `agency_note`. `route_operators.txt` records the period operator's tenure;
  merger/transition dates are unknown, so no successor rows are asserted.
- **Edge dating** snaps segment endpoints to ~111 m to match the same edge across
  snapshots; minor digitisation drift can date a few edges one snapshot late.
- `date_closed` is intentionally empty everywhere (no positive closure evidence).

## Tuning knobs (top of `build_hgtfs.py`)

`ERA_SPEED_KMH`, `DEPART_SEC`, `DEPOT_SNAP_KM`, `LABEL_NAME_KM`, `NODE_SNAP`,
`ROUTE_TYPE_STEAM_RAILWAY`.
