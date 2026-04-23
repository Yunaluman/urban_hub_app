from __future__ import annotations

from datetime import datetime

import pandas as pd
from django.db import transaction

from apps.pollution.models import PollutionMeasurement, PollutionSensor


def upsert_openaq_df(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    sensors_to_create = {
        row['sensor_id']: {
            'city': row['city'],
            'latitude': row['latitude'],
            'longitude': row['longitude'],
            'source': row.get('source') or 'openaq',
        }
        for row in df.to_dict('records')
    }
    with transaction.atomic():
        PollutionSensor.objects.bulk_create(
            [PollutionSensor(sensor_id=sid, **data) for sid, data in sensors_to_create.items()],
            update_conflicts=True,
            unique_fields=['sensor_id'],
            update_fields=['city', 'latitude', 'longitude', 'source'],
            batch_size=500,
        )

    sensor_map = {
        s.sensor_id: s
        for s in PollutionSensor.objects.filter(sensor_id__in=sensors_to_create.keys())
    }
    measurements = [
        PollutionMeasurement(
            sensor=sensor_map[row['sensor_id']],
            timestamp=_to_datetime(row['timestamp']),
            pollutant=row['pollutant'],
            value=float(row['value']),
            unit=str(row.get('unit') or 'µg/m³'),
        )
        for row in df.to_dict('records')
        if row['sensor_id'] in sensor_map
    ]

    with transaction.atomic():
        PollutionMeasurement.objects.bulk_create(
            measurements,
            update_conflicts=True,
            unique_fields=['sensor', 'timestamp', 'pollutant'],
            update_fields=['value', 'unit'],
            batch_size=2000,
        )
    return len(measurements)


def upsert_eea_df(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    samplingpoints = df['samplingpoint'].dropna().unique().tolist()
    city_by_sp = (
        df[['samplingpoint', 'city']]
        .drop_duplicates()
        .set_index('samplingpoint')['city']
        .to_dict()
    )
    sensor_ids = [f'EEA-{sp}' for sp in samplingpoints]

    with transaction.atomic():
        PollutionSensor.objects.bulk_create(
            [
                PollutionSensor(
                    sensor_id=f'EEA-{sp}',
                    city=city_by_sp.get(sp, ''),
                    latitude=None,
                    longitude=None,
                    source='eea',
                )
                for sp in samplingpoints
            ],
            ignore_conflicts=True,
            batch_size=500,
        )

    sensors = {
        s.sensor_id: s
        for s in PollutionSensor.objects.filter(sensor_id__in=sensor_ids)
    }
    measurements = []
    for rec in df.to_dict('records'):
        sensor = sensors.get(f"EEA-{rec['samplingpoint']}")
        if sensor is None:
            continue
        measurements.append(
            PollutionMeasurement(
                sensor=sensor,
                timestamp=rec['timestamp'].to_pydatetime()
                if hasattr(rec['timestamp'], 'to_pydatetime')
                else rec['timestamp'],
                pollutant=rec['pollutant'],
                value=float(rec['value']),
                unit=str(rec.get('unit') or 'µg/m³'),
            )
        )

    with transaction.atomic():
        PollutionMeasurement.objects.bulk_create(
            measurements,
            update_conflicts=True,
            unique_fields=['sensor', 'timestamp', 'pollutant'],
            update_fields=['value', 'unit'],
            batch_size=2000,
        )
    return len(measurements)


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
