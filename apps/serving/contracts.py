"""
Contrats « Gold serving » : grain, clés, colonnes alignées sur l’API dashboard.

Lecture nominale : Postgres (tables ci-dessous). MinIO : Parquet d’audit / export
(même sémantique, écrit par les tâches ``publish_gold_*``).

Convention ``city_key`` : chaîne vide ``''`` = agrégat national (équivalent
``__all__`` côté mobilité). Sinon nom de ville canonique (ex. ``Paris``).

Chaque ligne porte ``as_of`` (timestamptz du run de publication) pour la
fraîcheur et le debug.
"""

# --- Mobilité (api_views Mobility*) ---

# GoldMobilityStationLatest
#   Grain : 1 ligne par (network_id, station_id) — dernier snapshot ingéré.
#   Unique : (network_id, station_id)
#   Colonnes API : station_id, station_name, latitude, longitude,
#                  bikes_available, free_slots, total_slots, util_rate
#   + network_id (interne), network_city (filtre ville / national)

# GoldMobilityHourly
#   Grain : 1 ligne par (city_key, hour) hour in 0..23
#   Unique : (city_key, hour)
#   Colonnes API : hour, bikes_avg, slots_avg

# Mobilité « critical » : filtre ``util_rate < threshold`` en lecture depuis
#   GoldMobilityStationLatest (pas de table dédiée).

# --- Air (api_views Air*) ---

# GoldAirSeriesDay
#   Grain : 1 ligne par (city_key, pollutant, observation_date)
#   Unique : (city_key, pollutant, observation_date)
#   Colonnes API : date, value_avg, value_max

# GoldAirHourlyPattern
#   Grain : (city_key, pollutant, hour)
#   Unique : (city_key, pollutant, hour)
#   Colonnes API : hour, value_avg

# GoldAirHotspot
#   Grain : 1 ligne par (pollutant, sensor_id)
#   Unique : (pollutant, sensor_id)
#   Colonnes API : city, latitude, longitude, value_avg, pct_exceedance

# GoldAirWhoExceedance
#   Grain : (city_key, pollutant)
#   Unique : (city_key, pollutant)
#   Colonnes API : pollutant, days_exceeded

# --- Climat (api_views Climate*) ---

# GoldClimateTemperatureDay
#   Grain : (city_key, day)
#   Unique : (city_key, day)
#   Colonnes API : date, temp_avg, temp_min, temp_max

# GoldClimateSeasonalMonth
#   Grain : (city_key, month)  month 1..12
#   Unique : (city_key, month)
#   Colonnes API : city, month, temp_avg  (city renommé depuis city_key)

# GoldClimateExtremeDay
#   Grain : (city_key, std_factor, day, event_type)
#   Unique : (city_key, std_factor, day, event_type)
#   Colonnes API : city, date, temp, type

# GoldClimateVisibilitySample
#   Grain : 1 ligne par city_key (échantillon sérialisé JSON)
#   Unique : (city_key)
#   Colonnes API : points (liste de dicts température, vent, etc.)

# --- Cross ---
# Déjà servi par ``apps.analysis.models.CrossAnalysisResult`` + Parquet dans
# ``apps.analysis.tasks`` ; pas de duplication ici.
