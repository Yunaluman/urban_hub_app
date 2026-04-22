"""
Outfit advisor for UrbanHub.

Recommends clothing based on the latest weather observation and pollution level
for a given city.
"""

from typing import Optional, Tuple, List

from apps.meteo.models import WeatherObservation
from apps.pollution.models import PollutionMeasurement


# ---------------------------------------------------------------------------
# Constants — thresholds (Open/Closed: add/reorder tuples, never touch the
# lookup logic inside _base_outfit)
# ---------------------------------------------------------------------------

DEFAULT_CITY = 'Paris'

# Temperature bands: list of (upper_bound_exclusive, advice_label, items, emoji).
# Evaluated in ascending order; the last entry acts as the "else" (no upper bound).
# To add a new band: insert a new tuple in the right position — no other change needed.
TEMP_BANDS: List[Tuple[Optional[float], str, List[str], str]] = [
    (0.0,  "Grand froid", ["doudoune epaisse", "bonnet", "gants", "echarpe", "bottes"], "cold_face"),
    (8.0,  "Froid",       ["manteau", "pull chaud", "gants", "echarpe"],                "coat"),
    (14.0, "Frais",       ["veste", "pull", "jeans"],                                   "fallen_leaf"),
    (20.0, "Doux",        ["veste legere", "t-shirt", "baskets"],                       "slightly_smiling_face"),
    (28.0, "Chaud",       ["t-shirt", "short ou robe", "lunettes de soleil"],           "sunny"),
    (None, "Tres chaud",  ["vetements legers", "creme solaire", "chapeau", "bouteille d'eau"], "sunlight"),
]

# Weather alert thresholds
RAIN_ALERT_MM = 1.0          # mm  — rain alert threshold
COLD_RAIN_TEMP_C = 8.0       # °C  — below this, recommend waterproof coat instead of umbrella
WIND_ALERT_MS = 10.0         # m/s — strong wind alert threshold
PM25_ALERT_UGM3 = 25.0       # µg/m³ — pollution mask recommendation threshold


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_weather(city: str):
    return (
        WeatherObservation.objects
        .filter(station__city__iexact=city)
        .select_related('station')
        .first()
    )


def _latest_pm25(city: str) -> Optional[float]:
    m = (
        PollutionMeasurement.objects
        .filter(sensor__city__iexact=city, pollutant='pm25')
        .first()
    )
    return m.value if m else None


def _base_outfit(temp: float) -> Tuple[str, List[str], str]:
    """
    Return (advice, items, emoji) for the given temperature.

    Uses TEMP_BANDS — a table of threshold tuples — so adding new bands
    requires only a new entry in the constant, not a change to this function
    (Open/Closed Principle).
    """
    for upper_bound, advice, items, emoji in TEMP_BANDS:
        if upper_bound is None or temp < upper_bound:
            return advice, list(items), emoji
    # Unreachable if TEMP_BANDS ends with None upper_bound, but keeps mypy happy.
    _, advice, items, emoji = TEMP_BANDS[-1]
    return advice, list(items), emoji


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_outfit_advice(city: str = DEFAULT_CITY) -> dict:
    """
    Return clothing recommendations for the given city.

    Always returns a dict with consistent keys regardless of data availability:
      advice  : temperature category label (or unavailability message)
      items   : list of recommended clothing/accessory strings
      emoji   : shorthand emoji name for the temperature range
      alert   : additional warning string, or None
      data_available : bool — False when no weather observation exists
    """
    obs = _latest_weather(city)
    pm25 = _latest_pm25(city)

    if obs is None or obs.temperature is None:
        return {
            'advice': 'Donnees meteorologiques indisponibles',
            'items': [],
            'emoji': 'question',
            'alert': None,
            'data_available': False,
        }

    temp = obs.temperature
    precip = obs.precipitation or 0.0
    wind = obs.wind_speed or 0.0

    advice, items, emoji = _base_outfit(temp)

    alerts = []

    # Rain adjustment
    if precip > RAIN_ALERT_MM:
        items.append('impermeable' if temp < COLD_RAIN_TEMP_C else 'parapluie')
        alerts.append('Pluie prevue')

    # Wind alert
    if wind > WIND_ALERT_MS:
        alerts.append('Vent fort — evitez le velo')

    # Pollution alert
    if pm25 is not None and pm25 > PM25_ALERT_UGM3:
        alerts.append('Pollution elevee — masque recommande pour le sport outdoor')

    return {
        'advice': advice,
        'items': items,
        'emoji': emoji,
        'alert': ' | '.join(alerts) if alerts else None,
        'data_available': True,
    }
