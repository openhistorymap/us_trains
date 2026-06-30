# us_trains — Historical GTFS of the US railroad network (1840–1870)

An [**HGTFS**](https://hgtfs.github.io/) (Historical General Transit Feed
Specification) reconstruction of the United States railroad network across six
snapshot years — **1840, 1845, 1850, 1855, 1861, 1870** — built from the
[*Railroads and the Making of Modern America*](https://railroads.unl.edu/)
(RRMMA) historical GIS dataset, University of Nebraska-Lincoln.

Published by [Open History Map](https://openhistorymap.org/).

## The feed

`hgtfs/` (also packaged as [`hgtfs.zip`](hgtfs.zip)) is a valid GTFS feed that
uses the HGTFS temporal extensions — every entity carries `date_opened` with
uncertainty brackets, routes are `route_type = 1405` (Steam railway), and the
network's growth is encoded as a **dated graph** in `network_edges.txt`.

| file | rows | role |
|---|---|---|
| `agency.txt` | 995 | period operators (one per chartered line) |
| `routes.txt` | 995 | steam-railway routes, `route_type=1405` |
| `stops.txt` | 4,617 | graph nodes + Southern depots, with `date_opened*` brackets |
| `network_edges.txt` | 3,142 | **the dated network graph** (one edge per segment) |
| `trips.txt` / `stop_times.txt` | 1,102 / 3,667 | representative reconstructed service (`time_accuracy=1`) |
| `shapes.txt` | 434,010 pts | route geometry |
| `historical_sources.txt`, `route_operators.txt`, `events.txt` | 2 / 995 / 6 | HGTFS provenance & context |
| `calendar.txt`, `feed_info.txt` | — | observation window 1840–1870 |

### Network growth (cumulative edges existing by year)

| 1840 | 1845 | 1850 | 1855 | 1861 | 1870 |
|---|---|---|---|---|---|
| 475 | 675 | 1,488 | 1,922 | 2,566 | 3,142 |

## Reproduce

```bash
python3 build_hgtfs.py        # needs GDAL's ogr2ogr on PATH
```

The generator reads the two source files in this repo — `USrailshps.zip` and
`South_Depots.kml` — and writes `hgtfs/` + `hgtfs.zip`. It is pure Python
(stdlib only) apart from `ogr2ogr`. See **[HGTFS.md](HGTFS.md)** for the full
source→HGTFS field mapping and modelling decisions.

## Caveats (please read)

- **Schedules are reconstructed structure, not archival timetables.** RRMMA has
  no timetables; `stop_times` are derived from along-track distance at a fixed
  era speed and flagged `time_accuracy = 1` (estimated).
- **Depots cover the US South only**; elsewhere a trip has just its terminal nodes.
- **`date_closed` is empty everywhere** — HGTFS asks that closure be asserted
  only with positive evidence, which the snapshots don't provide.
- The modern successor company (`RROWNER1`, e.g. *CSX*) is kept only in
  `agency_note`; it never stands in for the period operator.

## Attribution & licence

Source geography © the **RRMMA** project (W. G. Thomas et al., University of
Nebraska-Lincoln, https://railroads.unl.edu/) — see their terms for reuse of the
underlying data. The HGTFS conversion code (`build_hgtfs.py`) and the derived
feed are released by Open History Map; add a `LICENSE` file to set explicit terms.
