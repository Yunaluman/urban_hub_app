"""
Management command : collecte les mesures de pollution OpenAQ pour la France.
Appelle directement la logique de la tâche Celery sans passer par le broker.

Usage:
    python manage.py fetch_openaq
"""
import time
from datetime import datetime

from django.core.management.base import BaseCommand

from apps.pollution.models import PollutionMeasurement, PollutionSensor
from apps.pollution.tasks import (
    _get_france_country_id,
    _get_locations,
    _fetch_latest,
    POLLUTANT_MAP,
)


class Command(BaseCommand):
    help = "Collecte les mesures de pollution OpenAQ pour la France"

    def handle(self, *args, **options):
        self.stdout.write("Récupération de l'ID pays France depuis OpenAQ...")

        country_id = _get_france_country_id()
        if not country_id:
            self.stdout.write(self.style.ERROR("Impossible de récupérer l'ID pays France depuis OpenAQ."))
            return

        self.stdout.write(f"  ID pays France : {country_id}")
        self.stdout.write("Récupération des stations OpenAQ France...")

        locations = _get_locations(country_id)
        self.stdout.write(f"  {len(locations)} stations récupérées.")

        sensors_map: dict[int, str] = {}
        for loc in locations:
            for sensor in loc.get('sensors', []):
                p = (sensor.get('parameter') or {}).get('name', '')
                if p and sensor.get('id'):
                    sensors_map[sensor['id']] = p.lower()

        all_records = []
        for loc in locations:
            for rec in _fetch_latest(loc['id']):
                rec['_location_id'] = loc['id']
                rec['_loc_data'] = loc
                all_records.append(rec)
            time.sleep(0.1)

        self.stdout.write(f"  {len(all_records)} mesures récupérées.")

        processed = 0
        for rec in all_records:
            param = sensors_map.get(rec.get('sensorsId'), '')
            pollutant_key = POLLUTANT_MAP.get(param)
            if not pollutant_key:
                continue

            loc = rec['_loc_data']
            loc_id = rec['_location_id']
            coords = rec.get('coordinates') or loc.get('coordinates') or {}
            city = loc.get('locality') or loc.get('city') or loc.get('name') or ''
            sensor_id = f"{loc_id}-{param}"

            sensor, _ = PollutionSensor.objects.update_or_create(
                sensor_id=sensor_id,
                defaults={
                    'city': city,
                    'latitude': coords.get('latitude') or 0.0,
                    'longitude': coords.get('longitude') or 0.0,
                    'source': 'openaq',
                },
            )

            dt_str = (rec.get('datetime') or {}).get('utc') or \
                     (rec.get('period') or {}).get('datetimeTo', {}).get('utc')
            if not dt_str:
                continue
            try:
                ts = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                value = float(rec.get('value') or 0)
                unit = (rec.get('parameter') or {}).get('units', 'µg/m³')
            except (TypeError, ValueError):
                continue

            PollutionMeasurement.objects.get_or_create(
                sensor=sensor,
                timestamp=ts,
                pollutant=pollutant_key,
                defaults={'value': value, 'unit': unit},
            )
            processed += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nTerminé : {processed} mesures de pollution importées."
        ))
