from rest_framework import serializers

from apps.mobility.models import BikeNetwork, BikeSnapshot, BikeStation


class BikeNetworkSerializer(serializers.ModelSerializer):
    class Meta:
        model = BikeNetwork
        fields = [
            'id',
            'network_id',
            'name',
            'city',
            'country',
            'latitude',
            'longitude',
        ]


class BikeStationSerializer(serializers.ModelSerializer):
    """The raw FK integer 'network' is omitted; use 'network_name'/'network_city'."""

    network_name = serializers.CharField(source='network.name', read_only=True)
    network_city = serializers.CharField(source='network.city', read_only=True)

    class Meta:
        model = BikeStation
        fields = [
            'id',
            'network_name',
            'network_city',
            'station_id',
            'station_name',
            'latitude',
            'longitude',
        ]


class BikeSnapshotSerializer(serializers.ModelSerializer):
    """The raw FK integer 'station' is omitted; use 'station_id'/'station_name'."""

    station_name = serializers.CharField(source='station.station_name', read_only=True)
    station_id = serializers.CharField(source='station.station_id', read_only=True)
    network_name = serializers.CharField(source='station.network.name', read_only=True)
    city = serializers.CharField(source='station.network.city', read_only=True)

    class Meta:
        model = BikeSnapshot
        fields = [
            'id',
            'station_id',
            'station_name',
            'network_name',
            'city',
            'timestamp',
            'bikes_available',
            'free_slots',
            'total_slots',
        ]
