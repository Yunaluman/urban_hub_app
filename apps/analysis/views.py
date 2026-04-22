import logging

from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CrossAnalysisResult
from .serializers import CrossAnalysisResultSerializer

logger = logging.getLogger(__name__)


class CrossAnalysisView(APIView):
    """
    GET /api/analysis/
    Returns the most recent CrossAnalysisResult for each analysis type.
    """

    def get(self, request):
        results = []
        for analysis_type, _ in CrossAnalysisResult.ANALYSIS_TYPE_CHOICES:
            record = (
                CrossAnalysisResult.objects
                .filter(analysis_type=analysis_type)
                .order_by('-created_at')
                .first()
            )
            if record:
                results.append(record)

        serializer = CrossAnalysisResultSerializer(results, many=True)
        return Response({
            'count': len(results),
            'results': serializer.data,
        })


class CorrelationMatrixView(APIView):
    """
    GET /api/analysis/correlation/
    Returns the latest météo/pollution correlation matrix formatted for Plotly.
    """

    def get(self, request):
        record = (
            CrossAnalysisResult.objects
            .filter(analysis_type='meteo_pollution')
            .order_by('-created_at')
            .first()
        )

        if record is None:
            return Response(
                {'detail': 'Aucune analyse météo/pollution disponible.'},
                status=404,
            )

        payload = record.result_json
        columns = payload.get('columns', [])
        data_rows = payload.get('data', [])
        n_samples = payload.get('n_samples', 0)

        # Build a Plotly-ready heatmap trace structure
        plotly_data = {
            'type': 'heatmap',
            'x': columns,
            'y': columns,
            'z': data_rows,
            'colorscale': 'RdBu',
            'zmid': 0,
            'text': data_rows,
            'hoverongaps': False,
        }

        plotly_layout = {
            'title': 'Corrélation Météo / Pollution',
            'xaxis': {'title': ''},
            'yaxis': {'title': '', 'autorange': 'reversed'},
            'margin': {'l': 120, 'r': 40, 't': 60, 'b': 120},
        }

        return Response({
            'period_start': record.period_start,
            'period_end': record.period_end,
            'city': record.city,
            'n_samples': n_samples,
            'plotly': {
                'data': [plotly_data],
                'layout': plotly_layout,
            },
        })
