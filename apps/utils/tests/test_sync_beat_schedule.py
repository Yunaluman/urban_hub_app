from io import StringIO
from unittest.mock import patch

from celery.schedules import crontab
from django.core.management import call_command
from django.test import TestCase
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from urbanhub.celery import app


class SyncBeatScheduleCommandTests(TestCase):
    def test_syncs_tasks_and_disables_stale_entries(self):
        stale_interval = IntervalSchedule.objects.create(every=60, period=IntervalSchedule.SECONDS)
        stale_task = PeriodicTask.objects.create(
            name='obsolete-task',
            task='apps.legacy.tasks.old_job',
            interval=stale_interval,
            enabled=True,
        )

        managed_schedule = {
            'sample-interval': {
                'task': 'apps.sample.tasks.collect',
                'schedule': 300.0,
            },
            'sample-cron': {
                'task': 'apps.sample.tasks.publish',
                'schedule': crontab(minute='20', hour='*'),
            },
        }

        with patch.dict(app.conf.beat_schedule, managed_schedule, clear=True):
            out = StringIO()
            call_command('sync_beat_schedule', '--disable-stale', stdout=out)

        interval_task = PeriodicTask.objects.get(name='sample-interval')
        self.assertTrue(interval_task.enabled)
        self.assertEqual(interval_task.task, 'apps.sample.tasks.collect')
        self.assertEqual(interval_task.interval.every, 300)

        cron_task = PeriodicTask.objects.get(name='sample-cron')
        self.assertTrue(cron_task.enabled)
        self.assertEqual(cron_task.task, 'apps.sample.tasks.publish')
        self.assertEqual(cron_task.crontab.minute, '20')

        stale_task.refresh_from_db()
        self.assertFalse(stale_task.enabled)
