# Dataset Overview

Actual counts from the source export (`05_results/`), computed directly from the files —
not estimates.

| Item | Count | Notes |
|---|---|---|
| Stops | 4,674 | `mtc_stops.geojson` |
| Routes (link geometry) | 586 | distinct `route_id` values across the loaded network |
| Routes (KPI summary) | 603 | rows in `all_days_route_loads_summed.csv` — the gap vs. 586 likely means a handful of routes have a KPI row but zero loaded links; worth investigating upstream if it matters for your use case |
| Loaded network segments (links) | 25,717 | route + direction + from-stop/to-stop combinations, **not** physical road segments |
| Zones (OD centroids) | 1,190 | `zone_centroids.csv` |
| OD zone-pairs | 50,227 | distinct origin-destination combinations in `od_long_combined.csv` |
| Total OD trip volume | 37,482,773 | monthly, ticketing-derived, all zone-pairs summed |
| Total link-level passenger volume (all-days summed) | 570,467,398 | **not directly comparable** to the OD trip total above — this counts segment-boardings (one trip across a 20-segment route contributes 20 rows of volume), while the OD figure counts distinct trips once |
| Daily source files | 31 | 23 weekday, 4 Saturday, 4 Sunday — this is what the day-type filter aggregates over |

## Why the two "total volume" numbers look inconsistent but aren't

It's easy to compare 570M (link volume) against 37M (OD trips) and assume something's double
counted or broken. They're measuring different things:

- **OD trip volume** — how many distinct passenger trips were made from zone A to zone B, counted once per trip, regardless of route or number of stops traveled.
- **Link volume** — how many passengers were onboard for each individual segment of their journey. A single trip riding a route for 20 stops contributes to the volume of all 20 segments it passed through, not just one.

So link volume will always be larger than OD trip volume for any network with more than one stop
per route, and the ratio between them is roughly "average number of segments per trip" — not a
data quality signal by itself.
