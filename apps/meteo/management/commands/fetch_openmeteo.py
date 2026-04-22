"""
Management command : fetch real-time hourly weather for Paris from Open-Meteo.
No API key required.

Usage:
    python manage.py fetch_openmeteo           # last 24 h
    python manage.py fetch_openmeteo --days 7  # last 7 days
"""
from datetime import datetime, timedelta, timezone

import requests
from django.core.management.base import BaseCommand

from apps.meteo.models import WeatherObservation, WeatherStation

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

PARIS_STATIONS = [
    {
        "station_id": "OPENMETEO-PARIS",
        "name": "Paris (Open-Meteo)",
        "latitude": 48.8566,
        "longitude": 2.3522,
        "city": "Paris",
    },
]


class Command(BaseCommand):
    help = "Fetch real-time hourly weather for Paris from Open-Meteo (no API key)"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=1, help="Number of past days to fetch (default: 1)")

    def handle(self, *args, **options):
        days = options["days"]
        self.stdout.write(f"Fetching Open-Meteo data for Paris — last {days} day(s)...")

        for station_info in PARIS_STATIONS:
            station, _ = WeatherStation.objects.update_or_create(
                station_id=station_info["station_id"],
                defaults={
                    "name": station_info["name"],
                    "latitude": station_info["latitude"],
                    "longitude": station_info["longitude"],
                    "city": station_info["city"],
                },
            )

            params = {
                "latitude": station_info["latitude"],
                "longitude": station_info["longitude"],
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,surface_pressure,precipitation,visibility",
                "past_days": days,
                "forecast_days": 0,
                "timezone": "Europe/Paris",
            }

            resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()["hourly"]

            timestamps = data["time"]
            observations = []
            for i, ts_str in enumerate(timestamps):
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)

                def _v(key):
                    val = data.get(key, [None])[i]
                    return None if val is None else val

                observations.append(WeatherObservation(
                    station=station,
                    timestamp=ts,
                    temperature=_v("temperature_2m"),
                    wind_speed=_v("wind_speed_10m"),
                    wind_direction=int(_v("wind_direction_10m")) if _v("wind_direction_10m") is not None else None,
                    pressure=_v("surface_pressure"),
                    precipitation=_v("precipitation"),
                    visibility=(_v("visibility") / 1000) if _v("visibility") is not None else None,
                ))

            WeatherObservation.objects.bulk_create(
                observations,
                update_conflicts=True,
                unique_fields=["station", "timestamp"],
                update_fields=["temperature", "wind_speed", "wind_direction",
                               "pressure", "precipitation", "visibility"],
                batch_size=500,
            )
            self.stdout.write(self.style.SUCCESS(
                f"  ✓ {len(observations)} observations importées pour {station_info['name']}"
            ))
