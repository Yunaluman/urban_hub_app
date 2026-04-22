from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.pollution.views import (
    PollutionMeasurementViewSet,
    PollutionSensorViewSet,
    PollutionStatsView,
)

router = DefaultRouter()
router.register(r'sensors', PollutionSensorViewSet, basename='pollutionsensor')
router.register(r'measurements', PollutionMeasurementViewSet, basename='pollutionmeasurement')

urlpatterns = [
    path('', include(router.urls)),
    path('stats/', PollutionStatsView.as_view(), name='pollution-stats'),
]
