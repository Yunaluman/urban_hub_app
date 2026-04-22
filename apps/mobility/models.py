from django.db import models


class BikeNetwork(models.Model):
    network_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=10, default='FR')
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)

    def __str__(self):
        return f"{self.name} ({self.city})"

    class Meta:
        db_table = 'mobility_network'


class BikeStation(models.Model):
    network = models.ForeignKey(BikeNetwork, on_delete=models.CASCADE, related_name='stations')
    station_id = models.CharField(max_length=100)
    station_name = models.CharField(max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        db_table = 'mobility_station'
        unique_together = ('network', 'station_id')

    def __str__(self):
        return f"{self.station_name} – {self.network.city}"


class BikeSnapshot(models.Model):
    station = models.ForeignKey(BikeStation, on_delete=models.CASCADE, related_name='snapshots')
    timestamp = models.DateTimeField(db_index=True)
    bikes_available = models.IntegerField(default=0)
    free_slots = models.IntegerField(default=0)
    total_slots = models.IntegerField(default=0)

    class Meta:
        db_table = 'mobility_snapshot'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['station', 'timestamp']),
        ]

    @property
    def utilization_rate(self):
        if self.total_slots == 0:
            return 0
        return round((self.bikes_available / self.total_slots) * 100, 1)
