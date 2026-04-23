import os
import sys

if sys.platform == 'win32':
    os.environ.setdefault('PGCLIENTENCODING', 'UTF8')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbanhub.settings')

import django  # noqa: E402
from celery import Celery  # noqa: E402
from celery.schedules import crontab  # noqa: E402

from apps.utils.tasking import register_task_telemetry  # noqa: E402


django.setup()

app = Celery('urbanhub')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
register_task_telemetry()

# ─── Schedules ────────────────────────────────────────────────────
app.conf.beat_schedule = {
    # ── Météo temps réel : Open-Meteo — toutes les heures (:00)
    # 10 grandes villes FR, données des 48 dernières heures
    'fetch-openmeteo-live': {
        'task': 'apps.meteo.tasks.fetch_openmeteo_live',
        'schedule': crontab(minute=0),
    },
    # ── Météo historique : NOAA GSOD — tous les jours à 03:00
    # 15 stations de référence FR, résumés journaliers depuis 2020
    'fetch-noaa-gsod': {
        'task': 'apps.meteo.tasks.fetch_noaa_gsod',
        'schedule': crontab(hour=3, minute=0),
    },
    # ── Flux Streaming : CityBikes — toutes les minutes
    'collect-citybikes': {
        'task': 'apps.mobility.tasks.collect_citybikes',
        # CityBikes rate-limits aggressively; 5 min avoids 429 storms.
        'schedule': 300.0,
    },
    # ── Flux IoT : OpenAQ — toutes les 5 minutes
    'collect-openaq': {
        'task': 'apps.pollution.tasks.collect_openaq',
        'schedule': crontab(minute='*/5'),
    },
    # ── Batch : EEA verified/UTD pollution (historisé) — quotidien
    'fetch-eea-historical': {
        'task': 'apps.pollution.tasks.fetch_eea_historical',
        'schedule': crontab(hour=4, minute=15),
    },
    # ── Analyse croisée — toutes les heures (:30)
    'run-cross-analysis': {
        'task': 'apps.analysis.tasks.run_cross_analysis',
        'schedule': crontab(minute=30),
    },
    # ── Gold serving (MinIO Parquet + Postgres) — après les ingest
    'publish-gold-mobility': {
        'task': 'apps.serving.tasks.publish_gold_mobility',
        'schedule': 600.0,
    },
    'publish-gold-air': {
        'task': 'apps.serving.tasks.publish_gold_air',
        'schedule': crontab(minute=20),
    },
    'publish-gold-climate': {
        'task': 'apps.serving.tasks.publish_gold_climate',
        'schedule': crontab(minute=25),
    },
    'publish-gold-dashboard-summaries': {
        'task': 'apps.serving.tasks.publish_gold_dashboard_summaries',
        'schedule': 600.0,
    },
}
