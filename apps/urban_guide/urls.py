from django.urls import path

from .views import (
    UrbanScoreView,
    OutfitAdvisorView,
    ActivitySuggesterView,
    UrbanDashboardView,
)

urlpatterns = [
    path('urban-score/', UrbanScoreView.as_view(), name='urban-score'),
    path('outfit/', OutfitAdvisorView.as_view(), name='outfit-advisor'),
    path('activities/', ActivitySuggesterView.as_view(), name='activity-suggester'),
    path('dashboard-data/', UrbanDashboardView.as_view(), name='urban-dashboard'),
]
