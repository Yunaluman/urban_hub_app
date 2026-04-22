"""
Management command : collecte les données CityBikes pour tous les réseaux français.
Appelle directement la logique de la tâche Celery sans passer par le broker.

Usage:
    python manage.py fetch_citybikes
"""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.core.management.base import BaseCommand

from apps.mobility.tasks import (
    _fetch_network_stations,
    _persist_network_data,
    CITYBIKES_NETWORKS_URL,
    MAX_WORKERS,
)

REQUEST_TIMEOUT = 10


class Command(BaseCommand):
    help = "Collecte les données CityBikes (vélos en libre-service) pour tous les réseaux français"

    def handle(self, *args, **options):
        self.stdout.write("Récupération de la liste des réseaux CityBikes...")

        try:
            response = requests.get(CITYBIKES_NETWORKS_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            all_networks = response.json().get('networks', [])
        except requests.RequestException as exc:
            self.stdout.write(self.style.ERROR(f"Erreur réseau CityBikes : {exc}"))
            return

        french_networks = [
            n for n in all_networks
            if n.get('location', {}).get('country', '').upper() == 'FR'
        ]
        self.stdout.write(f"  {len(french_networks)} réseaux français trouvés.")

        snapshot_timestamp = datetime.now(tz=timezone.utc)
        processed = 0
        errors = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_network = {
                executor.submit(_fetch_network_stations, n): n
                for n in french_networks
            }
            for future in as_completed(future_to_network):
                detailed = future.result()
                if detailed is None:
                    errors += 1
                    continue
                try:
                    _persist_network_data(detailed, snapshot_timestamp)
                    processed += 1
                    self.stdout.write(f"  ✓ {detailed.get('name', '?')} — {len(detailed.get('stations', []))} stations")
                except Exception as exc:
                    errors += 1
                    self.stdout.write(self.style.WARNING(f"  ✗ Erreur persistance {detailed.get('name', '?')}: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"\nTerminé : {processed} réseaux importés, {errors} erreurs."
        ))
