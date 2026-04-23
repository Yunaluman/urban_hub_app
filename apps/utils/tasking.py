from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from celery.signals import task_postrun, task_prerun
from django.utils import timezone

logger = logging.getLogger(__name__)

TASK_QUEUE_DEFAULT = 'default'
TASK_QUEUE_BRONZE = 'bronze'
TASK_QUEUE_SILVER = 'silver'
TASK_QUEUE_INGEST_POSTGRES = 'ingestion_postgres'
TASK_QUEUE_GOLD = 'gold'
TASK_QUEUE_ANALYSIS = 'analysis'


@dataclass(frozen=True)
class TaskRuntimeProfile:
    stage: str
    queue: str
    resource_tier: str
    workload_score: int


DEFAULT_TASK_PROFILE = TaskRuntimeProfile(
    stage='orchestration',
    queue=TASK_QUEUE_DEFAULT,
    resource_tier='light',
    workload_score=1,
)

TASK_PROFILES: dict[str, TaskRuntimeProfile] = {
    'apps.meteo.tasks.fetch_openmeteo_bronze': TaskRuntimeProfile('bronze', TASK_QUEUE_BRONZE, 'light', 1),
    'apps.meteo.tasks.fetch_openmeteo_silver': TaskRuntimeProfile('silver', TASK_QUEUE_SILVER, 'medium', 2),
    'apps.meteo.tasks.ingest_openmeteo_postgres': TaskRuntimeProfile(
        'ingestion_postgres', TASK_QUEUE_INGEST_POSTGRES, 'medium', 3
    ),
    'apps.meteo.tasks.fetch_openmeteo_live': DEFAULT_TASK_PROFILE,
    'apps.meteo.tasks.fetch_noaa_gsod_bronze': TaskRuntimeProfile('bronze', TASK_QUEUE_BRONZE, 'medium', 2),
    'apps.meteo.tasks.fetch_noaa_gsod_silver': TaskRuntimeProfile('silver', TASK_QUEUE_SILVER, 'heavy', 4),
    'apps.meteo.tasks.ingest_noaa_gsod_postgres': TaskRuntimeProfile(
        'ingestion_postgres', TASK_QUEUE_INGEST_POSTGRES, 'heavy', 4
    ),
    'apps.meteo.tasks.fetch_noaa_gsod': DEFAULT_TASK_PROFILE,
    'apps.mobility.tasks.collect_citybikes_bronze': TaskRuntimeProfile('bronze', TASK_QUEUE_BRONZE, 'light', 1),
    'apps.mobility.tasks.collect_citybikes_silver': TaskRuntimeProfile('silver', TASK_QUEUE_SILVER, 'medium', 2),
    'apps.mobility.tasks.ingest_citybikes_postgres': TaskRuntimeProfile(
        'ingestion_postgres', TASK_QUEUE_INGEST_POSTGRES, 'heavy', 4
    ),
    'apps.mobility.tasks.release_citybikes_lock': DEFAULT_TASK_PROFILE,
    'apps.mobility.tasks.collect_citybikes': DEFAULT_TASK_PROFILE,
    'apps.pollution.tasks.collect_openaq_bronze': TaskRuntimeProfile('bronze', TASK_QUEUE_BRONZE, 'medium', 2),
    'apps.pollution.tasks.collect_openaq_silver': TaskRuntimeProfile('silver', TASK_QUEUE_SILVER, 'medium', 3),
    'apps.pollution.tasks.ingest_openaq_postgres': TaskRuntimeProfile(
        'ingestion_postgres', TASK_QUEUE_INGEST_POSTGRES, 'heavy', 4
    ),
    'apps.pollution.tasks.collect_openaq': DEFAULT_TASK_PROFILE,
    'apps.pollution.tasks.fetch_eea_historical': TaskRuntimeProfile(
        'ingestion_postgres', TASK_QUEUE_INGEST_POSTGRES, 'critical', 5
    ),
    'apps.analysis.tasks.run_cross_analysis': TaskRuntimeProfile('analysis', TASK_QUEUE_ANALYSIS, 'medium', 3),
    'apps.serving.tasks.publish_gold_mobility': TaskRuntimeProfile('gold', TASK_QUEUE_GOLD, 'medium', 2),
    'apps.serving.tasks.publish_gold_air': TaskRuntimeProfile('gold', TASK_QUEUE_GOLD, 'heavy', 4),
    'apps.serving.tasks.publish_gold_climate': TaskRuntimeProfile('gold', TASK_QUEUE_GOLD, 'heavy', 3),
    'apps.serving.tasks.publish_gold_all': DEFAULT_TASK_PROFILE,
}

_ROW_COUNT_KEYS = (
    'rows_upserted',
    'records_processed',
    'networks_processed',
    'stations',
    'series',
    'hourly',
    'hotspots',
    'who',
    'cities',
    'rows',
)
_SIGNALS_REGISTERED = False


def get_task_profile(task_name: str) -> TaskRuntimeProfile:
    profile = TASK_PROFILES.get(task_name)
    if profile is not None:
        return profile
    if task_name.endswith('_bronze'):
        return TaskRuntimeProfile('bronze', TASK_QUEUE_BRONZE, 'light', 1)
    if task_name.endswith('_silver'):
        return TaskRuntimeProfile('silver', TASK_QUEUE_SILVER, 'medium', 2)
    if task_name.startswith('apps.serving.tasks.publish_gold'):
        return TaskRuntimeProfile('gold', TASK_QUEUE_GOLD, 'medium', 2)
    return DEFAULT_TASK_PROFILE


def build_task_routes() -> dict[str, dict[str, str]]:
    return {
        task_name: {'queue': profile.queue, 'routing_key': profile.queue}
        for task_name, profile in TASK_PROFILES.items()
    }


def summarize_task_result(result: Any) -> tuple[dict[str, Any], int]:
    if not isinstance(result, dict):
        return {'result_type': type(result).__name__}, 0

    summary: dict[str, Any] = {}
    rows_processed = 0

    for key, value in result.items():
        if isinstance(value, bool) or value is None:
            summary[key] = value
            continue
        if isinstance(value, int):
            summary[key] = value
            if key in _ROW_COUNT_KEYS:
                rows_processed += max(value, 0)
            continue
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                continue
            summary[key] = round(value, 3)
            continue
        if isinstance(value, (str, date, datetime)):
            summary[key] = value.isoformat() if hasattr(value, 'isoformat') else str(value)
            continue
        if isinstance(value, dict):
            summary[f'{key}_count'] = len(value)
            continue
        if isinstance(value, (list, tuple, set)):
            summary[f'{key}_count'] = len(value)
            continue
        summary[key] = str(value)

    return summary, rows_processed


def register_task_telemetry() -> None:
    global _SIGNALS_REGISTERED
    if _SIGNALS_REGISTERED:
        return
    task_prerun.connect(_on_task_prerun, weak=False)
    task_postrun.connect(_on_task_postrun, weak=False)
    _SIGNALS_REGISTERED = True


def _on_task_prerun(task_id=None, task=None, **kwargs) -> None:
    task_name = getattr(task, 'name', '')
    if not task_name.startswith('apps.'):
        return
    request = getattr(task, 'request', None)
    if request is None:
        return
    request._metric_started_at = timezone.now()
    request._metric_perf_started = time.perf_counter()


def _on_task_postrun(task_id=None, task=None, retval=None, state=None, **kwargs) -> None:
    task_name = getattr(task, 'name', '')
    if not task_name.startswith('apps.'):
        return

    request = getattr(task, 'request', None)
    if request is None:
        return

    profile = get_task_profile(task_name)
    started_at = getattr(request, '_metric_started_at', None) or timezone.now()
    perf_started = getattr(request, '_metric_perf_started', None)
    finished_at = timezone.now()
    duration_ms = None
    if perf_started is not None:
        duration_ms = max(int((time.perf_counter() - perf_started) * 1000), 0)

    delivery_info = getattr(request, 'delivery_info', {}) or {}
    queue_name = (
        delivery_info.get('routing_key')
        or delivery_info.get('queue')
        or profile.queue
    )
    summary, rows_processed = summarize_task_result(retval)

    try:
        from apps.utils.models import TaskExecutionMetric

        TaskExecutionMetric.objects.update_or_create(
            task_id=task_id,
            defaults={
                'root_id': str(getattr(request, 'root_id', '') or ''),
                'parent_id': str(getattr(request, 'parent_id', '') or ''),
                'task_name': task_name,
                'queue_name': str(queue_name),
                'stage': profile.stage,
                'resource_tier': profile.resource_tier,
                'workload_score': profile.workload_score,
                'status': str(state or 'unknown').lower(),
                'retries': int(getattr(request, 'retries', 0) or 0),
                'duration_ms': duration_ms,
                'peak_memory_mb': _get_peak_memory_mb(),
                'rows_processed': rows_processed,
                'worker_hostname': str(getattr(request, 'hostname', '') or ''),
                'summary_json': summary,
                'started_at': started_at,
                'finished_at': finished_at,
            },
        )
    except Exception as exc:
        logger.warning('task telemetry persistence failed for %s: %s', task_name, exc)


def _get_peak_memory_mb() -> float | None:
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == 'darwin':
            return round(rss / (1024 * 1024), 2)
        return round(rss / 1024, 2)
    except Exception:
        return None
