from django.db import models


class WeatherStation(models.Model):
    station_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200, blank=True)
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)
    elevation = models.FloatField(null=True)
    city = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.station_id} – {self.name}"

    class Meta:
        db_table = 'meteo_station'


class WeatherObservation(models.Model):
    station = models.ForeignKey(WeatherStation, on_delete=models.CASCADE, related_name='observations')
    timestamp = models.DateTimeField(db_index=True)
    temperature = models.FloatField(null=True, help_text="°C")
    wind_speed = models.FloatField(null=True, help_text="m/s")
    wind_direction = models.IntegerField(null=True, help_text="degrés")
    pressure = models.FloatField(null=True, help_text="hPa")
    precipitation = models.FloatField(null=True, help_text="mm")
    visibility = models.FloatField(null=True, help_text="km")
    raw_file = models.CharField(max_length=200, blank=True, help_text="Chemin MinIO")

    class Meta:
        db_table = 'meteo_observation'
        unique_together = ('station', 'timestamp')
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['station', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.station.station_id} @ {self.timestamp}"
