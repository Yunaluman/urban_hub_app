from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.dashboard.urls')),
    path('api/dashboard/', include('apps.dashboard.api_urls')),
    path('api/meteo/', include('apps.meteo.urls')),
    path('api/mobility/', include('apps.mobility.urls')),
    path('api/pollution/', include('apps.pollution.urls')),
    path('api/analysis/', include('apps.analysis.urls')),
    path('api/guide/', include('apps.urban_guide.urls')),
]
