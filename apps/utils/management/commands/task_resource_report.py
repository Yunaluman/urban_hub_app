from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, Max
from django.utils import timezone

from apps.utils.models import TaskExecutionMetric


class Command(BaseCommand):
    help = 'Classe les tâches Celery par coût observé pour aider à planifier leur exécution.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=10)

    def handle(self, *args, **options):
        days = max(int(options['days']), 1)
        limit = max(int(options['limit']), 1)
        since = timezone.now() - timedelta(days=days)

        rows = (
            TaskExecutionMetric.objects
            .filter(finished_at__gte=since)
            .values('task_name', 'queue_name', 'stage', 'resource_tier')
            .annotate(
                runs=Count('id'),
                avg_duration_ms=Avg('duration_ms'),
                max_duration_ms=Max('duration_ms'),
                avg_rows=Avg('rows_processed'),
                max_memory_mb=Max('peak_memory_mb'),
                max_workload=Max('workload_score'),
            )
            .order_by('-avg_duration_ms', '-max_workload')[:limit]
        )

        if not rows:
            self.stdout.write(self.style.WARNING('Aucune métrique disponible.'))
            return

        self.stdout.write(f'Tâches les plus coûteuses sur les {days} derniers jours:')
        for row in rows:
            self.stdout.write(
                (
                    f"- {row['task_name']} | queue={row['queue_name']} | stage={row['stage']} "
                    f"| tier={row['resource_tier']} | runs={row['runs']} "
                    f"| avg={_fmt_ms(row['avg_duration_ms'])} | max={_fmt_ms(row['max_duration_ms'])} "
                    f"| avg_rows={int(row['avg_rows'] or 0)} | peak_mem={_fmt_mem(row['max_memory_mb'])}"
                )
            )


def _fmt_ms(value) -> str:
    if value is None:
        return 'n/a'
    return f'{int(value)} ms'


def _fmt_mem(value) -> str:
    if value is None:
        return 'n/a'
    return f'{float(value):.1f} MB'
