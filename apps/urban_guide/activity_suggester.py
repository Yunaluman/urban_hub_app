"""
Activity suggester for UrbanHub.

Combines weather, pollution, and bike availability data to propose appropriate
activities for the day in a given city.
"""

import logging
from datetime import date
from typing import Optional

from apps.meteo.models import WeatherObservation
from apps.pollution.models import PollutionMeasurement
from apps.mobility.models import BikeSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CITY = 'Paris'

OUTDOOR_TEMP_MIN_C = 8.0        # °C  — minimum comfortable outdoor temperature
OUTDOOR_TEMP_MAX_C = 30.0       # °C  — maximum comfortable outdoor temperature
OUTDOOR_PRECIP_MAX_MM = 2.0     # mm  — precipitation above this = not outdoor-friendly
OUTDOOR_PM25_MAX = 25.0         # µg/m³ — PM2.5 above this = high pollution
OUTDOOR_WIND_MAX_MS = 12.0      # m/s — wind above this = not outdoor-friendly
BIKE_MIN_AVAILABILITY_PCT = 30.0  # %  — minimum bike availability to recommend cycling
RAIN_THRESHOLD_MM = 2.0         # mm  — precipitation at or above this = rainy
VISIBILITY_SUNNY_MIN_KM = 5.0   # km  — minimum visibility to consider it "sunny"


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


def _avg_bike_availability_pct(city: str) -> float:
    """
    Returns the average bike availability percentage across all stations today.
    Returns 0.0 when no data is present.
    """
    today = date.today()
    snapshots = (
        BikeSnapshot.objects
        .filter(
            station__network__city__iexact=city,
            timestamp__date=today,
        )
        .select_related('station')
    )

    ratios = []
    for snap in snapshots:
        if snap.total_slots and snap.total_slots > 0:
            ratios.append((snap.bikes_available / snap.total_slots) * 100)

    return round(sum(ratios) / len(ratios), 1) if ratios else 0.0


def _build_suggestions(outdoor_ok: bool, bike_recommended: bool, is_rainy: bool,
                       high_pollution: bool, is_sunny: bool) -> list:
    """
    Build an ordered list of activity suggestion dicts.
    Each dict: {name, type, reason, emoji}
    """
    suggestions = []

    if high_pollution:
        suggestions += [
            {
                'name': 'Sport en salle',
                'type': 'indoor',
                'reason': 'Pollution elevee — limitez l\'exposition exterieure',
                'emoji': 'weight_lifter',
            },
            {
                'name': 'Musee d\'Orsay',
                'type': 'indoor',
                'reason': 'Art impressionniste dans un air pur',
                'emoji': 'frame_with_picture',
            },
            {
                'name': 'Shopping galeries',
                'type': 'indoor',
                'reason': 'Passages couverts de Paris — loin de la pollution',
                'emoji': 'shopping_bags',
            },
        ]
        return suggestions

    if is_rainy:
        suggestions += [
            {
                'name': 'Musee du Louvre',
                'type': 'indoor',
                'reason': 'Ideal par temps pluvieux — des heures de decouverte',
                'emoji': 'classical_building',
            },
            {
                'name': 'Centre Pompidou',
                'type': 'indoor',
                'reason': 'Art moderne et vue sur Paris depuis le toit',
                'emoji': 'performing_arts',
            },
            {
                'name': 'Cafe lecture',
                'type': 'indoor',
                'reason': 'Pluie et un bon livre — combo parfait',
                'emoji': 'books',
            },
            {
                'name': 'Cinema',
                'type': 'indoor',
                'reason': 'Paris compte des centaines de salles indépendantes',
                'emoji': 'clapper_board',
            },
        ]
        return suggestions

    if outdoor_ok and is_sunny:
        suggestions += [
            {
                'name': 'Pique-nique au Champ de Mars',
                'type': 'outdoor',
                'reason': 'Beau temps — parfait face a la Tour Eiffel',
                'emoji': 'herb',
            },
            {
                'name': 'Velo le long de la Seine',
                'type': 'outdoor',
                'reason': 'Pistes cyclables ombragees et vue sur la Seine',
                'emoji': 'bicycle',
            },
            {
                'name': 'Terrasse cafe',
                'type': 'outdoor',
                'reason': 'Profitez du soleil parisien en terrasse',
                'emoji': 'coffee',
            },
        ]
        if bike_recommended:
            suggestions.append({
                'name': 'Balade Velib en ville',
                'type': 'outdoor',
                'reason': 'Velos disponibles et conditions ideales',
                'emoji': 'racing_bicycle',
            })
        return suggestions

    if outdoor_ok and not is_sunny:
        suggestions += [
            {
                'name': 'Marche d\'Aligre',
                'type': 'outdoor',
                'reason': 'Marche local anime — pas besoin de soleil',
                'emoji': 'shopping_cart',
            },
            {
                'name': 'Balade Montmartre',
                'type': 'outdoor',
                'reason': 'Ruelles et atmosphere boheme, ideales par temps doux',
                'emoji': 'artist_palette',
            },
            {
                'name': 'Terrasse couverte',
                'type': 'outdoor',
                'reason': 'Un verre a l\'abri sous une pergola',
                'emoji': 'beer_mug',
            },
        ]
        return suggestions

    # Fallback: indoor options when nothing else fits
    suggestions += [
        {
            'name': 'Musee du Louvre',
            'type': 'indoor',
            'reason': 'Incontournable par n\'importe quel temps',
            'emoji': 'classical_building',
        },
        {
            'name': 'Galeries Lafayette',
            'type': 'indoor',
            'reason': 'Architecture Art Deco et shopping sous la coupole',
            'emoji': 'shopping_bags',
        },
    ]
    return suggestions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_activities(city: str = 'Paris') -> dict:
    """
    Suggest activities for the given city based on current conditions.

    Result keys:
      outdoor_ok       : bool — safe and pleasant to be outside
      bike_recommended : bool — conditions good and bikes available
      suggestions      : list of activity dicts (name, type, reason, emoji)
    """
    obs = _latest_weather(city)
    pm25 = _latest_pm25(city)
    avg_bike_pct = _avg_bike_availability_pct(city)

    temp = obs.temperature if obs else None
    precip = (obs.precipitation or 0.0) if obs else 0.0
    wind = (obs.wind_speed or 0.0) if obs else 0.0
    pm25_val = pm25 if pm25 is not None else 0.0

    outdoor_ok = (
        temp is not None
        and 8 <= temp <= 30
        and precip < 2
        and pm25_val < 25
        and wind < 12
    )

    bike_recommended = outdoor_ok and avg_bike_pct > 30

    is_rainy = precip >= 2
    high_pollution = pm25_val >= 25
    # Consider sunny when no significant precipitation and not cloudy (proxy via visibility)
    visibility = (obs.visibility or 10.0) if obs else 10.0
    is_sunny = not is_rainy and visibility >= 5.0

    suggestions = _build_suggestions(
        outdoor_ok=outdoor_ok,
        bike_recommended=bike_recommended,
        is_rainy=is_rainy,
        high_pollution=high_pollution,
        is_sunny=is_sunny,
    )

    return {
        'outdoor_ok': outdoor_ok,
        'bike_recommended': bike_recommended,
        'avg_bike_availability_pct': avg_bike_pct,
        'suggestions': suggestions,
    }
