from __future__ import annotations

from datetime import timedelta

from celery.schedules import crontab as celery_crontab
from django.conf import settings
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask, PeriodicTasks

from urbanhub.celery import app


class Command(BaseCommand):
    help = "Synchronise les tâches périodiques django-celery-beat avec urbanhub.celery."

    def add_arguments(self, parser):
        parser.add_argument(
            '--disable-stale',
            action='store_true',
            help="Désactive les tâches applicatives présentes en base mais absentes du planning code.",
        )

    def handle(self, *args, **options):
        synced = 0
        expected_names: set[str] = set()

        for name, config in app.conf.beat_schedule.items():
            task_name = config.get('task')
            if not task_name or task_name.startswith('celery.'):
                continue

            expected_names.add(name)
            periodic_task, created = self._upsert_periodic_task(name=name, task_name=task_name, schedule=config['schedule'])
            synced += 1
            verb = 'Créée' if created else 'Mise à jour'
            self.stdout.write(f'{verb}: {periodic_task.name} -> {periodic_task.task}')

        disabled = 0
        if options['disable_stale']:
            stale_qs = PeriodicTask.objects.filter(task__startswith='apps.').exclude(name__in=expected_names).exclude(enabled=False)
            disabled = stale_qs.update(enabled=False)
            if disabled:
                self.stdout.write(f'Désactivées: {disabled} tâche(s) obsolète(s)')

        if synced or disabled:
            PeriodicTasks.update_changed()

        self.stdout.write(self.style.SUCCESS(f'Synchronisation terminée: {synced} tâche(s) gérée(s), {disabled} obsolète(s) désactivée(s).'))

    def _upsert_periodic_task(self, *, name: str, task_name: str, schedule):
        defaults = {
            'task': task_name,
            'enabled': True,
            'one_off': False,
            'args': '[]',
            'kwargs': '{}',
            'interval': None,
            'crontab': None,
        }

        if isinstance(schedule, celery_crontab):
            defaults['crontab'] = self._get_crontab_schedule(schedule)
        else:
            defaults['interval'] = self._get_interval_schedule(schedule)

        return PeriodicTask.objects.update_or_create(name=name, defaults=defaults)

    @staticmethod
    def _get_interval_schedule(schedule) -> IntervalSchedule:
        if isinstance(schedule, timedelta):
            total_seconds = int(schedule.total_seconds())
        elif isinstance(schedule, (int, float)):
            total_seconds = int(schedule)
        else:
            total_seconds = int(getattr(schedule, 'seconds'))

        if total_seconds <= 0:
            raise ValueError(f"Unsupported interval schedule: {schedule!r}")

        return IntervalSchedule.objects.get_or_create(
            every=total_seconds,
            period=IntervalSchedule.SECONDS,
        )[0]

    @staticmethod
    def _get_crontab_schedule(schedule: celery_crontab) -> CrontabSchedule:
        timezone = str(getattr(schedule, 'tz', None) or settings.TIME_ZONE)
        return CrontabSchedule.objects.get_or_create(
            minute=str(schedule._orig_minute),
            hour=str(schedule._orig_hour),
            day_of_week=str(schedule._orig_day_of_week),
            day_of_month=str(schedule._orig_day_of_month),
            month_of_year=str(schedule._orig_month_of_year),
            timezone=timezone,
        )[0]
