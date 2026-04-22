# UrbanHub — Jumeau Numérique Urbain

Plateforme d'analyse territoriale pour la prise de décision publique (Smart City).  
Données météo, pollution, mobilité douce — croisées pour produire des indicateurs d'aide à la gouvernance.

---

## Prérequis

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (v24+)
- [Anaconda](https://www.anaconda.com/download) ou Miniconda (pour l'interface Django locale)

---

## Démarrage rapide

```bash
# 1. Variables d'environnement
cp .env.example .env
# Éditer .env : POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, SECRET_KEY

# 2. Infrastructure Docker (postgres, redis, minio, celery, migrations automatiques)
docker compose up -d --remove-orphans
docker compose ps   # attendre que tous les services soient healthy

# 3. Environnement conda (une seule fois)
conda env create -f environment.yml
conda activate urbanhub
pip install -r requirements.txt

# 4. Lancer Django
python manage.py runserver 127.0.0.1:8000
```

Tableau de bord : **http://127.0.0.1:8000**

> Les migrations sont exécutées **automatiquement** par le service `migrate` au démarrage de Docker.  
> La collecte et la publication analytique sont gérées **automatiquement** par Celery Beat
> (Open-Meteo, NOAA GSOD, CityBikes, OpenAQ, EEA, analyses croisées, couches Gold).

---

## 1. Configuration

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `POSTGRES_PASSWORD` | Mot de passe PostgreSQL |
| `MINIO_ROOT_PASSWORD` | Mot de passe MinIO (min. 8 car.) |
| `SECRET_KEY` | Clé secrète Django (longue chaîne aléatoire) |

Les valeurs par défaut pointent vers `127.0.0.1` + ports Docker mappés sur l'hôte.  
Les services Celery reçoivent les noms de services Docker (`postgres`, `redis`, `minio`) via les blocs `environment:` de `docker-compose.yml`.

---

## 2. Infrastructure Docker

```bash
docker compose up -d
docker compose ps
```

| Service | Rôle | Port hôte |
|---------|------|-----------|
| `postgres` | Base time-series (TimescaleDB) | **5432** |
| `redis` | Broker Celery + cache Django | 6379 |
| `minio` | Data Lake S3 (API / console) | 9000 / **9001** |
| `migrate` | Migrations Django (one-shot) | — |
| `celery-worker` | Traitement asynchrone des tâches | — |
| `celery-beat` | Planificateur des collectes, analyses et publications Gold | — |
| `flower` | Monitoring Celery | **5555** |
| `pgweb` | Interface légère PostgreSQL | **8081** |
| `jupyter` | Notebooks / exploration data | **8888** |

**Django** (UI + API REST) tourne sur la machine hôte, pas dans Docker.

> **Windows — conflit port 5432** : si un PostgreSQL local est installé, il occupe souvent ce port.  
> Solutions : (1) arrêter le service Windows `postgresql-x64-*` ; (2) remapper dans `docker-compose.yml` (`"5433:5432"`) et mettre `POSTGRES_PORT=5433` dans `.env`.

---

## 3. Environnement Python local

```bash
conda env create -f environment.yml   # une seule fois
conda activate urbanhub
pip install -r requirements.txt
```

> `environment.yml` inclut les outils data science (PySpark, scikit-learn, geopandas) pour les notebooks.  
> `requirements.txt` contient les dépendances Django / Celery pour l'application.

**Git Bash — `Run 'conda init' before 'conda activate'`** :

```bash
# Option A — PowerShell ou Anaconda Prompt (recommandé sous Windows)
conda init powershell   # une seule fois, puis rouvrir

# Option B — Git Bash, à chaque session
eval "$(conda shell.bash hook)"
conda activate urbanhub
```

**Windows — `UnicodeDecodeError` à la connexion Postgres** :

Le projet utilise `psycopg` v3 (`psycopg[binary]`) qui règle en pratique ce bug. Si l'erreur persiste :

1. `pip uninstall psycopg2-binary` (si présent)
2. `pip install -r requirements.txt`
3. Vérifier que `.env` est enregistré en **UTF-8** (pas ANSI / Windows-1252)
4. Vérifier `PGCLIENTENCODING=UTF8` dans `.env`

---

## 4. Collecte et publications automatiques

La collecte est entièrement gérée par **Celery Beat** dès que Docker est lancé.
Le pipeline couvre l'ingestion, l'analyse croisée et la publication de jeux de données
agrégés "Gold" pour le dashboard.

| Tâche | Source | Fréquence |
|-------|--------|-----------|
| `fetch_openmeteo_live` | Open-Meteo (10 villes FR, 48 h glissantes) | Toutes les heures |
| `fetch_noaa_gsod` | NOAA GSOD (historique journalier, 15 stations FR) | Tous les jours à 03:00 |
| `collect_citybikes` | CityBikes API (réseaux FR) | Toutes les 5 minutes |
| `collect_openaq` | OpenAQ v3 (PM2.5, PM10, NO2, O3, CO, SO2) | Toutes les 5 minutes |
| `fetch_eea_historical` | EEA Air Quality Download Service | Tous les jours à 04:15 |
| `run_cross_analysis` | Corrélation météo × pollution × mobilité | Toutes les heures à :30 |
| `publish_gold_mobility` | Agrégats mobilité pour dashboard | Toutes les 10 minutes |
| `publish_gold_air` | Agrégats pollution pour dashboard | Toutes les heures à :20 |
| `publish_gold_climate` | Agrégats climat pour dashboard | Toutes les heures à :25 |

Surveiller l'exécution des tâches sur **Flower** : http://localhost:5555

---

## 5. Dashboard territorial (Django)

Servi par Django (templates Bootstrap + Plotly.js), API JSON sous `/api/dashboard/...`.

| Module | URL |
|--------|-----|
| Vue d'ensemble | http://127.0.0.1:8000 |
| Climatologie | http://127.0.0.1:8000/dashboard/climate/ |
| Air & santé | http://127.0.0.1:8000/dashboard/air/ |
| Mobilité | http://127.0.0.1:8000/dashboard/mobility/ |
| Analyse croisée | http://127.0.0.1:8000/dashboard/cross/ |
| Admin Django | http://127.0.0.1:8000/admin/ |
| API dashboard | http://127.0.0.1:8000/api/dashboard/ |
| API métier | http://127.0.0.1:8000/api/meteo/ |
| API métier | http://127.0.0.1:8000/api/mobility/ |
| API métier | http://127.0.0.1:8000/api/pollution/ |
| API analyse | http://127.0.0.1:8000/api/analysis/ |
| API Urban Guide | http://127.0.0.1:8000/api/guide/ |

### Urban Guide

Le dépôt contient aussi un module "Urban Guide" exposé sous `/api/guide/` :

| Endpoint | Rôle |
|----------|------|
| `/api/guide/urban-score/` | Score synthétique de qualité de journée urbaine |
| `/api/guide/outfit/` | Conseils vestimentaires selon météo + pollution |
| `/api/guide/activities/` | Suggestions d'activités selon météo + mobilité |
| `/api/guide/dashboard-data/` | Payload agrégé pour une UI guide |

---

## Accès aux services

| Interface | URL |
|-----------|-----|
| UrbanHub (Django) | http://127.0.0.1:8000 |
| MinIO Console | http://localhost:9001 |
| Flower (monitoring Celery) | http://localhost:5555 |
| pgweb (browser PostgreSQL) | http://localhost:8081 |
| Jupyter Lab | http://localhost:8888 |
| PostgreSQL / TimescaleDB | localhost:5432 |

---

## Architecture

```
Sources de données
  ├── Open-Meteo  (météo live, 10 villes FR)
  ├── NOAA GSOD   (météo historique journalière)
  ├── CityBikes   (réseaux vélos FR, temps réel)
  ├── OpenAQ      (capteurs pollution temps réel)
  └── EEA         (pollution historisée / vérifiée)
          │
          ▼
  Celery Beat + Workers  [Docker]
    ├── ingestion météo      → WeatherObservation (DB) + MinIO raw/processed
    ├── ingestion mobilité   → BikeSnapshot (DB) + MinIO raw/processed
    ├── ingestion pollution  → PollutionMeasurement (DB) + MinIO raw/processed
    ├── run_cross_analysis   → CrossAnalysisResult (DB) + MinIO analytics
    └── publish_gold_*       → tables Gold Postgres + Parquet Gold MinIO
          │
          ├── TimescaleDB (séries temporelles)
          └── MinIO (Data Lake — raw / processed / analytics)
                    │
          Django  [machine hôte :8000]
            ├── ORM → PostgreSQL
            ├── API REST (DRF)
            ├── Dashboard (templates + Plotly.js)
            └── Urban Guide (API de recommandation)
```

### Couches de données

| Couche | Support | Contenu |
|--------|---------|---------|
| `raw` / bronze | MinIO | snapshots bruts des APIs externes |
| `processed` / silver | MinIO | données nettoyées / normalisées |
| OLTP | PostgreSQL | tables métier Django (`WeatherObservation`, `BikeSnapshot`, `PollutionMeasurement`, etc.) |
| `gold` | PostgreSQL + MinIO analytics | agrégats servis rapidement au dashboard |

Les endpoints dashboard peuvent lire soit les tables métier ORM, soit les tables Gold
selon les flags `DASHBOARD_GOLD_MOBILITY`, `DASHBOARD_GOLD_AIR` et `DASHBOARD_GOLD_CLIMATE`
dans `.env`.

---

## Structure du projet

```
urban_hub_app/
├── manage.py               ← point d'entrée Django
├── Dockerfile              ← image Celery + Django
├── requirements.txt        ← dépendances Python
├── docker-compose.yml      ← infrastructure complète
├── .dockerignore
├── .env.example            ← modèle + commentaires Windows
├── environment.yml         ← env conda (data science local)
├── notebooks/              ← notebooks Jupyter d'exploration / analyse
├── scripts/                ← scripts utilitaires (ex. ingestion)
├── urbanhub/               ← configuration Django
│   ├── settings.py
│   ├── celery.py           ← schedules Celery Beat
│   └── urls.py
├── apps/
│   ├── dashboard/          ← UI (templates) + /api/dashboard/*
│   ├── meteo/              ← modèles, tâches Open-Meteo + NOAA, API
│   ├── mobility/           ← modèles, tâches CityBikes, API
│   ├── pollution/          ← modèles, tâches OpenAQ + EEA, API
│   ├── analysis/           ← corrélations croisées, API
│   ├── serving/            ← publication Gold Postgres + MinIO
│   ├── urban_guide/        ← score urbain, tenue, activités, événements
│   └── utils/
│       ├── lake.py         ← client MinIO / Data Lake
│       └── lake_readers.py ← lecture des derniers Parquet du lake
└── templates/dashboard/    ← templates HTML
```

---

## Arrêter l'infrastructure

```bash
docker compose down
```

Sans `-v`, les volumes (Postgres, MinIO) sont **conservés**. Relancer : `docker compose up -d`.

---

## Remise à plat (réinitialiser complètement)

```bash
# 1. Supprimer conteneurs + volumes (efface toutes les données)
docker compose down -v

# 2. Relancer l'infra (migrations auto incluses)
docker compose up -d --remove-orphans

# 3. Lancer Django (env conda activé)
conda activate urbanhub
python manage.py runserver 127.0.0.1:8000
```

**Rebuild d'images** (après modification de `requirements.txt` ou `Dockerfile`) :

```bash
docker compose build --no-cache
docker compose up -d
```
