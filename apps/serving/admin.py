from django.contrib import admin

from . import models


@admin.register(models.GoldMobilityStationLatest)
class GoldMobilityStationLatestAdmin(admin.ModelAdmin):
    list_display = ('network_id', 'station_id', 'network_city', 'as_of')
    list_filter = ('as_of',)


@admin.register(models.GoldMobilityHourly)
class GoldMobilityHourlyAdmin(admin.ModelAdmin):
    list_display = ('city_key', 'hour', 'as_of')
