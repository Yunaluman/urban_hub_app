"""
Requêtes analytiques pour le dashboard — implémentation ORM (Django).

Remplace l'ancienne couche SQL brute portée de src/dashboard/data/db.py.
Chaque fonction renvoie un pd.DataFrame dont les colonnes correspondent
exactement à ce qu'attendait l'ancienne version, sans changer l'interface
consommée par api_views.py.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from django.conf import settings
from django.db.models import (
    Avg,
    Case,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    StdDev,
    Value,
    When,
)
from collections import defaultdict

from django.db.models.functions import ExtractHour, ExtractMonth, ExtractYear, TruncDay

from apps.analysis.models import CrossAnalysisResult
from apps.meteo.models import WeatherObservation, WeatherStation
from apps.serving.constants import GOLD_NATIONAL_CITY_KEY
from apps.mobility.models import BikeSnapshot
from apps.pollution.models import PollutionMeasurement, PollutionSensor


# ─── Sérialisation ───────────────────────────────────────────────────────────

def jsonable_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Sérialise un DataFrame en liste de dicts JSON-compatibles."""
    out: list[dict[str, Any]] = []
    for rec in df.replace({np.nan: None}).to_dict('records'):
        row: dict[str, Any] = {}
        for k, v in rec.items():
            if v is None or (isinstance(v, float) and np.isnan(v)):
                row[k] = None
            elif hasattr(v, 'isoformat'):
                row[k] = v.isoformat()
            elif isinstance(v, Decimal):
                row[k] = float(v)
            elif isinstance(v, (np.integer, np.floating)):
                row[k] = float(v) if isinstance(v, np.floating) else int(v)
            else:
                row[k] = v
        out.append(row)
    return out


def _normalize_daily_date_column(df: pd.DataFrame, column: str = 'date') -> pd.DataFrame:
    """Normalise une clé journalière pandas/ORM vers un objet `date` homogène."""
    if df.empty or column not in df.columns:
        return df
    out = df.copy()
    out[column] = out[column].map(_coerce_to_local_date)
    out = out.dropna(subset=[column])
    return out


def _coerce_to_local_date(value: Any) -> date | None:
    """Convertit une valeur pandas/Python en `date` sans décaler les dates locales."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        return ts.tz_convert('Europe/Paris').date()
    return ts.date()


# ─── Expression réutilisable : taux d'utilisation vélos ──────────────────────

def _util_rate_expr() -> ExpressionWrapper:
    """Taux d'utilisation = bikes_available / total_slots * 100 (0 si total = 0)."""
    return ExpressionWrapper(
        Case(
            When(
                total_slots__gt=0,
                then=F('bikes_available') * Value(100.0) / F('total_slots'),
            ),
            default=Value(0.0),
            output_field=FloatField(),
        ),
        output_field=FloatField(),
    )


def _gold_city_key(city: str) -> str:
    """
    Clé stockée en Postgres Gold pour la ville / agrégat national.

    API : ``''`` ou ``'__all__'`` → agrégat national (``GOLD_NATIONAL_CITY_KEY``).
    """
    s = (city or '').strip()
    if not s or s == '__all__':
        return GOLD_NATIONAL_CITY_KEY
    return s


def _gold_city_key_filter(field: str, city: str) -> Q:
    """Filtre ORM sur un champ ``city_key`` Gold (national ou ville)."""
    ck = _gold_city_key(city)
    if ck == GOLD_NATIONAL_CITY_KEY:
        return Q(**{f'{field}__in': [GOLD_NATIONAL_CITY_KEY, '']})
    return Q(**{f'{field}__iexact': ck}) | Q(**{f'{field}__icontains': ck})


def _city_q(field: str, city: str) -> Q:
    """
    Filtre robuste sur le champ ville.

    Les pipelines d’ingestion peuvent produire des libellés hétérogènes
    (ex. "Paris", "Paris 13e", "PARIS", adresses). On tente d’abord un match
    strict (iexact) puis un fallback (icontains) pour maximiser la couverture.
    """
    city = (city or '').strip()
    if not city:
        return Q()
    return Q(**{f'{field}__iexact': city}) | Q(**{f'{field}__icontains': city})


# ─── Météo ───────────────────────────────────────────────────────────────────

def get_weather_stations() -> pd.DataFrame:
    qs = (
        WeatherStation.objects
        .filter(latitude__isnull=False, longitude__isnull=False)
        .values('station_id', 'name', 'city', 'latitude', 'longitude', 'elevation')
        .order_by('city')
    )
    return pd.DataFrame(list(qs))


def _use_gold_climate_temp() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_CLIMATE', False):
        return False
    from apps.serving.models import GoldClimateTemperatureDay

    return GoldClimateTemperatureDay.objects.exists()


def _use_gold_climate_seasonal() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_CLIMATE', False):
        return False
    from apps.serving.models import GoldClimateSeasonalMonth

    return GoldClimateSeasonalMonth.objects.exists()


def _use_gold_climate_extreme() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_CLIMATE', False):
        return False
    from apps.serving.models import GoldClimateExtremeDay

    return GoldClimateExtremeDay.objects.exists()


def _use_gold_climate_visibility() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_CLIMATE', False):
        return False
    from apps.serving.models import GoldClimateVisibilitySample

    return GoldClimateVisibilitySample.objects.exists()


def _orm_temperature_series(city: str, start: str, end: str) -> pd.DataFrame:
    qs = (
        WeatherObservation.objects.filter(
            _city_q('station__city', city),
            timestamp__date__gte=start,
            timestamp__date__lte=end,
            temperature__isnull=False,
        )
        .annotate(date=TruncDay('timestamp'))
        .values('date')
        .annotate(
            temp_avg=Avg('temperature'),
            temp_min=Min('temperature'),
            temp_max=Max('temperature'),
        )
        .order_by('date')
    )
    return pd.DataFrame(list(qs))


def _orm_seasonal_stats() -> pd.DataFrame:
    qs = (
        WeatherObservation.objects.filter(temperature__isnull=False)
        .annotate(month=ExtractMonth('timestamp'))
        .values('station__city', 'month')
        .annotate(temp_avg=Avg('temperature'))
        .order_by('station__city', 'month')
    )
    df = pd.DataFrame(list(qs))
    if not df.empty and 'station__city' in df.columns:
        df = df.rename(columns={'station__city': 'city'})
    return df


def _orm_extreme_days(std_factor: float = 2.5) -> pd.DataFrame:
    city_stats: dict[str, dict] = {
        row['station__city']: row
        for row in (
            WeatherObservation.objects.filter(temperature__isnull=False)
            .values('station__city')
            .annotate(mean=Avg('temperature'), std=StdDev('temperature'))
        )
    }
    if not city_stats:
        return pd.DataFrame()
    daily_qs = (
        WeatherObservation.objects.filter(temperature__isnull=False)
        .annotate(date=TruncDay('timestamp'))
        .values('station__city', 'date')
        .annotate(temp=Avg('temperature'))
        .order_by('-date')
    )
    rows = []
    for row in daily_qs:
        city = row['station__city']
        stats = city_stats.get(city, {})
        mean = stats.get('mean')
        std = stats.get('std') or 0.0
        if mean is None or std == 0:
            continue
        temp = row['temp']
        if temp > mean + std_factor * std:
            event_type = 'Canicule'
        elif temp < mean - std_factor * std:
            event_type = 'Grand froid'
        else:
            continue
        rows.append({'city': city, 'date': row['date'], 'temp': temp, 'type': event_type})
    return pd.DataFrame(rows)


def _orm_national_extreme_days(std_factor: float = 2.5) -> pd.DataFrame:
    """Extrêmes sur la moyenne journalière nationale (toutes stations)."""
    daily_qs = (
        WeatherObservation.objects.filter(temperature__isnull=False)
        .annotate(date=TruncDay('timestamp'))
        .values('date')
        .annotate(temp=Avg('temperature'))
    )
    df_daily = pd.DataFrame(list(daily_qs))
    if df_daily.empty or len(df_daily) < 3:
        return pd.DataFrame()
    mean = float(df_daily['temp'].mean())
    std = float(df_daily['temp'].std() or 0.0)
    if std == 0:
        return pd.DataFrame()
    rows = []
    for _, row in df_daily.iterrows():
        temp = float(row['temp'])
        if temp > mean + std_factor * std:
            rows.append(
                {
                    'city': GOLD_NATIONAL_CITY_KEY,
                    'date': row['date'],
                    'temp': temp,
                    'type': 'Canicule',
                }
            )
        elif temp < mean - std_factor * std:
            rows.append(
                {
                    'city': GOLD_NATIONAL_CITY_KEY,
                    'date': row['date'],
                    'temp': temp,
                    'type': 'Grand froid',
                }
            )
    return pd.DataFrame(rows)


def _orm_meteo_visibility_corr(city: str) -> pd.DataFrame:
    qs = (
        WeatherObservation.objects.filter(
            _city_q('station__city', city),
            visibility__isnull=False,
            temperature__isnull=False,
        )
        .values('temperature', 'wind_speed', 'pressure', 'precipitation', 'visibility')[:50000]
    )
    return pd.DataFrame(list(qs))


def get_temperature_series(city: str, start: str, end: str) -> pd.DataFrame:
    if _use_gold_climate_temp():
        from apps.serving.models import GoldClimateTemperatureDay

        m_as_of = GoldClimateTemperatureDay.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = (
                GoldClimateTemperatureDay.objects.filter(
                    as_of=m_as_of,
                    day__gte=start,
                    day__lte=end,
                )
                .filter(_gold_city_key_filter('city_key', city))
                .order_by('day')
                .values('day', 'temp_avg', 'temp_min', 'temp_max')
            )
            df = pd.DataFrame(list(qs))
            if not df.empty:
                return df.rename(columns={'day': 'date'})
    return _orm_temperature_series(city, start, end)


def get_seasonal_stats(city: str | None = None) -> pd.DataFrame:
    """
    Saisonnalité : par défaut (``city`` absent) toutes les villes hors agrégat national.

    ``city='__all__'`` ou national : uniquement les 12 lignes de l’agrégat national Gold.
    """
    if _use_gold_climate_seasonal():
        from apps.serving.models import GoldClimateSeasonalMonth

        m_as_of = GoldClimateSeasonalMonth.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = GoldClimateSeasonalMonth.objects.filter(as_of=m_as_of)
            if city is not None and str(city).strip():
                if _gold_city_key(str(city)) == GOLD_NATIONAL_CITY_KEY:
                    qs = qs.filter(
                        Q(city_key=GOLD_NATIONAL_CITY_KEY) | Q(city_key='')
                    )
                else:
                    qs = qs.filter(_gold_city_key_filter('city_key', str(city)))
            else:
                qs = qs.exclude(
                    city_key__in=[GOLD_NATIONAL_CITY_KEY, '']
                )
            qs = qs.order_by('city_key', 'month').values('city_key', 'month', 'temp_avg')
            df = pd.DataFrame(list(qs))
            if not df.empty:
                return df.rename(columns={'city_key': 'city'})
    if city is not None and str(city).strip() and _gold_city_key(str(city)) == GOLD_NATIONAL_CITY_KEY:
        return _orm_national_seasonal_stats()
    df = _orm_seasonal_stats()
    if (
        city is not None
        and str(city).strip()
        and _gold_city_key(str(city)) != GOLD_NATIONAL_CITY_KEY
        and not df.empty
        and 'city' in df.columns
    ):
        cn = str(city).strip().lower()
        df = df[df['city'].astype(str).str.lower() == cn]
    return df


def _orm_national_seasonal_stats() -> pd.DataFrame:
    """Moyenne mensuelle sur toutes les stations (série nationale unique)."""
    qs = (
        WeatherObservation.objects.filter(temperature__isnull=False)
        .annotate(month=ExtractMonth('timestamp'))
        .values('month')
        .annotate(temp_avg=Avg('temperature'))
        .order_by('month')
    )
    df = pd.DataFrame(list(qs))
    if not df.empty:
        df.insert(0, 'city', GOLD_NATIONAL_CITY_KEY)
    return df


def get_extreme_days(std_factor: float = 2.5, city: str | None = None) -> pd.DataFrame:
    """
    Jours extrêmes. ``city`` national (``''`` / ``'__all__'``) : série agrégée nationale.

    Avec Gold actif, le filtre ville s’applique en base ; sinon ORM multi-villes
    puis filtre côté API si besoin.
    """
    ck = _gold_city_key(city or '')
    if _use_gold_climate_extreme():
        from apps.serving.models import GoldClimateExtremeDay

        m_as_of = GoldClimateExtremeDay.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = GoldClimateExtremeDay.objects.filter(
                as_of=m_as_of,
                std_factor=std_factor,
            )
            if city is not None and str(city).strip():
                qs = qs.filter(_gold_city_key_filter('city_key', str(city)))
            df = pd.DataFrame(
                list(qs.values('city_key', 'day', 'temp', 'event_type'))
            )
            if not df.empty:
                return df.rename(
                    columns={
                        'city_key': 'city',
                        'day': 'date',
                        'event_type': 'type',
                    }
                )
    if ck == GOLD_NATIONAL_CITY_KEY:
        return _orm_national_extreme_days(std_factor)
    df = _orm_extreme_days(std_factor)
    if city and not df.empty and 'city' in df.columns:
        cnorm = str(city).strip().lower()
        df = df[df['city'].astype(str).str.lower() == cnorm]
    return df


def get_meteo_visibility_corr(city: str) -> pd.DataFrame:
    if _use_gold_climate_visibility():
        from apps.serving.models import GoldClimateVisibilitySample

        row = (
            GoldClimateVisibilitySample.objects.filter(
                _gold_city_key_filter('city_key', city)
            )
            .order_by('-as_of')
            .first()
        )
        if row and row.points_json:
            return pd.DataFrame(row.points_json)
    return _orm_meteo_visibility_corr(city)


# ─── Pollution ───────────────────────────────────────────────────────────────

# Seuils OMS par polluant (µg/m³)
_WHO_LIMITS: dict[str, float] = {
    'pm25': 15.0,
    'pm10': 45.0,
    'no2': 25.0,
    'o3': 60.0,
}


def _use_gold_air_series() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_AIR', False):
        return False
    from apps.serving.models import GoldAirSeriesDay

    return GoldAirSeriesDay.objects.exists()


def _use_gold_air_hourly() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_AIR', False):
        return False
    from apps.serving.models import GoldAirHourlyPattern

    return GoldAirHourlyPattern.objects.exists()


def _use_gold_air_hotspots() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_AIR', False):
        return False
    from apps.serving.models import GoldAirHotspot

    return GoldAirHotspot.objects.exists()


def _use_gold_air_who() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_AIR', False):
        return False
    from apps.serving.models import GoldAirWhoExceedance

    return GoldAirWhoExceedance.objects.exists()


def get_pollution_sensors() -> pd.DataFrame:
    qs = (
        PollutionSensor.objects
        .filter(latitude__isnull=False, longitude__isnull=False)
        .values('sensor_id', 'city', 'latitude', 'longitude')
    )
    return pd.DataFrame(list(qs))


def _orm_pollution_series(city: str, pollutant: str, start: str, end: str) -> pd.DataFrame:
    qs = (
        PollutionMeasurement.objects
        .filter(
            _city_q('sensor__city', city),
            pollutant=pollutant,
            timestamp__date__gte=start,
            timestamp__date__lte=end,
        )
        .annotate(date=TruncDay('timestamp'))
        .values('date')
        .annotate(value_avg=Avg('value'), value_max=Max('value'))
        .order_by('date')
    )
    return pd.DataFrame(list(qs))


def _orm_pollution_daily_pattern(city: str, pollutant: str) -> pd.DataFrame:
    qs = (
        PollutionMeasurement.objects
        .filter(_city_q('sensor__city', city), pollutant=pollutant)
        .annotate(hour=ExtractHour('timestamp'))
        .values('hour')
        .annotate(value_avg=Avg('value'))
        .order_by('hour')
    )
    return pd.DataFrame(list(qs))


def _orm_pollution_hotspots(pollutant: str, *, include_sensor_id: bool = False) -> pd.DataFrame:
    fields = ['sensor__city', 'sensor__latitude', 'sensor__longitude']
    if include_sensor_id:
        fields.append('sensor__sensor_id')
    qs = (
        PollutionMeasurement.objects.filter(pollutant=pollutant)
        .values(*fields)
        .annotate(
            value_avg=Avg('value'),
            pct_exceedance=Avg(
                Case(
                    When(value__gt=0, then=Value(100.0)),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            ),
        )
    )
    df = pd.DataFrame(list(qs))
    if not df.empty:
        ren = {
            'sensor__city': 'city',
            'sensor__latitude': 'latitude',
            'sensor__longitude': 'longitude',
        }
        if include_sensor_id:
            ren['sensor__sensor_id'] = 'sensor_id'
        df = df.rename(columns=ren)
    return df


def _orm_who_exceedance_days(city: str) -> pd.DataFrame:
    condition = Q()
    for pollutant, limit in _WHO_LIMITS.items():
        condition |= Q(pollutant=pollutant, value__gt=limit)
    qs = (
        PollutionMeasurement.objects.filter(_city_q('sensor__city', city))
        .filter(condition)
        .annotate(day=TruncDay('timestamp'))
        .values('pollutant')
        .annotate(days_exceeded=Count('day', distinct=True))
    )
    return pd.DataFrame(list(qs))


def get_pollution_series(city: str, pollutant: str, start: str, end: str) -> pd.DataFrame:
    if _use_gold_air_series():
        from apps.serving.models import GoldAirSeriesDay

        m_as_of = GoldAirSeriesDay.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = (
                GoldAirSeriesDay.objects.filter(
                    as_of=m_as_of,
                    pollutant=pollutant,
                    observation_date__gte=start,
                    observation_date__lte=end,
                )
                .filter(_gold_city_key_filter('city_key', city))
                .order_by('observation_date')
                .values('observation_date', 'value_avg', 'value_max')
            )
            df = pd.DataFrame(list(qs))
            if not df.empty:
                return df.rename(columns={'observation_date': 'date'})
    return _orm_pollution_series(city, pollutant, start, end)


def get_pollution_daily_pattern(city: str, pollutant: str) -> pd.DataFrame:
    if _use_gold_air_hourly():
        from apps.serving.models import GoldAirHourlyPattern

        m_as_of = GoldAirHourlyPattern.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = (
                GoldAirHourlyPattern.objects.filter(
                    as_of=m_as_of,
                    pollutant=pollutant,
                )
                .filter(_gold_city_key_filter('city_key', city))
                .order_by('hour')
                .values('hour', 'value_avg')
            )
            df = pd.DataFrame(list(qs))
            if not df.empty:
                return df
    return _orm_pollution_daily_pattern(city, pollutant)


def get_pollution_hotspots(pollutant: str, *, include_sensor_id: bool = False) -> pd.DataFrame:
    """
    Moyenne et pourcentage d'exceedance par capteur/ville.

    pct_exceedance = proportion de mesures > 0, multiplié par 100
    (indicateur de présence/disponibilité des données, cohérent avec
    la requête SQL d'origine qui utilisait threshold=0).

    ``include_sensor_id=True`` : ajoute ``sensor_id`` (pour publication Gold).
    """
    if not include_sensor_id and _use_gold_air_hotspots():
        from apps.serving.models import GoldAirHotspot

        m_as_of = GoldAirHotspot.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = (
                GoldAirHotspot.objects.filter(as_of=m_as_of, pollutant=pollutant)
                .values('city', 'latitude', 'longitude', 'value_avg', 'pct_exceedance')
            )
            df = pd.DataFrame(list(qs))
            if not df.empty:
                return df
    return _orm_pollution_hotspots(pollutant, include_sensor_id=include_sensor_id)


def get_who_exceedance_days(city: str) -> pd.DataFrame:
    """Nombre de jours de dépassement des seuils OMS par polluant."""
    if _use_gold_air_who():
        from apps.serving.models import GoldAirWhoExceedance

        m_as_of = GoldAirWhoExceedance.objects.aggregate(m=Max('as_of'))['m']
        if m_as_of:
            qs = (
                GoldAirWhoExceedance.objects.filter(as_of=m_as_of)
                .filter(_gold_city_key_filter('city_key', city))
                .values('pollutant', 'days_exceeded')
            )
            df = pd.DataFrame(list(qs))
            if not df.empty:
                return df
    return _orm_who_exceedance_days(city)


# ─── Mobilité ────────────────────────────────────────────────────────────────


def _use_gold_mobility() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_MOBILITY', False):
        return False
    from apps.serving.models import GoldMobilityStationLatest

    return GoldMobilityStationLatest.objects.exists()


def _use_gold_mobility_hourly() -> bool:
    if not getattr(settings, 'DASHBOARD_GOLD_MOBILITY', False):
        return False
    from apps.serving.models import GoldMobilityHourly

    return GoldMobilityHourly.objects.exists()


def _gold_bike_stations_df(city: str) -> pd.DataFrame:
    from apps.serving.models import GoldMobilityStationLatest

    m_as_of = GoldMobilityStationLatest.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return pd.DataFrame()
    qs = GoldMobilityStationLatest.objects.filter(as_of=m_as_of)
    if _gold_city_key(city) != GOLD_NATIONAL_CITY_KEY:
        qs = qs.filter(_city_q('network_city', city))
    df = pd.DataFrame(
        list(
            qs.values(
                'station_id',
                'station_name',
                'latitude',
                'longitude',
                'bikes_available',
                'free_slots',
                'total_slots',
                'util_rate',
            )
        )
    )
    return df


def _orm_bike_stations_latest(city: str) -> pd.DataFrame:
    """
    Stations pour la carte (ORM).

    IMPORTANT perf: on utilise le *dernier snapshot par station* (état courant),
    pas une moyenne sur tout l'historique, sinon l'API scanne une table énorme.
    """
    base = BikeSnapshot.objects.filter(_city_q('station__network__city', city)).select_related(
        'station', 'station__network'
    )
    latest_id = Subquery(
        base.filter(station=OuterRef('station')).order_by('-timestamp').values('id')[:1]
    )
    qs = (
        base.filter(id=latest_id)
        .annotate(util_rate=_util_rate_expr())
        .values(
            'station__station_id',
            'station__station_name',
            'station__latitude',
            'station__longitude',
            'bikes_available',
            'free_slots',
            'total_slots',
            'util_rate',
        )
    )
    df = pd.DataFrame(list(qs))
    if not df.empty:
        df = df.rename(
            columns={
                'station__station_id': 'station_id',
                'station__station_name': 'station_name',
                'station__latitude': 'latitude',
                'station__longitude': 'longitude',
            }
        )
    return df


def fetch_bike_stations_latest_for_publish(city: str) -> pd.DataFrame:
    """Dernier snapshot par station + identifiants réseau (publication Gold)."""
    base = BikeSnapshot.objects.filter(_city_q('station__network__city', city)).select_related(
        'station', 'station__network'
    )
    latest_id = Subquery(
        base.filter(station=OuterRef('station')).order_by('-timestamp').values('id')[:1]
    )
    qs = (
        base.filter(id=latest_id)
        .annotate(util_rate=_util_rate_expr())
        .values(
            'station__network__network_id',
            'station__network__city',
            'station__station_id',
            'station__station_name',
            'station__latitude',
            'station__longitude',
            'bikes_available',
            'free_slots',
            'total_slots',
            'util_rate',
        )
    )
    df = pd.DataFrame(list(qs))
    if not df.empty:
        df = df.rename(
            columns={
                'station__network__network_id': 'network_id',
                'station__network__city': 'network_city',
                'station__station_id': 'station_id',
                'station__station_name': 'station_name',
                'station__latitude': 'latitude',
                'station__longitude': 'longitude',
            }
        )
    return df


def get_bike_stations(city: str) -> pd.DataFrame:
    if _use_gold_mobility():
        df = _gold_bike_stations_df(city)
        if not df.empty:
            return df
    return _orm_bike_stations_latest(city)


def _gold_hourly_bike_df(city: str) -> pd.DataFrame:
    from apps.serving.models import GoldMobilityHourly

    m_as_of = GoldMobilityHourly.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return pd.DataFrame()
    qs = GoldMobilityHourly.objects.filter(as_of=m_as_of).filter(
        _gold_city_key_filter('city_key', city)
    )
    return pd.DataFrame(list(qs.values('hour', 'bikes_avg', 'slots_avg'))).sort_values('hour')


def get_hourly_bike_usage(city: str) -> pd.DataFrame:
    if _use_gold_mobility_hourly():
        df = _gold_hourly_bike_df(city)
        if not df.empty:
            return df
    return _orm_hourly_bike_usage(city)


def _orm_hourly_bike_usage(city: str) -> pd.DataFrame:
    qs = (
        BikeSnapshot.objects.filter(_city_q('station__network__city', city))
        .annotate(hour=ExtractHour('timestamp'))
        .values('hour')
        .annotate(
            bikes_avg=Avg('bikes_available'),
            slots_avg=Avg('free_slots'),
        )
        .order_by('hour')
    )
    return pd.DataFrame(list(qs))


def get_critical_stations(city: str, util_threshold: float = 20.0) -> pd.DataFrame:
    """Stations vélos sous le seuil (état courant = dernier snapshot par station)."""
    if _use_gold_mobility():
        df = _gold_bike_stations_df(city)
        if not df.empty:
            df = df[df['util_rate'] < util_threshold].sort_values('util_rate')
            return df[
                [
                    'station_name',
                    'latitude',
                    'longitude',
                    'bikes_available',
                    'total_slots',
                    'util_rate',
                ]
            ]
    base = BikeSnapshot.objects.filter(_city_q('station__network__city', city)).select_related(
        'station', 'station__network'
    )
    latest_id = Subquery(
        base.filter(station=OuterRef('station')).order_by('-timestamp').values('id')[:1]
    )
    qs = (
        base.filter(id=latest_id)
        .annotate(util_rate=_util_rate_expr())
        .values(
            'station__station_name',
            'station__latitude',
            'station__longitude',
            'bikes_available',
            'total_slots',
            'util_rate',
        )
        .filter(util_rate__lt=util_threshold)
        .order_by('util_rate')
    )
    df = pd.DataFrame(list(qs))
    if not df.empty:
        df = df.rename(
            columns={
                'station__station_name': 'station_name',
                'station__latitude': 'latitude',
                'station__longitude': 'longitude',
            }
        )
    return df


# ─── Analyse croisée ─────────────────────────────────────────────────────────

def _get_weather_daily(city: str) -> pd.DataFrame:
    qs = (
        WeatherObservation.objects
        .filter(_city_q('station__city', city), temperature__isnull=False)
        .annotate(date=TruncDay('timestamp'))
        .values('date')
        .annotate(
            temperature=Avg('temperature'),
            wind_speed=Avg('wind_speed'),
            precipitation=Avg('precipitation'),
        )
        .order_by('date')
    )
    return _normalize_daily_date_column(pd.DataFrame(list(qs)))


def _get_pollution_daily(city: str) -> pd.DataFrame:
    """Retourne un DataFrame pivoté : colonnes = polluants, index = date."""
    qs = (
        PollutionMeasurement.objects
        .filter(_city_q('sensor__city', city))
        .annotate(date=TruncDay('timestamp'))
        .values('date', 'pollutant')
        .annotate(value_avg=Avg('value'))
    )
    df = pd.DataFrame(list(qs))
    if df.empty:
        return df
    pivot = (
        df.pivot_table(index='date', columns='pollutant', values='value_avg')
        .reset_index()
    )
    pivot.columns.name = None
    return _normalize_daily_date_column(pivot)


def _get_bike_daily(city: str) -> pd.DataFrame:
    qs = (
        BikeSnapshot.objects
        .filter(_city_q('station__network__city', city))
        .annotate(date=TruncDay('timestamp'))
        .values('date')
        .annotate(util_rate=Avg(_util_rate_expr()))
        .order_by('date')
    )
    return _normalize_daily_date_column(pd.DataFrame(list(qs)))


def _get_weather_daily_gold(city: str) -> pd.DataFrame:
    from apps.serving.models import GoldClimateTemperatureDay
    m_as_of = GoldClimateTemperatureDay.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return pd.DataFrame()
    qs = (
        GoldClimateTemperatureDay.objects
        .filter(as_of=m_as_of)
        .filter(_gold_city_key_filter('city_key', city))
        .order_by('day')
        .values('day', 'temp_avg')
    )
    df = pd.DataFrame(list(qs))
    if df.empty:
        return pd.DataFrame()
    return _normalize_daily_date_column(
        df.rename(columns={'day': 'date', 'temp_avg': 'temperature'})
    )


def _get_pollution_daily_gold(city: str) -> pd.DataFrame:
    from apps.serving.models import GoldAirSeriesDay
    m_as_of = GoldAirSeriesDay.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return pd.DataFrame()
    qs = (
        GoldAirSeriesDay.objects
        .filter(as_of=m_as_of)
        .filter(_gold_city_key_filter('city_key', city))
        .values('observation_date', 'pollutant', 'value_avg')
    )
    df = pd.DataFrame(list(qs))
    if df.empty:
        return df
    pivot = df.pivot_table(index='observation_date', columns='pollutant', values='value_avg').reset_index()
    pivot.columns.name = None
    return _normalize_daily_date_column(
        pivot.rename(columns={'observation_date': 'date'})
    )


def build_cross_analysis_dataframe(city: str) -> pd.DataFrame:
    """
    Fusionne météo, pollution et mobilité par jour pour une ville.

    Délègue aux trois helpers ORM privés au lieu de recalculer en SQL brut.
    """
    df_meteo = _get_weather_daily_gold(city) if _use_gold_climate_temp() else pd.DataFrame()
    if df_meteo.empty:
        df_meteo = _get_weather_daily(city)

    df_poll = _get_pollution_daily_gold(city) if _use_gold_air_series() else pd.DataFrame()
    if df_poll.empty:
        df_poll = _get_pollution_daily(city)

    df_bikes = _get_bike_daily(city)

    if df_meteo.empty or df_poll.empty:
        return pd.DataFrame()

    df = df_meteo.merge(df_poll, on='date', how='inner')
    if not df_bikes.empty:
        df = df.merge(df_bikes, on='date', how='left')

    return df.sort_values('date').reset_index(drop=True)


def correlation_matrix_for_cross(df: pd.DataFrame) -> dict[str, Any]:
    """Matrice de corrélation Pearson sur les colonnes disponibles."""
    cols_corr = [
        'temperature', 'wind_speed', 'precipitation',
        'pm25', 'no2', 'o3', 'util_rate',
    ]
    available = [
        c for c in cols_corr
        if c in df.columns and df[c].notna().sum() > 10
    ]
    if len(available) < 2:
        return {'columns': [], 'z': [], 'labels': []}

    corr = df[available].corr().round(2)
    labels_fr = {
        'temperature': 'Température',
        'wind_speed': 'Vent',
        'precipitation': 'Précipitations',
        'pm25': 'PM2.5',
        'no2': 'NO₂',
        'o3': 'O₃',
        'util_rate': 'Vélos (%)',
    }
    label_list = [labels_fr.get(c, c) for c in corr.columns]
    z = [[float(v) for v in row] for row in corr.values.tolist()]
    return {'columns': label_list, 'z': z, 'keys': list(corr.columns)}


# ─── Dashboard home Gold preloads ─────────────────────────────────────────────

def home_meteo_monthly_gold(city: str) -> list[dict] | None:
    if not _use_gold_climate_temp():
        return None
    from apps.serving.models import GoldClimateTemperatureDay
    m_as_of = GoldClimateTemperatureDay.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return None
    qs = (
        GoldClimateTemperatureDay.objects
        .filter(as_of=m_as_of)
        .filter(_gold_city_key_filter('city_key', city))
        .annotate(year=ExtractYear('day'), month=ExtractMonth('day'))
        .values('year', 'month')
        .annotate(avg_temperature=Avg('temp_avg'))
        .order_by('year', 'month')
    )
    rows = list(qs)
    if not rows:
        return None
    return [
        {
            'year': r['year'],
            'month': r['month'],
            'avg_temperature': round(r['avg_temperature'], 2) if r['avg_temperature'] is not None else None,
        }
        for r in rows
    ]


def home_mobility_hourly_gold(city: str) -> list[dict] | None:
    if not _use_gold_mobility_hourly():
        return None
    from apps.serving.models import GoldMobilityHourly
    m_as_of = GoldMobilityHourly.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return None
    qs = (
        GoldMobilityHourly.objects
        .filter(as_of=m_as_of)
        .filter(_gold_city_key_filter('city_key', city))
        .order_by('hour')
        .values('hour', 'bikes_avg')
    )
    rows = list(qs)
    if not rows:
        return None
    return [{'hour': r['hour'], 'avg_bikes_available': round(r['bikes_avg'], 2)} for r in rows]


def home_pollution_variation_gold(city: str) -> dict | None:
    if not _use_gold_air_hourly():
        return None
    from apps.serving.models import GoldAirHourlyPattern
    m_as_of = GoldAirHourlyPattern.objects.aggregate(m=Max('as_of'))['m']
    if not m_as_of:
        return None
    qs = (
        GoldAirHourlyPattern.objects
        .filter(as_of=m_as_of)
        .filter(_gold_city_key_filter('city_key', city))
        .order_by('pollutant', 'hour')
        .values('pollutant', 'hour', 'value_avg')
    )
    result: dict[str, list] = defaultdict(list)
    for r in qs:
        v = r['value_avg']
        if v is not None and v >= 0:
            result[r['pollutant']].append({'hour': r['hour'], 'avg_value': round(v, 4)})
    return dict(result) if result else None
