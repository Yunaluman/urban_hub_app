"""
Urban Score calculator for UrbanHub.

Computes a daily composite quality-of-life score (0-100) for a given city
by combining air quality, weather comfort, and bike mobility signals.
"""

from datetime import date
from typing import TypedDict, Optional

from django.utils import timezone

from apps.meteo.models import WeatherObservation
from apps.pollution.models import PollutionMeasurement
from apps.mobility.models import BikeSnapshot


# ---------------------------------------------------------------------------
# Constants — WHO/operational thresholds (Open/Closed: extend here, not inside
# functions)
# ---------------------------------------------------------------------------

DEFAULT_CITY = 'Paris'

# WHO air-quality reference limits (µg/m³): (pollutant, clean_threshold, bad_threshold)
AIR_THRESHOLDS = {
    'pm25': (10.0, 75.0),
    'no2': (40.0, 200.0),
    'o3': (60.0, 180.0),
}

# Weather penalty thresholds
PRECIP_HEAVY_MM = 2.0       # mm  — heavy rain penalty
WIND_STRONG_MS = 15.0       # m/s — strong wind penalty
TEMP_COLD_C = 0.0           # °C  — cold penalty
TEMP_HOT_C = 35.0           # °C  — heat penalty

WEATHER_PENALTY_PRECIP = 30.0
WEATHER_PENALTY_WIND = 20.0
WEATHER_PENALTY_TEMP = 25.0

# Score label thresholds (inclusive lower bounds, descending order)
SCORE_LABELS = [
    (80.0, "Excellente journee urbaine"),
    (60.0, "Bonne journee"),
    (40.0, "Journee difficile"),
    (0.0,  "Evitez de sortir"),
]

# Composite weights (must sum to 1.0)
WEIGHT_AIR = 0.40
WEIGHT_WEATHER = 0.35
WEIGHT_MOBILITY = 0.25

# Neutral fallback score used when no data is available
NEUTRAL_SCORE = 50.0


# ---------------------------------------------------------------------------
# Return-type contract
# ---------------------------------------------------------------------------

class SubScore(TypedDict):
    score: float
    data_available: bool


class UrbanScoreResult(TypedDict):
    city: str
    air_score: float
    weather_score: float
    mobility_score: float
    global_score: float
    label: str
    updated_at: str
    data_available: bool
    air_data_available: bool
    weather_data_available: bool
    mobility_data_available: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_weather(city: str) -> Optional[object]:
    """Return the most recent WeatherObservation for the given city, or None."""
    return (
        WeatherObservation.objects
        .filter(station__city__iexact=city)
        .select_related('station')
        .first()
    )


def _latest_pollutant_value(city: str, pollutant: str) -> Optional[float]:
    """Return the most recent measurement value for a pollutant in the city, or None."""
    measurement = (
        PollutionMeasurement.objects
        .filter(sensor__city__iexact=city, pollutant=pollutant)
        .first()
    )
    return measurement.value if measurement else None


def _compute_air_score(city: str) -> SubScore:
    """
    Score 0-100 based on PM2.5, NO2, and O3 readings.

    Each available pollutant contributes equally; missing pollutants are skipped.
    Returns data_available=False (with NEUTRAL_SCORE) when no pollutant data
    exists so callers can distinguish "clean air" from "no data".
    """
    def linear_score(value: float, low: float, high: float) -> float:
        if value <= low:
            return 100.0
        if value >= high:
            return 0.0
        return 100.0 * (1.0 - (value - low) / (high - low))

    scores = []
    for pollutant, (low, high) in AIR_THRESHOLDS.items():
        value = _latest_pollutant_value(city, pollutant)
        if value is not None:
            scores.append(linear_score(value, low, high))

    if not scores:
        return SubScore(score=NEUTRAL_SCORE, data_available=False)

    return SubScore(score=round(sum(scores) / len(scores), 1), data_available=True)


def _compute_weather_score(obs) -> SubScore:
    """
    Score 0-100 based on a WeatherObservation object.
    Returns data_available=False (with NEUTRAL_SCORE) when obs is None.
    """
    if obs is None:
        return SubScore(score=NEUTRAL_SCORE, data_available=False)

    score = 100.0

    precip = obs.precipitation or 0.0
    wind = obs.wind_speed or 0.0
    temp = obs.temperature

    if precip > PRECIP_HEAVY_MM:
        score -= WEATHER_PENALTY_PRECIP
    if wind > WIND_STRONG_MS:
        score -= WEATHER_PENALTY_WIND
    if temp is not None and (temp < TEMP_COLD_C or temp > TEMP_HOT_C):
        score -= WEATHER_PENALTY_TEMP

    return SubScore(score=round(max(score, 0.0), 1), data_available=True)


def _compute_mobility_score(city: str) -> SubScore:
    """
    Score 0-100 based on average bike availability ratio across all BikeSnapshot
    records captured today for the given city.

    availability_ratio = bikes_available / total_slots  (per station)
    mobility_score = mean(availability_ratio) * 100

    Returns data_available=False (with NEUTRAL_SCORE) when no snapshots exist.
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
            ratios.append(snap.bikes_available / snap.total_slots)

    if not ratios:
        return SubScore(score=NEUTRAL_SCORE, data_available=False)

    return SubScore(score=round((sum(ratios) / len(ratios)) * 100, 1), data_available=True)


def _label_from_score(score: float) -> str:
    """Return a human-readable label for the given score.

    SCORE_LABELS is evaluated in descending threshold order so adding a new
    band only requires inserting a tuple — no existing logic changes needed.
    """
    for threshold, label in SCORE_LABELS:
        if score >= threshold:
            return label
    return SCORE_LABELS[-1][1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_urban_score(city: str = DEFAULT_CITY) -> UrbanScoreResult:
    """
    Compute a composite urban quality score for the given city.

    Always returns a fully-populated UrbanScoreResult. Individual sub-scores
    fall back to NEUTRAL_SCORE when data is unavailable, and the corresponding
    *_data_available flag is set to False so callers can surface a warning.

    Keys:
      air_score              : 0-100
      weather_score          : 0-100
      mobility_score         : 0-100
      global_score           : weighted average (air 40%, weather 35%, mobility 25%)
      label                  : human-readable quality label
      updated_at             : ISO-8601 timestamp
      data_available         : True only when all three sub-scores have real data
      air_data_available     : bool
      weather_data_available : bool
      mobility_data_available: bool
    """
    obs = _latest_weather(city)

    air = _compute_air_score(city)
    weather = _compute_weather_score(obs)
    mobility = _compute_mobility_score(city)

    global_score = round(
        air['score'] * WEIGHT_AIR
        + weather['score'] * WEIGHT_WEATHER
        + mobility['score'] * WEIGHT_MOBILITY,
        1,
    )

    return UrbanScoreResult(
        city=city,
        air_score=air['score'],
        weather_score=weather['score'],
        mobility_score=mobility['score'],
        global_score=global_score,
        label=_label_from_score(global_score),
        updated_at=timezone.now().isoformat(),
        data_available=air['data_available'] and weather['data_available'] and mobility['data_available'],
        air_data_available=air['data_available'],
        weather_data_available=weather['data_available'],
        mobility_data_available=mobility['data_available'],
    )
