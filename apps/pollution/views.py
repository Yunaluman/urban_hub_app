import logging
from collections import defaultdict

from django.db.models import Avg, Count, Q
from django.db.models.functions import ExtractHour
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.pollution.models import PollutionMeasurement, PollutionSensor
from apps.pollution.serializers import (
    PollutionMeasurementSerializer,
    PollutionSensorSerializer,
)

logger = logging.getLogger(__name__)

TARGET_CITIES = [
    'Paris', 'Lyon', 'Marseille', 'Bordeaux', 'Lille', 'Toulouse', 'Strasbourg'
]


class PollutionSensorViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve pollution sensors, with optional city filtering."""

    serializer_class = PollutionSensorSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = PollutionSensor.objects.order_by('city', 'sensor_id')

        city = self.request.query_params.get('city')
        if city:
            qs = qs.filter(city__icontains=city)

        return qs


def _parse_date_or_400(value: str, param_name: str):
    """Parse an ISO date string, raising DRF ValidationError (HTTP 400) on failure."""
    parsed = parse_date(value)
    if parsed is None:
        raise ValidationError(
            {param_name: f"Invalid date '{value}'. Expected YYYY-MM-DD format."}
        )
    return parsed


class PollutionMeasurementViewSet(viewsets.ReadOnlyModelViewSet):
    """
    List pollution measurements with optional filtering.

    Query parameters:
    - pollutant: one of pm25 / pm10 / no2 / o3 / co
    - city: partial match on sensor city
    - date_from: ISO date string (inclusive), returns 400 if malformed
    - date_to: ISO date string (inclusive), returns 400 if malformed
    """

    serializer_class = PollutionMeasurementSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = PollutionMeasurement.objects.select_related('sensor').order_by('-timestamp')

        pollutant = self.request.query_params.get('pollutant')
        if pollutant:
            qs = qs.filter(pollutant=pollutant)

        city = self.request.query_params.get('city')
        if city:
            qs = qs.filter(sensor__city__icontains=city)

        date_from = self.request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(timestamp__date__gte=_parse_date_or_400(date_from, 'date_from'))

        date_to = self.request.query_params.get('date_to')
        if date_to:
            qs = qs.filter(timestamp__date__lte=_parse_date_or_400(date_to, 'date_to'))

        return qs


class PollutionStatsView(APIView):
    """
    Aggregate pollution statistics.

    Returns:
    - highest_pollution_zones: top cities/sensors ranked by average pollutant value
    - daily_variation: per-pollutant average value grouped by hour-of-day
    - dominant_pollutants: which pollutant dominates (highest avg value) per city
    - anomaly_episodes: measurements where value > 2x the daily mean for that pollutant
    """

    permission_classes = [AllowAny]

    # Approximate reference max values for normalised comparison (WHO guidelines, µg/m³)
    _REFERENCE_MAX = {
        'pm25': 25.0,
        'pm10': 50.0,
        'no2': 200.0,
        'o3': 120.0,
        'co': 10000.0,
    }

    def get(self, request: Request) -> Response:
        city = request.query_params.get('city')
        base_qs = PollutionMeasurement.objects.all()
        if city:
            base_qs = base_qs.filter(sensor__city__icontains=city)

        # --- Highest pollution zones ---
        zone_stats = (
            base_qs.values(
                'sensor__city', 'pollutant'
            )
            .annotate(avg_value=Avg('value'), measurement_count=Count('id'))
            .order_by('sensor__city', '-avg_value')
        )

        # Normalise each pollutant value to 0-1 scale and pick max per city
        city_pollution: dict[str, dict] = defaultdict(
            lambda: {'city': '', 'max_normalised': 0.0, 'dominant_pollutant': '', 'details': []}
        )
        for row in zone_stats:
            city = row['sensor__city']
            pollutant = row['pollutant']
            avg_value = row['avg_value'] or 0.0
            ref_max = self._REFERENCE_MAX.get(pollutant, 1.0)
            normalised = round(avg_value / ref_max, 4)

            city_pollution[city]['city'] = city
            city_pollution[city]['details'].append(
                {
                    'pollutant': pollutant,
                    'avg_value': round(avg_value, 4),
                    'measurement_count': row['measurement_count'],
                    'normalised_index': normalised,
                }
            )
            if normalised > city_pollution[city]['max_normalised']:
                city_pollution[city]['max_normalised'] = normalised
                city_pollution[city]['dominant_pollutant'] = pollutant

        highest_pollution_zones = sorted(
            city_pollution.values(),
            key=lambda x: x['max_normalised'],
            reverse=True,
        )

        # --- Daily variation (hourly averages per pollutant) ---
        hourly_qs = (
            base_qs
            .annotate(hour=ExtractHour('timestamp'))
            .values('pollutant', 'hour')
            .annotate(avg_value=Avg('value'), sample_count=Count('id'))
            .order_by('pollutant', 'hour')
        )
        daily_variation: dict[str, list[dict]] = defaultdict(list)
        for row in hourly_qs:
            daily_variation[row['pollutant']].append(
                {
                    'hour': row['hour'],
                    'avg_value': round(row['avg_value'] or 0.0, 4),
                    'sample_count': row['sample_count'],
                }
            )
        daily_variation = dict(daily_variation)

        # --- Dominant pollutants per city ---
        dominant_pollutants: list[dict] = [
            {
                'city': data['city'],
                'dominant_pollutant': data['dominant_pollutant'],
                'normalised_index': data['max_normalised'],
            }
            for data in highest_pollution_zones
            if data['dominant_pollutant']
        ]

        # --- Anomaly episodes: value > 2x the daily mean for that pollutant ---
        # Compute daily mean per pollutant
        daily_means_qs = (
            base_qs.values('pollutant')
            .annotate(daily_mean=Avg('value'))
        )
        daily_means: dict[str, float] = {
            row['pollutant']: row['daily_mean'] or 0.0
            for row in daily_means_qs
        }

        # Build a filter for each pollutant where value > 2 * daily_mean
        anomaly_filter = Q()
        for pollutant, mean_val in daily_means.items():
            if mean_val > 0:
                anomaly_filter |= Q(pollutant=pollutant, value__gt=2 * mean_val)

        anomaly_episodes_data: list[dict] = []
        if anomaly_filter:
            anomalies = (
                base_qs.filter(anomaly_filter)
                .select_related('sensor')
                .order_by('-value')[:100]
            )
            anomaly_episodes_data = [
                {
                    'sensor_id': m.sensor.sensor_id,
                    'city': m.sensor.city,
                    'pollutant': m.pollutant,
                    'value': m.value,
                    'unit': m.unit,
                    'timestamp': m.timestamp.isoformat(),
                    'daily_mean': round(daily_means.get(m.pollutant, 0.0), 4),
                    'ratio_to_mean': round(
                        m.value / daily_means[m.pollutant]
                        if daily_means.get(m.pollutant)
                        else 0.0,
                        2,
                    ),
                }
                for m in anomalies
            ]

        return Response(
            {
                'highest_pollution_zones': highest_pollution_zones,
                'daily_variation': daily_variation,
                'dominant_pollutants': dominant_pollutants,
                'anomaly_episodes': {
                    'threshold': '2x daily mean per pollutant',
                    'daily_means': {k: round(v, 4) for k, v in daily_means.items()},
                    'episodes': anomaly_episodes_data,
                },
            }
        )
