from django.contrib import admin

from .models import PollutionMeasurement, PollutionSensor


@admin.register(PollutionSensor)
class PollutionSensorAdmin(admin.ModelAdmin):
    list_display = ('sensor_id', 'city', 'source', 'latitude', 'longitude')
    search_fields = ('sensor_id', 'city')
    list_filter = ('source', 'city')
    ordering = ('city', 'sensor_id')


@admin.register(PollutionMeasurement)
class PollutionMeasurementAdmin(admin.ModelAdmin):
    list_display = ('sensor', 'timestamp', 'pollutant', 'value', 'unit')
    list_filter = ('pollutant', 'sensor__city')
    search_fields = ('sensor__sensor_id', 'sensor__city')
    ordering = ('-timestamp',)
    raw_id_fields = ('sensor',)
    date_hierarchy = 'timestamp'
