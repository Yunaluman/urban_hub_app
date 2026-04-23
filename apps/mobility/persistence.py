from __future__ import annotations

from datetime import datetime

import pandas as pd
from django.db import transaction

from apps.mobility.models import BikeNetwork, BikeSnapshot, BikeStation


def fetch_or_create_network(network_data: dict) -> BikeNetwork | None:
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
        return None


def build_station_objects(stations_data: list[dict], bike_network: BikeNetwork) -> list[BikeStation]:
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


def persist_network_data(detailed: dict, snapshot_timestamp: datetime) -> None:
    with transaction.atomic():
        bike_network = fetch_or_create_network(detailed)
        if bike_network is None:
            return

        stations_data = detailed.get('stations', [])
        if not stations_data:
            return

        BikeStation.objects.bulk_create(
            build_station_objects(stations_data, bike_network),
            update_conflicts=True,
            unique_fields=['network', 'station_id'],
            update_fields=['station_name', 'latitude', 'longitude'],
            batch_size=500,
        )

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

    with transaction.atomic():
        BikeSnapshot.objects.bulk_create(snapshots, batch_size=500)


def persist_citybikes_station_rows(df: pd.DataFrame, snapshot_timestamp: datetime) -> int:
    if df.empty:
        return 0

    networks = (
        df[['network_id', 'network_name', 'city']]
        .drop_duplicates(subset=['network_id'])
        .to_dict('records')
    )
    with transaction.atomic():
        BikeNetwork.objects.bulk_create(
            [
                BikeNetwork(
                    network_id=row['network_id'],
                    name=row.get('network_name', ''),
                    city=row.get('city', ''),
                    country='FR',
                    latitude=0.0,
                    longitude=0.0,
                )
                for row in networks
            ],
            update_conflicts=True,
            unique_fields=['network_id'],
            update_fields=['name', 'city'],
            batch_size=200,
        )

    network_map = {
        n.network_id: n
        for n in BikeNetwork.objects.filter(network_id__in=[row['network_id'] for row in networks])
    }
    station_rows = []
    for row in df.to_dict('records'):
        network = network_map.get(row['network_id'])
        if network is None:
            continue
        station_rows.append(BikeStation(
            network=network,
            station_id=row['station_id'],
            station_name=row.get('station_name', ''),
            latitude=row.get('latitude') or 0.0,
            longitude=row.get('longitude') or 0.0,
        ))

    with transaction.atomic():
        BikeStation.objects.bulk_create(
            station_rows,
            update_conflicts=True,
            unique_fields=['network', 'station_id'],
            update_fields=['station_name', 'latitude', 'longitude'],
            batch_size=500,
        )

    station_map = {
        (station.network.network_id, station.station_id): station
        for station in BikeStation.objects.filter(
            network__network_id__in=[row['network_id'] for row in networks]
        ).select_related('network')
    }
    snapshots = []
    for row in df.to_dict('records'):
        station = station_map.get((row['network_id'], row['station_id']))
        if station is None:
            continue
        snapshots.append(BikeSnapshot(
            station=station,
            timestamp=snapshot_timestamp,
            bikes_available=int(row.get('bikes_available') or 0),
            free_slots=int(row.get('free_slots') or 0),
            total_slots=int(row.get('total_slots') or 0),
        ))

    with transaction.atomic():
        BikeSnapshot.objects.bulk_create(snapshots, batch_size=500)

    return len(snapshots)
