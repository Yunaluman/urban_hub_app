import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests
from celery import shared_task
from django.db import transaction

from apps.mobility.models import BikeNetwork, BikeSnapshot, BikeStation
from apps.utils.lake import upload_to_lake

logger = logging.getLogger(__name__)

CITYBIKES_NETWORKS_URL = 'https://api.citybik.es/v2/networks'
CITYBIKES_NETWORK_URL = 'https://api.citybik.es/v2/networks/{network_id}'
REQUEST_TIMEOUT = 10
MAX_WORKERS = 5
_MIN_RETRY_SECONDS = 300


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


def _upsert_network(network_data: dict) -> BikeNetwork | None:
    """Create or update a BikeNetwork record from raw API data."""
    try:
        location = network_data.get('location', {})
        obj, _ = BikeNetwork.objects.update_or_create(
            network_id=network_data['id'],
            defaults={
                'name': network_data.get('name', ''),
                'city': location.get('city', ''),
                'country': location.get('country', ''),
                'latitude': location.get('latitude') or 0.0,
                'longitude': location.get('longitude') or 0.0,
            },
        )
        return obj
    except Exception:
        logger.exception('Error upserting network %s', network_data.get('id'))
        return None


def _build_station_objects(
    stations_data: list[dict],
    bike_network: BikeNetwork,
) -> list[BikeStation]:
    """Build unsaved BikeStation instances from raw station dicts."""
    objects = []
    for station_data in stations_data:
        if not station_data.get('id'):
            continue
        objects.append(BikeStation(
            network=bike_network,
            station_id=station_data['id'],
            station_name=station_data.get('name', ''),
            latitude=station_data.get('latitude') or 0.0,
            longitude=station_data.get('longitude') or 0.0,
        ))
    return objects


def _persist_network_data(
    detailed: dict,
    snapshot_timestamp: datetime,
) -> None:
    """Persist one network's stations and snapshots inside a single transaction.

    Stations are upserted with bulk_create(update_conflicts=True).
    Snapshots are appended with a single bulk_create — BikeSnapshot has no
    unique constraint, so every collection run intentionally adds a new row.
    If any step fails the entire network write is rolled back, preventing
    partial state.
    """
    with transaction.atomic():
        bike_network = _upsert_network(detailed)
        if bike_network is None:
            return

        stations_data = detailed.get('stations', [])
        if not stations_data:
            return

        # Upsert stations in bulk (2 queries instead of N)
        station_objects = _build_station_objects(stations_data, bike_network)
        BikeStation.objects.bulk_create(
            station_objects,
            update_conflicts=True,
            unique_fields=['network', 'station_id'],
            update_fields=['station_name', 'latitude', 'longitude'],
            batch_size=500,
        )

        # Reload to get PKs assigned by the DB
        station_map: dict[str, BikeStation] = {
            s.station_id: s
            for s in BikeStation.objects.filter(network=bike_network)
        }

        snapshots = []
        for station_data in stations_data:
            station = station_map.get(station_data.get('id'))
            if station is None:
                continue
            free_bikes = station_data.get('free_bikes', 0) or 0
            empty_slots = station_data.get('empty_slots', 0) or 0
            snapshots.append(BikeSnapshot(
                station=station,
                timestamp=snapshot_timestamp,
                bikes_available=free_bikes,
                free_slots=empty_slots,
                total_slots=free_bikes + empty_slots,
            ))

        BikeSnapshot.objects.bulk_create(snapshots, batch_size=500)


@shared_task(bind=True, name='apps.mobility.tasks.collect_citybikes', max_retries=3,
             soft_time_limit=55, time_limit=60)
def collect_citybikes(self):
    """
    Collect CityBikes data for all French bike networks.

    Runs every minute via Celery Beat. Fetches each network's stations
    concurrently using a ThreadPoolExecutor (max 5 workers), persists
    BikeNetwork / BikeStation / BikeSnapshot records, and saves a raw
    JSON snapshot to MinIO under raw/mobility/citybikes_{timestamp}.json.
    """
    from django.core.cache import cache
    lock_id = 'lock:collect_citybikes'
    if not cache.add(lock_id, True, timeout=55):
        logger.info('collect_citybikes already running — skipping overlapping run')
        return {'skipped': True}

    snapshot_timestamp = datetime.now(tz=timezone.utc)
    timestamp_str = snapshot_timestamp.strftime('%Y%m%d_%H%M%S')
    all_raw: list[dict] = []

    # 1. Fetch all networks and filter for France
    try:
        response = requests.get(CITYBIKES_NETWORKS_URL, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429:
            retry_after = response.headers.get('Retry-After')
            try:
                wait_s = int(retry_after) if retry_after else _MIN_RETRY_SECONDS
            except ValueError:
                wait_s = _MIN_RETRY_SECONDS
            wait_s = max(wait_s, _MIN_RETRY_SECONDS)
            logger.warning(
                'CityBikes rate limited (429). Retrying in %ss.',
                wait_s,
            )
            raise self.retry(countdown=wait_s)
        response.raise_for_status()
        all_networks = response.json().get('networks', [])
    except requests.RequestException as exc:
        logger.error('Failed to fetch CityBikes network list: %s', exc)
        raise self.retry(exc=exc, countdown=30)

    french_networks = [
        n for n in all_networks
        if n.get('location', {}).get('country', '').upper() == 'FR'
    ]
    logger.info('Found %d French bike networks', len(french_networks))

    # 2. Fetch station details in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_network = {
            executor.submit(_fetch_network_stations, n): n
            for n in french_networks
        }

        for future in as_completed(future_to_network):
            detailed = future.result()
            if detailed is None:
                continue

            all_raw.append(detailed)
            _persist_network_data(detailed, snapshot_timestamp)

    # ── Bronze : snapshot brut ──────────────────────────────────────────────
    if all_raw:
        try:
            df_raw = pd.DataFrame(all_raw)
            upload_to_lake(
                df=df_raw,
                bucket='urbanhub-raw',
                path=f'mobility/citybikes/citybikes_{timestamp_str}.parquet',
            )
            logger.info('Uploaded raw CityBikes snapshot to MinIO (Bronze)')
        except Exception as exc:
            logger.error('Failed to upload CityBikes snapshot to MinIO: %s', exc)

    # ── Silver : stations normalisées avec disponibilité ────────────────────
    if all_raw:
        try:
            silver_rows = []
            for network in all_raw:
                net_id = network.get('id', '')
                net_name = network.get('name', '')
                location = network.get('location', {})
                city = location.get('city', '')
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
                        'utilization_pct': (
                            round(free_bikes / total * 100, 2) if total > 0 else 0.0
                        ),
                    })
            if silver_rows:
                df_silver = pd.DataFrame(silver_rows)
                upload_to_lake(
                    df=df_silver,
                    bucket='urbanhub-processed',
                    path=f'mobility/citybikes/citybikes_{timestamp_str}.parquet',
                )
                logger.info('Uploaded cleaned CityBikes snapshot to MinIO (Silver)')  # noqa: E501
        except Exception as exc:
            logger.error('Failed to upload Silver CityBikes to MinIO: %s', exc)

    cache.delete(lock_id)
    logger.info(
        'collect_citybikes complete: %d networks processed at %s',
        len(all_raw),
        snapshot_timestamp.isoformat(),
    )
    return {'networks_processed': len(all_raw), 'timestamp': timestamp_str}
