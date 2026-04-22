"""
Flux Batch – Météo NOAA pour Paris.

Pipeline :
  1. Téléchargement parallèle NOAA (2020 → année courante)
  2. Stockage brut  → MinIO  urbanhub-raw/meteo/noaa_hourly/bronze/paris/{year}/{station_id}.parquet
  3. Nettoyage
  4. Stockage traité → MinIO  urbanhub-processed/meteo/noaa_hourly/silver/paris/{year}/{station_id}.parquet
  5. Upsert PostgreSQL

Usage:
    python manage.py fetch_paris_meteo
    python manage.py fetch_paris_meteo --start-year 2022
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from django.core.management.base import BaseCommand

from apps.meteo.tasks import clean_noaa_df, fetch_station_year, _upsert_observations
from apps.utils.lake import upload_to_lake

logger = logging.getLogger(__name__)

START_YEAR = 2020
MAX_WORKERS = 8

PARIS_STATIONS = [
    ("07150099999", "Paris-Orly"),
    ("07156099999", "Paris-Le Bourget"),
    ("07157099999", "Paris-CDG"),
    ("07149099999", "Paris-Montsouris"),
]


def _stations_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"station_id": sid, "STATION NAME": name, "LAT": None, "LON": None, "ELEV(M)": None}
        for sid, name in PARIS_STATIONS
    ])


class Command(BaseCommand):
    help = "Batch NOAA météo Paris : téléchargement parallèle, MinIO, PostgreSQL"

    def add_arguments(self, parser):
        parser.add_argument("--start-year", type=int, default=START_YEAR)

    def handle(self, *args, **options):
        start_year = options["start_year"]
        end_year = datetime.now().year
        years = list(range(start_year, end_year + 1))
        jobs = [(sid, name, yr) for sid, name in PARIS_STATIONS for yr in years]

        self.stdout.write(
            f"Lancement de {len(jobs)} téléchargements ({MAX_WORKERS} workers) "
            f"— Paris {start_year}→{end_year}..."
        )

        raw_frames: list[pd.DataFrame] = []
        errors = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(fetch_station_year, sid, yr): (sid, name, yr)
                for sid, name, yr in jobs
            }
            for future in as_completed(futures):
                sid, name, yr = futures[future]
                df = future.result()
                if df is None:
                    errors += 1
                    continue

                # Stockage brut dans MinIO
                upload_to_lake(
                    df,
                    bucket="urbanhub-raw",
                    path=f"meteo/noaa_hourly/bronze/paris/{yr}/{sid}.parquet",
                )
                raw_frames.append(df)
                self.stdout.write(f"  ✓ {name} {yr} — {len(df)} lignes brutes")

        if not raw_frames:
            self.stdout.write(self.style.ERROR("Aucune donnée récupérée."))
            return

        self.stdout.write(f"\n{errors} fichier(s) non disponible(s) sur NOAA (normal pour années incomplètes).")

        # Nettoyage
        raw_df = pd.concat(raw_frames, ignore_index=True)
        clean_df = clean_noaa_df(raw_df)

        # Stockage traité dans MinIO (un fichier global Paris)
        upload_to_lake(
            clean_df,
            bucket="urbanhub-processed",
            path=f"meteo/noaa_hourly/silver/paris/paris_{start_year}_{end_year}_clean.parquet",
        )
        self.stdout.write(f"Données traitées sauvegardées dans MinIO.")

        # Upsert PostgreSQL
        _upsert_observations(clean_df, _stations_df())
        self.stdout.write(self.style.SUCCESS(
            f"\nTerminé : {len(clean_df)} observations importées en base."
        ))
