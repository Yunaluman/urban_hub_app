from django.db import models


class GoldMobilityStationLatest(models.Model):
    """Dernier snapshot par station (toutes villes) — sert carte + critique."""

    network_id = models.CharField(max_length=100, db_index=True)
    station_id = models.CharField(max_length=100)
    network_city = models.CharField(max_length=100, db_index=True)
    station_name = models.CharField(max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()
    bikes_available = models.IntegerField(default=0)
    free_slots = models.IntegerField(default=0)
    total_slots = models.IntegerField(default=0)
    util_rate = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_mobility_station_latest'
        constraints = [
            models.UniqueConstraint(
                fields=['network_id', 'station_id'],
                name='uq_gold_mobility_station_net_st',
            ),
        ]
        indexes = [
            models.Index(fields=['network_city', 'as_of']),
        ]


class GoldMobilityHourly(models.Model):
    """Profil horaire moyen vélos par city_key ('' = national)."""

    city_key = models.CharField(max_length=100, db_index=True)
    hour = models.PositiveSmallIntegerField()
    bikes_avg = models.FloatField()
    slots_avg = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_mobility_hourly'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'hour'],
                name='uq_gold_mobility_hourly_city_hour',
            ),
        ]


class GoldAirSeriesDay(models.Model):
    city_key = models.CharField(max_length=100, db_index=True)
    pollutant = models.CharField(max_length=32, db_index=True)
    observation_date = models.DateField()
    value_avg = models.FloatField()
    value_max = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_air_series_day'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'pollutant', 'observation_date'],
                name='uq_gold_air_series_day',
            ),
        ]


class GoldAirHourlyPattern(models.Model):
    city_key = models.CharField(max_length=100, db_index=True)
    pollutant = models.CharField(max_length=32, db_index=True)
    hour = models.PositiveSmallIntegerField()
    value_avg = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_air_hourly_pattern'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'pollutant', 'hour'],
                name='uq_gold_air_hourly_pattern',
            ),
        ]


class GoldAirHotspot(models.Model):
    pollutant = models.CharField(max_length=32, db_index=True)
    sensor_id = models.CharField(max_length=200)
    city = models.CharField(max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()
    value_avg = models.FloatField()
    pct_exceedance = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_air_hotspot'
        constraints = [
            models.UniqueConstraint(
                fields=['pollutant', 'sensor_id'],
                name='uq_gold_air_hotspot_poll_sensor',
            ),
        ]


class GoldAirWhoExceedance(models.Model):
    city_key = models.CharField(max_length=100, db_index=True)
    pollutant = models.CharField(max_length=32, db_index=True)
    days_exceeded = models.PositiveIntegerField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_air_who_exceedance'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'pollutant'],
                name='uq_gold_air_who_city_poll',
            ),
        ]


class GoldClimateTemperatureDay(models.Model):
    city_key = models.CharField(max_length=100, db_index=True)
    day = models.DateField()
    temp_avg = models.FloatField()
    temp_min = models.FloatField()
    temp_max = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_climate_temperature_day'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'day'],
                name='uq_gold_climate_temp_day',
            ),
        ]


class GoldClimateSeasonalMonth(models.Model):
    city_key = models.CharField(max_length=100, db_index=True)
    month = models.PositiveSmallIntegerField()
    temp_avg = models.FloatField()
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_climate_seasonal_month'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'month'],
                name='uq_gold_climate_seasonal_city_month',
            ),
        ]


class GoldClimateExtremeDay(models.Model):
    city_key = models.CharField(max_length=100, db_index=True)
    std_factor = models.FloatField()
    day = models.DateField()
    temp = models.FloatField()
    event_type = models.CharField(max_length=32)
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_climate_extreme_day'
        constraints = [
            models.UniqueConstraint(
                fields=['city_key', 'std_factor', 'day', 'event_type'],
                name='uq_gold_climate_extreme',
            ),
        ]


class GoldClimateVisibilitySample(models.Model):
    city_key = models.CharField(max_length=100, unique=True)
    points_json = models.JSONField(default=list)
    as_of = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'gold_climate_visibility_sample'
