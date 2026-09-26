# Data Schema

Two kinds of fields below: **source** fields (present verbatim in `05_results/`) and **derived**
fields (computed by `backend/data_prep.py` — method noted for each). Treat derived fields as
modeled estimates, not measured counts, when presenting to stakeholders.

## 1. Stops — `mtc_stops.geojson` (source) → `/api/stops` (enriched)

| Field | Type | Source or derived | Notes |
|---|---|---|---|
| `stop_id` | string | source | opaque ID, not a public-facing stop code |
| `stop_name` | string | source | |
| geometry | Point (lon,lat) | source | |
| `boardings_est` | float | **derived** | delta-load method: at each stop along a route-direction's reconstructed sequence, `boarding = max(volume_out_link − volume_in_link, 0)`; summed across every route serving the stop |
| `alightings_est` | float | **derived** | same method, opposite sign: `alighting = max(volume_in_link − volume_out_link, 0)` |
| `total_volume` | float | derived | `boardings_est + alightings_est` |
| `net_flow_ratio` | float, [-1,1] | derived | `(boardings − alightings) / total_volume`; near +1 = origin-heavy, near −1 = destination-heavy |
| `through_volume_proxy` | float | derived | onboard load that continues past the stop on the *same* route; **not** a cross-route transfer count (see §4) |

**What's missing for exact boarding/alighting:** stop-level automatic passenger counter (APC) data,
or a fare-transaction tap-on/tap-off table. The delta-load method is the standard planning-industry
fallback when only segment loads are available, but it will misattribute board/alight at stops
where a route pattern branches or loops — those route-directions fall back to a coarser
"through/half-split" estimate (see `_order_route_direction_chain` in `data_prep.py`).

## 2. Links — `mtc_loaded_links.geojson` (source) → `/api/links` (enriched)

| Field | Type | Source or derived | Notes |
|---|---|---|---|
| `route_id` | string | source | |
| `route` (route_short_name) | string | source | |
| `direction_id` | int (0/1) | source | |
| `from_stop`, `to_stop` | string | source | stop_id references |
| `from_name`, `to_name` | string | source | |
| `volume` | float | source | passengers carried on that segment, this route-direction, summed over the period selected by `day_type` |
| geometry | LineString | source | straight segment between the two stop points, not the true street/track alignment |
| `load_factor_approx` | float | **derived, assumption-dependent** | `volume / ASSUMED_CAPACITY_BY_MODE[mode]` — real vehicle capacity by route is not in this export; wire in fleet/GTFS `vehicle_capacity` to replace the assumption |

**day_type aggregation:** `all` uses the pre-summed master GeoJSON; `weekday`/`saturday`/`sunday`
re-aggregate the 31 per-day `*_loaded_links.csv` files (grouped via `day_type_summary.csv`) at
request time and join geometry back from the master file on
`(route_id, direction_id, from_stop, to_stop)`.

## 3. Routes — `all_days_route_loads_summed.csv` (source) → `/api/routes`

| Field | Type | Source or derived |
|---|---|---|
| `route_id` | string | source |
| `route_short_name`, `route_long_name` | string | source |
| `max_link_volume` | float | source — this is the **Max Load Point (MLP)** volume for the route |
| `avg_link_volume` | float | source |
| `n_loaded_links` | int | source |
| `total_pax_km_links` | float | source (already pre-computed upstream, presumably from true alignment length — **more trustworthy than** the haversine PKT approximation this dashboard computes at the system level, use this column over `/api/kpis/system`'s PKT when accuracy matters per-route) |

`/api/routes/{id}/profile` additionally derives, per segment: `seq` (position along the
reconstructed stop sequence), `load_factor` (assumption-dependent, see §2), and the route's
`max_load_point` (from_stop/to_stop/volume of the highest-loaded segment).

## 4. OD pairs — `od_long_combined.csv` (source) → `/api/od/arcs`, `/api/od/zones`

| Field (source file) | Maps to |
|---|---|
| `Origin`, `Destination` | zone IDs |
| `Trip volume` | ticketing-derived trip count between the zone pair, whole month combined |
| `Origin Lat/Long`, `Destination Lat/Long` | zone centroid coordinates (joined in upstream from `zone_centroids.csv`) |

This is **zone-to-zone demand**, not a boarding/alighting-stop OD — it answers "how many people
travel between zone A and zone B", independent of which routes/paths carry them. There is a
separate `*_od_costs.csv` file per day (`Origin, Destination, Trip_volume, Generalized_cost_sec,
Generalized_cost_min`) with the assignment's generalized travel cost per OD pair — not yet wired
into an endpoint; a natural `/api/od/costs` addition for a "travel time by OD" choropleth.

**Transfer ratio:** genuinely computing `transfers / total_boardings` requires itinerary-level
(leg-by-leg) assignment output — which route(s), in what order, a trip used — which this export
does not include (only aggregate segment volumes survived from the per-day assignment run). The
`through_volume_proxy` field is the closest available substitute and is explicitly not a transfer
count; don't present it to stakeholders as one.

## 5. What would need to be added for full parity with the request

| Requested item | Status | What's needed |
|---|---|---|
| TAZ polygons (not just centroids) | not present | a TAZ boundary shapefile/GeoJSON keyed on the same `zone` ID as `zone_centroids.csv` |
| Centroid connectors | not present | the connector edges from `zone_centroids_and_graphs/assignment_graph_*.pkl` (the graphs exist — connectors could be extracted from them with a short script, not attempted here since the `.pkl` is a solver-internal graph object, not a portable edge list) |
| Vehicle capacity / Load Factor (true) | not present | a route/mode → capacity lookup (GTFS `vehicle_type` + fleet spec) |
| Wait time / transfer time / commercial speed (true) | not present | GTFS `stop_times.txt` (scheduled) or AVL data (actual) |
| Time-of-day (AM/PM/Off-Peak) periods | not present | re-run `04_assignment/run_assignment.py` against time-sliced OD matrices (the pipeline currently only slices by day type) |
| Assignment convergence / relative gap | not present | enable per-iteration gap logging in `run_assignment.py`; only the converged result was exported |
| Modal split | not applicable | this network is single-mode (bus); would need other modes' assignment output alongside it |
