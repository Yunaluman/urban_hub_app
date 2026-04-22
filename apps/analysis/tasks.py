import logging
import math
from datetime import timedelta

import pandas as pd
from celery import shared_task
from django.utils import timezone

from apps.meteo.models import WeatherObservation
from apps.mobility.models import BikeSnapshot
from apps.pollution.models import PollutionMeasurement
from apps.utils.lake import upload_to_lake
from .models import CrossAnalysisResult

logger = logging.getLogger(__name__)

CITY = 'Paris'

def _json_matrix(df: pd.DataFrame) -> dict:
    """Build a JSON-safe matrix payload from a numeric DataFrame.

    Postgres JSON does not accept NaN/Infinity tokens. Pandas/NumPy float
    dtypes will re-coerce None back to NaN, so we sanitize at Python level.
    """
    data: list[list[float | None]] = []
    for row in df.to_numpy().tolist():
        out_row: list[float | None] = []
        for v in row:
            if v is None:
                out_row.append(None)
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                out_row.append(None)
                continue
            if math.isnan(fv) or math.isinf(fv):
                out_row.append(None)
            else:
                out_row.append(round(fv, 4))
        data.append(out_row)
    return {
        'columns': df.columns.tolist(),
        'index': df.index.tolist(),
        'data': data,
    }


def _floor_to_hour(dt):
    """Truncate a datetime to the hour boundary."""
    return dt.replace(minute=0, second=0, microsecond=0)


@shared_task(name='apps.analysis.tasks.run_cross_analysis', bind=True, max_retries=3)
def run_cross_analysis(self):
    """
    Hourly Celery task that performs cross-domain correlation analysis:
      1. Météo <-> Pollution  (correlation matrix)
      2. Météo <-> Mobilité   (rain/wind effect on bike usage)
    Results are persisted as CrossAnalysisResult records.
    """
    now = timezone.now()
    period_end = _floor_to_hour(now)
    period_start = period_end - timedelta(hours=24)

    timestamp_str = period_end.strftime('%Y%m%d_%H%M%S')

    try:
        _analyse_meteo_pollution(period_start, period_end, timestamp_str)
        _analyse_meteo_mobility(period_start, period_end, timestamp_str)
        logger.info('Cross-analysis completed successfully for %s – %s', period_start, period_end)
    except Exception as exc:
        logger.exception('Cross-analysis failed: %s', exc)
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Query 1 — Météo × Pollution correlation
# ---------------------------------------------------------------------------

def _analyse_meteo_pollution(period_start, period_end, timestamp_str=''):
    """
    Join WeatherObservation and PollutionMeasurement by hourly timestamp for
    Paris, then compute a Pearson correlation matrix across all numeric columns.
    """
    # --- Fetch meteo observations ---
    meteo_qs = (
        WeatherObservation.objects
        .filter(
            timestamp__gte=period_start,
            timestamp__lt=period_end,
            station__city__icontains=CITY,
        )
        .values('timestamp', 'temperature', 'wind_speed', 'precipitation', 'pressure')
    )

    # --- Fetch pollution measurements (pivot pollutants as columns) ---
    pollution_qs = (
        PollutionMeasurement.objects
        .filter(
            timestamp__gte=period_start,
            timestamp__lt=period_end,
            sensor__city__icontains=CITY,
        )
        .values('timestamp', 'pollutant', 'value')
    )

    if not meteo_qs.exists() or not pollution_qs.exists():
        logger.warning('Meteo/pollution: insufficient data for period %s – %s', period_start, period_end)
        return

    df_meteo = pd.DataFrame(list(meteo_qs))
    df_meteo['hour'] = pd.to_datetime(df_meteo['timestamp']).dt.floor('h')
    df_meteo_hourly = (
        df_meteo
        .groupby('hour')[['temperature', 'wind_speed', 'precipitation', 'pressure']]
        .mean()
    )

    df_pollution = pd.DataFrame(list(pollution_qs))
    df_pollution['hour'] = pd.to_datetime(df_pollution['timestamp']).dt.floor('h')
    df_pollution_pivot = (
        df_pollution
        .groupby(['hour', 'pollutant'])['value']
        .mean()
        .unstack(level='pollutant')
    )

    df_joined = df_meteo_hourly.join(df_pollution_pivot, how='inner').dropna()

    if df_joined.empty:
        logger.warning('Meteo/pollution join produced no rows for %s – %s', period_start, period_end)
        return

    corr_matrix = df_joined.corr(method='pearson').round(4)

    result_json = _json_matrix(corr_matrix)
    result_json['n_samples'] = len(df_joined)
    CrossAnalysisResult.objects.create(
        analysis_type='meteo_pollution',
        period_start=period_start,
        period_end=period_end,
        city=CITY,
        result_json=result_json,
    )

    # ── Gold : matrice de corrélation aplatie ────────────────────────────────
    try:
        df_gold = corr_matrix.reset_index().rename(columns={'index': 'variable'})
        df_gold.insert(0, 'analysis_type', 'meteo_pollution')
        df_gold.insert(1, 'city', CITY)
        df_gold.insert(2, 'period_start', period_start)
        df_gold.insert(3, 'period_end', period_end)
        df_gold.insert(4, 'n_samples', len(df_joined))
        ts = timestamp_str or period_end.strftime('%Y%m%d_%H%M%S')
        upload_to_lake(df=df_gold, bucket='urbanhub-analytics',
                       path=f'analysis/cross/gold/meteo_pollution_{ts}.parquet')
    except Exception as exc:
        logger.error('Erreur upload Gold meteo_pollution: %s', exc)

    logger.info('Stored meteo_pollution correlation (%d samples)', len(df_joined))


# ---------------------------------------------------------------------------
# Query 2 — Météo × Mobilité (rain/wind → bike usage)
# ---------------------------------------------------------------------------

def _analyse_meteo_mobility(period_start, period_end, timestamp_str=''):
    """
    Join WeatherObservation and BikeSnapshot by hourly timestamp for Paris.
    Computes correlation between precipitation/wind_speed and average bike
    availability, and the mean bike availability grouped by rain/wind quartiles.
    """
    meteo_qs = (
        WeatherObservation.objects
        .filter(
            timestamp__gte=period_start,
            timestamp__lt=period_end,
            station__city__icontains=CITY,
        )
        .values('timestamp', 'temperature', 'wind_speed', 'precipitation')
    )

    mobility_qs = (
        BikeSnapshot.objects
        .filter(
            timestamp__gte=period_start,
            timestamp__lt=period_end,
            station__network__city__icontains=CITY,
        )
        .values('timestamp', 'bikes_available', 'total_slots')
    )

    if not meteo_qs.exists() or not mobility_qs.exists():
        logger.warning('Meteo/mobility: insufficient data for period %s – %s', period_start, period_end)
        return

    df_meteo = pd.DataFrame(list(meteo_qs))
    df_meteo['hour'] = pd.to_datetime(df_meteo['timestamp']).dt.floor('h')
    df_meteo_hourly = (
        df_meteo
        .groupby('hour')[['temperature', 'wind_speed', 'precipitation']]
        .mean()
    )

    df_mobility = pd.DataFrame(list(mobility_qs))
    df_mobility['hour'] = pd.to_datetime(df_mobility['timestamp']).dt.floor('h')
    df_mobility['utilization'] = df_mobility.apply(
        lambda row: (row['bikes_available'] / row['total_slots'] * 100)
        if row['total_slots'] > 0 else 0,
        axis=1,
    )
    df_mobility_hourly = df_mobility.groupby('hour')['utilization'].mean().rename('bike_utilization')

    df_joined = df_meteo_hourly.join(df_mobility_hourly, how='inner').dropna()

    if df_joined.empty:
        logger.warning('Meteo/mobility join produced no rows for %s – %s', period_start, period_end)
        return

    corr = df_joined[['precipitation', 'wind_speed', 'temperature', 'bike_utilization']].corr(
        method='pearson'
    ).round(4)

    # Classify rainy hours (precipitation > 1 mm) and high-wind hours (> 5 m/s)
    df_joined['rainy'] = df_joined['precipitation'] > 1.0
    df_joined['high_wind'] = df_joined['wind_speed'] > 5.0

    rain_effect = (
        df_joined.groupby('rainy')['bike_utilization'].mean().round(2).to_dict()
    )
    wind_effect = (
        df_joined.groupby('high_wind')['bike_utilization'].mean().round(2).to_dict()
    )

    # Serialise boolean keys to strings for JSON compatibility
    rain_effect = {str(k): v for k, v in rain_effect.items()}
    wind_effect = {str(k): v for k, v in wind_effect.items()}

    CrossAnalysisResult.objects.create(
        analysis_type='meteo_mobility',
        period_start=period_start,
        period_end=period_end,
        city=CITY,
        result_json={
            'correlation': _json_matrix(corr),
            'rain_effect_on_bikes': rain_effect,
            'wind_effect_on_bikes': wind_effect,
            'n_samples': len(df_joined),
        },
    )

    # ── Gold : corrélations + effets pluie/vent ──────────────────────────────
    try:
        df_corr = corr.reset_index().rename(columns={'index': 'variable'})
        df_corr.insert(0, 'analysis_type', 'meteo_mobility')
        df_corr.insert(1, 'city', CITY)
        df_corr.insert(2, 'period_start', period_start)
        df_corr.insert(3, 'period_end', period_end)
        df_corr.insert(4, 'n_samples', len(df_joined))

        df_effects = pd.DataFrame([
            {'metric': 'rain_effect_bikes_dry',  'value': rain_effect.get('False', None)},
            {'metric': 'rain_effect_bikes_rain',  'value': rain_effect.get('True', None)},
            {'metric': 'wind_effect_bikes_calm',  'value': wind_effect.get('False', None)},
            {'metric': 'wind_effect_bikes_windy', 'value': wind_effect.get('True', None)},
        ])
        df_effects.insert(0, 'analysis_type', 'meteo_mobility')
        df_effects.insert(1, 'city', CITY)
        df_effects.insert(2, 'period_start', period_start)
        df_effects.insert(3, 'period_end', period_end)

        ts = timestamp_str or period_end.strftime('%Y%m%d_%H%M%S')
        upload_to_lake(df=df_corr, bucket='urbanhub-analytics',
                       path=f'analysis/cross/gold/meteo_mobility_corr_{ts}.parquet')
        upload_to_lake(df=df_effects, bucket='urbanhub-analytics',
                       path=f'analysis/cross/gold/meteo_mobility_effects_{ts}.parquet')
    except Exception as exc:
        logger.error('Erreur upload Gold meteo_mobility: %s', exc)

    logger.info('Stored meteo_mobility correlation (%d samples)', len(df_joined))
