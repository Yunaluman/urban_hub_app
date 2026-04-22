"""
Management command : ingestion batch EEA (air quality e-Reporting) pour villes FR.

Usage:
    python manage.py fetch_eea_historical --cities Paris,Lyon --years 5 --max-files 50
"""

from django.core.management.base import BaseCommand

from apps.pollution.tasks import fetch_eea_historical


class Command(BaseCommand):
    help = "Ingestion batch EEA Air Quality Download Service (historisé)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--cities',
            type=str,
            default='Paris,Lyon',
            help='Comma-separated list of cities (default: Paris,Lyon)',
        )
        parser.add_argument(
            '--years',
            type=int,
            default=5,
            help='Number of years to backfill (default: 5)',
        )
        parser.add_argument(
            '--max-files',
            type=int,
            default=50,
            help='Max number of parquet files to ingest when falling back to URLs (default: 50)',
        )

    def handle(self, *args, **options):
        cities = [c.strip() for c in (options['cities'] or '').split(',') if c.strip()]
        # Convenience: "--cities France" means no city filter (whole country)
        if len(cities) == 1 and cities[0].lower() in {'france', 'fr', '*', 'all'}:
            cities = []
        years = int(options['years'] or 5)
        max_files = int(options['max_files'] or 50)
        self.stdout.write(f'EEA backfill: cities={cities} years={years}')
        res = fetch_eea_historical.run(cities=cities, years=years, max_files=max_files)
        self.stdout.write(self.style.SUCCESS(f'Done: {res}'))

