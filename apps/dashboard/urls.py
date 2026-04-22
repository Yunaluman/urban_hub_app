from django.urls import path

from .views import (
    AirPageView,
    ClimatePageView,
    CrossAnalysisPageView,
    DashboardView,
    MobilityPageView,
)

app_name = 'dashboard'

urlpatterns = [
    path('', DashboardView.as_view(), name='index'),
    path('dashboard/climate/', ClimatePageView.as_view(), name='climate'),
    path('dashboard/air/', AirPageView.as_view(), name='air'),
    path('dashboard/mobility/', MobilityPageView.as_view(), name='mobility'),
    path('dashboard/cross/', CrossAnalysisPageView.as_view(), name='cross'),
]
