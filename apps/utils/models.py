from django.db import models


class TaskExecutionMetric(models.Model):
    task_id = models.CharField(max_length=128, unique=True)
    root_id = models.CharField(max_length=128, blank=True)
    parent_id = models.CharField(max_length=128, blank=True)
    task_name = models.CharField(max_length=255, db_index=True)
    queue_name = models.CharField(max_length=64, db_index=True)
    stage = models.CharField(max_length=32, db_index=True)
    resource_tier = models.CharField(max_length=32, db_index=True)
    workload_score = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=32, db_index=True)
    retries = models.PositiveSmallIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    peak_memory_mb = models.FloatField(null=True, blank=True)
    rows_processed = models.PositiveIntegerField(default=0)
    worker_hostname = models.CharField(max_length=255, blank=True)
    summary_json = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()

    class Meta:
        ordering = ['-finished_at']
        indexes = [
            models.Index(fields=['task_name', 'finished_at']),
            models.Index(fields=['queue_name', 'finished_at']),
            models.Index(fields=['stage', 'finished_at']),
        ]

    def __str__(self) -> str:
        return f'{self.task_name} [{self.status}] {self.finished_at.isoformat()}'
