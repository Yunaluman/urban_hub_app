"""
Endpoints JSON pour le dashboard (consommés par Plotly.js dans les templates).
"""

from __future__ import annotations

import logging
from datetime import date
import re

import pandas as pd
from django.core.cache import cache
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dashboard.constants import EU_THRESHOLDS, POLLUTANTS, WHO_THRESHOLDS
from apps.dashboard.services import queries as Q

logger = logging.getLogger(__name__)


def _parse_date(s: str | None, default: date) -> str:
    if not s:
        return default.isoformat()
    return s


def _cache_key(prefix: str, **params) -> str:
    items = '&'.join(f'{k}={params[k]}' for k in sorted(params.keys()))
    return f"dashboard:api:{prefix}?{items}"


def _cached_response(prefix: str, timeout_s: int, builder, **params):
    key = _cache_key(prefix, **params)
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
    except Exception:
        cached = None
    value = builder()
    try:
        cache.set(key, value, timeout=timeout_s)
    except Exception:
        pass
    return value


def _canonicalize_city(raw: str, canon: list[str]) -> str | None:
    """
    Normalise un libellé ville hétérogène vers une ville canonique.

    Ex: "80 Boulevard ... Paris 75015 France" -> "Paris"
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s_l = s.lower()
    # Match by substring against canonical city names, prefer the longest match.
    matches = [c for c in canon if c and c.lower() in s_l]
    if not matches:
        return None
    return max(matches, key=len)


_CITY_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' -]{2,60}$")


def _is_reasonable_city_name(s: str) -> bool:
    """
    Heuristique: un nom de ville "humain" (pas de codes, pas d'adresses).
    """
    if not s:
        return False
    s = str(s).strip()
    if not s or ',' in s:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    return bool(_CITY_RE.match(s))


class CitiesListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        def _build():
            # Union of available cities across domains (meteo, pollution, mobility)
            cities: set[str] = set()

            df_ws = Q.get_weather_stations()
            if not df_ws.empty and 'city' in df_ws.columns:
                cities |= set(df_ws['city'].dropna().astype(str).tolist())

            # Canonical base from meteo, then mobility, filtered to "reasonable" city names.
            canonical = sorted(
                {c.strip() for c in cities if str(c).strip() and _is_reasonable_city_name(str(c))}
            )

            try:
                from apps.pollution.models import PollutionSensor
                raw_poll = list(
                    PollutionSensor.objects.exclude(city='')
                    .values_list('city', flat=True)
                    .distinct()
                )
                # Map noisy pollution cities to canonical (meteo-driven) names when possible.
                for raw in raw_poll:
                    mapped = _canonicalize_city(str(raw), canonical)
                    if mapped:
                        cities.add(mapped)
            except Exception:
                pass

            try:
                from apps.mobility.models import BikeNetwork
                for c in (
                    BikeNetwork.objects.exclude(city='')
                    .values_list('city', flat=True)
                    .distinct()
                ):
                    if _is_reasonable_city_name(str(c)):
                        cities.add(str(c).strip())
            except Exception:
                pass

            out = sorted({c.strip() for c in cities if str(c).strip() and _is_reasonable_city_name(str(c))})
            base = out or ['Paris']
            if '__all__' not in base:
                base = base + ['__all__']
            return {'cities': base}

        payload = _cached_response('cities', 300, _build)
        return Response(payload)


class ClimateTemperatureView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        start = _parse_date(
            request.query_params.get('start'), date(2020, 1, 1)
        )
        end = _parse_date(request.query_params.get('end'), date.today())
        payload = _cached_response(
            'climate-temperature',
            120,
            lambda: {'series': Q.jsonable_records(Q.get_temperature_series(city, start, end))},
            city=city,
            start=start,
            end=end,
        )
        return Response(payload)


class ClimateSeasonalView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raw = request.query_params.get('city')

        def _build():
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                df = Q.get_seasonal_stats(None)
            elif str(raw).strip() == '__all__':
                df = Q.get_seasonal_stats('__all__')
            else:
                df = Q.get_seasonal_stats(str(raw).strip())
            return {'seasonal': Q.jsonable_records(df)}

        cache_city = '__default__' if raw is None else str(raw).strip()
        payload = _cached_response(
            'climate-seasonal',
            600,
            _build,
            city=cache_city,
        )
        return Response(payload)


class ClimateExtremesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            std = float(request.query_params.get('std_factor', '2.5'))
        except ValueError:
            std = 2.5
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        payload = _cached_response(
            'climate-extremes',
            600,
            lambda: {
                'extremes': Q.jsonable_records(
                    Q.get_extreme_days(std_factor=std, city=city or None)
                )
            },
            city=city,
            std=std,
        )
        return Response(payload)


class ClimateVisibilityView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''

        def _build():
            df = Q.get_meteo_visibility_corr(city)
            if len(df) > 3000:
                df = df.sample(3000, random_state=42)
            return {'points': Q.jsonable_records(df)}

        payload = _cached_response(
            'climate-visibility',
            300,
            _build,
            city=city,
        )
        return Response(payload)


class AirWhoView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        payload = _cached_response(
            'air-who',
            300,
            lambda: {
                'exceedances': Q.jsonable_records(Q.get_who_exceedance_days(city)),
                'who_thresholds': WHO_THRESHOLDS,
                'eu_thresholds': EU_THRESHOLDS,
            },
            city=city,
        )
        return Response(payload)


class AirSeriesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        pollutant = request.query_params.get('pollutant', 'pm25')
        if pollutant not in POLLUTANTS:
            pollutant = 'pm25'
        start = _parse_date(
            request.query_params.get('start'), date(2022, 1, 1)
        )
        end = _parse_date(request.query_params.get('end'), date.today())
        payload = _cached_response(
            'air-series',
            180,
            lambda: {
                'series': Q.jsonable_records(Q.get_pollution_series(city, pollutant, start, end)),
                'pollutant': pollutant,
                'who': WHO_THRESHOLDS.get(pollutant),
                'eu': EU_THRESHOLDS.get(pollutant),
            },
            city=city,
            pollutant=pollutant,
            start=start,
            end=end,
        )
        return Response(payload)


class AirHourlyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        pollutant = request.query_params.get('pollutant', 'pm25')
        if pollutant not in POLLUTANTS:
            pollutant = 'pm25'
        def _build():
            df = Q.get_pollution_daily_pattern(city, pollutant)
            # If we only have daily aggregates (hour always 0), do not pretend it's an hourly profile.
            if df.empty or ('hour' in df.columns and df['hour'].nunique() <= 1):
                return {'hourly': []}
            return {'hourly': Q.jsonable_records(df)}

        payload = _cached_response(
            'air-hourly',
            300,
            _build,
            city=city,
            pollutant=pollutant,
        )
        return Response(payload)


class AirHotspotsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        pollutant = request.query_params.get('pollutant', 'pm25')
        if pollutant not in POLLUTANTS:
            pollutant = 'pm25'
        payload = _cached_response(
            'air-hotspots',
            600,
            lambda: {
                'hotspots': Q.jsonable_records(Q.get_pollution_hotspots(pollutant)),
                'who': WHO_THRESHOLDS.get(pollutant, 0),
            },
            pollutant=pollutant,
        )
        return Response(payload)


class MobilityStationsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        payload = _cached_response(
            'mobility-stations',
            180,
            lambda: {'stations': Q.jsonable_records(Q.get_bike_stations(city))},
            city=city,
        )
        return Response(payload)


class MobilityHourlyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        payload = _cached_response(
            'mobility-hourly',
            300,
            lambda: {'hourly': Q.jsonable_records(Q.get_hourly_bike_usage(city))},
            city=city,
        )
        return Response(payload)


class MobilityCriticalView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = ''
        try:
            threshold = float(request.query_params.get('threshold', '20'))
        except ValueError:
            threshold = 20.0
        payload = _cached_response(
            'mobility-critical',
            180,
            lambda: {'critical': Q.jsonable_records(Q.get_critical_stations(city, util_threshold=threshold))},
            city=city,
            threshold=threshold,
        )
        return Response(payload)


class CrossAnalysisDataView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        city = request.query_params.get('city', 'Paris')
        if city == '__all__':
            city = 'Paris'

        def _build():
            df = Q.build_cross_analysis_dataframe(city)
            if df.empty:
                return {
                    'merged': [],
                    'correlation': {'columns': [], 'z': [], 'labels': []},
                    'scatter_temp_util': [],
                    'scatter_pm25_util': [],
                }
            corr = Q.correlation_matrix_for_cross(df)
            df_plot = df.sort_values('date').copy()
            merged = Q.jsonable_records(df_plot)

            scatter_temp = (
                df.dropna(subset=['temperature', 'util_rate'])
                if 'util_rate' in df.columns
                else pd.DataFrame()
            )
            scatter_pm = (
                df.dropna(subset=['pm25', 'util_rate'])
                if 'pm25' in df.columns and 'util_rate' in df.columns
                else pd.DataFrame()
            )

            return {
                'merged': merged,
                'correlation': corr,
                'scatter_temp_util': Q.jsonable_records(scatter_temp),
                'scatter_pm25_util': Q.jsonable_records(scatter_pm),
            }

        payload = _cached_response(
            'cross-data',
            300,
            _build,
            city=city,
        )
        return Response(payload)
