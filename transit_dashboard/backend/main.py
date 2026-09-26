"""
main.py — Transit Assignment Dashboard API
===========================================
Run:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000

Then open ../frontend/index.html (it points at http://localhost:8000 by
default — change API_BASE at the top of that file if you deploy elsewhere)
or hit http://localhost:8000/docs for interactive Swagger docs.

Endpoints are grouped to match the 5 map-layer families requested:
  /api/stops/*      -> Layer 1: Stop & Station (boarding/alighting)
  /api/links/*      -> Layer 2: Transit Link & Corridor Flow
  /api/od/*         -> Layer 3: OD Matrix (arcs + zone choropleth)
  /api/routes/*     -> Layer 4: Route / shortest-path / load-profile
  /api/zones        -> Layer 5: TAZ centroids (polygon boundaries are not
                       in this dataset — see docs/DATA_SCHEMA.md)
  /api/kpis/*       -> dashboard KPI cards + charts
"""
from functools import lru_cache
from typing import Optional, Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

import data_prep as dp

app = FastAPI(
    title="Transit Assignment Dashboard API",
    description="Serves static macro transit-assignment results as GeoJSON/JSON for planning dashboards.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_methods=["*"],
    allow_headers=["*"],
)
# The full loaded-network response is several MB of JSON (25k+ segments) —
# gzip cuts that by ~85% over the wire. Biggest single fix for "slow to load".
app.add_middleware(GZipMiddleware, minimum_size=1000)

DayType = Literal["all", "weekday", "saturday", "sunday"]


# ---------------------------------------------------------------------------
# Response caches: the underlying dataframes are already cached in
# data_prep.py, but re-looping them into GeoJSON feature dicts on every
# request was still measurable at network scale (25k+ link features).
# Caching the *built* feature list per distinct filter combo means repeat
# requests (the frontend re-fetches on every toggle) are near-instant.
# ---------------------------------------------------------------------------
@lru_cache(maxsize=64)
def _stops_features_cached(day_type: str, route_id: Optional[str], min_total_volume: float) -> tuple:
    stops_meta = {f["properties"]["stop_id"]: f for f in dp.load_stops_geojson()["features"]}
    metrics = dp.compute_stop_boardings_alightings(day_type)

    if route_id:
        links = dp.load_links_df()
        links = links[links.route_id == str(route_id)]
        allowed = set(links.from_stop) | set(links.to_stop)
        metrics = metrics[metrics.stop_id.isin(allowed)]

    metrics = metrics[metrics.total_volume >= min_total_volume]

    features = []
    for row in metrics.itertuples():
        feat = stops_meta.get(row.stop_id)
        if not feat:
            continue
        features.append({
            "type": "Feature",
            "geometry": feat["geometry"],
            "properties": {
                "stop_id": row.stop_id,
                "stop_name": feat["properties"].get("stop_name"),
                "boardings_est": round(row.boardings, 1),
                "alightings_est": round(row.alightings, 1),
                "total_volume": round(row.total_volume, 1),
                "net_flow_ratio": round(row.net_flow_ratio, 3),
                "through_volume_proxy": round(row.through, 1),
            },
        })
    return tuple(features)


@lru_cache(maxsize=64)
def _links_features_cached(day_type: str, route_id: Optional[str], direction_id: Optional[int],
                            min_volume: float, mode: str) -> tuple:
    links = dp.load_links_df() if day_type == "all" else dp.links_for_day_type(day_type)
    if route_id:
        links = links[links.route_id == str(route_id)]
    if direction_id is not None:
        links = links[links.direction_id == direction_id]
    links = links[links.volume >= min_volume]

    cap = dp.ASSUMED_CAPACITY_BY_MODE.get(mode, dp.ASSUMED_CAPACITY_BY_MODE["default"])
    features = []
    for row in links.itertuples():
        features.append({
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "route_id": row.route_id,
                "route": row.route_short_name,
                "direction_id": row.direction_id,
                "from_stop": row.from_stop, "to_stop": row.to_stop,
                "from_name": row.from_name, "to_name": row.to_name,
                "volume": row.volume,
                "load_factor_approx": round(row.volume / cap, 3),
            },
        })
    return tuple(features)


# ---------------------------------------------------------------------------
# Layer 1 — Stops (boarding / alighting)
# ---------------------------------------------------------------------------
@app.get("/api/stops")
def get_stops(
    day_type: DayType = "all",
    min_total_volume: float = Query(0, description="Filter out stops below this total boarding+alighting volume"),
    route_id: Optional[str] = Query(None, description="Only include stops served by this route_id"),
):
    """GeoJSON FeatureCollection of stops sized by total volume and colored
    by net_flow_ratio (boardings-alightings)/(boardings+alightings): near +1
    = predominantly a boarding point (origin-heavy), near -1 = predominantly
    an alighting point (destination-heavy), ~0 = balanced/through stop.
    Boardings/alightings are DERIVED (delta-load method) — see data_prep.py.
    """
    features = _stops_features_cached(day_type, route_id, min_total_volume)
    return {"type": "FeatureCollection", "features": features,
            "meta": {"count": len(features), "day_type": day_type,
                     "definitions": "boardings_est/alightings_est are derived via the delta-load method, not direct APC counts."}}


@app.get("/api/stops/{stop_id}")
def get_stop_detail(stop_id: str, day_type: DayType = "all"):
    metrics = dp.compute_stop_boardings_alightings(day_type)
    row = metrics[metrics.stop_id == stop_id]
    if row.empty:
        raise HTTPException(404, "stop not found or has zero volume for this day_type")
    links = dp.load_links_df()
    serving_routes = sorted(set(links[(links.from_stop == stop_id) | (links.to_stop == stop_id)]["route_short_name"]))
    r = row.iloc[0]
    return {
        "stop_id": stop_id,
        "boardings_est": round(float(r.boardings), 1),
        "alightings_est": round(float(r.alightings), 1),
        "total_volume": round(float(r.total_volume), 1),
        "net_flow_ratio": round(float(r.net_flow_ratio), 3),
        "routes_serving": serving_routes,
    }


# ---------------------------------------------------------------------------
# Layer 2 — Links / corridor flow
# ---------------------------------------------------------------------------
@app.get("/api/links")
def get_links(
    day_type: DayType = "all",
    route_id: Optional[str] = None,
    direction_id: Optional[int] = None,
    min_volume: float = 0,
    mode: str = Query("bus", description="used only to select the assumed vehicle capacity for load_factor"),
):
    """GeoJSON FeatureCollection of loaded network segments. `load_factor`
    = volume / ASSUMED capacity for `mode` (see data_prep.ASSUMED_CAPACITY_BY_MODE)
    — treat as illustrative until real fleet/capacity data is wired in.
    Directional layering: each feature already carries `direction_id`; the
    frontend offsets direction 0 vs 1 parallel lines client-side so inbound/
    outbound don't overdraw each other.
    """
    cap = dp.ASSUMED_CAPACITY_BY_MODE.get(mode, dp.ASSUMED_CAPACITY_BY_MODE["default"])
    features = _links_features_cached(day_type, route_id, direction_id, min_volume, mode)
    return {"type": "FeatureCollection", "features": list(features),
            "meta": {"count": len(features), "day_type": day_type, "assumed_capacity": cap}}


# ---------------------------------------------------------------------------
# Layer 3 — OD matrix (arcs + zone totals)
# ---------------------------------------------------------------------------
@app.get("/api/od/arcs")
def get_od_arcs(top_n: int = Query(150, le=2000), min_volume: float = 0):
    """Top-N OD pairs by trip volume, as origin/destination coordinate pairs
    for arc rendering. This is monthly-combined ticketing-derived demand
    (all day types combined) — there is no time-of-day cut in the source file."""
    od = dp.load_od_pairs()
    od = od[od["Trip volume"] >= min_volume].sort_values("Trip volume", ascending=False).head(top_n)
    arcs = []
    for _, r in od.iterrows():
        arcs.append({
            "origin_zone": str(r["Origin"]), "dest_zone": str(r["Destination"]),
            "volume": float(r["Trip volume"]),
            "origin_lat": r["Origin Lat"], "origin_lon": r["Origin Long"],
            "dest_lat": r["Destination Lat"], "dest_lon": r["Destination Long"],
        })
    return {"arcs": arcs, "meta": {"count": len(arcs)}}


@app.get("/api/od/zones")
def get_od_zone_totals():
    """Zone centroids with total production (origin trips) and attraction
    (destination trips), for a choropleth/proportional-symbol layer."""
    od = dp.load_od_pairs()
    centroids = dp.load_zone_centroids()
    prod = od.groupby("Origin")["Trip volume"].sum().rename("production")
    attr = od.groupby("Destination")["Trip volume"].sum().rename("attraction")
    z = centroids.set_index("zone").join(prod).join(attr).fillna(0).reset_index()
    return {"zones": z.to_dict(orient="records")}


# ---------------------------------------------------------------------------
# Layer 4 — Routes / load profile / Max Load Point
# ---------------------------------------------------------------------------
@app.get("/api/routes")
def list_routes():
    df = dp.load_route_kpis()
    return {"routes": df.to_dict(orient="records")}


@app.get("/api/routes/{route_id}/profile")
def route_profile(route_id: str, direction_id: int = 0, day_type: DayType = "all", mode: str = "bus"):
    profile = dp.route_load_profile(route_id, direction_id, day_type, mode)
    if not profile["segments"]:
        raise HTTPException(404, "no segments found for this route/direction/day_type")
    return profile


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
@app.get("/api/kpis/system")
def kpis_system(day_type: DayType = "all", mode: str = "bus"):
    return dp.system_kpis(day_type, mode)


@app.get("/api/kpis/day-types")
def kpis_day_types():
    """Weekday/Saturday/Sunday calendar tag for every daily source file —
    powers a small calendar/day-type chart."""
    return dp.load_day_type_map().to_dict(orient="records")


@app.get("/api/kpis/convergence")
def kpis_convergence():
    """Assignment relative-gap convergence curve. NOT present in this
    export: only the final converged loaded-network output was written to
    05_results/. Re-running 04_assignment/run_assignment.py with per-
    iteration gap logging enabled would populate this. Returns 501 rather
    than fabricating a curve."""
    raise HTTPException(501, "Convergence/relative-gap log was not exported by this model run. "
                              "Enable iteration logging in run_assignment.py to populate this endpoint.")


@app.get("/health")
def health():
    return {"status": "ok"}
