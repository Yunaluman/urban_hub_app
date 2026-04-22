"""
Paris Open Data events fetcher for UrbanHub.

Fetches upcoming events from the official Paris Open Data API and caches
the result for one hour to avoid excessive API calls.
"""

from datetime import date, timedelta

import requests
from django.core.cache import cache


PARIS_EVENTS_API = (
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
    "/que-faire-a-paris-/records"
)
CACHE_KEY = "paris_events_today"
CACHE_TTL = 3600  # 1 hour in seconds


def _is_today_or_tomorrow(date_str: str) -> bool:
    """
    Return True when date_str (ISO-8601 prefix, e.g. '2026-04-20T...' or '2026-04-20')
    falls on today or tomorrow.
    """
    if not date_str:
        return False
    try:
        event_date = date.fromisoformat(date_str[:10])
        today = date.today()
        return today <= event_date <= today + timedelta(days=1)
    except ValueError:
        return False


def _parse_event(record: dict) -> dict:
    """
    Extract a clean event dict from a raw API record.
    """
    fields = record.get('fields', record)  # v2.1 returns flat fields at top level

    tags_raw = fields.get('tags', '') or ''
    tags = [t.strip() for t in tags_raw.split(';') if t.strip()] if isinstance(tags_raw, str) else tags_raw

    price_type = (fields.get('price_type') or '').lower()
    is_free = 'gratuit' in price_type or price_type == 'free'

    return {
        'title': fields.get('title') or fields.get('nom') or '',
        'date_start': fields.get('date_start') or '',
        'date_end': fields.get('date_end') or '',
        'address': fields.get('address_name') or fields.get('address') or '',
        'tags': tags,
        'url': fields.get('url') or '',
        'is_free': is_free,
    }


def fetch_paris_events() -> list:
    """
    Return a list of Paris events happening today or tomorrow.

    Results are cached under the key ``paris_events_today`` for one hour.
    Falls back to an empty list on network or parsing errors.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    today_str = date.today().isoformat()
    params = {
        'limit': 20,
        'where': f'date_start>="{today_str}"',
        'order_by': 'date_start ASC',
    }

    try:
        response = requests.get(PARIS_EVENTS_API, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception:
        # Return empty list — do not cache failures so the next request retries
        return []

    records = data.get('results', data.get('records', []))

    events = []
    for record in records:
        event = _parse_event(record)
        if _is_today_or_tomorrow(event['date_start']):
            events.append(event)

    cache.set(CACHE_KEY, events, CACHE_TTL)
    return events
