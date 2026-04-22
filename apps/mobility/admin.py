from django.contrib import admin

from .models import BikeNetwork, BikeSnapshot, BikeStation


@admin.register(BikeNetwork)
class BikeNetworkAdmin(admin.ModelAdmin):
    list_display = ('network_id', 'name', 'city', 'country', 'latitude', 'longitude')
    search_fields = ('network_id', 'name', 'city')
    list_filter = ('country', 'city')
    ordering = ('country', 'city', 'name')


@admin.register(BikeStation)
class BikeStationAdmin(admin.ModelAdmin):
    list_display = ('station_id', 'station_name', 'network', 'latitude', 'longitude')
    search_fields = ('station_id', 'station_name')
    list_filter = ('network__city', 'network__country')
    ordering = ('network', 'station_name')
    raw_id_fields = ('network',)


@admin.register(BikeSnapshot)
class BikeSnapshotAdmin(admin.ModelAdmin):
    list_display = ('station', 'timestamp', 'bikes_available', 'free_slots', 'total_slots')
    list_filter = ('station__network__city',)
    search_fields = ('station__station_id', 'station__station_name')
    ordering = ('-timestamp',)
    raw_id_fields = ('station',)
    date_hierarchy = 'timestamp'
