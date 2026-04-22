from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import WeatherObservationViewSet, WeatherStationViewSet, WeatherStatsView

router = DefaultRouter()
router.register(r'stations', WeatherStationViewSet, basename='weather-station')
router.register(r'observations', WeatherObservationViewSet, basename='weather-observation')

urlpatterns = [
    path('', include(router.urls)),
    path('stats/', WeatherStatsView.as_view(), name='weather-stats'),
]
