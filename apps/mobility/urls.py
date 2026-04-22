from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.mobility.views import (
    BikeNetworkViewSet,
    BikeSnapshotViewSet,
    BikeStationViewSet,
    BikeStatsView,
)

router = DefaultRouter()
router.register(r'networks', BikeNetworkViewSet, basename='bikenetwork')
router.register(r'stations', BikeStationViewSet, basename='bikestation')
router.register(r'snapshots', BikeSnapshotViewSet, basename='bikesnapshot')

urlpatterns = [
    path('', include(router.urls)),
    path('stats/', BikeStatsView.as_view(), name='bike-stats'),
]
