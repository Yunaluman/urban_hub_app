"""
Publication Gold : Parquet MinIO (urbanhub-analytics) puis upsert Postgres.

Les lectures ORM brutes utilisent les helpers ``_orm_*`` de ``queries`` pour
éviter de repasser par les tables Gold pendant la publication.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone as dt_tz

import pandas as pd
from celery import chain, shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Case, FloatField, Max, Value, When
from django.db.models import Q as DQ
from django.db.models.functions import ExtractHour, TruncDay
from django.utils import timezone

from apps.dashboard.constants import POLLUTANTS
from apps.dashboard.services import queries as Q
from apps.pollution.models import PollutionMeasurement
from apps.utils.lake import upload_to_lake

from . import models
from .constants import GOLD_NATIONAL_CITY_KEY

logger = logging.getLogger(__name__)


def _analytics_bucket() -> str:
    return settings.MINIO_BUCKETS.get('analytics', 'urbanhub-analytics')


def _distinct_city_keys() -> list[str]:
    from apps.meteo.models import WeatherStation
    from apps.mobility.models import BikeNetwork
    from apps.pollution.models import PollutionSensor

    s: set[str] = set()
    for qs in (
        BikeNetwork.objects.exclude(city='').values_list('city', flat=True),
        PollutionSensor.objects.exclude(city='').values_list('city', flat=True),
        WeatherStation.objects.exclude(city='').values_list('city', flat=True),
    ):
        for c in qs:
            if c and str(c).strip():
                s.add(str(c).strip())
    return sorted(s)


def _upload_parquet_safe(df: pd.DataFrame, path: str) -> None:
    if df.empty:
        return
    try:
        upload_to_lake(df=df, bucket=_analytics_bucket(), path=path)
    except Exception as exc:
        logger.error('gold_publish parquet failed path=%s: %s', path, exc)


@shared_task(name='apps.serving.tasks.publish_gold_mobility')
def publish_gold_mobility():
    as_of = timezone.now()
    ts = as_of.strftime('%Y%m%d_%H%M%S')
    df = Q.fetch_bike_stations_latest_for_publish('')
    if df.empty:
        logger.warning('publish_gold_mobility: no OLTP snapshot rows')
        return {'rows': 0}
    df_pub = df.copy()
    df_pub['as_of'] = as_of
    _upload_parquet_safe(df_pub, f'mobility/citybikes/gold/stations_{ts}.parquet')

    objs = [
        models.GoldMobilityStationLatest(
            network_id=str(r['network_id']),
            station_id=str(r['station_id']),
            network_city=str(r.get('network_city') or ''),
            station_name=str(r['station_name'])[:200],
            latitude=float(r['latitude']),
            longitude=float(r['longitude']),
            bikes_available=int(r['bikes_available'] or 0),
            free_slots=int(r['free_slots'] or 0),
            total_slots=int(r['total_slots'] or 0),
            util_rate=float(r['util_rate'] or 0.0),
            as_of=as_of,
        )
        for r in df_pub.to_dict('records')
    ]
    with transaction.atomic():
        models.GoldMobilityStationLatest.objects.bulk_create(
            objs,
            batch_size=800,
            update_conflicts=True,
            unique_fields=['network_id', 'station_id'],
            update_fields=[
                'network_city',
                'station_name',
                'latitude',
                'longitude',
                'bikes_available',
                'free_slots',
                'total_slots',
                'util_rate',
                'as_of',
            ],
        )

    city_keys = [GOLD_NATIONAL_CITY_KEY] + _distinct_city_keys()
    hourly_frames = []
    for ck in city_keys:
        orm_city = '' if ck == GOLD_NATIONAL_CITY_KEY else ck
        dfh = Q._orm_hourly_bike_usage(orm_city)
        if dfh.empty:
            continue
        dfc = dfh.copy()
        dfc['city_key'] = ck
        dfc['as_of'] = as_of
        hourly_frames.append(dfc)
    if hourly_frames:
        hdf = pd.concat(hourly_frames, ignore_index=True)
        _upload_parquet_safe(hdf, f'mobility/citybikes/gold/hourly_{ts}.parquet')
        hobjs = [
            models.GoldMobilityHourly(
                city_key=str(r['city_key']),
                hour=int(r['hour']),
                bikes_avg=float(r['bikes_avg'] or 0.0),
                slots_avg=float(r['slots_avg'] or 0.0),
                as_of=as_of,
            )
            for r in hdf.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldMobilityHourly.objects.bulk_create(
                hobjs,
                batch_size=500,
                update_conflicts=True,
                unique_fields=['city_key', 'hour'],
                update_fields=['bikes_avg', 'slots_avg', 'as_of'],
            )

    logger.info(
        'gold_publish mobility stations=%d hourly_keys=%d as_of=%s',
        len(objs),
        len(hourly_frames),
        as_of,
    )
    return {'stations': len(objs), 'hourly_city_keys': len(hourly_frames)}


@shared_task(name='apps.serving.tasks.publish_gold_air',
             soft_time_limit=3600, time_limit=3700)
def publish_gold_air():
    """
    Publication Gold Air — 4 GROUP BY globaux au lieu de N×P requêtes par ville/polluant.

    Un seul passage Postgres par agrégat, traitement pandas en mémoire,
    puis écriture atomique Parquet MinIO → upsert Postgres.
    """
    as_of = timezone.now()
    ts = as_of.strftime('%Y%m%d_%H%M%S')
    # UTC range equivalent to '2022-01-01'..'today' in Europe/Paris.
    # Using timestamp__gte/lte (not __date__) so Postgres can use the btree index
    # on timestamp instead of applying AT TIME ZONE on every row (seq scan).
    start_utc = datetime(2021, 12, 31, 22, 0, 0, tzinfo=dt_tz.utc)  # 2h before Paris 2022-01-01
    end_utc = datetime.now(tz=dt_tz.utc) + timedelta(hours=2)

    # ── 1. Séries journalières — 1 GROUP BY (city, pollutant, day) ─────────────
    logger.info('gold_air: séries journalières...')
    df_series = pd.DataFrame(list(
        PollutionMeasurement.objects
        .filter(timestamp__gte=start_utc, timestamp__lte=end_utc,
                pollutant__in=POLLUTANTS)
        .annotate(day=TruncDay('timestamp'))
        .values('sensor__city', 'pollutant', 'day')
        .annotate(value_avg=Avg('value'), value_max=Max('value'))
    ))
    n_series = 0
    if not df_series.empty:
        df_series = df_series.rename(columns={'sensor__city': 'city'})
        df_series['city_key'] = df_series['city'].str.strip()
        df_series['observation_date'] = pd.to_datetime(df_series['day']).dt.date
        df_series['as_of'] = as_of
        _upload_parquet_safe(df_series, f'pollution/openaq/gold/series_{ts}.parquet')
        sobjs = [
            models.GoldAirSeriesDay(
                city_key=str(r['city_key'])[:100],
                pollutant=str(r['pollutant']),
                observation_date=r['observation_date'],
                value_avg=float(r['value_avg'] or 0.0),
                value_max=float(r['value_max'] or 0.0),
                as_of=as_of,
            )
            for r in df_series.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldAirSeriesDay.objects.bulk_create(
                sobjs, batch_size=2000,
                update_conflicts=True,
                unique_fields=['city_key', 'pollutant', 'observation_date'],
                update_fields=['value_avg', 'value_max', 'as_of'],
            )
        n_series = len(sobjs)
        logger.info('gold_air series rows=%d', n_series)

    # ── 2. Profil horaire — 1 GROUP BY (city, pollutant, hour) ─────────────────
    logger.info('gold_air: profils horaires...')
    df_hourly = pd.DataFrame(list(
        PollutionMeasurement.objects
        .filter(pollutant__in=POLLUTANTS)
        .annotate(hour=ExtractHour('timestamp'))
        .values('sensor__city', 'pollutant', 'hour')
        .annotate(value_avg=Avg('value'))
    ))
    n_hourly = 0
    if not df_hourly.empty:
        df_hourly = df_hourly.rename(columns={'sensor__city': 'city'})
        df_hourly['city_key'] = df_hourly['city'].str.strip()
        df_hourly['as_of'] = as_of
        _upload_parquet_safe(df_hourly, f'pollution/openaq/gold/hourly_pattern_{ts}.parquet')
        hobjs = [
            models.GoldAirHourlyPattern(
                city_key=str(r['city_key'])[:100],
                pollutant=str(r['pollutant']),
                hour=int(r['hour']),
                value_avg=float(r['value_avg'] or 0.0),
                as_of=as_of,
            )
            for r in df_hourly.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldAirHourlyPattern.objects.bulk_create(
                hobjs, batch_size=1000,
                update_conflicts=True,
                unique_fields=['city_key', 'pollutant', 'hour'],
                update_fields=['value_avg', 'as_of'],
            )
        n_hourly = len(hobjs)
        logger.info('gold_air hourly rows=%d', n_hourly)

    # ── 3. Hotspots — 1 GROUP BY (sensor, pollutant) ───────────────────────────
    logger.info('gold_air: hotspots...')
    df_hotspot = pd.DataFrame(list(
        PollutionMeasurement.objects
        .filter(pollutant__in=POLLUTANTS,
                sensor__latitude__isnull=False,
                sensor__longitude__isnull=False)
        .values('sensor__sensor_id', 'sensor__city',
                'sensor__latitude', 'sensor__longitude', 'pollutant')
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
    ))
    n_hotspot = 0
    if not df_hotspot.empty:
        df_hotspot = df_hotspot.rename(columns={
            'sensor__sensor_id': 'sensor_id',
            'sensor__city': 'city',
            'sensor__latitude': 'latitude',
            'sensor__longitude': 'longitude',
        })
        df_hotspot['as_of'] = as_of
        for poll in POLLUTANTS:
            t = df_hotspot[df_hotspot['pollutant'] == poll]
            if not t.empty:
                _upload_parquet_safe(t, f'pollution/openaq/gold/hotspots_{poll}_{ts}.parquet')
        hp_objs = [
            models.GoldAirHotspot(
                pollutant=str(r['pollutant']),
                sensor_id=str(r['sensor_id'])[:200],
                city=str(r['city'])[:200],
                latitude=float(r['latitude'] or 0.0),
                longitude=float(r['longitude'] or 0.0),
                value_avg=float(r['value_avg'] or 0.0),
                pct_exceedance=float(r['pct_exceedance'] or 0.0),
                as_of=as_of,
            )
            for r in df_hotspot.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldAirHotspot.objects.bulk_create(
                hp_objs, batch_size=800,
                update_conflicts=True,
                unique_fields=['pollutant', 'sensor_id'],
                update_fields=['city', 'latitude', 'longitude', 'value_avg', 'pct_exceedance', 'as_of'],
            )
        n_hotspot = len(hp_objs)
        logger.info('gold_air hotspots rows=%d', n_hotspot)

    # ── 4. Dépassements OMS — 1 GROUP BY (city, pollutant, day) distinct ───────
    logger.info('gold_air: dépassements OMS...')
    _WHO_LIMITS: dict[str, float] = {'pm25': 15.0, 'pm10': 45.0, 'no2': 25.0, 'o3': 60.0}
    who_cond = DQ()
    for poll, limit in _WHO_LIMITS.items():
        who_cond |= DQ(pollutant=poll, value__gt=limit)
    df_who_raw = pd.DataFrame(list(
        PollutionMeasurement.objects
        .filter(who_cond)
        .annotate(day=TruncDay('timestamp'))
        .values('sensor__city', 'pollutant', 'day')
        .distinct()
    ))
    n_who = 0
    if not df_who_raw.empty:
        df_who_raw = df_who_raw.rename(columns={'sensor__city': 'city'})
        df_who_raw['city_key'] = df_who_raw['city'].str.strip()
        df_who_agg = (
            df_who_raw.groupby(['city_key', 'pollutant'])['day']
            .nunique().reset_index()
            .rename(columns={'day': 'days_exceeded'})
        )
        df_who_agg['as_of'] = as_of
        _upload_parquet_safe(df_who_agg, f'pollution/openaq/gold/who_exceedance_{ts}.parquet')
        wobjs = [
            models.GoldAirWhoExceedance(
                city_key=str(r['city_key'])[:100],
                pollutant=str(r['pollutant']),
                days_exceeded=int(r['days_exceeded'] or 0),
                as_of=as_of,
            )
            for r in df_who_agg.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldAirWhoExceedance.objects.bulk_create(
                wobjs, batch_size=500,
                update_conflicts=True,
                unique_fields=['city_key', 'pollutant'],
                update_fields=['days_exceeded', 'as_of'],
            )
        n_who = len(wobjs)
        logger.info('gold_air who rows=%d', n_who)

    logger.info('gold_publish air series=%d hourly=%d hotspots=%d who=%d as_of=%s',
                n_series, n_hourly, n_hotspot, n_who, as_of)
    return {'series': n_series, 'hourly': n_hourly, 'hotspots': n_hotspot, 'who': n_who}


@shared_task(name='apps.serving.tasks.publish_gold_climate',
             soft_time_limit=1800, time_limit=1900)
def publish_gold_climate():
    as_of = timezone.now()
    ts = as_of.strftime('%Y%m%d_%H%M%S')
    cities = _distinct_city_keys()
    start = '2020-01-01'
    end = date.today().isoformat()
    temp_frames = []
    for city in cities:
        ck = Q._city_key(city)
        dft = Q._orm_temperature_series(city, start, end)
        if not dft.empty:
            t = dft.copy()
            t['city_key'] = ck
            t['day'] = pd.to_datetime(t['date']).dt.date
            t['as_of'] = as_of
            t = t.drop(columns=['date'])
            temp_frames.append(t)
    if temp_frames:
        tdf = pd.concat(temp_frames, ignore_index=True)
        _upload_parquet_safe(tdf, f'meteo/openmeteo/gold/temperature_daily_{ts}.parquet')
        tobjs = [
            models.GoldClimateTemperatureDay(
                city_key=str(r['city_key']),
                day=r['day'],
                temp_avg=float(r['temp_avg'] or 0.0),
                temp_min=float(r['temp_min'] or 0.0),
                temp_max=float(r['temp_max'] or 0.0),
                as_of=as_of,
            )
            for r in tdf.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldClimateTemperatureDay.objects.bulk_create(
                tobjs,
                batch_size=1000,
                update_conflicts=True,
                unique_fields=['city_key', 'day'],
                update_fields=['temp_avg', 'temp_min', 'temp_max', 'as_of'],
            )

    dfs = Q._orm_seasonal_stats()
    if not dfs.empty:
        dfs = dfs.rename(columns={'city': 'city_key'})
        dfs['as_of'] = as_of
        _upload_parquet_safe(dfs, f'meteo/openmeteo/gold/seasonal_{ts}.parquet')
        sobjs = [
            models.GoldClimateSeasonalMonth(
                city_key=str(r['city_key'])[:100],
                month=int(r['month']),
                temp_avg=float(r['temp_avg'] or 0.0),
                as_of=as_of,
            )
            for r in dfs.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldClimateSeasonalMonth.objects.bulk_create(
                sobjs,
                batch_size=500,
                update_conflicts=True,
                unique_fields=['city_key', 'month'],
                update_fields=['temp_avg', 'as_of'],
            )

    for std in (2.5, 3.0):
        dfe = Q._orm_extreme_days(std_factor=std)
        if dfe.empty:
            continue
        e = dfe.rename(columns={'city': 'city_key', 'type': 'event_type'})
        e['day'] = pd.to_datetime(e['date']).dt.date
        e['std_factor'] = std
        e['as_of'] = as_of
        e = e.drop(columns=['date'])
        _upload_parquet_safe(e, f'meteo/openmeteo/gold/extremes_std{std}_{ts}.parquet')
        eobjs = [
            models.GoldClimateExtremeDay(
                city_key=str(r['city_key'])[:100],
                std_factor=float(r['std_factor']),
                day=r['day'],
                temp=float(r['temp'] or 0.0),
                event_type=str(r['event_type'])[:32],
                as_of=as_of,
            )
            for r in e.to_dict('records')
        ]
        with transaction.atomic():
            models.GoldClimateExtremeDay.objects.bulk_create(
                eobjs,
                batch_size=800,
                update_conflicts=True,
                unique_fields=['city_key', 'std_factor', 'day', 'event_type'],
                update_fields=['temp', 'as_of'],
            )
    for city in cities:
        ck = Q._city_key(city)
        raw = Q._orm_meteo_visibility_corr(ck)
        if raw.empty:
            continue
        sample = raw
        if len(sample) > 3000:
            sample = sample.sample(3000, random_state=42)
        points = Q.jsonable_records(sample)
        models.GoldClimateVisibilitySample.objects.update_or_create(
            city_key=ck,
            defaults={'points_json': points, 'as_of': as_of},
        )
    raw_nat = Q._orm_meteo_visibility_corr('')
    if not raw_nat.empty:
        sample_nat = raw_nat
        if len(sample_nat) > 3000:
            sample_nat = sample_nat.sample(3000, random_state=42)
        points_nat = Q.jsonable_records(sample_nat)
        models.GoldClimateVisibilitySample.objects.update_or_create(
            city_key=GOLD_NATIONAL_CITY_KEY,
            defaults={'points_json': points_nat, 'as_of': as_of},
        )
    logger.info('gold_publish climate cities=%d as_of=%s', len(cities), as_of)
    return {'cities': len(cities)}


@shared_task(name='apps.serving.tasks.publish_gold_all')
def publish_gold_all():
    """Chaîne Celery : mobilité puis air puis climat."""
    chain(
        publish_gold_mobility.s(),
        publish_gold_air.s(),
        publish_gold_climate.s(),
    ).apply_async()
    return {'queued': True}
