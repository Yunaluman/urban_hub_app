# Seuils OMS / EU (µg/m³) — alignés sur src/dashboard/config.py
WHO_THRESHOLDS = {
    'pm25': 15,
    'pm10': 45,
    'no2': 10,
    'o3': 60,
    'co': 4000,
}

EU_THRESHOLDS = {
    'pm25': 25,
    'pm10': 40,
    'no2': 40,
    'o3': 120,
    'co': 10000,
}

POLLUTANT_LABELS = {
    'pm25': 'PM2.5',
    'pm10': 'PM10',
    'no2': 'NO₂',
    'o3': 'O₃',
    'co': 'CO',
}

POLLUTANTS = list(POLLUTANT_LABELS.keys())
