# Transit Assignment Dashboard

A FastAPI backend + MapLibre/Plotly frontend built directly on your `05\_results/` static macro
transit-assignment outputs (real data included under `data/`, not sample/mock data).

```
transit\_dashboard/
├── backend/
│   ├── main.py            FastAPI app — endpoints for stops, links, routes, OD, KPIs
│   ├── data\_prep.py        loads + derives every planning metric (documented assumptions)
│   └── requirements.txt
├── frontend/
│   └── index.html          MapLibre GL JS + Plotly dashboard, no build step
├── data/                   your real extracted results, wired in and ready to serve
│   ├── mtc\_stops.geojson, mtc\_loaded\_links.geojson
│   ├── all\_days\_route\_loads\_summed.csv, day\_type\_summary.csv, zone\_centroids.csv
│   ├── od\_long\_combined.csv
│   └── daily/               31 days of per-day loaded\_links + route\_loads (for day-type filtering)
└── docs/
    ├── ARCHITECTURE.md      data flow, layer→endpoint mapping, filter caveats
    └── DATA\_SCHEMA.md       field-by-field: source vs. derived, and what's missing for full parity
```

## Quick start

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then open `frontend/index.html` in a browser (double-click works, or serve it with
`python -m http.server` from inside `frontend/`). Swagger docs for every endpoint: `http://localhost:8000/docs`.

## Read this first: what's real vs. derived vs. missing

This export output file is a converged, aggregated assignment output — segment-level passenger volumes per
route/direction, plus route- and zone-level summaries. It does **not** contain per-stop
boarding/alighting counts, vehicle capacities, true alignment lengths/speeds, time-of-day splits,
or the assignment's convergence log. Rather than fabricate those, the backend:

* **derives** boardings/alightings via the standard delta-load method (clearly labeled `\_est` in
every API response),
* **flags assumptions** it had to make (vehicle capacity, commercial speed) as adjustable constants
in `data\_prep.py`, and
* **returns HTTP 501** on the one thing it can't approximate honestly (assignment convergence),
rather than plotting a fake curve.

Full detail, endpoint-by-endpoint, in `docs/DATA\_SCHEMA.md`.

