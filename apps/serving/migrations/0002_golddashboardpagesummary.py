# Generated manually for apps.serving Gold dashboard summaries
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('serving', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GoldDashboardPageSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('page', models.CharField(db_index=True, max_length=32)),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('payload_json', models.JSONField(default=dict)),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_dashboard_page_summary',
            },
        ),
        migrations.AddConstraint(
            model_name='golddashboardpagesummary',
            constraint=models.UniqueConstraint(fields=('page', 'city_key'), name='uq_gold_dashboard_page_summary'),
        ),
    ]
