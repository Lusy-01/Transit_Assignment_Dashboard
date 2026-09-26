"""
data_prep.py
------------
Loads the static transit-assignment outputs produced by the modeling pipeline
(05_results/) and derives the planning-grade metrics the dashboard needs.

IMPORTANT MODELING NOTE (read before trusting a number downstream):
The raw outputs give SEGMENT (link) volumes per route/direction — the
standard "loaded network" output of a transit assignment. They do NOT
contain per-stop boarding/alighting counts, vehicle capacities, headways,
or a time-of-day breakdown finer than "day type" (weekday / Saturday /
Sunday). Everything below that is not a literal column in the source data
is clearly labeled as DERIVED and the method is documented inline:

  - Boardings / Alightings per stop  -> derived via the delta-load method
    (see `compute_stop_boardings_alightings`)
  - Load Factor / V-C ratio          -> requires vehicle capacity, which is
    not in this dataset. Exposed as configurable assumptions
    (see ASSUMED_CAPACITY_BY_MODE) so a planner can plug in real GTFS/fleet
    data later; treat the resulting ratios as illustrative, not calibrated.
  - PKT / PHT                        -> requires link length + speed, which
    is not in this dataset either. We approximate link length via haversine
    distance between stop coordinates (a straight-line, not alignment-length,
    proxy) and use a configurable average commercial speed per mode to
    estimate travel time. Flagged as APPROX in API responses.
  - Time-of-day (AM/PM/Off-Peak)     -> NOT available. The source pipeline
    only exports day-type (weekday/Sat/Sun) resolution. The API's
    `period` filter therefore maps to day-type, not time-of-day, and this
    is documented in the endpoint docstring so the frontend doesn't imply
    a precision the data doesn't have.
"""
from __future__ import annotations

import math
import json
from pathlib import Path
from functools import lru_cache
from typing import Optional

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------------------
# Assumptions a planner should replace with real values when available.
# ---------------------------------------------------------------------------
ASSUMED_CAPACITY_BY_MODE = {
    "bus": 65,          # standard single-deck bus, seated + standing
    "rail": 1200,
    "metro": 1800,
    "default": 65,
}
ASSUMED_COMMERCIAL_SPEED_KMH = {
    "bus": 18.0,
    "rail": 35.0,
    "metro": 32.0,
    "default": 18.0,
}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _haversine_km_vec(lat1, lon1, lat2, lon2):
    """Vectorized (numpy) haversine over whole columns at once — the plain
    DataFrame.apply(..., axis=1) version does a Python-level function call
    per row and is the single slowest step in /api/kpis/system on a large
    network (thousands of rows); this replaces it with array math."""
    import numpy as np
    R = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# Raw loaders (cached — these files don't change at runtime)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_stops_geojson() -> dict:
    with open(DATA_DIR / "mtc_stops.geojson") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_links_geojson() -> dict:
    """Master link geometry (all-days-summed volumes), one feature per
    route_id/direction/from_stop/to_stop segment."""
    with open(DATA_DIR / "mtc_loaded_links.geojson") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_links_df() -> pd.DataFrame:
    """Flatten the link geojson to a DataFrame with geometry + endpoint
    coordinates attached, keyed on (route_id, direction, from_stop, to_stop)."""
    gj = load_links_geojson()
    rows = []
    for feat in gj["features"]:
        p = feat["properties"]
        coords = feat["geometry"]["coordinates"]
        rows.append({
            "route_id": str(p["route_id"]),
            "route_short_name": p.get("route"),
            "direction_id": int(p.get("direction", 0)),
            "from_stop": p["from_stop"],
            "to_stop": p["to_stop"],
            "from_name": p.get("from_name"),
            "to_name": p.get("to_name"),
            "volume": float(p.get("volume", 0)),
            "geometry": feat["geometry"],
            "from_lon": coords[0][0], "from_lat": coords[0][1],
            "to_lon": coords[-1][0], "to_lat": coords[-1][1],
        })
    return pd.DataFrame(rows)


@lru_cache(maxsize=1)
def load_route_kpis() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "all_days_route_loads_summed.csv")


@lru_cache(maxsize=1)
def load_day_type_map() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "day_type_summary.csv")
    df = df[df["day_type"].isin(["weekday", "saturday", "sunday"])]
    return df


@lru_cache(maxsize=1)
def load_zone_centroids() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "zone_centroids.csv")


@lru_cache(maxsize=1)
def load_od_pairs() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "od_long_combined.csv")


def load_daily_links(file_stub: str) -> pd.DataFrame:
    """Per-day segment volumes for one source file (no geometry — join to
    load_links_df() on the composite key to get coordinates)."""
    path = DATA_DIR / "daily" / f"{file_stub}_loaded_links.csv"
    df = pd.read_csv(path)
    df["route_id"] = df["route_id"].astype(str)
    return df


def links_for_day_type(day_type: str) -> pd.DataFrame:
    """Aggregate per-day link volumes across every file tagged with the
    requested day_type, then join geometry from the master link layer."""
    dmap = load_day_type_map()
    stubs = dmap.loc[dmap["day_type"] == day_type, "file"].tolist()
    if not stubs:
        return load_links_df()
    frames = [load_daily_links(s) for s in stubs]
    combined = pd.concat(frames, ignore_index=True)
    agg = combined.groupby(
        ["route_id", "direction_id", "from_stop", "to_stop"], as_index=False
    )["volume"].sum()
    geo = load_links_df().drop(columns=["volume"])
    merged = agg.merge(geo, on=["route_id", "direction_id", "from_stop", "to_stop"], how="left")
    return merged.dropna(subset=["geometry"])


# ---------------------------------------------------------------------------
# Derived metric #1: per-stop boardings / alightings (delta-load method)
# ---------------------------------------------------------------------------
def _order_route_direction_chain(edges: pd.DataFrame) -> list[str]:
    """Reconstruct the stop sequence of one route+direction from its
    from_stop->to_stop edges by walking the directed chain. Assumes a simple
    (non-branching) pattern, which holds for the vast majority of bus routes;
    falls back to an unordered stop list (order-sensitive metrics skipped)
    if the edges don't form a single chain (e.g. loop routes, branches)."""
    succ = dict(zip(edges["from_stop"], edges["to_stop"]))
    all_from, all_to = set(edges["from_stop"]), set(edges["to_stop"])
    starts = list(all_from - all_to)
    if len(starts) != 1:
        return []  # branching/loop pattern — can't linearize safely
    chain = [starts[0]]
    seen = {starts[0]}
    cur = starts[0]
    while cur in succ:
        nxt = succ[cur]
        if nxt in seen:
            return []  # cycle — bail out
        chain.append(nxt)
        seen.add(nxt)
        cur = nxt
    return chain


@lru_cache(maxsize=4)
def compute_stop_boardings_alightings(day_type: str = "all") -> pd.DataFrame:
    """For every stop, estimate total boardings / alightings / through-volume
    by reconstructing each route-direction's ordered load profile and taking
    consecutive differences (the standard manual on-off survey inference
    method planners use when only segment loads are available):
        boarding(stop_i)  = max(load(i,i+1) - load(i-1,i), 0)
        alighting(stop_i) = max(load(i-1,i) - load(i,i+1), 0)
    First stop of a pattern: boarding = load of its first segment.
    Last stop: alighting = load of its last segment.
    Results are summed across every route-direction serving a stop.
    """
    links = load_links_df() if day_type == "all" else links_for_day_type(day_type)
    out = {}
    for (route_id, direction_id), grp in links.groupby(["route_id", "direction_id"]):
        chain = _order_route_direction_chain(grp[["from_stop", "to_stop"]])
        if not chain:
            # fallback: treat every stop as both an origin and destination
            # of its incident links (can't separate board vs alight)
            for _, row in grp.iterrows():
                for s in (row.from_stop, row.to_stop):
                    d = out.setdefault(s, {"boardings": 0.0, "alightings": 0.0, "through": 0.0})
                    d["through"] += row.volume / 2
            continue
        vol_by_edge = {(r.from_stop, r.to_stop): r.volume for r in grp.itertuples()}
        loads = [vol_by_edge[(chain[i], chain[i + 1])] for i in range(len(chain) - 1)]
        for i, stop in enumerate(chain):
            d = out.setdefault(stop, {"boardings": 0.0, "alightings": 0.0, "through": 0.0})
            prev_load = loads[i - 1] if i > 0 else 0.0
            next_load = loads[i] if i < len(loads) else 0.0
            if i == 0:
                d["boardings"] += next_load
            elif i == len(chain) - 1:
                d["alightings"] += prev_load
            else:
                delta = next_load - prev_load
                if delta > 0:
                    d["boardings"] += delta
                else:
                    d["alightings"] += -delta
                d["through"] += min(prev_load, next_load)
    df = pd.DataFrame([{"stop_id": k, **v} for k, v in out.items()])
    if df.empty:
        return df
    df["total_volume"] = df["boardings"] + df["alightings"]
    df["net_flow_ratio"] = (df["boardings"] - df["alightings"]) / df["total_volume"].replace(0, pd.NA)
    df["net_flow_ratio"] = df["net_flow_ratio"].fillna(0.0)
    # transfer volume proxy: passengers passing through a stop already
    # onboard one route who are joined by/parted from others at the same
    # physical stop are not distinguishable from this data; `through` here
    # is the "still-onboard" component of THIS route only, not a cross-route
    # transfer count — labeled as such in the API.
    return df


# ---------------------------------------------------------------------------
# Derived metric #2: route load profile, Max Load Point (MLP), Load Factor
# ---------------------------------------------------------------------------
def route_load_profile(route_id: str, direction_id: int, day_type: str = "all", mode: str = "bus") -> dict:
    links = load_links_df() if day_type == "all" else links_for_day_type(day_type)
    grp = links[(links.route_id == str(route_id)) & (links.direction_id == direction_id)]
    if grp.empty:
        return {"route_id": route_id, "direction_id": direction_id, "segments": []}
    chain = _order_route_direction_chain(grp[["from_stop", "to_stop"]])
    cap = ASSUMED_CAPACITY_BY_MODE.get(mode, ASSUMED_CAPACITY_BY_MODE["default"])
    segs = []
    ordered = grp.set_index(["from_stop", "to_stop"])
    if chain:
        pairs = list(zip(chain[:-1], chain[1:]))
    else:
        pairs = list(zip(grp.from_stop, grp.to_stop))
    for i, (fs, ts) in enumerate(pairs):
        try:
            row = ordered.loc[(fs, ts)]
        except KeyError:
            continue
        vol = float(row["volume"])
        segs.append({
            "seq": i,
            "from_stop": fs, "to_stop": ts,
            "from_name": row.get("from_name"), "to_name": row.get("to_name"),
            "volume": vol,
            "load_factor": round(vol / cap, 3),  # ASSUMPTION: see ASSUMED_CAPACITY_BY_MODE
        })
    if not segs:
        return {"route_id": route_id, "direction_id": direction_id, "segments": []}
    mlp = max(segs, key=lambda s: s["volume"])
    return {
        "route_id": route_id,
        "direction_id": direction_id,
        "assumed_vehicle_capacity": cap,
        "max_load_point": {"from_stop": mlp["from_stop"], "to_stop": mlp["to_stop"], "volume": mlp["volume"]},
        "segments": segs,
    }


# ---------------------------------------------------------------------------
# Derived metric #3: system KPIs (PKT/PHT approx, transfer ratio)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4)
def system_kpis(day_type: str = "all", mode: str = "bus") -> dict:
    links = load_links_df() if day_type == "all" else links_for_day_type(day_type)
    speed = ASSUMED_COMMERCIAL_SPEED_KMH.get(mode, ASSUMED_COMMERCIAL_SPEED_KMH["default"])

    dist_km = _haversine_km_vec(links.from_lat.values, links.from_lon.values, links.to_lat.values, links.to_lon.values)
    pkt = float((links.volume * dist_km).sum())               # passenger-km traveled (APPROX: straight-line dist)
    pht = pkt / speed                                          # passenger-hours (APPROX: constant assumed speed)

    stops = compute_stop_boardings_alightings(day_type)
    total_boardings = float(stops["boardings"].sum()) if not stops.empty else 0.0
    # transfer ratio needs a true transfer count (requires itinerary-level
    # assignment output, not present here). We report the alighting/boarding
    # imbalance-free "through volume" share as an illustrative proxy and flag it.
    through_total = float(stops["through"].sum()) if not stops.empty else 0.0

    return {
        "day_type": day_type,
        "total_boardings_est": round(total_boardings, 0),
        "total_pkt_km_approx": round(pkt, 0),
        "total_pht_hours_approx": round(pht, 1),
        "assumed_commercial_speed_kmh": speed,
        "through_volume_proxy": round(through_total, 0),
        "n_routes": int(links.route_id.nunique()),
        "n_stops_served": int(pd.concat([links.from_stop, links.to_stop]).nunique()),
        "notes": {
            "total_boardings_est": "Derived via delta-load method on segment volumes (see compute_stop_boardings_alightings); not a direct farebox/APC count.",
            "total_pkt_km_approx": "volume x straight-line haversine distance between stop coordinates, NOT true alignment length.",
            "total_pht_hours_approx": "PKT / assumed constant commercial speed for the mode; real speeds vary by segment/time-of-day.",
            "through_volume_proxy": "NOT a transfer count. True transfers require itinerary/leg-level assignment output, which this dataset does not include.",
        },
    }
