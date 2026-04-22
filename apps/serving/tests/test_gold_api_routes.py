"""
Vérifie que les routes JSON du dashboard passent bien par la couche Postgres Gold
lorsque DASHBOARD_GOLD_* est True : l’ORM est neutralisé (mock → DataFrame vide)
alors que les tables Gold contiennent des sentinelles reconnaissables.
"""

from __future__ import annotations

from datetime import date
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.serving import models as serving_models


@override_settings(
    DASHBOARD_GOLD_MOBILITY=True,
    DASHBOARD_GOLD_AIR=True,
    DASHBOARD_GOLD_CLIMATE=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    },
)
class GoldDashboardApiRoutesTests(TestCase):
    """Couvre toutes les routes api/dashboard qui lisent le Gold (hors cities + cross)."""

    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.as_of = timezone.now()
        a = cls.as_of
        # Mobilité
        serving_models.GoldMobilityStationLatest.objects.create(
            network_id='gold_net',
            station_id='GOLD_STATION_UI',
            network_city='Paris',
            station_name='Station Gold Test',
            latitude=48.86,
            longitude=2.35,
            bikes_available=7,
            free_slots=3,
            total_slots=10,
            util_rate=70.0,
            as_of=a,
        )
        for h in (9, 10):
            serving_models.GoldMobilityHourly.objects.create(
                city_key='Paris',
                hour=h,
                bikes_avg=float(h),
                slots_avg=1.0,
                as_of=a,
            )
        # Air
        serving_models.GoldAirSeriesDay.objects.create(
            city_key='Paris',
            pollutant='pm25',
            observation_date=date(2024, 6, 1),
            value_avg=12.3,
            value_max=20.0,
            as_of=a,
        )
        for hour, vavg in ((13, 10.0), (14, 15.5)):
            serving_models.GoldAirHourlyPattern.objects.create(
                city_key='Paris',
                pollutant='pm25',
                hour=hour,
                value_avg=vavg,
                as_of=a,
            )
        serving_models.GoldAirHotspot.objects.create(
            pollutant='pm25',
            sensor_id='GOLD_SENSOR_HOTSPOT',
            city='Paris',
            latitude=48.87,
            longitude=2.36,
            value_avg=22.0,
            pct_exceedance=50.0,
            as_of=a,
        )
        serving_models.GoldAirWhoExceedance.objects.create(
            city_key='Paris',
            pollutant='pm25',
            days_exceeded=42,
            as_of=a,
        )
        # Climat
        serving_models.GoldClimateTemperatureDay.objects.create(
            city_key='Paris',
            day=date(2024, 5, 1),
            temp_avg=18.0,
            temp_min=12.0,
            temp_max=24.0,
            as_of=a,
        )
        serving_models.GoldClimateSeasonalMonth.objects.create(
            city_key='Paris',
            month=6,
            temp_avg=20.5,
            as_of=a,
        )
        serving_models.GoldClimateExtremeDay.objects.create(
            city_key='Paris',
            std_factor=2.5,
            day=date(2024, 7, 1),
            temp=40.0,
            event_type='Canicule',
            as_of=a,
        )
        serving_models.GoldClimateVisibilitySample.objects.create(
            city_key='Paris',
            points_json=[
                {
                    'temperature': 20.0,
                    'wind_speed': 3.0,
                    'pressure': 1013.0,
                    'precipitation': 0.0,
                    'visibility': 10.0,
                }
            ],
            as_of=a,
        )

    def setUp(self):
        self.client = APIClient()

    def test_mobility_stations_reads_gold_not_orm(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(Q, '_orm_bike_stations_latest', return_value=pd.DataFrame()):
            r = self.client.get('/api/dashboard/mobility/stations/', {'city': 'Paris'})
        self.assertEqual(r.status_code, 200)
        stations = r.json()['stations']
        self.assertTrue(any(s.get('station_id') == 'GOLD_STATION_UI' for s in stations))

    def test_mobility_hourly_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(Q, '_orm_hourly_bike_usage', return_value=pd.DataFrame()):
            r = self.client.get('/api/dashboard/mobility/hourly/', {'city': 'Paris'})
        self.assertEqual(r.status_code, 200)
        hours = {h['hour'] for h in r.json()['hourly']}
        self.assertIn(9, hours)
        self.assertIn(10, hours)

    def test_mobility_critical_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(Q, '_orm_bike_stations_latest', return_value=pd.DataFrame()):
            r = self.client.get(
                '/api/dashboard/mobility/critical/',
                {'city': 'Paris', 'threshold': '80'},
            )
        self.assertEqual(r.status_code, 200)
        crit = r.json()['critical']
        self.assertTrue(
            any(c.get('station_name') == 'Station Gold Test' for c in crit)
        )

    def test_air_series_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(
            Q, '_orm_pollution_series', return_value=pd.DataFrame()
        ):
            r = self.client.get(
                '/api/dashboard/air/series/',
                {
                    'city': 'Paris',
                    'pollutant': 'pm25',
                    'start': '2024-06-01',
                    'end': '2024-06-01',
                },
            )
        self.assertEqual(r.status_code, 200)
        series = r.json()['series']
        self.assertTrue(any(abs(float(s.get('value_avg', 0)) - 12.3) < 0.01 for s in series))

    def test_air_hourly_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(
            Q, '_orm_pollution_daily_pattern', return_value=pd.DataFrame()
        ):
            r = self.client.get(
                '/api/dashboard/air/hourly/',
                {'city': 'Paris', 'pollutant': 'pm25'},
            )
        self.assertEqual(r.status_code, 200)
        hourly = r.json()['hourly']
        self.assertTrue(any(h.get('hour') == 14 for h in hourly))

    def test_air_hotspots_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(
            Q, '_orm_pollution_hotspots', return_value=pd.DataFrame()
        ):
            r = self.client.get('/api/dashboard/air/hotspots/', {'pollutant': 'pm25'})
        self.assertEqual(r.status_code, 200)
        spots = r.json()['hotspots']
        self.assertTrue(any(float(s.get('value_avg', 0)) == 22.0 for s in spots))

    def test_air_who_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(Q, '_orm_who_exceedance_days', return_value=pd.DataFrame()):
            r = self.client.get('/api/dashboard/air/who/', {'city': 'Paris'})
        self.assertEqual(r.status_code, 200)
        exc = r.json()['exceedances']
        self.assertTrue(any(e.get('days_exceeded') == 42 for e in exc))

    def test_climate_temperature_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(
            Q, '_orm_temperature_series', return_value=pd.DataFrame()
        ):
            r = self.client.get(
                '/api/dashboard/climate/temperature/',
                {
                    'city': 'Paris',
                    'start': '2024-05-01',
                    'end': '2024-05-01',
                },
            )
        self.assertEqual(r.status_code, 200)
        series = r.json()['series']
        self.assertTrue(any(abs(float(s.get('temp_avg', 0)) - 18.0) < 0.01 for s in series))

    def test_climate_seasonal_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(Q, '_orm_seasonal_stats', return_value=pd.DataFrame()):
            r = self.client.get('/api/dashboard/climate/seasonal/')
        self.assertEqual(r.status_code, 200)
        rows = r.json()['seasonal']
        self.assertTrue(
            any(rw.get('city') == 'Paris' and rw.get('month') == 6 for rw in rows)
        )

    def test_climate_extremes_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(Q, '_orm_extreme_days', return_value=pd.DataFrame()):
            r = self.client.get(
                '/api/dashboard/climate/extremes/',
                {'city': 'Paris', 'std_factor': '2.5'},
            )
        self.assertEqual(r.status_code, 200)
        ex = r.json()['extremes']
        self.assertTrue(any(e.get('type') == 'Canicule' for e in ex))

    def test_climate_visibility_reads_gold(self):
        import pandas as pd

        from apps.dashboard.services import queries as Q

        with mock.patch.object(
            Q, '_orm_meteo_visibility_corr', return_value=pd.DataFrame()
        ):
            r = self.client.get('/api/dashboard/climate/visibility/', {'city': 'Paris'})
        self.assertEqual(r.status_code, 200)
        pts = r.json()['points']
        self.assertEqual(len(pts), 1)
        self.assertAlmostEqual(pts[0]['temperature'], 20.0)
