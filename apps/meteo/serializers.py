from rest_framework import serializers

from .models import WeatherObservation, WeatherStation


class WeatherStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeatherStation
        fields = [
            'id',
            'station_id',
            'name',
            'latitude',
            'longitude',
            'elevation',
            'city',
        ]


class WeatherObservationSerializer(serializers.ModelSerializer):
    """Detail serializer — includes raw_file for single-record retrieval.

    The internal FK integer ('station') is intentionally omitted; consumers
    should use 'station_id' (the human-readable identifier) instead.
    """

    station_id = serializers.CharField(source='station.station_id', read_only=True)
    station_name = serializers.CharField(source='station.name', read_only=True)

    class Meta:
        model = WeatherObservation
        fields = [
            'id',
            'station_id',
            'station_name',
            'timestamp',
            'temperature',
            'wind_speed',
            'wind_direction',
            'pressure',
            'precipitation',
            'visibility',
            'raw_file',
        ]


class WeatherObservationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer used for list endpoints (omits raw_file)."""

    station_id = serializers.CharField(source='station.station_id', read_only=True)

    class Meta:
        model = WeatherObservation
        fields = [
            'id',
            'station_id',
            'timestamp',
            'temperature',
            'wind_speed',
            'wind_direction',
            'pressure',
            'precipitation',
            'visibility',
        ]
