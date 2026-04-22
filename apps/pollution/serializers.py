from rest_framework import serializers

from apps.pollution.models import PollutionMeasurement, PollutionSensor


class PollutionSensorSerializer(serializers.ModelSerializer):
    class Meta:
        model = PollutionSensor
        fields = [
            'id',
            'sensor_id',
            'city',
            'latitude',
            'longitude',
            'source',
        ]


class PollutionMeasurementSerializer(serializers.ModelSerializer):
    sensor_city = serializers.CharField(source='sensor.city', read_only=True)
    sensor_source = serializers.CharField(source='sensor.source', read_only=True)

    class Meta:
        model = PollutionMeasurement
        fields = [
            'id',
            'sensor',
            'sensor_city',
            'sensor_source',
            'timestamp',
            'pollutant',
            'value',
            'unit',
        ]
