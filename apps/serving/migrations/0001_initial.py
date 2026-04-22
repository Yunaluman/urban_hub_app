# Generated manually for apps.serving Gold models

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='GoldMobilityStationLatest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('network_id', models.CharField(db_index=True, max_length=100)),
                ('station_id', models.CharField(max_length=100)),
                ('network_city', models.CharField(db_index=True, max_length=100)),
                ('station_name', models.CharField(max_length=200)),
                ('latitude', models.FloatField()),
                ('longitude', models.FloatField()),
                ('bikes_available', models.IntegerField(default=0)),
                ('free_slots', models.IntegerField(default=0)),
                ('total_slots', models.IntegerField(default=0)),
                ('util_rate', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_mobility_station_latest',
            },
        ),
        migrations.CreateModel(
            name='GoldMobilityHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('hour', models.PositiveSmallIntegerField()),
                ('bikes_avg', models.FloatField()),
                ('slots_avg', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_mobility_hourly',
            },
        ),
        migrations.CreateModel(
            name='GoldAirSeriesDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('pollutant', models.CharField(db_index=True, max_length=32)),
                ('observation_date', models.DateField()),
                ('value_avg', models.FloatField()),
                ('value_max', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_air_series_day',
            },
        ),
        migrations.CreateModel(
            name='GoldAirHourlyPattern',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('pollutant', models.CharField(db_index=True, max_length=32)),
                ('hour', models.PositiveSmallIntegerField()),
                ('value_avg', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_air_hourly_pattern',
            },
        ),
        migrations.CreateModel(
            name='GoldAirHotspot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pollutant', models.CharField(db_index=True, max_length=32)),
                ('sensor_id', models.CharField(max_length=200)),
                ('city', models.CharField(max_length=200)),
                ('latitude', models.FloatField()),
                ('longitude', models.FloatField()),
                ('value_avg', models.FloatField()),
                ('pct_exceedance', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_air_hotspot',
            },
        ),
        migrations.CreateModel(
            name='GoldAirWhoExceedance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('pollutant', models.CharField(max_length=32)),
                ('days_exceeded', models.PositiveIntegerField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_air_who_exceedance',
            },
        ),
        migrations.CreateModel(
            name='GoldClimateTemperatureDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('day', models.DateField()),
                ('temp_avg', models.FloatField()),
                ('temp_min', models.FloatField()),
                ('temp_max', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_climate_temperature_day',
            },
        ),
        migrations.CreateModel(
            name='GoldClimateSeasonalMonth',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('month', models.PositiveSmallIntegerField()),
                ('temp_avg', models.FloatField()),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_climate_seasonal_month',
            },
        ),
        migrations.CreateModel(
            name='GoldClimateExtremeDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(db_index=True, max_length=100)),
                ('std_factor', models.FloatField()),
                ('day', models.DateField()),
                ('temp', models.FloatField()),
                ('event_type', models.CharField(max_length=32)),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_climate_extreme_day',
            },
        ),
        migrations.CreateModel(
            name='GoldClimateVisibilitySample',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_key', models.CharField(max_length=100, unique=True)),
                ('points_json', models.JSONField(default=list)),
                ('as_of', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'gold_climate_visibility_sample',
            },
        ),
        migrations.AddConstraint(
            model_name='goldmobilitystationlatest',
            constraint=models.UniqueConstraint(fields=('network_id', 'station_id'), name='uq_gold_mobility_station_net_st'),
        ),
        migrations.AddIndex(
            model_name='goldmobilitystationlatest',
            index=models.Index(fields=['network_city', 'as_of'], name='gold_mobility_network_city_as_of_idx'),
        ),
        migrations.AddConstraint(
            model_name='goldmobilityhourly',
            constraint=models.UniqueConstraint(fields=('city_key', 'hour'), name='uq_gold_mobility_hourly_city_hour'),
        ),
        migrations.AddConstraint(
            model_name='goldairseriesday',
            constraint=models.UniqueConstraint(fields=('city_key', 'pollutant', 'observation_date'), name='uq_gold_air_series_day'),
        ),
        migrations.AddConstraint(
            model_name='goldairhourlypattern',
            constraint=models.UniqueConstraint(fields=('city_key', 'pollutant', 'hour'), name='uq_gold_air_hourly_pattern'),
        ),
        migrations.AddConstraint(
            model_name='goldairhotspot',
            constraint=models.UniqueConstraint(fields=('pollutant', 'sensor_id'), name='uq_gold_air_hotspot_poll_sensor'),
        ),
        migrations.AddConstraint(
            model_name='goldairwhoexceedance',
            constraint=models.UniqueConstraint(fields=('city_key', 'pollutant'), name='uq_gold_air_who_city_poll'),
        ),
        migrations.AddConstraint(
            model_name='goldclimatetemperatureday',
            constraint=models.UniqueConstraint(fields=('city_key', 'day'), name='uq_gold_climate_temp_day'),
        ),
        migrations.AddConstraint(
            model_name='goldclimateseasonalmonth',
            constraint=models.UniqueConstraint(fields=('city_key', 'month'), name='uq_gold_climate_seasonal_city_month'),
        ),
        migrations.AddConstraint(
            model_name='goldclimateextremeday',
            constraint=models.UniqueConstraint(fields=('city_key', 'std_factor', 'day', 'event_type'), name='uq_gold_climate_extreme'),
        ),
    ]
