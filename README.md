# UrbanHub — Urban Digital Twin

**English** · [Français](README.fr.md)

A territorial analytics platform for public decision-making (Smart City).
Weather, pollution and soft-mobility data — cross-referenced to produce governance indicators.

---

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (v24+)
- [Anaconda](https://www.anaconda.com/download) or Miniconda (for the local Django UI)

---

## Quick start

```bash
# 1. Environment variables
cp .env.example .env
# Edit .env: POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, SECRET_KEY

# 2. Docker infrastructure (postgres, redis, minio, celery, automatic migrations)
docker compose up -d --remove-orphans
docker compose ps   # wait until every service is healthy

# 3. Conda environment (once)
conda env create -f environment.yml
conda activate urbanhub
pip install -r requirements.txt

# 4. Run Django
python manage.py runserver 127.0.0.1:8000
```

Dashboard: **http://127.0.0.1:8000**

> Migrations run **automatically** through the `migrate` service when Docker starts.
> Collection and analytics publishing are handled **automatically** by Celery Beat
> (Open-Meteo, NOAA GSOD, CityBikes, OpenAQ, EEA, cross analyses, Gold layers).

---

## 1. Configuration

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `MINIO_ROOT_PASSWORD` | MinIO password (min. 8 chars) |
| `SECRET_KEY` | Django secret key (long random string) |

Defaults point to `127.0.0.1` + Docker ports mapped on the host.
Celery services receive the Docker service names (`postgres`, `redis`, `minio`) through the `environment:` blocks of `docker-compose.yml`.

---

## 2. Docker infrastructure

```bash
docker compose up -d
docker compose ps
```

| Service | Role | Host port |
|---------|------|-----------|
| `postgres` | Time-series database (TimescaleDB) | **5432** |
| `redis` | Celery broker + Django cache | 6379 |
| `minio` | S3 data lake (API / console) | 9000 / **9001** |
| `migrate` | Django migrations (one-shot) | — |
| `celery-worker-bronze` | External collection + orchestration | — |
| `celery-worker-silver` | Normalization / Silver Parquet | — |
| `celery-worker-postgres` | PostgreSQL upserts / heavy batches | — |
| `celery-worker-gold` | Gold dashboard publications | — |
| `celery-worker-analysis` | Cross analyses | — |
| `celery-beat` | Scheduler for collection, analyses and Gold publications | — |
| `flower` | Celery monitoring | **5555** |
| `pgweb` | Lightweight PostgreSQL UI | **8081** |
| `jupyter` | Notebooks / data exploration | **8888** |

**Django** (UI + REST API) runs on the host machine, not in Docker.

> **Windows — port 5432 conflict**: a locally installed PostgreSQL often holds this port.
> Fixes: (1) stop the Windows service `postgresql-x64-*`; (2) remap in `docker-compose.yml` (`"5433:5432"`) and set `POSTGRES_PORT=5433` in `.env`.

---

## 3. Local Python environment

```bash
conda env create -f environment.yml   # once
conda activate urbanhub
pip install -r requirements.txt
```

> `environment.yml` includes data-science tools (PySpark, scikit-learn, geopandas) for the notebooks.
> `requirements.txt` holds the Django / Celery dependencies for the app.

**Git Bash — `Run 'conda init' before 'conda activate'`**:

```bash
# Option A — PowerShell or Anaconda Prompt (recommended on Windows)
conda init powershell   # once, then reopen

# Option B — Git Bash, every session
eval "$(conda shell.bash hook)"
conda activate urbanhub
```

**Windows — `UnicodeDecodeError` when connecting to Postgres**:

The project uses `psycopg` v3 (`psycopg[binary]`), which fixes this bug in practice. If the error persists:

1. `pip uninstall psycopg2-binary` (if present)
2. `pip install -r requirements.txt`
3. Make sure `.env` is saved as **UTF-8** (not ANSI / Windows-1252)
4. Make sure `PGCLIENTENCODING=UTF8` is in `.env`

---

## 4. Automatic collection and publishing

Collection is fully handled by **Celery Beat** as soon as Docker is up.
The pipeline covers ingestion, cross analysis and publication of aggregated
"Gold" datasets for the dashboard.

Celery workers are **specialized by workload**:
`bronze` for network collection, `silver` for normalization, `ingestion_postgres`
for heavy DB writes, `gold` for dashboard aggregates and `analysis`
for cross correlations.

| Task | Source | Frequency |
|-------|--------|-----------|
| `fetch_openmeteo_live` | Open-Meteo (10 French cities, rolling 48 h) | Every hour |
| `fetch_noaa_gsod` | NOAA GSOD (daily history, 15 French stations) | Daily at 03:00 |
| `collect_citybikes` | CityBikes API (French networks) | Every 5 minutes |
| `collect_openaq` | OpenAQ v3 (PM2.5, PM10, NO2, O3, CO, SO2) | Every 5 minutes |
| `fetch_eea_historical` | EEA Air Quality Download Service | Daily at 04:15 |
| `run_cross_analysis` | Weather × pollution × mobility correlation | Every hour at :30 |
| `publish_gold_mobility` | Mobility aggregates for the dashboard | Every 10 minutes |
| `publish_gold_air` | Pollution aggregates for the dashboard | Every hour at :20 |
| `publish_gold_climate` | Climate aggregates for the dashboard | Every hour at :25 |

Monitor task execution in **Flower**: http://localhost:5555

Compare the most expensive tasks observed:
`python manage.py task_resource_report --days 7 --limit 10`

---

## 5. Territorial dashboard (Django)

Served by Django (Bootstrap templates + Plotly.js), JSON API under `/api/dashboard/...`.

### UI behavior

- Dashboard views start on **`All cities`** (`__all__`) by default, i.e. the national aggregate when Gold tables are available.
- The city filter then switches to a specific territory without changing the chart layout.
- Explanations are **local to each card / chart** through the `i` icon (tooltip), not in a global panel.
- Cross analysis also works on the national aggregate when weather, pollution and mobility series overlap enough.

| Module | URL |
|--------|-----|
| Overview | http://127.0.0.1:8000 |
| Climatology | http://127.0.0.1:8000/dashboard/climate/ |
| Air & health | http://127.0.0.1:8000/dashboard/air/ |
| Mobility | http://127.0.0.1:8000/dashboard/mobility/ |
| Cross analysis | http://127.0.0.1:8000/dashboard/cross/ |
| Django admin | http://127.0.0.1:8000/admin/ |
| Dashboard API | http://127.0.0.1:8000/api/dashboard/ |
| Domain API | http://127.0.0.1:8000/api/meteo/ |
| Domain API | http://127.0.0.1:8000/api/mobility/ |
| Domain API | http://127.0.0.1:8000/api/pollution/ |
| Analysis API | http://127.0.0.1:8000/api/analysis/ |

---

## Service access

| Interface | URL |
|-----------|-----|
| UrbanHub (Django) | http://127.0.0.1:8000 |
| MinIO Console | http://localhost:9001 |
| Flower (Celery monitoring) | http://localhost:5555 |
| pgweb (PostgreSQL browser) | http://localhost:8081 |
| Jupyter Lab | http://localhost:8888 |
| PostgreSQL / TimescaleDB | localhost:5432 |

---

## Architecture

```
Data sources
  ├── Open-Meteo  (live weather, 10 French cities)
  ├── NOAA GSOD   (daily weather history)
  ├── CityBikes   (French bike networks, real time)
  ├── OpenAQ      (real-time pollution sensors)
  └── EEA         (historical / validated pollution)
          │
          ▼
  Celery Beat + Workers  [Docker]
    ├── bronze               → API collection / raw MinIO archives
    ├── silver               → normalization / processed MinIO Parquet
    ├── ingestion_postgres   → WeatherObservation / BikeSnapshot / PollutionMeasurement
    ├── run_cross_analysis   → CrossAnalysisResult (DB) + MinIO analytics
    └── publish_gold_*       → Gold Postgres tables + Gold MinIO Parquet
          │
          ├── TimescaleDB (time series)
          └── MinIO (data lake — raw / processed / analytics)
                    │
          Django  [host machine :8000]
            ├── ORM → PostgreSQL
            ├── REST API (DRF)
            ├── Dashboard (templates + Plotly.js)
            └── Urban Guide (recommendation API)
```

### Data layers

| Layer | Storage | Content |
|---------|---------|---------|
| `raw` / bronze | MinIO | raw snapshots of external APIs |
| `processed` / silver | MinIO | cleaned / normalized data |
| `ingestion_postgres` / OLTP | PostgreSQL | Django domain tables (`WeatherObservation`, `BikeSnapshot`, `PollutionMeasurement`, etc.) |
| `gold` | PostgreSQL + MinIO analytics | aggregates served fast to the dashboard |

Dashboard endpoints read either the ORM domain tables or the Gold tables
depending on the `DASHBOARD_GOLD_MOBILITY`, `DASHBOARD_GOLD_AIR` and `DASHBOARD_GOLD_CLIMATE`
flags in `.env`.

The target UI experience is:

- land on the **national Gold** aggregate;
- quick read through KPIs + charts;
- zoom in locally with the city filter;
- on-demand help through the `i` tooltips.

### Restart after a refactor

```bash
docker compose down
docker compose up -d --build
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Useful checks:

- `docker compose ps`
- `python manage.py check`
- `python manage.py task_resource_report --days 7 --limit 10`

---

## Project structure

```
urban_hub_app/
├── manage.py               ← Django entry point
├── Dockerfile              ← Celery + Django image
├── requirements.txt        ← Python dependencies
├── docker-compose.yml      ← full infrastructure
├── .dockerignore
├── .env.example            ← template + Windows notes
├── environment.yml         ← conda env (local data science)
├── notebooks/              ← Jupyter exploration / analysis notebooks
├── scripts/                ← utility scripts (e.g. ingestion)
├── urbanhub/               ← Django configuration
│   ├── settings.py
│   ├── celery.py           ← Celery Beat schedules
│   └── urls.py
├── apps/
│   ├── dashboard/          ← UI (templates) + /api/dashboard/*
│   ├── meteo/              ← models, Open-Meteo + NOAA tasks, API
│   ├── mobility/           ← models, CityBikes tasks, API
│   ├── pollution/          ← models, OpenAQ + EEA tasks, API
│   ├── analysis/           ← cross correlations, API
│   ├── serving/            ← Gold publishing to Postgres + MinIO
│   ├── urban_guide/        ← exploratory module, on hold
│   └── utils/
│       ├── lake.py         ← MinIO / data lake client
│       └── lake_readers.py ← reads the latest lake Parquet files
└── templates/dashboard/    ← HTML templates
```

---

## Stopping the infrastructure

```bash
docker compose down
```

Without `-v`, volumes (Postgres, MinIO) are **kept**. Restart: `docker compose up -d`.

---

## Full reset

```bash
# 1. Remove containers + volumes (erases all data)
docker compose down -v

# 2. Restart the infrastructure (automatic migrations included)
docker compose up -d --remove-orphans

# 3. Run Django (conda env activated)
conda activate urbanhub
python manage.py runserver 127.0.0.1:8000
```

**Image rebuild** (after changing `requirements.txt` or `Dockerfile`):

```bash
docker compose build --no-cache
docker compose up -d
```
