from django.db import models


class PollutionSensor(models.Model):
    sensor_id = models.CharField(max_length=100, unique=True)
    city = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)
    source = models.CharField(max_length=100, default='OpenAQ')

    def __str__(self):
        return f"Capteur {self.sensor_id} – {self.city}"

    class Meta:
        db_table = 'pollution_sensor'


class PollutionMeasurement(models.Model):
    POLLUTANTS = [
        ('pm25', 'PM2.5'),
        ('pm10', 'PM10'),
        ('no2', 'NO2'),
        ('o3', 'O3'),
        ('co', 'CO'),
        ('so2', 'SO2'),
    ]

    sensor = models.ForeignKey(PollutionSensor, on_delete=models.CASCADE, related_name='measurements')
    timestamp = models.DateTimeField(db_index=True)
    pollutant = models.CharField(max_length=10, choices=POLLUTANTS, db_index=True)
    value = models.FloatField()
    unit = models.CharField(max_length=20, default='µg/m³')

    class Meta:
        db_table = 'pollution_measurement'
        ordering = ['-timestamp']
        unique_together = ('sensor', 'timestamp', 'pollutant')
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['pollutant', 'timestamp']),
            models.Index(fields=['sensor', 'pollutant', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.pollutant} = {self.value} {self.unit} @ {self.timestamp}"
