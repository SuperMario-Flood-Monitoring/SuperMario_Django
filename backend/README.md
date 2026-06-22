# Intelligent Urban Flood Backend

Django backend for storing facility baseline values, running EPA SWMM through
PySWMM, and broadcasting step and final simulation results.

## Run locally

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## API

- `GET/POST /api/facilities/`: list facilities or initialize baseline values
- `GET/PUT/DELETE /api/facilities/<id>/`: facility detail
- `GET/POST /api/simulations/`: list recent runs or start a simulation
- `POST /api/simulations/stop/`: stop the simulation engine
- `GET /api/simulations/demo/`: browser-based PySWMM test page
- `WS /ws/simulation/`: subscribe to simulation results

Initialize facilities:

```json
{
  "facilities": [
    {
      "name": "catch-basin-1",
      "facility_type": "CATCH_BASIN",
      "location": "A district",
      "normal_value": 10,
      "unit": "cm",
      "metadata": {
        "anomaly_threshold": 15
      }
    }
  ]
}
```

Start a simulation:

```json
{
  "rainfall_status": "HEAVY_RAIN",
  "rainfall_amount": 80,
  "duration_minutes": 30,
  "parameters": {},
  "model": {},
  "control": {}
}
```

Use the complete sample payload shown at `/api/simulations/demo/` or in
`swmm_engine/models`.

The demo page exposes editable facility initialization JSON and streams
normalized water level, blockage, obstruction cause, and failure status once
per second for up to thirty simulated seconds.

## Docker

```powershell
docker compose up --build
```

SQLite data is persisted in the `sqlite_data` volume.
