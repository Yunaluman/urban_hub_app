from __future__ import annotations

import pandas as pd
from django.db import transaction

from .models import WeatherObservation, WeatherStation


FR_CITIES = [
    {'station_id': 'OPENMETEO-PARIS', 'name': 'Paris', 'city': 'Paris', 'lat': 48.8566, 'lon': 2.3522},
    {'station_id': 'OPENMETEO-LYON', 'name': 'Lyon', 'city': 'Lyon', 'lat': 45.7485, 'lon': 4.8467},
    {'station_id': 'OPENMETEO-MARSEILLE', 'name': 'Marseille', 'city': 'Marseille', 'lat': 43.2965, 'lon': 5.3698},
    {'station_id': 'OPENMETEO-BORDEAUX', 'name': 'Bordeaux', 'city': 'Bordeaux', 'lat': 44.8378, 'lon': -0.5792},
    {'station_id': 'OPENMETEO-TOULOUSE', 'name': 'Toulouse', 'city': 'Toulouse', 'lat': 43.6047, 'lon': 1.4442},
    {'station_id': 'OPENMETEO-STRASBOURG', 'name': 'Strasbourg', 'city': 'Strasbourg', 'lat': 48.5734, 'lon': 7.7521},
    {'station_id': 'OPENMETEO-LILLE', 'name': 'Lille', 'city': 'Lille', 'lat': 50.6292, 'lon': 3.0573},
    {'station_id': 'OPENMETEO-NICE', 'name': 'Nice', 'city': 'Nice', 'lat': 43.7102, 'lon': 7.2620},
    {'station_id': 'OPENMETEO-NANTES', 'name': 'Nantes', 'city': 'Nantes', 'lat': 47.2184, 'lon': -1.5536},
    {'station_id': 'OPENMETEO-MONTPELLIER', 'name': 'Montpellier', 'city': 'Montpellier', 'lat': 43.6119, 'lon': 3.8772},
]

NOAA_GSOD_STATIONS = [
    {'station_id': '07156-99999', 'name': 'Paris-CDG', 'city': 'Paris', 'lat': 49.013, 'lon': 2.551},
    {'station_id': '07149-99999', 'name': 'Paris-Orly', 'city': 'Paris', 'lat': 48.716, 'lon': 2.379},
    {'station_id': '07481-99999', 'name': 'Lyon-Saint-Exupery', 'city': 'Lyon', 'lat': 45.727, 'lon': 5.090},
    {'station_id': '07650-99999', 'name': 'Marseille-Provence', 'city': 'Marseille', 'lat': 43.436, 'lon': 5.214},
    {'station_id': '07510-99999', 'name': 'Bordeaux-Merignac', 'city': 'Bordeaux', 'lat': 44.828, 'lon': -0.715},
    {'station_id': '07630-99999', 'name': 'Toulouse-Blagnac', 'city': 'Toulouse', 'lat': 43.629, 'lon': 1.367},
    {'station_id': '07070-99999', 'name': 'Strasbourg-Entzheim', 'city': 'Strasbourg', 'lat': 48.538, 'lon': 7.628},
    {'station_id': '07015-99999', 'name': 'Lille-Lesquin', 'city': 'Lille', 'lat': 50.563, 'lon': 3.089},
    {'station_id': '07690-99999', 'name': 'Nice-Cote-d-Azur', 'city': 'Nice', 'lat': 43.658, 'lon': 7.208},
    {'station_id': '07222-99999', 'name': 'Nantes-Atlantique', 'city': 'Nantes', 'lat': 47.158, 'lon': -1.609},
    {'station_id': '07643-99999', 'name': 'Montpellier-Frej', 'city': 'Montpellier', 'lat': 43.576, 'lon': 3.963},
    {'station_id': '07110-99999', 'name': 'Brest-Guipavas', 'city': 'Brest', 'lat': 48.447, 'lon': -4.418},
    {'station_id': '07460-99999', 'name': 'Clermont-Ferrand', 'city': 'Clermont-Ferrand', 'lat': 45.786, 'lon': 3.165},
    {'station_id': '07130-99999', 'name': 'Rennes-Saint-Jacques', 'city': 'Rennes', 'lat': 48.069, 'lon': -1.730},
    {'station_id': '07578-99999', 'name': 'Biarritz-Anglet', 'city': 'Biarritz', 'lat': 43.468, 'lon': -1.523},
]


def upsert_openmeteo(df: pd.DataFrame) -> None:
    city_map = {c['station_id']: c for c in FR_CITIES}

    with transaction.atomic():
        WeatherStation.objects.bulk_create(
            [
                WeatherStation(
                    station_id=c['station_id'],
                    name=c['name'],
                    city=c['city'],
                    latitude=c['lat'],
                    longitude=c['lon'],
                    elevation=None,
                )
                for c in FR_CITIES
            ],
            update_conflicts=True,
            unique_fields=['station_id'],
            update_fields=['name', 'city', 'latitude', 'longitude'],
            batch_size=50,
        )

    station_map = {
        s.station_id: s
        for s in WeatherStation.objects.filter(station_id__in=list(city_map.keys()))
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

    with transaction.atomic():
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


def upsert_gsod(df: pd.DataFrame) -> None:
    station_lookup = {s['station_id']: s for s in NOAA_GSOD_STATIONS}

    with transaction.atomic():
        WeatherStation.objects.bulk_create(
            [
                WeatherStation(
                    station_id=s['station_id'],
                    name=s['name'],
                    latitude=s['lat'],
                    longitude=s['lon'],
                    elevation=None,
                )
                for s in NOAA_GSOD_STATIONS
            ],
            update_conflicts=True,
            unique_fields=['station_id'],
            update_fields=['name', 'latitude', 'longitude'],
            batch_size=50,
        )

    station_map = {
        s.station_id: s
        for s in WeatherStation.objects.filter(station_id__in=list(station_lookup.keys()))
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

    with transaction.atomic():
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


def safe_float(val):
    return _safe_float(val)


def safe_int(val):
    return _safe_int(val)


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
