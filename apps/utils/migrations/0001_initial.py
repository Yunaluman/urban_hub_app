from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='TaskExecutionMetric',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('task_id', models.CharField(max_length=128, unique=True)),
                ('root_id', models.CharField(blank=True, max_length=128)),
                ('parent_id', models.CharField(blank=True, max_length=128)),
                ('task_name', models.CharField(db_index=True, max_length=255)),
                ('queue_name', models.CharField(db_index=True, max_length=64)),
                ('stage', models.CharField(db_index=True, max_length=32)),
                ('resource_tier', models.CharField(db_index=True, max_length=32)),
                ('workload_score', models.PositiveSmallIntegerField(default=1)),
                ('status', models.CharField(db_index=True, max_length=32)),
                ('retries', models.PositiveSmallIntegerField(default=0)),
                ('duration_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('peak_memory_mb', models.FloatField(blank=True, null=True)),
                ('rows_processed', models.PositiveIntegerField(default=0)),
                ('worker_hostname', models.CharField(blank=True, max_length=255)),
                ('summary_json', models.JSONField(blank=True, default=dict)),
                ('started_at', models.DateTimeField()),
                ('finished_at', models.DateTimeField()),
            ],
            options={
                'ordering': ['-finished_at'],
            },
        ),
        migrations.AddIndex(
            model_name='taskexecutionmetric',
            index=models.Index(fields=['task_name', 'finished_at'], name='utils_taske_task_na_5ac6a3_idx'),
        ),
        migrations.AddIndex(
            model_name='taskexecutionmetric',
            index=models.Index(fields=['queue_name', 'finished_at'], name='utils_taske_queue_n_185873_idx'),
        ),
        migrations.AddIndex(
            model_name='taskexecutionmetric',
            index=models.Index(fields=['stage', 'finished_at'], name='utils_taske_stage_b536e9_idx'),
        ),
    ]
