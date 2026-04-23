"""
Tâches météo — Bronze et Silver séparés.

fetch_openmeteo_live  → fetch_openmeteo_bronze  puis fetch_openmeteo_silver
fetch_noaa_gsod       → fetch_noaa_gsod_bronze   puis fetch_noaa_gsod_silver

Chaque étape est aussi une tâche Celery indépendante, déclenchable manuellement.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests
from celery import chain, shared_task

from ..utils.lake import download_from_lake, upload_to_lake
from .persistence import FR_CITIES, NOAA_GSOD_STATIONS, upsert_gsod, upsert_openmeteo

logger = logging.getLogger(__name__)

# ── Open-Meteo ─────────────────────────────────────────────────────────────

OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'

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
        def _v(key, idx=i):
            val = hourly.get(key, [None])
            v = val[idx] if idx < len(val) else None
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


@shared_task(name='apps.meteo.tasks.fetch_openmeteo_bronze')
def fetch_openmeteo_bronze():
    """Bronze : collecte brute Open-Meteo pour 10 villes FR (48 h glissantes)."""
    timestamp_str = datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')
    all_rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_fetch_openmeteo_city, c, 2): c for c in FR_CITIES}
        for future in as_completed(futures):
            all_rows.extend(future.result() or [])

    if not all_rows:
        logger.warning('fetch_openmeteo_bronze: aucune donnée récupérée')
        return None

    try:
        upload_to_lake(
            df=pd.DataFrame(all_rows),
            bucket='urbanhub-raw',
            path=f'meteo/openmeteo/openmeteo_{timestamp_str}.parquet',
        )
    except Exception as exc:
        logger.error('Bronze OpenMeteo upload: %s', exc)

    logger.info('fetch_openmeteo_bronze: %d lignes collectées', len(all_rows))
    return {'rows': all_rows, 'timestamp_str': timestamp_str}


@shared_task(bind=True, name='apps.meteo.tasks.fetch_openmeteo_silver', max_retries=3)
def fetch_openmeteo_silver(self, bronze_result):
    """Silver : nettoie, déduplique et archive en Parquet les données Open-Meteo."""
    if not bronze_result:
        logger.warning('fetch_openmeteo_silver: pas de données bronze')
        return {'rows_processed': 0}

    df = pd.DataFrame(bronze_result['rows'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.drop_duplicates(subset=['_station_id', 'timestamp'])
    processed_path = f'meteo/openmeteo/openmeteo_{bronze_result["timestamp_str"]}.parquet'

    try:
        upload_to_lake(
            df=df,
            bucket='urbanhub-processed',
            path=processed_path,
        )
    except Exception as exc:
        logger.error('Silver OpenMeteo upload: %s', exc)

    logger.info('fetch_openmeteo_silver: %d observations normalisées', len(df))
    return {
        'processed_path': processed_path,
        'timestamp_str': bronze_result['timestamp_str'],
        'rows_processed': len(df),
    }


@shared_task(bind=True, name='apps.meteo.tasks.ingest_openmeteo_postgres', max_retries=3)
def ingest_openmeteo_postgres(self, silver_result):
    """Postgres : upsert WeatherStation + WeatherObservation pour Open-Meteo."""
    if not silver_result or not silver_result.get('processed_path'):
        logger.warning('ingest_openmeteo_postgres: pas de données silver')
        return {'rows_upserted': 0}

    try:
        df = download_from_lake('urbanhub-processed', silver_result['processed_path'])
        upsert_openmeteo(df)
    except Exception as exc:
        logger.error('PG upsert OpenMeteo: %s', exc)
        raise self.retry(exc=exc, countdown=60)

    logger.info('ingest_openmeteo_postgres: %d observations upsertées', len(df))
    return {
        'rows_upserted': len(df),
        'timestamp_str': silver_result.get('timestamp_str', ''),
    }


@shared_task(name='apps.meteo.tasks.fetch_openmeteo_live')
def fetch_openmeteo_live():
    """Orchestre Bronze → Silver → Postgres pour Open-Meteo."""
    chain(
        fetch_openmeteo_bronze.s(),
        fetch_openmeteo_silver.s(),
        ingest_openmeteo_postgres.s(),
    ).apply_async()
    return {'queued': True}


# ── NOAA GSOD ─────────────────────────────────────────────────────────────

NOAA_GSOD_URL = os.environ.get(
    'NOAA_GSOD_URL',
    'https://www.ncei.noaa.gov/data/global-summary-of-the-day/access',
)

# Par défaut : 2 dernières années seulement (était 2020 = 7 ans × 15 stations = 105 téléchargements)
_DEFAULT_GSOD_START = str(datetime.now().year - 1)
GSOD_START_YEAR = int(os.environ.get('GSOD_START_YEAR', _DEFAULT_GSOD_START))

# Colonnes brutes nécessaires pour _clean_gsod_df (réduit la taille des données passées)
_GSOD_ESSENTIAL_COLS = ['_station_id', 'DATE', 'TEMP', 'WDSP', 'SLP', 'PRCP', 'VISIB']


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
    """Normalise un DataFrame GSOD brut vers SI."""
    df = df.copy()

    df['timestamp'] = pd.to_datetime(df['DATE'], errors='coerce', utc=True)
    df = df.dropna(subset=['timestamp'])

    if 'TEMP' in df.columns:
        t = pd.to_numeric(df['TEMP'], errors='coerce')
        df['temperature'] = ((t - 32) * 5 / 9).where(t != 9999.9)

    if 'WDSP' in df.columns:
        w = pd.to_numeric(df['WDSP'], errors='coerce')
        df['wind_speed'] = (w * 0.51444).where(w != 999.9)

    if 'SLP' in df.columns:
        p = pd.to_numeric(df['SLP'], errors='coerce')
        df['pressure'] = p.where(p != 9999.9)

    if 'PRCP' in df.columns:
        prcp_str = df['PRCP'].astype(str).str.extract(r'([\d.]+)')[0]
        prcp = pd.to_numeric(prcp_str, errors='coerce')
        df['precipitation'] = (prcp * 25.4).where(prcp != 99.99)

    if 'VISIB' in df.columns:
        v = pd.to_numeric(df['VISIB'], errors='coerce')
        df['visibility'] = (v * 1.60934).where(v != 999.9)

    df['wind_direction'] = None

    cols = [
        '_station_id', 'timestamp', 'temperature', 'wind_speed',
        'wind_direction', 'pressure', 'precipitation', 'visibility',
    ]
    existing = [c for c in cols if c in df.columns]
    return df[existing].drop_duplicates(subset=['_station_id', 'timestamp'])


@shared_task(name='apps.meteo.tasks.fetch_noaa_gsod_bronze')
def fetch_noaa_gsod_bronze():
    """Bronze : télécharge les CSV NOAA GSOD et archive les données brutes."""
    timestamp_str = datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')
    current_year = datetime.now().year
    years = list(range(GSOD_START_YEAR, current_year + 1))

    jobs = [(s, y) for s in NOAA_GSOD_STATIONS for y in years]
    logger.info(
        'GSOD bronze: %d fichiers (%d stations × %d ans)',
        len(jobs), len(NOAA_GSOD_STATIONS), len(years),
    )

    results: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_fetch_gsod_station, s, y): (s, y) for s, y in jobs}
        for future in as_completed(futures):
            df = future.result()
            if df is not None:
                results.append(df)

    if not results:
        logger.warning('GSOD bronze: aucun fichier récupéré')
        return None

    df_raw = pd.concat(results, ignore_index=True)

    try:
        upload_to_lake(
            df=df_raw,
            bucket='urbanhub-raw',
            path=f'meteo/noaa_gsod/noaa_gsod_{timestamp_str}.parquet',
        )
    except Exception as exc:
        logger.error('Bronze GSOD upload: %s', exc)

    # Retourner seulement les colonnes nécessaires pour limiter la taille
    essential = [c for c in _GSOD_ESSENTIAL_COLS if c in df_raw.columns]
    logger.info('GSOD bronze: %d lignes collectées', len(df_raw))
    return {'rows': df_raw[essential].to_dict('records'), 'timestamp_str': timestamp_str}


@shared_task(bind=True, name='apps.meteo.tasks.fetch_noaa_gsod_silver', max_retries=2)
def fetch_noaa_gsod_silver(self, bronze_result):
    """Silver : nettoie les données GSOD et archive le Parquet traité."""
    if not bronze_result:
        logger.warning('fetch_noaa_gsod_silver: pas de données bronze')
        return {'rows_processed': 0}

    df_raw = pd.DataFrame(bronze_result['rows'])
    df_clean = _clean_gsod_df(df_raw)
    processed_path = f'meteo/noaa_gsod/noaa_gsod_{bronze_result["timestamp_str"]}.parquet'

    try:
        upload_to_lake(
            df=df_clean,
            bucket='urbanhub-processed',
            path=processed_path,
        )
    except Exception as exc:
        logger.error('Silver GSOD upload: %s', exc)

    logger.info('fetch_noaa_gsod_silver: %d observations journalières normalisées', len(df_clean))
    return {
        'processed_path': processed_path,
        'timestamp_str': bronze_result['timestamp_str'],
        'rows_processed': len(df_clean),
    }


@shared_task(bind=True, name='apps.meteo.tasks.ingest_noaa_gsod_postgres', max_retries=2)
def ingest_noaa_gsod_postgres(self, silver_result):
    """Postgres : upsert WeatherStation + WeatherObservation pour NOAA GSOD."""
    if not silver_result or not silver_result.get('processed_path'):
        logger.warning('ingest_noaa_gsod_postgres: pas de données silver')
        return {'rows_upserted': 0}

    try:
        df_clean = download_from_lake('urbanhub-processed', silver_result['processed_path'])
        upsert_gsod(df_clean)
    except Exception as exc:
        logger.error('PG upsert GSOD: %s', exc)
        raise self.retry(exc=exc, countdown=300)

    logger.info('ingest_noaa_gsod_postgres: %d observations journalières importées', len(df_clean))
    return {
        'rows_upserted': len(df_clean),
        'timestamp_str': silver_result.get('timestamp_str', ''),
    }


@shared_task(name='apps.meteo.tasks.fetch_noaa_gsod')
def fetch_noaa_gsod():
    """Orchestre Bronze → Silver → Postgres pour NOAA GSOD."""
    chain(
        fetch_noaa_gsod_bronze.s(),
        fetch_noaa_gsod_silver.s(),
        ingest_noaa_gsod_postgres.s(),
    ).apply_async()
    return {'queued': True}

