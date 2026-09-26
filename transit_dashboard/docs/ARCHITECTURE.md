# System Architecture — Transit Assignment Dashboard

## 1. Data flow

```
05_results/ (your model outputs)                    backend/ (FastAPI)                 frontend/
─────────────────────────────                       ────────────────                   ─────────
mtc_loaded_links.geojson  ──────────────┐
  (route/dir/from/to + geometry)        │
mtc_stops.geojson  ──────────────┐      │            data_prep.py
  (stop_id, stop_name, point)    │      ├──────►      · loads + caches CSV/GeoJSON       index.html
all_days_route_loads_summed.csv ─┤      │            · joins per-day volumes to          (MapLibre GL JS
day_type_summary.csv ────────────┤      │              master link geometry              + Plotly, no
zone_centroids.csv ──────────────┤      │            · derives boardings/alightings      build step)
od_long_combined.csv ────────────┘      │              per stop (delta-load method)
daily/*_loaded_links.csv (31 files) ────┘            · derives PKT/PHT/load-factor            │
                                                        under explicit assumptions             │
                                                                    │                            │
                                                                    ▼                            │
                                                              main.py (FastAPI)                  │
                                                              /api/stops   (GeoJSON)  ───────────┤
                                                              /api/links   (GeoJSON)  ───────────┤  fetch()
                                                              /api/od/arcs (JSON)     ───────────┤  at runtime
                                                              /api/od/zones(JSON)     ───────────┤
                                                              /api/routes/*(JSON)     ───────────┤
                                                              /api/kpis/*  (JSON)     ───────────┘
```

**Why this split.** The static assignment outputs are CSV + one pre-built GeoJSON pair (stops,
loaded links). Re-deriving planning metrics (boardings/alightings, load factor, PKT/PHT) is
non-trivial enough (see `data_prep.py` docstring) that it belongs server-side, cached, and unit-
testable — not recomputed in browser JS on every filter change. The frontend only ever asks for
already-filtered, already-aggregated JSON/GeoJSON, so it stays a static file with no build step.

## 2. Why a live backend instead of a pre-baked static site

Two things make a purely static (pre-tiled) deployment the wrong first move here:

1. **The "day type" filter needs on-the-fly aggregation.** Only the all-days-summed link volumes
   ship as a single GeoJSON. Weekday/Saturday/Sunday link volumes have to be re-aggregated from
   31 daily CSVs (`links_for_day_type()`), which is cheap in Pandas but wasteful to pre-materialize
   as 4x the static GeoJSON.
2. **Derived metrics are assumption-dependent.** Boarding/alighting, load factor, and PKT/PHT all
   depend on parameters (assumed vehicle capacity, assumed commercial speed) a planner will want to
   change. Baking those into static files means re-exporting every time an assumption changes;
   serving them from `data_prep.py` means changing one constant.

Once the network stabilizes, the same `data_prep.py` functions can be run offline to emit static
GeoJSON/PMTiles per day-type for a CDN-hosted version — the derivation logic doesn't change, only
whether it runs per-request or per-build.

## 3. Map layer → API endpoint mapping

| Requested layer | Endpoint | Notes |
|---|---|---|
| 1. Stop & Station (boarding/alighting) | `GET /api/stops` | boardings/alightings are **derived** (delta-load method), not direct APC counts — see Data Schema doc |
| 2. Transit Link & Corridor Flow | `GET /api/links` | `load_factor_approx` needs a real vehicle-capacity input; ships with a placeholder assumption |
| 3. OD Matrix (arcs + zone choropleth) | `GET /api/od/arcs`, `GET /api/od/zones` | ticketing-derived OD, monthly combined — no time-of-day cut in the source export |
| 4. Route / shortest-path / load profile | `GET /api/routes`, `GET /api/routes/{id}/profile` | "shortest path" in the assignment sense (skims) isn't in this export — profile is the loaded route pattern, with Max Load Point |
| 5. TAZ / centroids / connectors | `GET /api/od/zones` | only zone **centroids** exist in the export, not TAZ polygons or centroid-connector links — see Data Schema doc for what would need to be added |

## 4. Filtering & interactivity, mapped to what the data actually supports

| Requested control | Implemented as | Caveat |
|---|---|---|
| Time period (AM/PM/Off-Peak) | **Not implemented as time-of-day** — mapped to `day_type` (weekday/Saturday/Sunday) | The pipeline's own README confirms only day-type resolution was exported; true peak/off-peak needs the model re-run with time-sliced OD |
| Mode (Bus/Rail/Metro) | `mode` query param drives the *assumed capacity/speed* used for load factor & PKT/PHT | The network itself is single-mode (bus) in this export; multi-mode filtering needs a `mode` column added to the source GTFS/route table |
| Route ID | `route_id` param on `/api/stops`, `/api/links`, `/api/routes/{id}/profile` | fully supported |
| TAZ/zone selection | zone-level totals via `/api/od/zones` | no TAZ polygon geometry in the export — see Data Schema §5 |
| Click link → update stop chart | frontend: click handler on `links-layer` calls `/api/routes/{id}/profile` and redraws the Plotly bar chart | implemented in `frontend/index.html` |
| Click stop → highlight routes | frontend: click handler on `stops-layer` calls `/api/stops/{id}` for `routes_serving` | implemented |
| Scenario comparison (baseline vs. assigned) | **Not implemented** | this export is a single assignment run; a real toggle needs a second run's `all_days_loaded_links_summed.csv` to diff against — the schema below is written so a second file drops in cleanly |

## 5. Adding scenario comparison later

`/api/links` and `/api/stops` are already parameterized by `day_type`; extending to
`scenario=baseline|proposed` is the same shape of change — add a second data directory
(`data/baseline/…`), a `scenario` query param, and a diff endpoint that subtracts matched
`(route_id, direction_id, from_stop, to_stop)` volumes between the two. Not built here because
only one scenario's outputs were provided.

## 6. Running it

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# then open frontend/index.html directly in a browser (it calls http://localhost:8000)
```

`frontend/index.html` is a static file with no build step (MapLibre GL JS + Plotly.js from CDN),
so any static file server works too (`python -m http.server` from the `frontend/` folder).
