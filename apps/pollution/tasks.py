import logging
import os
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from zipfile import BadZipFile, ZipFile

import pandas as pd
import requests
from requests import exceptions as req_exc
from celery import chain, shared_task

from apps.utils.lake import download_from_lake, upload_bytes_to_lake, upload_to_lake
from apps.pollution.persistence import upsert_eea_df, upsert_openaq_df

logger = logging.getLogger(__name__)

BASE_URL      = 'https://api.openaq.org/v3'
REQUEST_TIMEOUT = 15

POLLUTANT_MAP = {
    'pm25': 'pm25', 'pm2.5': 'pm25',
    'pm10': 'pm10',
    'no2':  'no2',  'nitrogen dioxide': 'no2',
    'o3':   'o3',   'ozone': 'o3',
    'co':   'co',   'carbon monoxide': 'co',
    'so2':  'so2',  'sulfur dioxide': 'so2',
}


def _headers() -> dict:
    h = {'Accept': 'application/json'}
    key = (os.getenv('OPENAQ_API_KEY', '') or '').strip()
    if key:
        h['X-API-Key'] = key
    return h


_PAGE_SIZE = 100


def _get_paginated(path: str, params: dict) -> list[dict]:
    results = []
    p = {**params, 'limit': _PAGE_SIZE, 'page': 1}
    while True:
        try:
            r = requests.get(f'{BASE_URL}{path}', params=p, headers=_headers(), timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                time.sleep(5)
                continue
            r.raise_for_status()
            data = r.json().get('results', [])
        except Exception as e:
            logger.warning('OpenAQ %s page %d: %s', path, p['page'], e)
            break
        if not data:
            break
        results.extend(data)
        if len(data) < _PAGE_SIZE:
            break
        p['page'] += 1
        time.sleep(0.5)
    return results


def _get_france_country_id() -> int | None:
    # France = ID 22 in OpenAQ v3 (verified; /countries?iso=FR param is ignored by the API)
    countries = _get_paginated('/countries', {})
    for c in countries:
        if c.get('code', '').upper() == 'FR':
            return c.get('id')
    return 22  # fallback: France's stable OpenAQ v3 ID


def _get_locations(country_id: int) -> list[dict]:
    return _get_paginated('/locations', {'countries_id': country_id})


def _fetch_latest(loc_id: int) -> list[dict]:
    try:
        r = requests.get(f'{BASE_URL}/locations/{loc_id}/latest',
                         headers=_headers(), timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json().get('results', [])
    except Exception as e:
        logger.debug('Erreur location %s: %s', loc_id, e)
        return []


@shared_task(bind=True, name='apps.pollution.tasks.collect_openaq_bronze', max_retries=3)
def collect_openaq_bronze(self):
    """Bronze : collecte les mesures OpenAQ France et archive la snapshot brute."""
    snapshot_ts = datetime.now(tz=timezone.utc)
    timestamp_str = snapshot_ts.strftime('%Y%m%d_%H%M%S')

    try:
        country_id = _get_france_country_id()
        if not country_id:
            logger.error('ID pays France introuvable')
            return None

        locations = _get_locations(country_id)
        logger.info('%d stations FR récupérées', len(locations))

        sensors_map: dict[int, str] = {}
        for loc in locations:
            for sensor in loc.get('sensors', []):
                p = (sensor.get('parameter') or {}).get('name', '')
                if p and sensor.get('id'):
                    sensors_map[sensor['id']] = p.lower()

        all_records = []
        for loc in locations:
            for rec in _fetch_latest(loc['id']):
                rec['_location_id'] = loc['id']
                rec['_loc_data'] = loc
                all_records.append(rec)
            time.sleep(0.1)

        logger.info('%d mesures récupérées', len(all_records))

    except Exception as exc:
        logger.exception('Erreur collecte OpenAQ bronze')
        raise self.retry(exc=exc, countdown=60)

    # Archive brute MinIO (sans _loc_data pour alléger)
    if all_records:
        try:
            df_raw = pd.DataFrame([
                {k: v for k, v in r.items() if k != '_loc_data'}
                for r in all_records
            ])
            upload_to_lake(
                df=df_raw,
                bucket='urbanhub-raw',
                path=f'pollution/openaq/openaq_{timestamp_str}.parquet',
            )
        except Exception as exc:
            logger.error('Bronze OpenAQ upload: %s', exc)

    # Pré-traitement : extraction des champs utiles pour Silver
    processed: list[dict] = []
    for rec in all_records:
        param = sensors_map.get(rec.get('sensorsId'), '')
        pollutant_key = POLLUTANT_MAP.get(param)
        if not pollutant_key:
            continue
        loc = rec['_loc_data']
        loc_id = rec['_location_id']
        coords = rec.get('coordinates') or loc.get('coordinates') or {}
        city = loc.get('locality') or loc.get('city') or loc.get('name') or ''
        dt_str = (rec.get('datetime') or {}).get('utc') or \
                 (rec.get('period') or {}).get('datetimeTo', {}).get('utc')
        if not dt_str:
            continue
        try:
            value = float(rec.get('value') or 0)
            unit = (rec.get('parameter') or {}).get('units', 'µg/m³')
        except (TypeError, ValueError):
            continue
        processed.append({
            'sensor_id': f'{loc_id}-{param}',
            'city': city,
            'latitude': coords.get('latitude') or 0.0,
            'longitude': coords.get('longitude') or 0.0,
            'timestamp': dt_str,
            'pollutant': pollutant_key,
            'value': value,
            'unit': unit,
        })

    logger.info('collect_openaq_bronze: %d mesures pré-traitées', len(processed))
    return {'processed': processed, 'timestamp_str': timestamp_str}


@shared_task(bind=True, name='apps.pollution.tasks.collect_openaq_silver', max_retries=3)
def collect_openaq_silver(self, bronze_result):
    """Silver : nettoie et archive les mesures OpenAQ normalisées."""
    if not bronze_result or not bronze_result.get('processed'):
        logger.warning('collect_openaq_silver: pas de données bronze')
        return {'records_processed': 0}

    processed = bronze_result['processed']
    timestamp_str = bronze_result['timestamp_str']
    processed_path = f'pollution/openaq/openaq_{timestamp_str}.parquet'
    records_processed = 0

    try:
        df_silver = pd.DataFrame(processed)
        df_silver['timestamp'] = pd.to_datetime(df_silver['timestamp'], utc=True)
        df_silver['source'] = 'openaq'
        df_silver = df_silver.drop_duplicates(
            subset=['timestamp', 'pollutant', 'latitude', 'longitude']
        )
        upload_to_lake(
            df=df_silver,
            bucket='urbanhub-processed',
            path=processed_path,
        )
        records_processed = len(df_silver)
    except Exception as exc:
        logger.error('Silver OpenAQ upload: %s', exc)

    logger.info('collect_openaq_silver: %d mesures normalisées', records_processed)
    return {
        'records_processed': records_processed,
        'processed_path': processed_path if records_processed else '',
        'timestamp_str': timestamp_str,
    }


@shared_task(bind=True, name='apps.pollution.tasks.ingest_openaq_postgres', max_retries=3)
def ingest_openaq_postgres(self, silver_result):
    """Postgres : upsert capteurs et mesures OpenAQ."""
    if not silver_result or not silver_result.get('processed_path'):
        logger.warning('ingest_openaq_postgres: pas de données silver')
        return {'rows_upserted': 0}

    try:
        df_silver = download_from_lake('urbanhub-processed', silver_result['processed_path'])
        count = upsert_openaq_df(df_silver)
    except Exception as exc:
        logger.error('PG upsert OpenAQ: %s', exc)
        raise self.retry(exc=exc, countdown=60)

    logger.info('ingest_openaq_postgres: %d mesures insérées', count)
    return {'rows_upserted': count, 'timestamp_str': silver_result.get('timestamp_str', '')}


@shared_task(name='apps.pollution.tasks.collect_openaq')
def collect_openaq():
    """Orchestre Bronze → Silver → Postgres pour OpenAQ."""
    chain(
        collect_openaq_bronze.s(),
        collect_openaq_silver.s(),
        ingest_openaq_postgres.s(),
    ).apply_async()
    return {'queued': True}


# ─────────────────────────────────────────────────────────────────────────────
# EEA Air Quality Download Service (batch historisé)
# ─────────────────────────────────────────────────────────────────────────────

EEA_DOWNLOADS_API = 'https://eeadmz1-downloads-api-appservice.azurewebsites.net'
EEA_REQUEST_TIMEOUT = int((os.getenv('EEA_REQUEST_TIMEOUT', '') or '60').strip() or 60)

# The EEA parquet files we observed are hourly (AggType=hour). Request hour to get URLs/archives
# and aggregate later for daily analytics.
EEA_AGGREGATION_TYPE = (os.getenv('EEA_AGGREGATION_TYPE', 'hour') or 'hour').strip().lower()

# EEA notations are accepted by the API (doc: pollutants can be notations).
EEA_POLLUTANTS = ['PM2.5', 'PM10', 'NO2', 'O3', 'CO', 'SO2']
EEA_POLLUTANT_MAP = {
    'PM2.5': 'pm25',
    'PM25': 'pm25',
    'PM10': 'pm10',
    'NO2': 'no2',
    'O3': 'o3',
    'CO': 'co',
    'SO2': 'so2',
}

# In parquet exports, "Pollutant" can be a numeric concept id from the Eionet
# vocabulary http://dd.eionet.europa.eu/vocabulary/aq/pollutant/
# (example: 7 -> O3, 5 -> PM10, 6001 -> PM2.5).
EEA_POLLUTANT_ID_TO_NOTATION: dict[int, str] = {
    1: 'SO2',
    5: 'PM10',
    7: 'O3',
    8: 'NO2',
    10: 'CO',
    6001: 'PM2.5',
}

EEA_ENGINE = (os.getenv('EEA_DOWNLOAD_ENGINE', 'classic') or 'classic').strip().lower()

# City values are not free-form; the UI uses Urban Audit geometries and names like
# "Paris (greater city)". Using plain "Paris" can produce an empty ZIP.
EEA_CITY_ALIASES: dict[str, str] = {
    'paris': 'Paris (greater city)',
    'lyon': 'Lyon (greater city)',
    'marseille': 'Marseille (greater city)',
    'lille': 'Lille (greater city)',
    'toulouse': 'Toulouse (greater city)',
    'bordeaux': 'Bordeaux (greater city)',
    'strasbourg': 'Strasbourg (greater city)',
}


def _eea_city_value(city: str) -> str:
    c = (city or '').strip()
    if not c:
        return c
    return EEA_CITY_ALIASES.get(c.lower(), c)


def _month_windows(start_dt: datetime, end_dt: datetime) -> list[tuple[datetime, datetime]]:
    """Split [start_dt, end_dt] into month windows (UTC)."""
    out = []
    cur = start_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur < end_dt:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        w_end = min(nxt, end_dt)
        out.append((cur, w_end))
        cur = nxt
    return out


def _eea_dataset_for_year(year: int) -> int:
    # Doc: dataset 2 = verified E1a (2013–2022), dataset 1 = UTD/E2a (from 2023)
    return 2 if year <= 2022 else 1


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _is_zip_bytes(content: bytes) -> bool:
    # ZIP local file header signature
    return bool(content) and content[:2] == b'PK'


def _poll_eea_async(url: str, timeout_s: int = 3600) -> bytes:
    """Poll an async URL until it returns the ZIP content.

    The async endpoint may first return 404 while the archive is being built.
    Some deployments return a *text URL* pointing to the real ZIP; handle that too.
    """
    t0 = time.time()
    current_url = url.strip().strip('"')
    if current_url.startswith('/'):
        current_url = f'{EEA_DOWNLOADS_API}{current_url}'
    while True:
        if time.time() - t0 > timeout_s:
            raise TimeoutError('EEA async download timed out')
        r = requests.get(current_url, timeout=EEA_REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code == 404:
            time.sleep(10)
            continue
        r.raise_for_status()
        content = r.content or b''
        if _is_zip_bytes(content):
            return content

        # Try to interpret body as a URL to the actual ZIP (common pattern)
        body = (r.text or '').strip().strip('"')
        if body.startswith('http://') or body.startswith('https://'):
            current_url = body
            time.sleep(1)
            continue

        # Not a ZIP and not a URL: surface a helpful error (first bytes + content-type)
        ctype = r.headers.get('Content-Type', '')
        preview = content[:200]
        raise ValueError(
            f'EEA async response is not a ZIP (content-type={ctype}, bytes={len(content)}). '
            f'Preview={preview!r}'
        )


def _parse_eea_zip(zip_bytes: bytes, city: str) -> pd.DataFrame:
    """Parse a ZIP of Parquet files (EEA) into a normalized DataFrame."""
    rows = []
    try:
        with ZipFile(BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            parquet_names = [n for n in names if n.lower().endswith('.parquet')]
            if not parquet_names:
                logger.warning('EEA ZIP has no parquet entries for city=%s (entries=%s)', city, names[:10])
                return pd.DataFrame()

            for name in parquet_names:
                with zf.open(name) as fp:
                    df = pd.read_parquet(fp, engine='pyarrow')
                if df.empty:
                    continue
                # Expected columns from doc: Samplingpoint, Pollutant, Start, End, Value, Unit, AggType...
                cols = {c.lower(): c for c in df.columns}
                sampling_col = cols.get('samplingpoint')
                pollutant_col = cols.get('pollutant')
                start_col = cols.get('start')
                value_col = cols.get('value')
                unit_col = cols.get('unit')
                if not (sampling_col and pollutant_col and start_col and value_col):
                    logger.warning(
                        'EEA parquet missing expected cols (file=%s, cols=%s)',
                        name,
                        list(df.columns)[:20],
                    )
                    continue
                tmp = df[[sampling_col, pollutant_col, start_col, value_col] + ([unit_col] if unit_col else [])].copy()
                tmp.columns = ['samplingpoint', 'pollutant_raw', 'timestamp', 'value'] + (['unit_raw'] if unit_col else [])
                tmp['city'] = city
                tmp['source'] = 'eea'
                rows.append(tmp)
    except BadZipFile:
        logger.warning('EEA payload is not a valid ZIP (len=%d) for city=%s', len(zip_bytes), city)
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out['timestamp'] = pd.to_datetime(out['timestamp'], utc=True, errors='coerce')
    out = out.dropna(subset=['timestamp', 'samplingpoint'])
    out['pollutant_raw'] = out['pollutant_raw']
    # pollutant can be numeric id or notation text
    poll_id = pd.to_numeric(out['pollutant_raw'], errors='coerce')
    pollutant_norm = out['pollutant_raw'].astype(str).str.strip()
    mask_num = poll_id.notna()
    if mask_num.any():
        mapped = poll_id[mask_num].astype('int64').map(EEA_POLLUTANT_ID_TO_NOTATION)
        pollutant_norm.loc[mask_num] = mapped.fillna(pollutant_norm.loc[mask_num])
    out['pollutant'] = pollutant_norm.str.upper().str.replace(' ', '', regex=False)
    out['pollutant'] = out['pollutant'].map(EEA_POLLUTANT_MAP)
    out = out.dropna(subset=['pollutant'])
    out['value'] = pd.to_numeric(out['value'], errors='coerce')
    # Drop invalid/sentinel values commonly used in EEA exports
    out.loc[out['value'] <= -998, 'value'] = pd.NA
    if 'validity' in out.columns:
        out['validity'] = pd.to_numeric(out['validity'], errors='coerce')
        out.loc[out['validity'] < 0, 'value'] = pd.NA
    out = out.dropna(subset=['value'])
    out['unit'] = out.get('unit_raw', 'µg/m³')
    out['unit'] = out['unit'].astype(str).str.strip().replace({'ug.m-3': 'µg/m³', 'µg/m3': 'µg/m³'})
    out = out.drop(columns=[c for c in ['unit_raw'] if c in out.columns])
    out = out.drop_duplicates(subset=['samplingpoint', 'timestamp', 'pollutant'])
    return out


def _eea_urls(dataset: int, cities: list[str] | None = None) -> list[str]:
    """Fetch list of parquet URLs for a dataset (stable endpoint).

    Per EEA doc, this ignores datetime filters and returns full time series parquet URLs.
    """
    cities = cities or []
    request_body = {
        'countries': ['FR'],
        'cities': cities,
        'pollutants': EEA_POLLUTANTS,
        'dataset': dataset,
        'aggregationType': EEA_AGGREGATION_TYPE,
        'source': 'Customscript',
        'email': os.getenv('EEA_CONTACT_EMAIL', ''),
    }
    # EEA can be slow; do a small retry/backoff on timeouts.
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f'{EEA_DOWNLOADS_API}/ParquetFile/urls',
                json=request_body,
                timeout=EEA_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            break
        except (req_exc.ReadTimeout, req_exc.ConnectTimeout) as exc:
            last_exc = exc
            time.sleep(2.0 * (attempt + 1))
            continue
    else:
        raise last_exc or TimeoutError('EEA urls request timed out')
    ctype = (resp.headers.get('Content-Type') or '').lower()

    # 1) JSON variants (list of urls, or {"urls": [...]}, or objects containing url fields)
    try:
        data = resp.json()
        if isinstance(data, list):
            urls = []
            for item in data:
                if isinstance(item, str) and item.startswith(('http://', 'https://')):
                    urls.append(item)
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str) and v.startswith(('http://', 'https://')):
                            urls.append(v)
            if urls:
                return urls
        if isinstance(data, dict):
            urls_val = data.get('urls') or data.get('Urls') or data.get('parquetUrls') or data.get('ParquetUrls')
            if isinstance(urls_val, list):
                out = [u for u in urls_val if isinstance(u, str) and u.startswith(('http://', 'https://'))]
                if out:
                    return out
    except Exception:
        pass

    # 2) Text/CSV variants: extract any http(s) URL anywhere in the body.
    text = resp.text or ''
    urls = re.findall(r'https?://[^\\s\\\"\\)\\]]+', text)
    urls = [u.strip().strip('"') for u in urls if u.strip()]
    if urls:
        return urls

    preview = (text.strip().replace('\r', '')[:300]) if text else ''
    logger.warning('EEA urls returned 0 urls (ctype=%s, preview=%r)', ctype, preview)
    return []


def _download_parquet_url(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return pd.read_parquet(BytesIO(r.content), engine='pyarrow')


def _parse_eea_parquet_df(df: pd.DataFrame, city: str) -> pd.DataFrame:
    """Normalize a single EEA parquet dataframe (same schema as ZIP entries)."""
    if df is None or df.empty:
        return pd.DataFrame()
    cols = {c.lower(): c for c in df.columns}
    sampling_col = cols.get('samplingpoint')
    pollutant_col = cols.get('pollutant')
    start_col = cols.get('start')
    value_col = cols.get('value')
    unit_col = cols.get('unit')
    if not (sampling_col and pollutant_col and start_col and value_col):
        logger.warning('EEA parquet missing expected cols (cols=%s)', list(df.columns)[:20])
        return pd.DataFrame()
    validity_col = cols.get('validity')
    wanted = [sampling_col, pollutant_col, start_col, value_col]
    if unit_col:
        wanted.append(unit_col)
    if validity_col:
        wanted.append(validity_col)
    tmp = df[wanted].copy()
    new_cols = ['samplingpoint', 'pollutant_raw', 'timestamp', 'value']
    if unit_col:
        new_cols.append('unit_raw')
    if validity_col:
        new_cols.append('validity')
    tmp.columns = new_cols
    tmp['city'] = city
    tmp['source'] = 'eea'

    out = tmp
    out['timestamp'] = pd.to_datetime(out['timestamp'], utc=True, errors='coerce')
    out = out.dropna(subset=['timestamp', 'samplingpoint'])
    out['pollutant_raw'] = out['pollutant_raw']
    poll_id = pd.to_numeric(out['pollutant_raw'], errors='coerce')
    pollutant_norm = out['pollutant_raw'].astype(str).str.strip()
    mask_num = poll_id.notna()
    if mask_num.any():
        mapped = poll_id[mask_num].astype('int64').map(EEA_POLLUTANT_ID_TO_NOTATION)
        pollutant_norm.loc[mask_num] = mapped.fillna(pollutant_norm.loc[mask_num])
    out['pollutant'] = pollutant_norm.str.upper().str.replace(' ', '', regex=False)
    out['pollutant'] = out['pollutant'].map(EEA_POLLUTANT_MAP)
    out = out.dropna(subset=['pollutant'])
    out['value'] = pd.to_numeric(out['value'], errors='coerce')
    out.loc[out['value'] <= -998, 'value'] = pd.NA
    if 'validity' in out.columns:
        out['validity'] = pd.to_numeric(out['validity'], errors='coerce')
        out.loc[out['validity'] < 0, 'value'] = pd.NA
    out = out.dropna(subset=['value'])
    out['unit'] = out.get('unit_raw', 'µg/m³')
    out['unit'] = out['unit'].astype(str).str.strip().replace({'ug.m-3': 'µg/m³', 'µg/m3': 'µg/m³'})
    out = out.drop(columns=[c for c in ['unit_raw'] if c in out.columns])
    out = out.drop_duplicates(subset=['samplingpoint', 'timestamp', 'pollutant'])
    return out


@shared_task(bind=True, name='apps.pollution.tasks.fetch_eea_historical', max_retries=3)
def fetch_eea_historical(
    self,
    cities: list[str] | None = None,
    years: int = 5,
    max_files: int = 50,
):
    """Batch ingest historical air-quality data from EEA for selected French cities.

    Uses EEA Download Service async endpoint with month chunking and daily aggregation.
    """
    # If cities is empty/None -> no city filter (whole country).
    if cities is None:
        cities = []

    end_dt = datetime.now(tz=timezone.utc)
    start_dt = end_dt.replace(year=end_dt.year - years)

    windows = _month_windows(start_dt, end_dt)
    total_rows = 0
    urls_ingested = 0
    zip_windows_processed = 0

    # Iterate once for whole France if no city filter, else per-city.
    city_loop = cities if cities else ['']
    for city in city_loop:
        eea_city = _eea_city_value(city) if city else ''
        for w_start, w_end in windows:
            # For CLI debugging runs, reuse max_files as a cap on month windows when using ZIP mode.
            if max_files and zip_windows_processed >= max_files and not urls_ingested:
                logger.info('EEA zip mode reached max_files=%d windows, stopping early.', max_files)
                break

            dataset = _eea_dataset_for_year(w_start.year)
            request_body = {
                'countries': ['FR'],
                'cities': [eea_city] if eea_city else [],
                'pollutants': EEA_POLLUTANTS,
                'dataset': dataset,
                'dateTimeStart': _iso_z(w_start),
                'dateTimeEnd': _iso_z(w_end),
                'aggregationType': EEA_AGGREGATION_TYPE,
                'source': 'Customscript',
                'email': os.getenv('EEA_CONTACT_EMAIL', ''),
            }
            try:
                # Since 2025-10-01, EEA introduced a new backend engine; classic endpoints remain.
                # Default to classic for stability; can be overridden via EEA_DOWNLOAD_ENGINE=new.
                if EEA_ENGINE == 'new':
                    async_ep = '/ParquetFile/async'
                else:
                    async_ep = '/ParquetFile/async/classic'

                # EEA can be slow; retry a bit on timeouts.
                last_exc: Exception | None = None
                for attempt in range(3):
                    try:
                        resp = requests.post(
                            f'{EEA_DOWNLOADS_API}{async_ep}',
                            json=request_body,
                            timeout=EEA_REQUEST_TIMEOUT,
                        )
                        resp.raise_for_status()
                        break
                    except (req_exc.ReadTimeout, req_exc.ConnectTimeout) as exc:
                        last_exc = exc
                        time.sleep(2.0 * (attempt + 1))
                        continue
                else:
                    raise last_exc or TimeoutError('EEA async request timed out')
                resp.raise_for_status()
                async_url = resp.text.strip().strip('"')
                zip_bytes = _poll_eea_async(async_url)
            except Exception as exc:
                logger.warning('EEA download failed %s %s–%s: %s', city, w_start.date(), w_end.date(), exc)
                continue

            # Guardrail: skip bogus archives (empty/non-zip)
            if not _is_zip_bytes(zip_bytes):
                logger.warning(
                    'EEA download produced non-zip payload (%d bytes) for %s %s–%s',
                    len(zip_bytes),
                    city,
                    w_start.date(),
                    w_end.date(),
                )
                continue

            # ZIP can be empty (e.g., city mismatch). Retry once without city filter (whole country),
            # which is the most reliable mode.
            if len(zip_bytes) <= 64 and request_body.get('cities'):
                logger.warning(
                    'EEA returned tiny ZIP (%d bytes). Retrying without city filter (FR).',
                    len(zip_bytes),
                )
                request_body['cities'] = []
                try:
                    resp = requests.post(
                        f'{EEA_DOWNLOADS_API}{async_ep}',
                        json=request_body,
                        timeout=REQUEST_TIMEOUT,
                    )
                    resp.raise_for_status()
                    async_url = resp.text.strip().strip('"')
                    zip_bytes = _poll_eea_async(async_url)
                except Exception as exc:
                    logger.warning('EEA retry (no city) failed %s–%s: %s', w_start.date(), w_end.date(), exc)
                    continue

            # If ZIP is still empty/tiny, fall back to the stable URL list endpoint and ingest a limited number.
            if len(zip_bytes) <= 64:
                logger.warning(
                    'EEA ZIP empty/tiny (%d bytes). Falling back to ParquetFile/urls (dataset=%s).',
                    len(zip_bytes),
                    dataset,
                )
                try:
                    urls = _eea_urls(dataset=dataset, cities=request_body.get('cities') or [])
                except Exception as exc:
                    logger.warning('EEA urls endpoint failed: %s', exc)
                    continue

                if not urls:
                    logger.warning(
                        'EEA urls returned 0 urls (dataset=%s cities=%s)',
                        dataset,
                        request_body.get('cities'),
                    )
                    # urls endpoint ignores date windows; do not retry month-by-month.
                    break

                ts = datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')
                city_part = city if city else 'FR'
                bronze_urls_path = f'pollution/eea/bronze/{city_part}/{dataset}/urls_{ts}.txt'
                try:
                    upload_bytes_to_lake(
                        ('\\n'.join(urls)).encode('utf-8'),
                        bucket='urbanhub-raw',
                        path=bronze_urls_path,
                        content_type='text/plain',
                    )
                except Exception as exc:
                    logger.error('EEA urls bronze upload failed: %s', exc)

                for u in urls:
                    if urls_ingested >= max_files:
                        break
                    try:
                        df_raw = _download_parquet_url(u)
                        df_norm = _parse_eea_parquet_df(df_raw, city=(city or 'France'))
                        if df_norm.empty:
                            continue
                        total_rows += upsert_eea_df(df_norm)
                        urls_ingested += 1
                    except Exception as exc:
                        logger.warning('EEA parquet url failed (%s): %s', u, exc)
                    time.sleep(0.2)

                if urls_ingested >= max_files:
                    logger.info('EEA ingestion reached max_files=%d, stopping early.', max_files)
                    break

                # urls endpoint ignores date windows; do not loop month-by-month.
                break

            logger.info(
                'EEA download ok: city=%s dataset=%s window=%s..%s bytes=%d',
                city,
                dataset,
                w_start.date(),
                w_end.date(),
                len(zip_bytes),
            )
            zip_windows_processed += 1

            ts = datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')
            city_part = city if city else 'FR'
            bronze_path = f'pollution/eea/bronze/{city_part}/{dataset}/{w_start:%Y%m}_download_{ts}.zip'
            try:
                upload_bytes_to_lake(zip_bytes, bucket='urbanhub-raw', path=bronze_path, content_type='application/zip')
            except Exception as exc:
                logger.error('EEA bronze upload failed: %s', exc)

            df = _parse_eea_zip(zip_bytes, city=(city or 'France'))
            if df.empty:
                logger.warning(
                    'EEA parse produced 0 rows: city=%s dataset=%s window=%s..%s',
                    city,
                    dataset,
                    w_start.date(),
                    w_end.date(),
                )
                continue
            logger.info(
                'EEA parse ok: city=%s dataset=%s window=%s..%s rows=%d',
                city,
                dataset,
                w_start.date(),
                w_end.date(),
                len(df),
            )

            # Silver
            silver_path = f'pollution/eea/silver/{city_part}/{dataset}/{w_start:%Y%m}.parquet'
            try:
                upload_to_lake(df=df, bucket='urbanhub-processed', path=silver_path)
            except Exception as exc:
                logger.error('EEA silver upload failed: %s', exc)

            try:
                up = upsert_eea_df(df)
                total_rows += up
                logger.info(
                    'EEA upsert ok: city=%s dataset=%s window=%s..%s measurements=%d (total=%d)',
                    city,
                    dataset,
                    w_start.date(),
                    w_end.date(),
                    up,
                    total_rows,
                )
            except Exception as exc:
                logger.exception('EEA upsert failed: %s', exc)

            # be considerate to the service
            time.sleep(1.0)
        # If we fell back to urls (and thus broke the window loop), also stop the city loop.
        if urls_ingested:
            break

    logger.info('fetch_eea_historical: %d rows upserted', total_rows)
    return {
        'rows_upserted': total_rows,
        'cities': cities,
        'years': years,
        'urls_ingested': urls_ingested,
        'max_files': max_files,
    }
