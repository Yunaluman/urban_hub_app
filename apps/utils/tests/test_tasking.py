from django.test import SimpleTestCase

from apps.utils.tasking import (
    TASK_QUEUE_BRONZE,
    TASK_QUEUE_GOLD,
    TASK_QUEUE_INGEST_POSTGRES,
    get_task_profile,
    summarize_task_result,
)


class TaskingHelpersTests(SimpleTestCase):
    def test_profile_uses_explicit_queue(self):
        profile = get_task_profile('apps.serving.tasks.publish_gold_air')
        self.assertEqual(profile.queue, TASK_QUEUE_GOLD)
        self.assertEqual(profile.stage, 'gold')

    def test_profile_uses_suffix_fallback(self):
        profile = get_task_profile('apps.fake.tasks.some_bronze')
        self.assertEqual(profile.queue, TASK_QUEUE_BRONZE)
        self.assertEqual(profile.stage, 'bronze')

    def test_profile_uses_default_for_unknown_task(self):
        profile = get_task_profile('apps.fake.tasks.other')
        self.assertNotEqual(profile.queue, TASK_QUEUE_INGEST_POSTGRES)

    def test_summarize_task_result_extracts_row_counts(self):
        summary, rows = summarize_task_result({
            'rows_upserted': 120,
            'queued': True,
            'cities': 4,
            'payload': ['a', 'b'],
        })
        self.assertEqual(rows, 124)
        self.assertEqual(summary['payload_count'], 2)
