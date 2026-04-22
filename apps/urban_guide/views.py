"""
DRF views for the UrbanGuide feature module.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .urban_score import compute_urban_score
from .outfit_advisor import get_outfit_advice
from .activity_suggester import suggest_activities
from .paris_events import fetch_paris_events
from apps.meteo.models import WeatherObservation


class UrbanScoreView(APIView):
    """
    GET /api/guide/urban-score/

    Returns the composite urban quality score for Paris (or ?city= override).
    """

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        data = compute_urban_score(city=city)
        return Response(data, status=status.HTTP_200_OK)


class OutfitAdvisorView(APIView):
    """
    GET /api/guide/outfit/

    Returns clothing recommendations based on current weather and pollution.
    Optional query param: ?city=
    """

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        data = get_outfit_advice(city=city)
        return Response(data, status=status.HTTP_200_OK)


class ActivitySuggesterView(APIView):
    """
    GET /api/guide/activities/

    Returns activity suggestions based on weather, pollution, and bike availability.
    Optional query param: ?city=
    """

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        data = suggest_activities(city=city)
        return Response(data, status=status.HTTP_200_OK)


class UrbanDashboardView(APIView):
    """
    GET /api/guide/dashboard-data/

    Aggregates all UrbanGuide signals into a single response for the frontend:
      - urban_score    : composite city score
      - outfit         : clothing advice
      - activities     : activity suggestions
      - weather        : latest weather summary
      - events         : today/tomorrow Paris events
    """

    def get(self, request):
        city = request.query_params.get('city', 'Paris')

        urban_score = compute_urban_score(city=city)
        outfit = get_outfit_advice(city=city)
        activities = suggest_activities(city=city)
        events = fetch_paris_events()

        # Latest weather summary (lightweight — avoid re-querying in each service)
        obs = (
            WeatherObservation.objects
            .filter(station__city__iexact=city)
            .select_related('station')
            .first()
        )
        weather_summary = None
        if obs:
            weather_summary = {
                'temperature': obs.temperature,
                'wind_speed': obs.wind_speed,
                'precipitation': obs.precipitation,
                'visibility': obs.visibility,
                'pressure': obs.pressure,
                'timestamp': obs.timestamp.isoformat(),
                'station': obs.station.name or obs.station.station_id,
            }

        payload = {
            'city': city,
            'urban_score': urban_score,
            'outfit': outfit,
            'activities': activities,
            'weather': weather_summary,
            'events': events,
        }

        return Response(payload, status=status.HTTP_200_OK)
