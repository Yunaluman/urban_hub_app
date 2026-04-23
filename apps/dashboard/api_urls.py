from django.urls import path

from apps.dashboard import api_views

app_name = 'dashboard_api'

urlpatterns = [
    path('cities/', api_views.CitiesListView.as_view(), name='cities'),
    path('summary/<str:page>/', api_views.DashboardPageSummaryView.as_view(), name='page-summary'),
    path('climate/temperature/', api_views.ClimateTemperatureView.as_view(), name='climate-temperature'),
    path('climate/seasonal/', api_views.ClimateSeasonalView.as_view(), name='climate-seasonal'),
    path('climate/extremes/', api_views.ClimateExtremesView.as_view(), name='climate-extremes'),
    path('climate/visibility/', api_views.ClimateVisibilityView.as_view(), name='climate-visibility'),
    path('air/who/', api_views.AirWhoView.as_view(), name='air-who'),
    path('air/series/', api_views.AirSeriesView.as_view(), name='air-series'),
    path('air/hourly/', api_views.AirHourlyView.as_view(), name='air-hourly'),
    path('air/hotspots/', api_views.AirHotspotsView.as_view(), name='air-hotspots'),
    path('mobility/stations/', api_views.MobilityStationsView.as_view(), name='mobility-stations'),
    path('mobility/hourly/', api_views.MobilityHourlyView.as_view(), name='mobility-hourly'),
    path('mobility/critical/', api_views.MobilityCriticalView.as_view(), name='mobility-critical'),
    path('cross/data/', api_views.CrossAnalysisDataView.as_view(), name='cross-data'),
]
