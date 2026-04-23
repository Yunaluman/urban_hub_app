import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests
from celery import chain, shared_task

from apps.utils.lake import download_from_lake, upload_to_lake
from apps.mobility.persistence import persist_citybikes_station_rows

logger = logging.getLogger(__name__)

CITYBIKES_NETWORKS_URL = 'https://api.citybik.es/v2/networks'
CITYBIKES_NETWORK_URL = 'https://api.citybik.es/v2/networks/{network_id}'
REQUEST_TIMEOUT = 10
MAX_WORKERS = 3  # était 5 — réduit pour limiter la charge système
_MIN_RETRY_SECONDS = 300


# ── Helpers (réutilisés par le management command) ──────────────────────────

def _fetch_network_stations(network: dict) -> dict | None:
    """Fetch station detail for a single network. Returns None on error."""
    network_id = network.get('id')
    try:
        response = requests.get(
            CITYBIKES_NETWORK_URL.format(network_id=network_id),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get('network', {})
    except requests.RequestException as exc:
        logger.warning('Failed to fetch stations for network %s: %s', network_id, exc)
        return None


# ── Tâches Celery ────────────────────────────────────────────────────────────

@shared_task(name='apps.mobility.tasks.collect_citybikes_bronze')
def collect_citybikes_bronze() -> dict | None:
    """Bronze : collecte les données CityBikes FR et archive la snapshot brute."""
    snapshot_timestamp = datetime.now(tz=timezone.utc)
    timestamp_str = snapshot_timestamp.strftime('%Y%m%d_%H%M%S')

    try:
        response = requests.get(CITYBIKES_NETWORKS_URL, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429:
            retry_after = response.headers.get('Retry-After')
            try:
                wait_s = int(retry_after) if retry_after else _MIN_RETRY_SECONDS
            except ValueError:
                wait_s = _MIN_RETRY_SECONDS
            wait_s = max(wait_s, _MIN_RETRY_SECONDS)
            logger.warning('CityBikes rate limited (429). Wait %ss.', wait_s)
            return {'rate_limited': True, 'wait_s': wait_s}
        response.raise_for_status()
        all_networks = response.json().get('networks', [])
    except requests.RequestException as exc:
        logger.error('Failed to fetch CityBikes network list: %s', exc)
        return None

    french_networks = [
        n for n in all_networks
        if n.get('location', {}).get('country', '').upper() == 'FR'
    ]
    logger.info('Found %d French bike networks', len(french_networks))

    all_raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_network_stations, n): n for n in french_networks}
        for future in as_completed(futures):
            detailed = future.result()
            if detailed:
                all_raw.append(detailed)

    if all_raw:
        try:
            upload_to_lake(
                df=pd.DataFrame(all_raw),
                bucket='urbanhub-raw',
                path=f'mobility/citybikes/citybikes_{timestamp_str}.parquet',
            )
        except Exception as exc:
            logger.error('Bronze CityBikes upload: %s', exc)

    logger.info('collect_citybikes_bronze: %d réseaux collectés', len(all_raw))
    return {
        'all_raw': all_raw,
        'timestamp_str': timestamp_str,
        'snapshot_ts': snapshot_timestamp.isoformat(),
    }


@shared_task(name='apps.mobility.tasks.collect_citybikes_silver')
def collect_citybikes_silver(bronze_result: dict) -> dict:
    """Silver : normalise les données CityBikes et archive le Parquet traité."""
    if not bronze_result or not bronze_result.get('all_raw'):
        logger.warning('collect_citybikes_silver: pas de données bronze')
        return {'networks_processed': 0, 'rows_processed': 0}

    all_raw = bronze_result['all_raw']
    timestamp_str = bronze_result['timestamp_str']
    snapshot_timestamp = datetime.fromisoformat(bronze_result['snapshot_ts'])

    # Silver Parquet
    silver_rows = []
    processed_path = f'mobility/citybikes/citybikes_{timestamp_str}.parquet'
    try:
        for network in all_raw:
            net_id = network.get('id', '')
            net_name = network.get('name', '')
            city = network.get('location', {}).get('city', '')
            for station in network.get('stations', []):
                free_bikes = station.get('free_bikes') or 0
                empty_slots = station.get('empty_slots') or 0
                total = free_bikes + empty_slots
                silver_rows.append({
                    'timestamp': snapshot_timestamp,
                    'network_id': net_id,
                    'network_name': net_name,
                    'city': city,
                    'station_id': station.get('id', ''),
                    'station_name': station.get('name', ''),
                    'latitude': station.get('latitude') or 0.0,
                    'longitude': station.get('longitude') or 0.0,
                    'bikes_available': free_bikes,
                    'free_slots': empty_slots,
                    'total_slots': total,
                    'utilization_pct': round(free_bikes / total * 100, 2) if total > 0 else 0.0,
                })
        if silver_rows:
            upload_to_lake(
                df=pd.DataFrame(silver_rows),
                bucket='urbanhub-processed',
                path=processed_path,
            )
    except Exception as exc:
        logger.error('Silver CityBikes upload: %s', exc)

    logger.info(
        'collect_citybikes_silver: %d réseaux normalisés à %s',
        len(all_raw),
        snapshot_timestamp.isoformat(),
    )
    return {
        'networks_processed': len(all_raw),
        'rows_processed': len(silver_rows),
        'processed_path': processed_path if silver_rows else '',
        'timestamp_str': timestamp_str,
        'snapshot_ts': snapshot_timestamp.isoformat(),
    }


@shared_task(bind=True, name='apps.mobility.tasks.ingest_citybikes_postgres', max_retries=3,
             soft_time_limit=300, time_limit=320)
def ingest_citybikes_postgres(self, silver_result: dict) -> dict:
    """Postgres : upsert réseaux, stations et snapshots CityBikes."""
    if not silver_result or not silver_result.get('processed_path'):
        logger.warning('ingest_citybikes_postgres: pas de données silver')
        return {'rows_upserted': 0}

    try:
        df = download_from_lake('urbanhub-processed', silver_result['processed_path'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        snapshot_timestamp = datetime.fromisoformat(silver_result['snapshot_ts'])
        rows = persist_citybikes_station_rows(df, snapshot_timestamp)
    except Exception as exc:
        logger.error('PG ingest CityBikes: %s', exc)
        raise self.retry(exc=exc, countdown=60)

    logger.info('ingest_citybikes_postgres: %d snapshots persistés', rows)
    return {
        'rows_upserted': rows,
        'timestamp_str': silver_result.get('timestamp_str', ''),
    }


@shared_task(name='apps.mobility.tasks.release_citybikes_lock')
def release_citybikes_lock(_result: dict | None, lock_id: str) -> dict:
    from django.core.cache import cache

    cache.delete(lock_id)
    return {'released_lock': True}


@shared_task(bind=True, name='apps.mobility.tasks.collect_citybikes', max_retries=3,
             soft_time_limit=300, time_limit=320)
def collect_citybikes(self):
    """Orchestre Bronze → Silver → Postgres pour CityBikes (avec verrou anti-overlap)."""
    from django.core.cache import cache
    lock_id = 'lock:collect_citybikes'
    if not cache.add(lock_id, True, timeout=300):
        logger.info('collect_citybikes already running — skipping overlapping run')
        return {'skipped': True}

    chain(
        collect_citybikes_bronze.s(),
        collect_citybikes_silver.s(),
        ingest_citybikes_postgres.s(),
        release_citybikes_lock.s(lock_id),
    ).apply_async()
    return {'queued': True}
