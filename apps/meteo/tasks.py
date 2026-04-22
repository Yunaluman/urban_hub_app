"""
Tâches météo — deux sources complémentaires :

1. fetch_openmeteo_live  (horaire)
   Source : Open-Meteo (gratuit, sans clé, <1 s par ville)
   Périmètre : 10 grandes villes françaises, dernières 48 h
   → Bronze + Silver (MinIO) + PostgreSQL

2. fetch_noaa_gsod  (quotidien)
   Source : NOAA Global Summary of the Day (CSV par station/année)
   Périmètre : 15 stations majeures françaises (aéroports de référence)
   → Bronze + Silver (MinIO) + PostgreSQL
   Données : résumés journaliers — historisation longue durée
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests
from celery import shared_task
from django.db import transaction

from .models import WeatherObservation, WeatherStation
from ..utils.lake import upload_to_lake

logger = logging.getLogger(__name__)

# ── Open-Meteo ─────────────────────────────────────────────────────────────

OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'

def _city(sid, name, lat, lon):
    return {'station_id': sid, 'name': name, 'city': name, 'lat': lat, 'lon': lon}


FR_CITIES = [
    _city('OPENMETEO-PARIS',        'Paris',        48.8566,   2.3522),
    _city('OPENMETEO-LYON',         'Lyon',         45.7485,   4.8467),
    _city('OPENMETEO-MARSEILLE',    'Marseille',    43.2965,   5.3698),
    _city('OPENMETEO-BORDEAUX',     'Bordeaux',     44.8378,  -0.5792),
    _city('OPENMETEO-TOULOUSE',     'Toulouse',     43.6047,   1.4442),
    _city('OPENMETEO-STRASBOURG',   'Strasbourg',   48.5734,   7.7521),
    _city('OPENMETEO-LILLE',        'Lille',        50.6292,   3.0573),
    _city('OPENMETEO-NICE',         'Nice',         43.7102,   7.2620),
    _city('OPENMETEO-NANTES',       'Nantes',       47.2184,  -1.5536),
    _city('OPENMETEO-MONTPELLIER',  'Montpellier',  43.6119,   3.8772),
]

OPEN_METEO_VARIABLES = (
    'temperature_2m,wind_speed_10m,wind_direction_10m,'
    'surface_pressure,precipitation,visibility'
)


def _fetch_openmeteo_city(city: dict, past_days: int = 2) -> list[dict]:
    """Fetch hourly observations for one city from Open-Meteo."""
    params = {
        'latitude': city['lat'],
        'longitude': city['lon'],
        'hourly': OPEN_METEO_VARIABLES,
        'past_days': past_days,
        'forecast_days': 0,
        'timezone': 'UTC',
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning('Open-Meteo %s: %s', city['name'], exc)
        return []

    hourly = resp.json().get('hourly', {})
    times = hourly.get('time', [])
    rows = []
    for i, ts_str in enumerate(times):
        def _v(key):
            val = hourly.get(key, [None])
            v = val[i] if i < len(val) else None
            return None if v is None else v

        rows.append({
            '_station_id': city['station_id'],
            'timestamp': ts_str,
            'temperature': _v('temperature_2m'),
            'wind_speed': _v('wind_speed_10m'),
            'wind_direction': _v('wind_direction_10m'),
            'pressure': _v('surface_pressure'),
            'precipitation': _v('precipitation'),
            'visibility': (
                _v('visibility') / 1000
                if _v('visibility') is not None else None
            ),
        })
    return rows


@shared_task(bind=True, name='apps.meteo.tasks.fetch_openmeteo_live', max_retries=3)
def fetch_openmeteo_live(self):
    """
    Collecte horaire Open-Meteo pour 10 grandes villes françaises.
    Couvre les 48 dernières heures pour rattraper les exécutions manquées.
    """
    timestamp_str = datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')
    all_rows = []

    with ThreadPoolExecutor(max_workers=min(len(FR_CITIES), 6)) as executor:
        futures = {executor.submit(_fetch_openmeteo_city, city, 2): city for city in FR_CITIES}
        for future in as_completed(futures):
            rows = future.result()
            if rows:
                all_rows.extend(rows)

    if not all_rows:
        logger.warning('fetch_openmeteo_live: aucune donnée récupérée')
        return

    df_raw = pd.DataFrame(all_rows)

    # ── Bronze ────────────────────────────────────────────────────────────
    try:
        upload_to_lake(
            df=df_raw,
            bucket='urbanhub-raw',
            path=f'meteo/openmeteo/openmeteo_{timestamp_str}.parquet',
        )
    except Exception as exc:
        logger.error('Bronze OpenMeteo upload failed: %s', exc)

    # ── Silver : typage + dédup ───────────────────────────────────────────
    try:
        df_silver = df_raw.copy()
        df_silver['timestamp'] = pd.to_datetime(
            df_silver['timestamp'], utc=True
        )
        df_silver = df_silver.drop_duplicates(
            subset=['_station_id', 'timestamp']
        )
        upload_to_lake(
            df=df_silver,
            bucket='urbanhub-processed',
            path=f'meteo/openmeteo/openmeteo_{timestamp_str}.parquet',
        )
    except Exception as exc:
        logger.error('Silver OpenMeteo upload failed: %s', exc)

    # ── PostgreSQL ────────────────────────────────────────────────────────
    try:
        _upsert_openmeteo(df_silver)
    except Exception as exc:
        logger.error('PG upsert OpenMeteo failed: %s', exc)
        raise self.retry(exc=exc, countdown=60)

    logger.info(
        'fetch_openmeteo_live: %d observations (%d villes)',
        len(df_silver), len(FR_CITIES),
    )


def _upsert_openmeteo(df: pd.DataFrame) -> None:
    """Upsert WeatherStation + WeatherObservation pour Open-Meteo."""
    city_map = {c['station_id']: c for c in FR_CITIES}

    with transaction.atomic():
        station_objects = [
            WeatherStation(
                station_id=c['station_id'],
                name=c['name'],
                city=c['city'],
                latitude=c['lat'],
                longitude=c['lon'],
                elevation=None,
            )
            for c in FR_CITIES
        ]
        WeatherStation.objects.bulk_create(
            station_objects,
            update_conflicts=True,
            unique_fields=['station_id'],
            update_fields=['name', 'city', 'latitude', 'longitude'],
            batch_size=50,
        )
        station_map = {
            s.station_id: s
            for s in WeatherStation.objects.filter(
                station_id__in=list(city_map.keys())
            )
        }

        observations = [
            WeatherObservation(
                station=station_map[row['_station_id']],
                timestamp=row['timestamp'],
                temperature=_safe_float(row.get('temperature')),
                wind_speed=_safe_float(row.get('wind_speed')),
                wind_direction=_safe_int(row.get('wind_direction')),
                pressure=_safe_float(row.get('pressure')),
                precipitation=_safe_float(row.get('precipitation')),
                visibility=_safe_float(row.get('visibility')),
            )
            for row in df.to_dict('records')
            if row.get('_station_id') in station_map
        ]
        WeatherObservation.objects.bulk_create(
            observations,
            update_conflicts=True,
            unique_fields=['station', 'timestamp'],
            update_fields=[
                'temperature', 'wind_speed', 'wind_direction',
                'pressure', 'precipitation', 'visibility',
            ],
            batch_size=1000,
        )


# ── NOAA GSOD — historique journalier ──────────────────────────────────────

NOAA_GSOD_URL = os.environ.get(
    'NOAA_GSOD_URL',
    'https://www.ncei.noaa.gov/data/global-summary-of-the-day/access',
)

def _gsod(sid, name, city, lat, lon):
    return {'station_id': sid, 'name': name, 'city': city, 'lat': lat, 'lon': lon}


# 15 stations OACI de référence (aéroports principaux, USAF-WBAN stables)
NOAA_GSOD_STATIONS = [
    _gsod('07156-99999', 'Paris-CDG',            'Paris',            49.013,  2.551),
    _gsod('07149-99999', 'Paris-Orly',           'Paris',            48.716,  2.379),
    _gsod('07481-99999', 'Lyon-Saint-Exupery',   'Lyon',             45.727,  5.090),
    _gsod('07650-99999', 'Marseille-Provence',   'Marseille',        43.436,  5.214),
    _gsod('07510-99999', 'Bordeaux-Merignac',    'Bordeaux',         44.828, -0.715),
    _gsod('07630-99999', 'Toulouse-Blagnac',     'Toulouse',         43.629,  1.367),
    _gsod('07070-99999', 'Strasbourg-Entzheim',  'Strasbourg',       48.538,  7.628),
    _gsod('07015-99999', 'Lille-Lesquin',        'Lille',            50.563,  3.089),
    _gsod('07690-99999', 'Nice-Cote-d-Azur',     'Nice',             43.658,  7.208),
    _gsod('07222-99999', 'Nantes-Atlantique',    'Nantes',           47.158, -1.609),
    _gsod('07643-99999', 'Montpellier-Frej',     'Montpellier',      43.576,  3.963),
    _gsod('07110-99999', 'Brest-Guipavas',       'Brest',            48.447, -4.418),
    _gsod('07460-99999', 'Clermont-Ferrand',     'Clermont-Ferrand', 45.786,  3.165),
    _gsod('07130-99999', 'Rennes-Saint-Jacques', 'Rennes',           48.069, -1.730),
    _gsod('07578-99999', 'Biarritz-Anglet',      'Biarritz',         43.468, -1.523),
]

# Année de départ pour l'historique GSOD
GSOD_START_YEAR = int(os.environ.get('GSOD_START_YEAR', '2020'))


def _fetch_gsod_station(station: dict, year: int) -> pd.DataFrame | None:
    """Télécharge le CSV GSOD pour une station et une année."""
    url = f"{NOAA_GSOD_URL}/{year}/{station['station_id']}.csv"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text), low_memory=False)
        df['_station_id'] = station['station_id']
        return df
    except Exception as exc:
        logger.warning('GSOD %s/%d: %s', station['station_id'], year, exc)
        return None


def _clean_gsod_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise un DataFrame GSOD brut.
    GSOD est en unités US ; on convertit vers SI.
    """
    df = df.copy()

    # Timestamp journalier → minuit UTC
    df['timestamp'] = pd.to_datetime(df['DATE'], errors='coerce', utc=True)
    df = df.dropna(subset=['timestamp'])

    # Température °F → °C  (9999.9 = manquant)
    if 'TEMP' in df.columns:
        t = pd.to_numeric(df['TEMP'], errors='coerce')
        df['temperature'] = ((t - 32) * 5 / 9).where(t != 9999.9)

    # Vent en nœuds → m/s  (999.9 = manquant)
    if 'WDSP' in df.columns:
        w = pd.to_numeric(df['WDSP'], errors='coerce')
        df['wind_speed'] = (w * 0.51444).where(w != 999.9)

    # Pression au niveau de la mer (millibars = hPa, déjà SI) (9999.9 = manquant)
    if 'SLP' in df.columns:
        p = pd.to_numeric(df['SLP'], errors='coerce')
        df['pressure'] = p.where(p != 9999.9)

    # Précipitations en pouces → mm  (99.99 = manquant)
    if 'PRCP' in df.columns:
        prcp_str = df['PRCP'].astype(str).str.extract(r'([\d.]+)')[0]
        prcp = pd.to_numeric(prcp_str, errors='coerce')
        df['precipitation'] = (prcp * 25.4).where(prcp != 99.99)

    # Visibilité en miles → km  (999.9 = manquant)
    if 'VISIB' in df.columns:
        v = pd.to_numeric(df['VISIB'], errors='coerce')
        df['visibility'] = (v * 1.60934).where(v != 999.9)

    # Direction du vent (degrés, 999 = manquant)
    df['wind_direction'] = None

    cols = [
        '_station_id', 'timestamp', 'temperature', 'wind_speed',
        'wind_direction', 'pressure', 'precipitation', 'visibility',
    ]
    existing = [c for c in cols if c in df.columns]
    return df[existing].drop_duplicates(subset=['_station_id', 'timestamp'])


@shared_task(bind=True, name='apps.meteo.tasks.fetch_noaa_gsod', max_retries=2)
def fetch_noaa_gsod(self):
    """
    Collecte quotidienne des résumés journaliers NOAA GSOD.
    15 stations de référence, de GSOD_START_YEAR à aujourd'hui.
    Chaque fichier = 1 station × 1 an (365 lignes max) → très rapide.
    """
    timestamp_str = datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')
    current_year = datetime.now().year
    years = list(range(GSOD_START_YEAR, current_year + 1))

    jobs = [(s, y) for s in NOAA_GSOD_STATIONS for y in years]
    logger.info(
        'GSOD : %d fichiers à télécharger (%d stations × %d ans)',
        len(jobs), len(NOAA_GSOD_STATIONS), len(years),
    )

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_job = {
            executor.submit(_fetch_gsod_station, s, y): (s, y)
            for s, y in jobs
        }
        for future in as_completed(future_to_job):
            df = future.result()
            if df is not None:
                results.append(df)

    if not results:
        logger.warning('GSOD : aucun fichier récupéré')
        return

    df_raw = pd.concat(results, ignore_index=True)

    # ── Bronze ────────────────────────────────────────────────────────────
    try:
        upload_to_lake(
            df=df_raw,
            bucket='urbanhub-raw',
            path=f'meteo/noaa_gsod/noaa_gsod_{timestamp_str}.parquet',
        )
    except Exception as exc:
        logger.error('Bronze GSOD upload failed: %s', exc)

    # ── Silver ────────────────────────────────────────────────────────────
    df_clean = _clean_gsod_df(df_raw)
    try:
        upload_to_lake(
            df=df_clean,
            bucket='urbanhub-processed',
            path=f'meteo/noaa_gsod/noaa_gsod_{timestamp_str}.parquet',
        )
    except Exception as exc:
        logger.error('Silver GSOD upload failed: %s', exc)

    # ── PostgreSQL ────────────────────────────────────────────────────────
    try:
        _upsert_gsod(df_clean)
    except Exception as exc:
        logger.error('PG upsert GSOD failed: %s', exc)
        raise self.retry(exc=exc, countdown=300)

    logger.info(
        'fetch_noaa_gsod: %d observations journalières importées',
        len(df_clean),
    )


def _upsert_gsod(df: pd.DataFrame) -> None:
    """Upsert WeatherStation + WeatherObservation pour NOAA GSOD."""
    station_lookup = {s['station_id']: s for s in NOAA_GSOD_STATIONS}

    with transaction.atomic():
        station_objects = [
            WeatherStation(
                station_id=s['station_id'],
                name=s['name'],
                latitude=s['lat'],
                longitude=s['lon'],
                elevation=None,
            )
            for s in NOAA_GSOD_STATIONS
        ]
        WeatherStation.objects.bulk_create(
            station_objects,
            update_conflicts=True,
            unique_fields=['station_id'],
            update_fields=['name', 'latitude', 'longitude'],
            batch_size=50,
        )
        station_map = {
            s.station_id: s
            for s in WeatherStation.objects.filter(
                station_id__in=list(station_lookup.keys())
            )
        }

        observations = [
            WeatherObservation(
                station=station_map[row['_station_id']],
                timestamp=row['timestamp'],
                temperature=_safe_float(row.get('temperature')),
                wind_speed=_safe_float(row.get('wind_speed')),
                wind_direction=_safe_int(row.get('wind_direction')),
                pressure=_safe_float(row.get('pressure')),
                precipitation=_safe_float(row.get('precipitation')),
                visibility=_safe_float(row.get('visibility')),
            )
            for row in df.to_dict('records')
            if row.get('_station_id') in station_map
        ]
        WeatherObservation.objects.bulk_create(
            observations,
            update_conflicts=True,
            unique_fields=['station', 'timestamp'],
            update_fields=[
                'temperature', 'wind_speed', 'wind_direction',
                'pressure', 'precipitation', 'visibility',
            ],
            batch_size=1000,
        )


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe_float(val):
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def _safe_int(val):
    try:
        v = float(val)
        return None if pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None
