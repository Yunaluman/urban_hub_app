from django.urls import path
from .views import CorrelationMatrixView, CrossAnalysisView

app_name = 'analysis'

urlpatterns = [
    path('', CrossAnalysisView.as_view(), name='cross-analysis'),
    path('correlation/', CorrelationMatrixView.as_view(), name='correlation-matrix'),
]
