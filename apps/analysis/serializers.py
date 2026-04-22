from rest_framework import serializers
from .models import CrossAnalysisResult


class CrossAnalysisResultSerializer(serializers.ModelSerializer):
    analysis_type_display = serializers.CharField(
        source='get_analysis_type_display',
        read_only=True,
    )

    class Meta:
        model = CrossAnalysisResult
        fields = [
            'id',
            'analysis_type',
            'analysis_type_display',
            'period_start',
            'period_end',
            'result_json',
            'created_at',
            'city',
        ]
        read_only_fields = ['id', 'created_at', 'analysis_type_display']
