from django.contrib import admin

from .models import CrossAnalysisResult


@admin.register(CrossAnalysisResult)
class CrossAnalysisResultAdmin(admin.ModelAdmin):
    list_display = ('analysis_type', 'city', 'period_start', 'period_end', 'created_at')
    list_filter = ('analysis_type', 'city')
    search_fields = ('city',)
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
