#!/usr/bin/env python3
"""
build_hgtfs.py  —  Reconstruct an HGTFS feed for the US rail network (1840-1870)
=================================================================================

Conforms to the **Historical GTFS (HGTFS)** specification at
https://hgtfs.github.io/ (reference: src/pages/Reference.tsx).

HGTFS extends GTFS so that "every entity is valid over a span of time": instead
of weekly calendar snapshots, agencies / stops / routes / network edges carry
`date_opened` / `date_closed` (with `date_opened_min`/`_max` + `date_precision`
to express honest historical uncertainty). It adds historical route types
(1400-1499; 1405 = Steam railway) and four HGTFS files: historical_sources.txt,
network_edges.txt (the network as a *dated graph*), route_operators.txt, and
events.txt.

Source data (this folder) — the RRMMA dataset, "Railroads and the Making of
Modern America", University of Nebraska-Lincoln (https://railroads.unl.edu/):

  USrailshps.zip      US railroad LINE geometry for 6 snapshot years
                      (1840,1845,1850,1855,1861,1870), attributes NAME / MARK /
                      RROWNER1-3 (modern successor) / GAUGE / STATE.
  South_Depots.kml    Southern point layers Depot / Junction / Endpoint plus a
                      "Feature Labels (NAME)" layer of 1520 named places.

How the snapshots become HGTFS dates:  a line/segment/stop first *observed* in
the snapshot for year Y is "open by Y0101" (a lower bound). If it was absent in
the previous snapshot P, its opening is bracketed (P0101, Y0101] and labelled
`window_P_Y`; otherwise `open_by_Y`. This is exactly HGTFS's uncertainty model.

Schedules (trips/stop_times) are RECONSTRUCTED structure, not archival
timetables: one representative trip per line (latest-observed extent), times =
along-track distance / era speed, flagged with `time_accuracy = 1` (estimated).
The per-year growth of the network lives in network_edges.txt (each edge dated).

Pure-Python (stdlib) + ogr2ogr for GDAL reads. Run:  python3 build_hgtfs.py
"""

import csv
import json
import math
import os
import re
import subprocess
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, ".hgtfs_work")
OUT = os.path.join(HERE, "hgtfs")
YEARS = [1840, 1845, 1850, 1855, 1861, 1870]
PREV = {1845: 1840, 1850: 1845, 1855: 1850, 1861: 1855, 1870: 1861}

# --- reconstruction parameters ----------------------------------------------
ERA_SPEED_KMH = 24.0
DEPART_SEC = 8 * 3600
DEPOT_SNAP_KM = 2.0
LABEL_NAME_KM = 6.0
TERMINAL_MERGE_KM = 1.0
NODE_SNAP = 3                  # decimals (~111 m) for graph-node identity
ROUTE_TYPE_STEAM_RAILWAY = 1405
PLACEHOLDER_NAMES = {"no name given", "no name givan"}

SRC_LINES = "rrmma-lines"
SRC_DEPOTS = "rrmma-depots"
SERVICE_ID = "rrmma_1840_1870"


# =============================================================================
# 0. Extract + convert source data to GeoJSON via ogr2ogr
# =============================================================================
def sh(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prepare_geojson():
    os.makedirs(WORK, exist_ok=True)
    shp_root = os.path.join(WORK, "USrailshps")
    if not os.path.isdir(shp_root):
        with zipfile.ZipFile(os.path.join(HERE, "USrailshps.zip")) as z:
            z.extractall(WORK)
    for y in YEARS:
        out = os.path.join(WORK, f"RR{y}.geojson")
        if not os.path.exists(out):
            sh(["ogr2ogr", "-f", "GeoJSON", out,
                os.path.join(shp_root, f"RR{y}", f"RR{y}WGS84.shp")])
    kml = os.path.join(HERE, "South_Depots.kml")
    for layer, fname in [("Depot", "Depot"), ("Endpoint", "Endpoint"),
                         ("Junction", "Junction"), ("Feature Labels (NAME)", "Labels")]:
        out = os.path.join(WORK, f"{fname}.geojson")
        if not os.path.exists(out):
            sh(["ogr2ogr", "-f", "GeoJSON", out, kml, layer])


def load_geojson(name):
    with open(os.path.join(WORK, f"{name}.geojson")) as f:
        return json.load(f)["features"]


# =============================================================================
# 1. Geometry helpers
# =============================================================================
def haversine_km(lon1, lat1, lon2, lat2):
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def make_proj(lat0):
    kx = 111.320 * math.cos(math.radians(lat0))
    return lambda lon, lat: (lon * kx, lat * 110.574)


def flatten_lines(geom):
    if not geom:
        return []
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return list(geom["coordinates"])
    return []


def node_key(pt):
    return (round(pt[0], NODE_SNAP), round(pt[1], NODE_SNAP))


def connected_components(segs):
    segs = [s for s in segs if len(s) >= 2]
    parent = list(range(len(segs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    node_to_seg = {}
    for i, s in enumerate(segs):
        for end in (s[0], s[-1]):
            k = node_key(end)
            if k in node_to_seg:
                parent[find(i)] = find(node_to_seg[k])
            else:
                node_to_seg[k] = i
    comps = {}
    for i in range(len(segs)):
        comps.setdefault(find(i), []).append(segs[i])
    return list(comps.values())


def polyline_len_deg(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def chain_segments(segments):
    segs = [s for s in segments if len(s) >= 2]
    if not segs:
        return []
    segs.sort(key=lambda s: -polyline_len_deg(s))
    path = list(segs.pop(0))
    JOIN = 0.02
    changed = True
    while segs and changed:
        changed = False
        head, tail = path[0], path[-1]
        best = None
        for i, s in enumerate(segs):
            for where, end in (("tail", tail), ("head", head)):
                for rev, pt in ((False, s[0]), (True, s[-1])):
                    d = math.hypot(end[0] - pt[0], end[1] - pt[1])
                    if best is None or d < best[3]:
                        best = (i, where, rev, d)
        if best and best[3] <= JOIN:
            i, where, rev, _ = best
            s = segs.pop(i)
            if rev:
                s = s[::-1]
            path = (path + s[1:]) if where == "tail" else (s[:-1] + path)
            changed = True
    for s in segs:
        path += s
    return path


def cumulative_km(pts):
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + haversine_km(pts[i][0], pts[i][1],
                                          pts[i + 1][0], pts[i + 1][1]))
    return cum


SEG_CELL = 0.05


def build_seg_grid(pts):
    grid = {}
    for i in range(len(pts) - 1):
        for lon, lat in (pts[i], pts[i + 1]):
            grid.setdefault((round(lon / SEG_CELL), round(lat / SEG_CELL)), set()).add(i)
    return grid


def project_point(lon, lat, pts, cum, proj, seg_grid):
    px, py = proj(lon, lat)
    cx, cy = round(lon / SEG_CELL), round(lat / SEG_CELL)
    idxs = set()
    for i in range(cx - 1, cx + 2):
        for j in range(cy - 1, cy + 2):
            idxs |= seg_grid.get((i, j), set())
    if not idxs:
        return (float("inf"), 0.0)
    best = (float("inf"), 0.0)
    for i in idxs:
        ax, ay = proj(pts[i][0], pts[i][1])
        bx, by = proj(pts[i + 1][0], pts[i + 1][1])
        dx, dy = bx - ax, by - ay
        seglen2 = dx * dx + dy * dy
        t = 0.0 if seglen2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seglen2))
        perp = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if perp < best[0]:
            best = (perp, cum[i] + t * (cum[i + 1] - cum[i]))
    return best


class Grid:
    CELL = 0.5

    def __init__(self):
        self.cells = {}

    def add(self, lon, lat, payload):
        self.cells.setdefault((round(lon / self.CELL), round(lat / self.CELL)), []).append((lon, lat, payload))

    def near(self, lon, lat, ring=1):
        cx, cy = round(lon / self.CELL), round(lat / self.CELL)
        out = []
        for i in range(cx - ring, cx + ring + 1):
            for j in range(cy - ring, cy + ring + 1):
                out.extend(self.cells.get((i, j), []))
        return out

    def bbox(self, mnlon, mnlat, mxlon, mxlat, pad=0.05):
        out = []
        i0, i1 = round((mnlon - pad) / self.CELL), round((mxlon + pad) / self.CELL)
        j0, j1 = round((mnlat - pad) / self.CELL), round((mxlat + pad) / self.CELL)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                out.extend(self.cells.get((i, j), []))
        return out


# =============================================================================
# 2. Text / id / date helpers
# =============================================================================
def norm_name(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_") or "x"


def color_for(key):
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0xFFFFFF
    r, g, b = (h >> 16) & 255, (h >> 8) & 255, h & 255
    return f"{60 + r % 150:02X}{60 + g % 150:02X}{60 + b % 150:02X}"


def hms(seconds):
    s = int(round(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def prop(props, *keys):
    for k in keys:
        v = props.get(k)
        if v not in (None, ""):
            return v
    return ""


def gauge_text(props):
    g = prop(props, "GAUGE")
    if g:
        return g
    try:
        ft, inch = float(props.get("GAUGE_FT") or 0), float(props.get("GAUGE_IN") or 0)
    except (TypeError, ValueError):
        return ""
    if ft or inch:
        return f"{ft:g}' {inch:g}\""
    return ""


def date_fields(first_year):
    """HGTFS uncertainty brackets for an entity first observed in `first_year`.
    Returns (date_opened, date_opened_min, date_opened_max, date_precision)."""
    do = f"{first_year}0101"
    if first_year == YEARS[0]:
        return do, "", do, f"open_by_{first_year}"
    p = PREV[first_year]
    return do, f"{p}0101", do, f"window_{p}_{first_year}"


# =============================================================================
# 3. Build
# =============================================================================
def build():
    prepare_geojson()
    os.makedirs(OUT, exist_ok=True)

    # ---- point indices: labels (names), depots (intermediate stops) --------
    labels = Grid()
    for f in load_geojson("Labels"):
        c = f["geometry"]["coordinates"]
        nm = norm_name(f["properties"].get("Name"))
        if nm:
            labels.add(c[0], c[1], nm)

    depots_grid = Grid()
    for layer, kind in [("Depot", "depot"), ("Junction", "junction"), ("Endpoint", "endpoint")]:
        for f in load_geojson(layer):
            c = f["geometry"]["coordinates"]
            depots_grid.add(c[0], c[1], kind)

    def name_at(lon, lat):
        best, bd = "", LABEL_NAME_KM
        for clon, clat, nm in labels.near(lon, lat):
            d = haversine_km(lon, lat, clon, clat)
            if d < bd:
                bd, best = d, nm
        return best

    # ---- Pass A: scan every year -> routes, graph nodes, dated edges -------
    routes = {}          # rid -> attrs
    slug_taken = {}
    nodes = {}           # node_key -> {lon,lat,first_year}
    edges = {}           # (na,nb,rid) -> {first_year, line_name}

    def route_id_for(name):
        base = slugify(name)
        rid, n = base, 2
        while rid in slug_taken and slug_taken[rid] != name:
            rid = f"{base}_{n}"
            n += 1
        slug_taken[rid] = name
        return rid

    def touch_node(pt, year):
        k = node_key(pt)
        nd = nodes.get(k)
        if nd is None:
            nodes[k] = {"lon": pt[0], "lat": pt[1], "first_year": year}
        elif year < nd["first_year"]:
            nd["first_year"] = year
        return k

    for year in YEARS:
        by_name = {}
        for fi, f in enumerate(load_geojson(f"RR{year}")):
            props = f["properties"]
            raw = norm_name(props.get("NAME"))
            charter = bool(raw) and raw.lower() not in PLACEHOLDER_NAMES
            name = raw if charter else norm_name(props.get("RROWNER1"))
            if not name:
                st = norm_name(prop(props, "STATE"))
                name = f"Unnamed line {year}-{fi}" + (f" ({st})" if st else "")
            g = by_name.setdefault(name, {"segs": [], "props": [], "charter": False})
            g["segs"].extend(flatten_lines(f["geometry"]))
            g["props"].append(props)
            g["charter"] = g["charter"] or charter

        for name, grp in by_name.items():
            segs = [s for s in grp["segs"] if len(s) >= 2]
            if not segs:
                continue
            rid = route_id_for(name)
            rec = routes.get(rid)
            if rec is None:
                rec = {"route_id": rid, "name": name, "marks": {}, "owners": {},
                       "gauges": {}, "states": set(), "years": set(),
                       "geom_by_year": {}, "charter": False, "color": color_for(rid)}
                routes[rid] = rec
            rec["years"].add(year)
            rec["geom_by_year"][year] = segs
            rec["charter"] = rec["charter"] or grp["charter"]
            for props in grp["props"]:
                for store, key in [("marks", "MARK1"), ("owners", "RROWNER1")]:
                    v = norm_name(prop(props, key))
                    if v:
                        rec[store][v] = rec[store].get(v, 0) + 1
                gt = gauge_text(props)
                if gt:
                    rec["gauges"][gt] = rec["gauges"].get(gt, 0) + 1
                st = norm_name(prop(props, "STATE"))
                if st:
                    rec["states"].add(st)
            # dated graph: one edge per segment endpoint-pair, earliest year wins
            for s in segs:
                na, nb = touch_node(s[0], year), touch_node(s[-1], year)
                if na == nb:
                    continue
                ek = (min(na, nb), max(na, nb), rid)
                e = edges.get(ek)
                if e is None or year < e["first_year"]:
                    edges[ek] = {"first_year": year, "line_name": name}

    # ---- assign a stop_id to every graph node (referenced by edges/trips) --
    node_sid = {}
    for i, k in enumerate(nodes, start=1):
        node_sid[k] = f"n{i:05d}"

    # depot stops are created lazily as trips snap onto them
    depot_stops = {}     # (rlon,rlat) -> {id,lon,lat,kind,first_year}

    def get_depot_stop(lon, lat, kind, route_year):
        key = (round(lon, 5), round(lat, 5))
        ds = depot_stops.get(key)
        if ds is None:
            ds = {"id": f"d{len(depot_stops) + 1:05d}", "lon": lon, "lat": lat,
                  "kind": kind, "first_year": route_year}
            depot_stops[key] = ds
        elif route_year < ds["first_year"]:
            ds["first_year"] = route_year
        return ds["id"]

    trips_rows, stoptimes_rows, shapes_rows = [], [], []

    # ---- Pass B: representative trip per route (latest-observed extent) -----
    for rid, rec in routes.items():
        rec["first_year"] = min(rec["years"])
        latest = max(rec["years"])
        for ci, path in enumerate(p for p in (chain_segments(c)
                                              for c in connected_components(rec["geom_by_year"][latest]))
                                   if len(p) >= 2):
            shape_id = trip_id = f"{rid}__c{ci}"
            cum = cumulative_km(path)
            comp_shapes = [(shape_id, round(pt[1], 6), round(pt[0], 6), seq, round(d, 4))
                           for seq, (pt, d) in enumerate(zip(path, cum))]

            proj = make_proj(sum(p[1] for p in path) / len(path))
            stoppts = [(0.0, node_key(path[0]), "node"),
                       (cum[-1], node_key(path[-1]), "node")]
            lons = [p[0] for p in path]
            lats = [p[1] for p in path]
            cand = depots_grid.bbox(min(lons), min(lats), max(lons), max(lats))
            seg_grid = build_seg_grid(path) if cand else None
            for clon, clat, kind in cand:
                perp, along = project_point(clon, clat, path, cum, proj, seg_grid)
                if perp <= DEPOT_SNAP_KM:
                    stoppts.append((along, get_depot_stop(clon, clat, kind, rec["first_year"]), "depot"))
            stoppts.sort(key=lambda r: r[0])

            seq_stops, last = [], -1e9
            for along, ref, kind in stoppts:
                sid = node_sid[ref] if kind == "node" else ref
                if seq_stops and seq_stops[-1][0] == sid:
                    continue
                if seq_stops and along - last < 0.25:
                    continue
                seq_stops.append((sid, along))
                last = along
            if len(seq_stops) < 2:
                continue

            shapes_rows.extend(comp_shapes)
            trips_rows.append([rid, SERVICE_ID, trip_id,
                               stop_name_of(seq_stops[-1][0], node_sid, nodes, depot_stops, name_at),
                               0, shape_id, SRC_LINES])
            for order, (sid, along) in enumerate(seq_stops, start=1):
                t = DEPART_SEC + (along / ERA_SPEED_KMH) * 3600.0
                stoptimes_rows.append([trip_id, hms(t), hms(t), sid, order, 1, round(along, 4)])

    write_feed(routes, nodes, node_sid, depot_stops, name_at,
               edges, trips_rows, stoptimes_rows, shapes_rows)


def stop_name_of(sid, node_sid, nodes, depot_stops, name_at):
    """Resolve a stop_id to a display name (used for trip_headsign)."""
    if sid.startswith("n"):
        for k, v in node_sid.items():
            if v == sid:
                nd = nodes[k]
                return name_at(nd["lon"], nd["lat"]) or f"Node ({nd['lat']:.3f},{nd['lon']:.3f})"
    for ds in depot_stops.values():
        if ds["id"] == sid:
            return name_at(ds["lon"], ds["lat"]) or f"{ds['kind'].title()} ({ds['lat']:.3f},{ds['lon']:.3f})"
    return sid


# =============================================================================
# 4. Emit HGTFS files
# =============================================================================
def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_feed(routes, nodes, node_sid, depot_stops, name_at,
               edges, trips_rows, stoptimes_rows, shapes_rows):
    O = lambda n: os.path.join(OUT, n)

    # a stop must be emitted if a trip stops there (stop_times) or an edge
    # touches it (network_edges) — collect both so referential integrity holds.
    referenced_ids = {row[3] for row in stoptimes_rows}   # stop_id column
    edge_nodes = set()
    for (na, nb, _rid) in edges:
        edge_nodes.add(na)
        edge_nodes.add(nb)

    # agency.txt — one period operator per chartered line (NAME)
    agency_rows = []
    for rid, rec in sorted(routes.items()):
        do, _, _, _ = date_fields(rec["first_year"])
        succ = max(rec["owners"], key=rec["owners"].get) if rec["owners"] else ""
        note = []
        if succ and succ.lower() != rec["name"].lower():
            note.append(f"2012 successor: {succ}")
        if rec["states"]:
            note.append("states: " + ", ".join(sorted(rec["states"])))
        agency_rows.append([rid, rec["name"], "https://railroads.unl.edu/",
                            "America/New_York", "en", do, "", "; ".join(note)])
    write_csv(O("agency.txt"),
              ["agency_id", "agency_name", "agency_url", "agency_timezone",
               "agency_lang", "date_opened", "date_closed", "agency_note"], agency_rows)

    # routes.txt — Steam railway (1405), with date_opened
    route_rows = []
    for rid, rec in sorted(routes.items()):
        do, _, _, _ = date_fields(rec["first_year"])
        short = max(rec["marks"], key=rec["marks"].get) if rec["marks"] else ""
        gauge = max(rec["gauges"], key=rec["gauges"].get) if rec["gauges"] else ""
        desc = []
        if gauge:
            desc.append(f"Gauge {gauge}")
        if rec["states"]:
            desc.append("in " + ", ".join(sorted(rec["states"])))
        desc.append("observed " + ",".join(str(y) for y in sorted(rec["years"])))
        route_rows.append([rid, rid, short, rec["name"], "; ".join(desc),
                           ROUTE_TYPE_STEAM_RAILWAY, rec["color"], do, ""])
    write_csv(O("routes.txt"),
              ["route_id", "agency_id", "route_short_name", "route_long_name",
               "route_desc", "route_type", "route_color",
               "date_opened", "date_closed"], route_rows)

    # stops.txt — graph nodes (edge endpoints + trip terminals) + used depots
    stop_rows = []
    n_nodes = 0
    for k, nd in nodes.items():
        if k not in edge_nodes and node_sid[k] not in referenced_ids:
            continue
        do, dmin, dmax, prec = date_fields(nd["first_year"])
        nm = name_at(nd["lon"], nd["lat"]) or f"Node ({nd['lat']:.3f},{nd['lon']:.3f})"
        stop_rows.append([node_sid[k], nm, round(nd["lat"], 6), round(nd["lon"], 6),
                          "Railway network node (line endpoint / junction)", 0, 1,
                          do, dmin, dmax, prec, ""])
        n_nodes += 1
    n_depots = 0
    for ds in depot_stops.values():
        if ds["id"] not in referenced_ids:
            continue
        do, dmin, dmax, prec = date_fields(ds["first_year"])
        nm = name_at(ds["lon"], ds["lat"]) or f"{ds['kind'].title()} ({ds['lat']:.3f},{ds['lon']:.3f})"
        stop_rows.append([ds["id"], nm, round(ds["lat"], 6), round(ds["lon"], 6),
                          ds["kind"].title() + " (South_Depots)", 0, 0,
                          do, dmin, dmax, prec, ""])
        n_depots += 1
    write_csv(O("stops.txt"),
              ["stop_id", "stop_name", "stop_lat", "stop_lon", "stop_desc",
               "location_type", "location_accuracy", "date_opened",
               "date_opened_min", "date_opened_max", "date_precision",
               "date_closed"], stop_rows)

    # calendar.txt — single observation-window service (validity is per-entity)
    write_csv(O("calendar.txt"),
              ["service_id", "monday", "tuesday", "wednesday", "thursday",
               "friday", "saturday", "sunday", "start_date", "end_date"],
              [[SERVICE_ID, 1, 1, 1, 1, 1, 1, 1, f"{YEARS[0]}0101", f"{YEARS[-1]}1231"]])

    # trips.txt / stop_times.txt / shapes.txt
    write_csv(O("trips.txt"),
              ["route_id", "service_id", "trip_id", "trip_headsign",
               "direction_id", "shape_id", "source_id"], trips_rows)
    write_csv(O("stop_times.txt"),
              ["trip_id", "arrival_time", "departure_time", "stop_id",
               "stop_sequence", "time_accuracy", "shape_dist_traveled"], stoptimes_rows)
    write_csv(O("shapes.txt"),
              ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence",
               "shape_dist_traveled"], shapes_rows)

    # network_edges.txt — THE dated graph (one edge per segment, earliest year)
    edge_rows = []
    for (na, nb, rid), e in edges.items():
        do, _, _, _ = date_fields(e["first_year"])
        edge_rows.append([node_sid[na], node_sid[nb], rid, do, "",
                          "dated_line", e["line_name"]])
    write_csv(O("network_edges.txt"),
              ["from_stop_id", "to_stop_id", "route_id", "date_opened",
               "date_closed", "evidence", "line_name"], edge_rows)

    # route_operators.txt — period operator tenure (successor lineage in note)
    op_rows = []
    for rid, rec in sorted(routes.items()):
        do, _, _, _ = date_fields(rec["first_year"])
        op_rows.append([rid, rid, do, "", "RRMMA NAME field",
                        "high" if rec["charter"] else "low"])
    write_csv(O("route_operators.txt"),
              ["route_id", "agency_id", "valid_from", "valid_to", "source",
               "confidence"], op_rows)

    # historical_sources.txt
    write_csv(O("historical_sources.txt"),
              ["source_id", "source_name", "source_author", "source_year",
               "source_url", "source_type", "source_notes"],
              [[SRC_LINES,
                "Railroads and the Making of Modern America - US railroad GIS, snapshots 1840-1870",
                "William G. Thomas et al., University of Nebraska-Lincoln", 2012,
                "https://railroads.unl.edu/", 5,
                "Scholarly GIS compilation of historical US railroad lines (shapefiles by year)."],
               [SRC_DEPOTS,
                "RRMMA Southern depots, junctions and endpoints",
                "University of Nebraska-Lincoln", 2012,
                "https://railroads.unl.edu/", 5,
                "Point layers for the US South; station names from the label layer."]])

    # events.txt — dated context that frames the 1840-1870 timeline
    write_csv(O("events.txt"),
              ["event_id", "date", "end_date", "name", "description", "period_uri"],
              [["e1", "18270228", "", "Baltimore & Ohio Railroad chartered",
                "First US common-carrier railroad chartered.", ""],
               ["e2", "18301225", "", "First scheduled US steam service",
                "South Carolina Canal & Rail Road runs the 'Best Friend of Charleston'.", ""],
               ["e3", "18500920", "", "Illinois Central land grant",
                "First federal railroad land-grant act.", ""],
               ["e4", "18610412", "18650409", "American Civil War",
                "Rail networks become strategic infrastructure; Southern lines heavily damaged.", ""],
               ["e5", "18620701", "", "Pacific Railway Act",
                "Authorises the first transcontinental railroad.", ""],
               ["e6", "18690510", "", "First Transcontinental Railroad completed",
                "Golden Spike driven at Promontory Summit, Utah.", ""]])

    # feed_info.txt
    write_csv(O("feed_info.txt"),
              ["feed_publisher_name", "feed_publisher_url", "feed_lang",
               "feed_start_date", "feed_end_date", "feed_version"],
              [["Open History Map - HGTFS reconstruction from RRMMA (U. Nebraska-Lincoln). "
                "Geography is archival; trips/stop_times are reconstructed structure "
                "(time_accuracy=1), not historical timetables.",
                "https://hgtfs.github.io/", "en",
                f"{YEARS[0]}0101", f"{YEARS[-1]}1231", "hgtfs-rrmma-1840-1870"]])

    # ---- summary ----
    print(f"HGTFS feed written to {OUT}/  (route_type=1405 Steam railway)")
    print(f"  agency.txt            {len(agency_rows):>7}  period operators (date_opened set)")
    print(f"  routes.txt            {len(route_rows):>7}  steam-railway routes")
    print(f"  stops.txt             {len(stop_rows):>7}  ({n_nodes} graph nodes + {n_depots} depots)")
    print(f"  network_edges.txt     {len(edge_rows):>7}  dated graph edges")
    print(f"  trips.txt             {len(trips_rows):>7}  representative trips")
    print(f"  stop_times.txt        {len(stoptimes_rows):>7}  stop events (time_accuracy=1)")
    print(f"  shapes.txt            {len(shapes_rows):>7}  shape points")
    print(f"  route_operators.txt   {len(op_rows):>7}")
    print(f"  historical_sources.txt{2:>7} | events.txt{6:>7} | calendar.txt 1 | feed_info.txt 1")
    yr = {}
    for (_na, _nb, _rid), e in edges.items():
        yr[e["first_year"]] = yr.get(e["first_year"], 0) + 1
    print("  edges first dated per year:", ", ".join(f"{k}={v}" for k, v in sorted(yr.items())))


if __name__ == "__main__":
    build()
