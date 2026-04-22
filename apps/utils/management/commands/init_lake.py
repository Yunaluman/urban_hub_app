"""
Management command : initialise les buckets MinIO du Data Lake.

Crée les trois buckets (urbanhub-raw, urbanhub-processed, urbanhub-analytics)
s'ils n'existent pas encore, puis dépose un fichier Parquet de test dans chacun
pour vérifier que la chaîne d'écriture fonctionne.

Usage:
    python manage.py init_lake
    python manage.py init_lake --skip-test-data   # buckets uniquement, sans données de test
"""
from datetime import datetime, timezone

import pandas as pd
from django.core.management.base import BaseCommand

from apps.utils.lake import BUCKETS, _ensure_bucket, _get_client, upload_to_lake


class Command(BaseCommand):
    help = "Initialise les buckets MinIO et vérifie l'upload Parquet"

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-test-data',
            action='store_true',
            default=False,
            help="Crée uniquement les buckets sans uploader de données de test",
        )

    def handle(self, *args, **options):
        client = _get_client()

        self.stdout.write("Initialisation du Data Lake MinIO…\n")

        for bucket in BUCKETS:
            _ensure_bucket(client, bucket)
            self.stdout.write(f"  bucket prêt : {bucket}")

        if options['skip_test_data']:
            self.stdout.write(self.style.SUCCESS("\nBuckets créés. (données de test ignorées)"))
            return

        self.stdout.write("\nUpload des données de test…")

        ts = datetime.now(tz=timezone.utc).isoformat()

        test_datasets = [
            (
                'urbanhub-raw',
                'test/init_check.parquet',
                pd.DataFrame({'bucket': ['urbanhub-raw'], 'initialized_at': [ts], 'status': ['ok']}),
            ),
            (
                'urbanhub-processed',
                'test/init_check.parquet',
                pd.DataFrame({'bucket': ['urbanhub-processed'], 'initialized_at': [ts], 'status': ['ok']}),
            ),
            (
                'urbanhub-analytics',
                'test/init_check.parquet',
                pd.DataFrame({'bucket': ['urbanhub-analytics'], 'initialized_at': [ts], 'status': ['ok']}),
            ),
        ]

        success = 0
        for bucket, path, df in test_datasets:
            try:
                upload_to_lake(df=df, bucket=bucket, path=path)
                self.stdout.write(f"  {bucket}/{path}  OK")
                success += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {bucket}/{path}  ERREUR : {exc}"))

        if success == len(test_datasets):
            self.stdout.write(self.style.SUCCESS(
                f"\nData Lake prêt — {len(BUCKETS)} buckets, {success} fichiers de test uploadés."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"\n{success}/{len(test_datasets)} uploads réussis. Vérifier la connexion MinIO."
            ))
