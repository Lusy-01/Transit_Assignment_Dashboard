# Chennai MTC Transit Assignment Dashboard

[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=for-the-badge&logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/HTML)
[![MapLibre GL JS](https://img.shields.io/badge/MapLibre_GL_JS-396CB2?style=for-the-badge&logo=mapbox&logoColor=white)](https://maplibre.org/maplibre-gl-js/docs/)
[![Plotly](https://img.shields.io/badge/Plotly-3F4F75?style=for-the-badge&logo=plotly&logoColor=white)](https://plotly.com/javascript/)

A FastAPI backend + MapLibre/Plotly frontend for exploring a static macro transit-assignment
model of Chennai's MTC bus network — corridor loads, boarding/alighting estimates, route load
profiles, and zone-to-zone OD demand. Built directly on real extracted model outputs (included
under `data/`, not sample/mock data).

<!-- Add a screenshot or short GIF of the dashboard here once you have one, e.g.:
![Dashboard screenshot](docs/screenshots/dashboard.png)
-->

## Quick start

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then open `frontend/index.html` in a browser (double-click works, or serve it with
`python -m http.server` from inside `frontend/`). Swagger docs for every endpoint:
`http://localhost:8000/docs`.

## Project structure

```
transit_dashboard/
├── backend/
│   ├── main.py             FastAPI app — endpoints for stops, links, routes, OD, KPIs
│   ├── data_prep.py        loads + derives every planning metric (documented assumptions)
│   └── requirements.txt
├── frontend/
│   └── index.html          MapLibre GL JS + Plotly dashboard, no build step
├── data/                   real extracted model results, wired in and ready to serve
│   ├── mtc_stops.geojson, mtc_loaded_links.geojson
│   ├── all_days_route_loads_summed.csv, day_type_summary.csv, zone_centroids.csv
│   ├── od_long_combined.csv
│   └── daily/              31 days of per-day loaded_links + route_loads (for day-type filtering)
└── docs/
    ├── ARCHITECTURE.md      data flow, layer→endpoint mapping, filter caveats
    └── DATA_SCHEMA.md        field-by-field: source vs. derived, and what's missing for full parity
```

## Read this first: what's real vs. derived vs. missing

The underlying model export is a converged, aggregated assignment output — segment-level
passenger volumes per route/direction, plus route- and zone-level summaries. It does **not**
contain per-stop boarding/alighting counts, vehicle capacities, true alignment lengths/speeds,
time-of-day splits, or the assignment's convergence log. Rather than fabricate those, the backend:

- **derives** boardings/alightings via the standard delta-load method (clearly labeled `_est` in
  every API response),
- **flags assumptions** it had to make (vehicle capacity, commercial speed) as adjustable
  constants in `data_prep.py`, and
- **returns HTTP 501** on the one thing it can't approximate honestly (assignment convergence),
  rather than plotting a fake curve.

Full detail, endpoint-by-endpoint, in `docs/DATA_SCHEMA.md`.

## License

Add a `LICENSE` file (GitHub's "Add file → Create new file → LICENSE" has a built-in template
picker, e.g. MIT) if you want others to be able to reuse this code — a public repo with no license
file is "all rights reserved" by default.
