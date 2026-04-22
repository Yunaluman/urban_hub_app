from django.db.models import Avg, Count, Q
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework import filters, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import WeatherObservation, WeatherStation
from .serializers import (
    WeatherObservationListSerializer,
    WeatherObservationSerializer,
    WeatherStationSerializer,
)


def _parse_date_param(value: str, param_name: str):
    """Parse an ISO 8601 date or datetime string.

    Returns a parsed object on success.
    Raises ValueError with a descriptive message on failure.
    """
    parsed = parse_datetime(value) or parse_date(value)
    if parsed is None:
        raise ValueError(
            f"Invalid value for '{param_name}': '{value}'. "
            "Expected ISO 8601 date (YYYY-MM-DD) or datetime."
        )
    return parsed


def _build_observation_queryset(query_params):
    """Apply common filters to WeatherObservation querysets.

    Raises ValueError if date params cannot be parsed.
    """
    queryset = WeatherObservation.objects.all()

    station_id = query_params.get('station_id')
    if station_id:
        queryset = queryset.filter(station__station_id=station_id)

    start_date = query_params.get('start_date')
    if start_date:
        queryset = queryset.filter(timestamp__gte=_parse_date_param(start_date, 'start_date'))

    end_date = query_params.get('end_date')
    if end_date:
        queryset = queryset.filter(timestamp__lte=_parse_date_param(end_date, 'end_date'))

    return queryset


class WeatherStationViewSet(viewsets.ReadOnlyModelViewSet):
    """List all French weather stations, optionally filtered by city.

    Query parameters:
        city (str): Filter stations by city name (case-insensitive, partial match).
    """

    serializer_class = WeatherStationSerializer
    permission_classes = [AllowAny]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'station_id', 'city']
    ordering_fields = ['station_id', 'name', 'city']
    ordering = ['station_id']

    def get_queryset(self):
        queryset = WeatherStation.objects.all()
        city = self.request.query_params.get('city')
        if city:
            queryset = queryset.filter(city__icontains=city)
        return queryset


class WeatherObservationViewSet(viewsets.ReadOnlyModelViewSet):
    """List weather observations with optional filters.

    Query parameters:
        station_id (str):  Filter by the station's station_id field.
        start_date (str):  ISO 8601 date/datetime — lower bound on timestamp.
        end_date   (str):  ISO 8601 date/datetime — upper bound on timestamp.
    """

    permission_classes = [AllowAny]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['timestamp', 'temperature', 'wind_speed']
    ordering = ['-timestamp']

    def get_serializer_class(self):
        if self.action == 'list':
            return WeatherObservationListSerializer
        return WeatherObservationSerializer

    def get_queryset(self):
        try:
            return _build_observation_queryset(
                self.request.query_params
            ).select_related('station')
        except ValueError as exc:
            # Raising ValidationError here causes DRF to return 400 automatically.
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': str(exc)})


class WeatherStatsView(APIView):
    """Aggregated meteorological statistics.

    Returns:
        avg_temp_by_month:    Average temperature grouped by (year, month).
        extreme_days:         Days where temperature exceeded 35 °C or dropped
                              below -5 °C, with counts per category.
        temp_visibility_corr: Pearson correlation coefficient between temperature
                              and visibility computed in Python from the DB values.

    Query parameters (all optional):
        station_id (str):  Restrict stats to a single station.
        start_date (str):  ISO 8601 lower bound on timestamp.
        end_date   (str):  ISO 8601 upper bound on timestamp.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            queryset = _build_observation_queryset(request.query_params)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        city = request.query_params.get('city')
        if city:
            queryset = queryset.filter(station__city__icontains=city)

        # --- Average temperature by month ---
        avg_temp_by_month = (
            queryset
            .values('timestamp__year', 'timestamp__month')
            .annotate(avg_temperature=Avg('temperature'))
            .order_by('timestamp__year', 'timestamp__month')
        )

        avg_temp_data = [
            {
                'year': row['timestamp__year'],
                'month': row['timestamp__month'],
                'avg_temperature': (
                    round(row['avg_temperature'], 2)
                    if row['avg_temperature'] is not None
                    else None
                ),
            }
            for row in avg_temp_by_month
        ]

        # --- Extreme days ---
        hot_days = (
            queryset
            .filter(temperature__gt=35)
            .values('timestamp__date')
            .annotate(count=Count('id'))
            .count()
        )
        cold_days = (
            queryset
            .filter(temperature__lt=-5)
            .values('timestamp__date')
            .annotate(count=Count('id'))
            .count()
        )

        extreme_days = {
            'hot_days_above_35c': hot_days,
            'cold_days_below_minus5c': cold_days,
        }

        # --- Temperature / visibility correlation ---
        pairs = list(
            queryset
            .filter(
                Q(temperature__isnull=False) & Q(visibility__isnull=False)
            )
            .values_list('temperature', 'visibility')
        )

        correlation = self._pearson_correlation(pairs)

        return Response({
            'avg_temp_by_month': avg_temp_data,
            'extreme_days': extreme_days,
            'temp_visibility_correlation': correlation,
        })

    @staticmethod
    def _pearson_correlation(pairs: list) -> float | None:
        """Compute the Pearson correlation coefficient for a list of (x, y) pairs."""
        n = len(pairs)
        if n < 2:
            return None

        x_vals = [p[0] for p in pairs]
        y_vals = [p[1] for p in pairs]

        mean_x = sum(x_vals) / n
        mean_y = sum(y_vals) / n

        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals))
        denom_x = sum((x - mean_x) ** 2 for x in x_vals) ** 0.5
        denom_y = sum((y - mean_y) ** 2 for y in y_vals) ** 0.5

        if denom_x == 0 or denom_y == 0:
            return None

        return round(numerator / (denom_x * denom_y), 4)
