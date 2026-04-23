import logging
from collections import defaultdict
from datetime import date

from django.core.cache import cache
import re
from django.db.models import Avg, Count, Q
from django.db.models.functions import ExtractHour
from django.views.generic import TemplateView

from apps.dashboard.services import queries as svc
from apps.meteo.models import WeatherObservation, WeatherStation
from apps.mobility.models import BikeNetwork, BikeSnapshot
from apps.pollution.models import PollutionMeasurement, PollutionSensor

logger = logging.getLogger(__name__)

_CITY_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' -]{2,60}$")
ALL_CITIES = '__all__'


def _is_reasonable_city_name(s: str) -> bool:
    if not s:
        return False
    s = str(s).strip()
    if not s or ',' in s:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    return bool(_CITY_RE.match(s))


def _last_update(model, field='timestamp'):
    return (
        model.objects.order_by(f'-{field}')
        .values_list(field, flat=True)
        .first()
    )


def _safe(fn, fallback=None):
    """Execute fn(), return fallback on any exception."""
    try:
        return fn()
    except Exception as exc:
        logger.warning('Dashboard preload error: %s', exc)
        return fallback


def _jsonable(df):
    """Convert DataFrame to list of dicts, or empty list if None/empty."""
    if df is None or df.empty:
        return []
    return svc.jsonable_records(df)


def _city_filter_value(selected_city: str) -> str:
    """`__all__` côté UI devient filtre vide côté requêtes."""
    return '' if selected_city == ALL_CITIES else selected_city


def _city_display_label(selected_city: str) -> str:
    return 'Toutes les villes' if selected_city == ALL_CITIES else selected_city


class DashboardView(TemplateView):
    template_name = 'dashboard/index.html'

    @staticmethod
    def _get_available_cities():
        """
        Return sorted list of cities available across domains.

        Important: la donnée "city" peut être hétérogène (pollution), donc on
        nettoie et on déduplique côté Python.
        """
        cities: set[str] = set()
        meteo = set(
            WeatherStation.objects.exclude(city='')
            .values_list('city', flat=True)
            .distinct()
        )
        mobi = set(
            BikeNetwork.objects.exclude(city='')
            .values_list('city', flat=True)
            .distinct()
        )
        # Canonical = clean names from meteo + mobility.
        canonical = sorted({str(c).strip() for c in (meteo | mobi) if str(c).strip()})
        cities |= {c for c in canonical if _is_reasonable_city_name(c)}

        # Pollution "city" can be an address or administrative code → map back to canonical when possible.
        for raw in (
            PollutionSensor.objects.exclude(city='')
            .values_list('city', flat=True)
            .distinct()
        ):
            s = str(raw).strip()
            if not s:
                continue
            s_l = s.lower()
            matches = [c for c in canonical if c.lower() in s_l]
            if matches:
                mapped = max(matches, key=len)
                if _is_reasonable_city_name(mapped):
                    cities.add(mapped)

        out = sorted({str(c).strip() for c in cities if str(c).strip() and _is_reasonable_city_name(str(c))})
        return [ALL_CITIES] + out

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # --- City filter ---
        available_cities = self._get_available_cities()
        default_city = ALL_CITIES
        selected_city = self.request.GET.get('city', default_city)
        if selected_city not in available_cities:
            selected_city = default_city
        filter_city = _city_filter_value(selected_city)
        selected_city_label = _city_display_label(selected_city)

        # Cache the heavy aggregates per city (counts + preloads).
        cache_key = f"dashboard:index:context:{selected_city}"
        cached = None
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            cached.update(
                {
                    'available_cities': available_cities,
                    'selected_city': selected_city,
                    'selected_city_label': selected_city_label,
                }
            )
            return {**context, **cached}

        # --- Filtered querysets ---
        if filter_city:
            meteo_obs_qs = WeatherObservation.objects.filter(station__city=filter_city)
            mobility_snap_qs = BikeSnapshot.objects.filter(station__network__city=filter_city)
            # Pollution city field is messy (addresses, codes) → icontains for best coverage
            poll_meas_qs = PollutionMeasurement.objects.filter(sensor__city__icontains=filter_city)
            context['meteo_station_count'] = WeatherStation.objects.filter(city=filter_city).count()
            context['bike_network_count'] = BikeNetwork.objects.filter(city=filter_city).count()
            context['pollution_sensor_count'] = PollutionSensor.objects.filter(city__icontains=filter_city).count()
        else:
            meteo_obs_qs = WeatherObservation.objects.all()
            mobility_snap_qs = BikeSnapshot.objects.all()
            poll_meas_qs = PollutionMeasurement.objects.all()
            context['meteo_station_count'] = WeatherStation.objects.count()
            context['bike_network_count'] = BikeNetwork.objects.count()
            context['pollution_sensor_count'] = PollutionSensor.objects.count()

        # --- Performance guardrails: limit preloaded aggregates to recent data ---
        # The detailed pages still allow full-period exploration via the API filters.
        today = date.today()
        recent_year_start = date(today.year - 3, 1, 1)   # 3 years for monthly temps
        recent_days_start = date(today.year - 1, 1, 1)   # 1 year for hourly profiles

        # --- Counts ---
        context['total_observations'] = (
            meteo_obs_qs.count()
            + poll_meas_qs.count()
            + mobility_snap_qs.count()
        )

        # --- Last update timestamps ---
        def _qs_last(qs):
            return qs.order_by('-timestamp').values_list('timestamp', flat=True).first()

        last_meteo = _safe(lambda: _qs_last(meteo_obs_qs))
        last_mobility = _safe(lambda: _qs_last(mobility_snap_qs))
        last_pollution = _safe(lambda: _qs_last(poll_meas_qs))
        context['last_meteo_update'] = last_meteo
        context['last_mobility_update'] = last_mobility
        context['last_pollution_update'] = last_pollution
        timestamps = [t for t in (last_meteo, last_mobility, last_pollution) if t]
        context['last_update'] = max(timestamps) if timestamps else None

        # --- Pre-load: meteo avg temperature by month ---
        gold_meteo = _safe(lambda: svc.home_meteo_monthly_gold(filter_city))
        if gold_meteo is not None:
            context['preload_meteo_monthly'] = gold_meteo
        else:
            monthly = _safe(lambda: list(
                meteo_obs_qs
                .filter(timestamp__date__gte=recent_year_start)
                .values('timestamp__year', 'timestamp__month')
                .annotate(avg_temperature=Avg('temperature'))
                .order_by('timestamp__year', 'timestamp__month')
            ), [])
            context['preload_meteo_monthly'] = [
                {
                    'year': r['timestamp__year'],
                    'month': r['timestamp__month'],
                    'avg_temperature': round(r['avg_temperature'], 2) if r['avg_temperature'] is not None else None,
                }
                for r in monthly
            ]

        # --- Pre-load: mobility hourly bike peaks ---
        gold_mob = _safe(lambda: svc.home_mobility_hourly_gold(filter_city))
        if gold_mob is not None:
            context['preload_mobility_hourly'] = gold_mob
        else:
            hourly_mob = _safe(lambda: list(
                mobility_snap_qs
                .filter(timestamp__date__gte=recent_days_start)
                .annotate(hour=ExtractHour('timestamp'))
                .values('hour')
                .annotate(avg_bikes_available=Avg('bikes_available'))
                .order_by('hour')
            ), [])
            context['preload_mobility_hourly'] = [
                {'hour': r['hour'], 'avg_bikes_available': round(r['avg_bikes_available'] or 0, 2)}
                for r in hourly_mob
            ]

        # --- Pre-load: pollution daily variation ---
        gold_poll = _safe(lambda: svc.home_pollution_variation_gold(filter_city))
        if gold_poll is not None:
            context['preload_pollution_variation'] = gold_poll
        else:
            poll_rows = _safe(lambda: list(
                poll_meas_qs
                .filter(timestamp__date__gte=recent_days_start)
                .annotate(hour=ExtractHour('timestamp'))
                .values('pollutant', 'hour')
                .annotate(avg_value=Avg('value'))
                .order_by('pollutant', 'hour')
            ), [])
            poll_variation = defaultdict(list)
            for r in poll_rows:
                v = r['avg_value']
                if v is not None and v >= 0:
                    poll_variation[r['pollutant']].append({'hour': r['hour'], 'avg_value': round(v, 4)})
            context['preload_pollution_variation'] = dict(poll_variation)

        # Store only serializable values in cache.
        to_cache = {
            'meteo_station_count': context.get('meteo_station_count'),
            'bike_network_count': context.get('bike_network_count'),
            'pollution_sensor_count': context.get('pollution_sensor_count'),
            'total_observations': context.get('total_observations'),
            'last_meteo_update': context.get('last_meteo_update'),
            'last_mobility_update': context.get('last_mobility_update'),
            'last_pollution_update': context.get('last_pollution_update'),
            'last_update': context.get('last_update'),
            'preload_meteo_monthly': context.get('preload_meteo_monthly', []),
            'preload_mobility_hourly': context.get('preload_mobility_hourly', []),
            'preload_pollution_variation': context.get('preload_pollution_variation', {}),
        }
        try:
            cache.set(cache_key, to_cache, timeout=120)
        except Exception:
            pass

        context['available_cities'] = available_cities
        context['selected_city'] = selected_city
        context['selected_city_label'] = selected_city_label
        return context


class ClimatePageView(TemplateView):
    template_name = 'dashboard/climate.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cache_key = "dashboard:page:climate:context"
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return {**context, **cached}

        context['station_count'] = WeatherStation.objects.count()
        context['observation_count'] = WeatherObservation.objects.count()
        context['last_update'] = _last_update(WeatherObservation)

        # --- Pre-load: global KPIs (rendered server-side, no spinner) ---
        context['preload_hot_days'] = _safe(lambda: (
            WeatherObservation.objects
            .filter(temperature__gt=35)
            .values('timestamp__date')
            .annotate(c=Count('id'))
            .count()
        ), 0)
        context['preload_cold_days'] = _safe(lambda: (
            WeatherObservation.objects
            .filter(temperature__lt=-5)
            .values('timestamp__date')
            .annotate(c=Count('id'))
            .count()
        ), 0)

        # --- Pre-load: seasonal chart (city-independent, always valid) ---
        context['preload_seasonal'] = _jsonable(_safe(svc.get_seasonal_stats))

        # --- Pre-load: agrégat national Gold par défaut (last 3 years) ---
        start = date(date.today().year - 3, 1, 1).isoformat()
        end = date.today().isoformat()
        context['preload_temp_series'] = _jsonable(
            _safe(lambda: svc.get_temperature_series('', start, end))
        )

        to_cache = {
            'station_count': context.get('station_count'),
            'observation_count': context.get('observation_count'),
            'last_update': context.get('last_update'),
            'preload_hot_days': context.get('preload_hot_days'),
            'preload_cold_days': context.get('preload_cold_days'),
            'preload_seasonal': context.get('preload_seasonal', []),
            'preload_temp_series': context.get('preload_temp_series', []),
        }
        try:
            cache.set(cache_key, to_cache, timeout=300)
        except Exception:
            pass
        return context


class AirPageView(TemplateView):
    template_name = 'dashboard/air.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cache_key = "dashboard:page:air:context"
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return {**context, **cached}

        context['sensor_count'] = PollutionSensor.objects.count()
        context['measurement_count'] = PollutionMeasurement.objects.count()
        context['last_update'] = _last_update(PollutionMeasurement)

        # --- Pre-load: anomaly count (rendered server-side) ---
        daily_means = _safe(lambda: {
            row['pollutant']: row['avg_val']
            for row in PollutionMeasurement.objects
            .values('pollutant')
            .annotate(avg_val=Avg('value'))
        }, {})
        anomaly_filter = Q()
        for poll, mean in (daily_means or {}).items():
            if mean and mean > 0:
                anomaly_filter |= Q(pollutant=poll, value__gt=2 * mean)
        context['preload_anomaly_count'] = _safe(
            lambda: PollutionMeasurement.objects.filter(anomaly_filter).count() if anomaly_filter else 0, 0
        )

        # --- Pre-load: dépassements OMS agrégés nationaux ---
        context['preload_who'] = _jsonable(_safe(lambda: svc.get_who_exceedance_days('')))

        # --- Pre-load: PM2.5 national (last year) ---
        start = date(date.today().year - 1, 1, 1).isoformat()
        end = date.today().isoformat()
        context['preload_air_series'] = _jsonable(
            _safe(lambda: svc.get_pollution_series('', 'pm25', start, end))
        )

        # --- Pre-load: PM2.5 national hourly pattern ---
        context['preload_air_hourly'] = _jsonable(
            _safe(lambda: svc.get_pollution_daily_pattern('', 'pm25'))
        )

        to_cache = {
            'sensor_count': context.get('sensor_count'),
            'measurement_count': context.get('measurement_count'),
            'last_update': context.get('last_update'),
            'preload_anomaly_count': context.get('preload_anomaly_count'),
            'preload_who': context.get('preload_who', []),
            'preload_air_series': context.get('preload_air_series', []),
            'preload_air_hourly': context.get('preload_air_hourly', []),
        }
        try:
            cache.set(cache_key, to_cache, timeout=180)
        except Exception:
            pass
        return context


class MobilityPageView(TemplateView):
    template_name = 'dashboard/mobility.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cache_key = "dashboard:page:mobility:context"
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return {**context, **cached}

        context['network_count'] = BikeNetwork.objects.count()
        context['snapshot_count'] = BikeSnapshot.objects.count()
        context['last_update'] = _last_update(BikeSnapshot)

        # --- Pre-load: agrégat national hourly bike usage ---
        context['preload_mob_hourly'] = _jsonable(
            _safe(lambda: svc.get_hourly_bike_usage(''))
        )

        # --- Pre-load: stations agrégées nationales ---
        context['preload_mob_stations'] = _jsonable(
            _safe(lambda: svc.get_bike_stations(''))
        )

        # --- Pre-load: stations critiques agrégées nationales (threshold 20%) ---
        context['preload_mob_critical'] = _jsonable(
            _safe(lambda: svc.get_critical_stations('', util_threshold=20.0))
        )

        to_cache = {
            'network_count': context.get('network_count'),
            'snapshot_count': context.get('snapshot_count'),
            'last_update': context.get('last_update'),
            'preload_mob_hourly': context.get('preload_mob_hourly', []),
            'preload_mob_stations': context.get('preload_mob_stations', []),
            'preload_mob_critical': context.get('preload_mob_critical', []),
        }
        try:
            cache.set(cache_key, to_cache, timeout=180)
        except Exception:
            pass
        return context


class CrossAnalysisPageView(TemplateView):
    template_name = 'dashboard/cross.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cache_key = "dashboard:page:cross:context"
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return {**context, **cached}

        context['meteo_count'] = WeatherObservation.objects.count()
        context['pollution_count'] = PollutionMeasurement.objects.count()
        context['mobility_count'] = BikeSnapshot.objects.count()
        to_cache = {
            'meteo_count': context.get('meteo_count'),
            'pollution_count': context.get('pollution_count'),
            'mobility_count': context.get('mobility_count'),
        }
        try:
            cache.set(cache_key, to_cache, timeout=300)
        except Exception:
            pass
        return context
