from django.contrib import admin

from .models import WeatherObservation, WeatherStation


@admin.register(WeatherStation)
class WeatherStationAdmin(admin.ModelAdmin):
    list_display = ('station_id', 'name', 'city', 'latitude', 'longitude', 'elevation')
    search_fields = ('station_id', 'name', 'city')
    list_filter = ('city',)
    ordering = ('city', 'name')


@admin.register(WeatherObservation)
class WeatherObservationAdmin(admin.ModelAdmin):
    list_display = ('station', 'timestamp', 'temperature', 'wind_speed', 'precipitation', 'pressure')
    list_filter = ('station__city',)
    search_fields = ('station__station_id', 'station__name')
    ordering = ('-timestamp',)
    raw_id_fields = ('station',)
    date_hierarchy = 'timestamp'
