import logging

from django.db.models import Avg, Count, OuterRef, Subquery
from django.db.models.functions import ExtractHour
from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mobility.models import BikeNetwork, BikeSnapshot, BikeStation
from apps.mobility.serializers import (
    BikeNetworkSerializer,
    BikeSnapshotSerializer,
    BikeStationSerializer,
)

logger = logging.getLogger(__name__)


class BikeNetworkViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve French bike networks."""

    serializer_class = BikeNetworkSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return BikeNetwork.objects.filter(country='FR').order_by('city', 'name')


class BikeStationViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve bike stations, with optional network/city filtering."""

    serializer_class = BikeStationSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = BikeStation.objects.select_related('network').order_by(
            'network__city', 'station_name'
        )

        network_id = self.request.query_params.get('network_id')
        if network_id:
            qs = qs.filter(network__network_id=network_id)

        city = self.request.query_params.get('city')
        if city:
            qs = qs.filter(network__city__icontains=city)

        return qs


class BikeSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """
    List bike snapshots.

    By default returns the latest snapshot per station.
    Pass ?station_id=<id> to filter by a specific station.
    Pass ?all=true to retrieve all snapshots without the latest-per-station constraint.
    """

    serializer_class = BikeSnapshotSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        station_id = self.request.query_params.get('station_id')
        all_snapshots = self.request.query_params.get('all', '').lower() == 'true'

        qs = BikeSnapshot.objects.select_related(
            'station', 'station__network'
        ).order_by('-timestamp')

        if station_id:
            qs = qs.filter(station__station_id=station_id)

        if not all_snapshots:
            # Subquery: latest snapshot id per station
            latest_subquery = (
                BikeSnapshot.objects.filter(station=OuterRef('station'))
                .order_by('-timestamp')
                .values('id')[:1]
            )
            qs = qs.filter(id__in=Subquery(latest_subquery))

        return qs


class BikeStatsView(APIView):
    """
    Aggregate statistics across all bike stations.

    Returns:
    - top_10_busiest: stations with highest average bikes_available
    - geographic_imbalance: cities where avg occupancy deviates most from overall mean
    - hourly_usage_peaks: per-hour average bikes_available across all stations
    """

    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        city = request.query_params.get('city')
        base_qs = BikeSnapshot.objects.all()
        if city:
            base_qs = base_qs.filter(station__network__city__icontains=city)

        # --- Top 10 busiest stations (by average bikes available) ---
        top_10 = (
            base_qs.values(
                'station__station_id',
                'station__station_name',
                'station__network__city',
                'station__latitude',
                'station__longitude',
            )
            .annotate(avg_bikes=Avg('bikes_available'))
            .order_by('-avg_bikes')[:10]
        )

        top_10_data = [
            {
                'station_id': row['station__station_id'],
                'station_name': row['station__station_name'],
                'city': row['station__network__city'],
                'latitude': row['station__latitude'],
                'longitude': row['station__longitude'],
                'avg_bikes_available': round(row['avg_bikes'] or 0, 2),
            }
            for row in top_10
        ]

        # --- Geographic imbalance ---
        # Average occupancy ratio (bikes_available / total_slots) per city
        city_stats = (
            base_qs.filter(total_slots__gt=0)
            .values('station__network__city')
            .annotate(avg_bikes=Avg('bikes_available'), avg_total=Avg('total_slots'))
        )

        city_occupancy: list[dict] = []
        for row in city_stats:
            city = row['station__network__city']
            avg_bikes = row['avg_bikes'] or 0
            avg_total = row['avg_total'] or 1
            occupancy_ratio = round(avg_bikes / avg_total, 4)
            city_occupancy.append({'city': city, 'occupancy_ratio': occupancy_ratio})

        if city_occupancy:
            overall_mean = sum(c['occupancy_ratio'] for c in city_occupancy) / len(
                city_occupancy
            )
            for entry in city_occupancy:
                entry['deviation_from_mean'] = round(
                    entry['occupancy_ratio'] - overall_mean, 4
                )
            city_occupancy.sort(key=lambda x: abs(x['deviation_from_mean']), reverse=True)
        else:
            overall_mean = 0.0

        geographic_imbalance = {
            'overall_mean_occupancy': round(overall_mean, 4),
            'cities': city_occupancy,
        }

        # --- Hourly usage peaks ---
        # Average bikes_available grouped by hour-of-day, computed in the database.
        # Previously this pulled every row into Python memory; the ORM aggregation
        # below performs the grouping in SQL, eliminating the full table scan.
        hourly_qs = (
            base_qs
            .annotate(hour=ExtractHour('timestamp'))
            .values('hour')
            .annotate(
                avg_bikes_available=Avg('bikes_available'),
                sample_count=Count('id'),
            )
            .order_by('hour')
        )

        hourly_peaks = [
            {
                'hour': row['hour'],
                'avg_bikes_available': round(row['avg_bikes_available'] or 0, 2),
                'sample_count': row['sample_count'],
            }
            for row in hourly_qs
        ]

        return Response(
            {
                'top_10_busiest_stations': top_10_data,
                'geographic_imbalance': geographic_imbalance,
                'hourly_usage_peaks': hourly_peaks,
            }
        )
