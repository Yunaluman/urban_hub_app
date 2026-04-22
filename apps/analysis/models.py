from django.db import models


class CrossAnalysisResult(models.Model):
    ANALYSIS_TYPE_CHOICES = [
        ('meteo_pollution', 'Météo / Pollution'),
        ('meteo_mobility', 'Météo / Mobilité'),
    ]

    analysis_type = models.CharField(
        max_length=32,
        choices=ANALYSIS_TYPE_CHOICES,
        db_index=True,
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    result_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    city = models.CharField(max_length=128, default='Paris')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Résultat d\'analyse croisée'
        verbose_name_plural = 'Résultats d\'analyses croisées'

    def __str__(self):
        return f"{self.get_analysis_type_display()} | {self.city} | {self.created_at:%Y-%m-%d %H:%M}"
