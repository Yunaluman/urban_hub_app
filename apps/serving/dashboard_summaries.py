from __future__ import annotations

from datetime import datetime

from django.db.models import Avg, Count, Max, Q

from apps.dashboard.constants import POLLUTANTS
from apps.dashboard.services.cities import is_reasonable_city_name
from apps.meteo.models import WeatherObservation, WeatherStation
from apps.mobility.models import BikeNetwork, BikeSnapshot
from apps.pollution.models import PollutionMeasurement, PollutionSensor

from .constants import GOLD_NATIONAL_CITY_KEY
from .models import GoldDashboardPageSummary

PAGE_HOME = 'home'
PAGE_CLIMATE = 'climate'
PAGE_AIR = 'air'
PAGE_MOBILITY = 'mobility'
PAGE_CROSS = 'cross'
SUMMARY_PAGES = (PAGE_HOME, PAGE_CLIMATE, PAGE_AIR, PAGE_MOBILITY, PAGE_CROSS)

def _is_reasonable_city_name(value: str) -> bool:
    return is_reasonable_city_name(value)


def _dashboard_city_keys() -> list[str]:
    meteo = {
        str(city).strip()
        for city in WeatherStation.objects.exclude(city='').values_list('city', flat=True)
        if str(city).strip()
    }
    mobility = {
        str(city).strip()
        for city in BikeNetwork.objects.exclude(city='').values_list('city', flat=True)
        if str(city).strip()
    }
    return sorted({city for city in (meteo | mobility) if _is_reasonable_city_name(city)})


def _city_value(city_key: str) -> str:
    city = (city_key or '').strip()
    if not city or city == GOLD_NATIONAL_CITY_KEY:
        return ''
    return city


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _count_distinct_days(qs, **filters) -> int:
    return qs.filter(**filters).values('timestamp__date').distinct().count()


def _precompute_grouped_stats() -> dict[str, dict]:
    """
    Pré-calcul des stats par ville pour réduire drastiquement le nombre de requêtes.

    L'objectif est d'éviter de recalculer `count()`/`aggregate()` dans des boucles
    (pages × villes). On accepte que la pollution reste un cas particulier car
    la valeur "city" est hétérogène (souvent des adresses).
    """
    # ── Station / réseaux (petites tables) ────────────────────────────────────
    station_counts = {
        (row['city'] or '').strip(): int(row['c'] or 0)
        for row in (
            WeatherStation.objects.exclude(city='')
            .values('city')
            .annotate(c=Count('id'))
        )
    }
    network_counts = {
        (row['city'] or '').strip(): int(row['c'] or 0)
        for row in (
            BikeNetwork.objects.exclude(city='')
            .values('city')
            .annotate(c=Count('id'))
        )
    }

    # ── Observations météo (grosse table) : group by ville en 1 passe ─────────
    meteo_rows = (
        WeatherObservation.objects.values('station__city')
        .annotate(
            observation_count=Count('id'),
            last_update=Max('timestamp'),
            hot_days=Count(
                'timestamp__date',
                distinct=True,
                filter=Q(temperature__gt=35),
            ),
            cold_days=Count(
                'timestamp__date',
                distinct=True,
                filter=Q(temperature__lt=-5),
            ),
        )
    )
    meteo_by_city: dict[str, dict] = {}
    for r in meteo_rows:
        city = (r.get('station__city') or '').strip()
        if not city:
            continue
        meteo_by_city[city] = {
            'observation_count': int(r.get('observation_count') or 0),
            'last_update': r.get('last_update'),
            'hot_days': int(r.get('hot_days') or 0),
            'cold_days': int(r.get('cold_days') or 0),
        }

    # ── Snapshots mobilité (grosse table) : group by ville en 1 passe ─────────
    mobility_rows = (
        BikeSnapshot.objects.values('station__network__city')
        .annotate(
            snapshot_count=Count('id'),
            last_update=Max('timestamp'),
        )
    )
    mobility_by_city: dict[str, dict] = {}
    for r in mobility_rows:
        city = (r.get('station__network__city') or '').strip()
        if not city:
            continue
        mobility_by_city[city] = {
            'snapshot_count': int(r.get('snapshot_count') or 0),
            'last_update': r.get('last_update'),
        }

    # ── National agrégé (sans filtre ville) ───────────────────────────────────
    meteo_nat = WeatherObservation.objects.aggregate(
        observation_count=Count('id'),
        last_update=Max('timestamp'),
        hot_days=Count('timestamp__date', distinct=True, filter=Q(temperature__gt=35)),
        cold_days=Count('timestamp__date', distinct=True, filter=Q(temperature__lt=-5)),
    )
    mobility_nat = BikeSnapshot.objects.aggregate(
        snapshot_count=Count('id'),
        last_update=Max('timestamp'),
    )

    return {
        'stations': station_counts,
        'networks': network_counts,
        'meteo': meteo_by_city,
        'mobility': mobility_by_city,
        'meteo_nat': meteo_nat,
        'mobility_nat': mobility_nat,
    }


def _home_payload(city_key: str) -> dict:
    city = _city_value(city_key)
    if city:
        meteo_station_count = WeatherStation.objects.filter(city=city).count()
        bike_network_count = BikeNetwork.objects.filter(city=city).count()
        pollution_sensor_count = PollutionSensor.objects.filter(city__icontains=city).count()
        meteo_qs = WeatherObservation.objects.filter(station__city=city)
        mobility_qs = BikeSnapshot.objects.filter(station__network__city=city)
        pollution_qs = PollutionMeasurement.objects.filter(sensor__city__icontains=city)
    else:
        meteo_station_count = WeatherStation.objects.count()
        bike_network_count = BikeNetwork.objects.count()
        pollution_sensor_count = PollutionSensor.objects.count()
        meteo_qs = WeatherObservation.objects.all()
        mobility_qs = BikeSnapshot.objects.all()
        pollution_qs = PollutionMeasurement.objects.all()

    last_meteo_update = meteo_qs.aggregate(m=Max('timestamp'))['m']
    last_mobility_update = mobility_qs.aggregate(m=Max('timestamp'))['m']
    last_pollution_update = pollution_qs.aggregate(m=Max('timestamp'))['m']
    last_candidates = [stamp for stamp in (last_meteo_update, last_mobility_update, last_pollution_update) if stamp]

    return {
        'meteo_station_count': meteo_station_count,
        'bike_network_count': bike_network_count,
        'pollution_sensor_count': pollution_sensor_count,
        'total_observations': meteo_qs.count() + mobility_qs.count() + pollution_qs.count(),
        'last_meteo_update': _iso_or_none(last_meteo_update),
        'last_mobility_update': _iso_or_none(last_mobility_update),
        'last_pollution_update': _iso_or_none(last_pollution_update),
        'last_update': _iso_or_none(max(last_candidates) if last_candidates else None),
    }


def _build_page_payload_fast(page: str, city_key: str, grouped: dict[str, dict]) -> dict:
    """
    Version optimisée: réutilise les agrégats groupés (météo + mobilité) pour
    éviter des dizaines/centaines de requêtes lorsque l'on publie les résumés.
    """
    city = _city_value(city_key)

    # Pollution: la colonne `city` peut être bruitée (adresses). On conserve le
    # filtrage historique à base de `icontains` pour ne pas changer le contrat.
    if city:
        pollution_sensor_qs = PollutionSensor.objects.filter(city__icontains=city)
        pollution_meas_qs = PollutionMeasurement.objects.filter(sensor__city__icontains=city)
    else:
        pollution_sensor_qs = PollutionSensor.objects.all()
        pollution_meas_qs = PollutionMeasurement.objects.all()

    if page == PAGE_HOME:
        if city:
            meteo_station_count = grouped['stations'].get(city, 0)
            bike_network_count = grouped['networks'].get(city, 0)
            meteo = grouped['meteo'].get(city, {})
            mobility = grouped['mobility'].get(city, {})
        else:
            meteo_station_count = int(WeatherStation.objects.count())
            bike_network_count = int(BikeNetwork.objects.count())
            meteo = grouped.get('meteo_nat', {}) or {}
            mobility = grouped.get('mobility_nat', {}) or {}

        last_meteo_update = meteo.get('last_update')
        last_mobility_update = mobility.get('last_update')
        last_pollution_update = pollution_meas_qs.aggregate(m=Max('timestamp'))['m']
        last_candidates = [
            stamp
            for stamp in (last_meteo_update, last_mobility_update, last_pollution_update)
            if stamp
        ]

        meteo_obs = int(meteo.get('observation_count') or 0)
        mobility_obs = int(mobility.get('snapshot_count') or 0)
        pollution_obs = int(pollution_meas_qs.count())

        return {
            'meteo_station_count': int(meteo_station_count),
            'bike_network_count': int(bike_network_count),
            'pollution_sensor_count': int(pollution_sensor_qs.count()),
            'total_observations': meteo_obs + mobility_obs + pollution_obs,
            'last_meteo_update': _iso_or_none(last_meteo_update),
            'last_mobility_update': _iso_or_none(last_mobility_update),
            'last_pollution_update': _iso_or_none(last_pollution_update),
            'last_update': _iso_or_none(max(last_candidates) if last_candidates else None),
        }

    if page == PAGE_CLIMATE:
        if city:
            station_count = grouped['stations'].get(city, 0)
            meteo = grouped['meteo'].get(city, {})
        else:
            station_count = int(WeatherStation.objects.count())
            meteo = grouped.get('meteo_nat', {}) or {}

        return {
            'station_count': int(station_count),
            'observation_count': int(meteo.get('observation_count') or 0),
            'hot_days': int(meteo.get('hot_days') or 0),
            'cold_days': int(meteo.get('cold_days') or 0),
            'last_update': _iso_or_none(meteo.get('last_update')),
        }

    if page == PAGE_MOBILITY:
        if city:
            network_count = grouped['networks'].get(city, 0)
            mob = grouped['mobility'].get(city, {})
        else:
            network_count = int(BikeNetwork.objects.count())
            mob = grouped.get('mobility_nat', {}) or {}
        return {
            'network_count': int(network_count),
            'snapshot_count': int(mob.get('snapshot_count') or 0),
            'last_update': _iso_or_none(mob.get('last_update')),
        }

    if page == PAGE_CROSS:
        if city:
            meteo_count = int(grouped['meteo'].get(city, {}).get('observation_count') or 0)
            mobility_count = int(grouped['mobility'].get(city, {}).get('snapshot_count') or 0)
        else:
            meteo_count = int((grouped.get('meteo_nat', {}) or {}).get('observation_count') or 0)
            mobility_count = int((grouped.get('mobility_nat', {}) or {}).get('snapshot_count') or 0)
        pollution_count = int(pollution_meas_qs.count())
        last_pollution = pollution_meas_qs.aggregate(m=Max('timestamp'))['m']
        last_candidates = [
            stamp
            for stamp in (
                (grouped['meteo'].get(city, {}) if city else grouped.get('meteo_nat', {})).get('last_update'),
                (grouped['mobility'].get(city, {}) if city else grouped.get('mobility_nat', {})).get('last_update'),
                last_pollution,
            )
            if stamp
        ]
        return {
            'meteo_count': meteo_count,
            'pollution_count': pollution_count,
            'mobility_count': mobility_count,
            'last_update': _iso_or_none(max(last_candidates) if last_candidates else None),
        }

    # Air: conserve l'implémentation existante (anomalies etc.) pour limiter le risque.
    return build_page_payload(page, city_key)


def _climate_payload(city_key: str) -> dict:
    city = _city_value(city_key)
    station_qs = WeatherStation.objects.filter(city=city) if city else WeatherStation.objects.all()
    obs_qs = WeatherObservation.objects.filter(station__city=city) if city else WeatherObservation.objects.all()
    return {
        'station_count': station_qs.count(),
        'observation_count': obs_qs.count(),
        'hot_days': _count_distinct_days(obs_qs, temperature__gt=35),
        'cold_days': _count_distinct_days(obs_qs, temperature__lt=-5),
        'last_update': _iso_or_none(obs_qs.aggregate(m=Max('timestamp'))['m']),
    }


def _air_payload(city_key: str) -> dict:
    city = _city_value(city_key)
    sensor_qs = PollutionSensor.objects.filter(city__icontains=city) if city else PollutionSensor.objects.all()
    meas_qs = (
        PollutionMeasurement.objects.filter(sensor__city__icontains=city)
        if city else PollutionMeasurement.objects.all()
    )

    daily_means = {
        row['pollutant']: row['avg_val']
        for row in meas_qs.values('pollutant').annotate(avg_val=Avg('value'))
    }
    anomaly_filter = Q()
    for pollutant, mean in daily_means.items():
        if mean and mean > 0:
            anomaly_filter |= Q(pollutant=pollutant, value__gt=2 * mean)

    anomaly_count = meas_qs.filter(anomaly_filter).count() if anomaly_filter else 0
    return {
        'sensor_count': sensor_qs.count(),
        'measurement_count': meas_qs.count(),
        'anomaly_count': anomaly_count,
        'pollutant_count': len(POLLUTANTS),
        'last_update': _iso_or_none(meas_qs.aggregate(m=Max('timestamp'))['m']),
    }


def _mobility_payload(city_key: str) -> dict:
    city = _city_value(city_key)
    network_qs = BikeNetwork.objects.filter(city=city) if city else BikeNetwork.objects.all()
    snap_qs = BikeSnapshot.objects.filter(station__network__city=city) if city else BikeSnapshot.objects.all()
    return {
        'network_count': network_qs.count(),
        'snapshot_count': snap_qs.count(),
        'last_update': _iso_or_none(snap_qs.aggregate(m=Max('timestamp'))['m']),
    }


def _cross_payload(city_key: str) -> dict:
    city = _city_value(city_key)
    meteo_qs = WeatherObservation.objects.filter(station__city=city) if city else WeatherObservation.objects.all()
    pollution_qs = (
        PollutionMeasurement.objects.filter(sensor__city__icontains=city)
        if city else PollutionMeasurement.objects.all()
    )
    mobility_qs = BikeSnapshot.objects.filter(station__network__city=city) if city else BikeSnapshot.objects.all()
    return {
        'meteo_count': meteo_qs.count(),
        'pollution_count': pollution_qs.count(),
        'mobility_count': mobility_qs.count(),
        'last_update': _iso_or_none(
            max(
                [stamp for stamp in (
                    meteo_qs.aggregate(m=Max('timestamp'))['m'],
                    pollution_qs.aggregate(m=Max('timestamp'))['m'],
                    mobility_qs.aggregate(m=Max('timestamp'))['m'],
                ) if stamp],
                default=None,
            )
        ),
    }


def build_page_payload(page: str, city_key: str) -> dict:
    if page == PAGE_HOME:
        return _home_payload(city_key)
    if page == PAGE_CLIMATE:
        return _climate_payload(city_key)
    if page == PAGE_AIR:
        return _air_payload(city_key)
    if page == PAGE_MOBILITY:
        return _mobility_payload(city_key)
    if page == PAGE_CROSS:
        return _cross_payload(city_key)
    raise ValueError(f'Unsupported dashboard summary page: {page}')


def publish_dashboard_page_summaries(*, as_of) -> dict[str, int]:
    city_keys = [GOLD_NATIONAL_CITY_KEY] + _dashboard_city_keys()
    total_rows = 0

    grouped = _precompute_grouped_stats()

    for page in SUMMARY_PAGES:
        objs = [
            GoldDashboardPageSummary(
                page=page,
                city_key=city_key,
                payload_json=_build_page_payload_fast(page, city_key, grouped),
                as_of=as_of,
            )
            for city_key in city_keys
        ]
        GoldDashboardPageSummary.objects.bulk_create(
            objs,
            batch_size=200,
            update_conflicts=True,
            unique_fields=['page', 'city_key'],
            update_fields=['payload_json', 'as_of'],
        )
        total_rows += len(objs)

    return {'pages': len(SUMMARY_PAGES), 'cities': len(city_keys), 'rows': total_rows}
