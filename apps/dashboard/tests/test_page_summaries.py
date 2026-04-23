from django.test import TestCase, override_settings
from django.utils import timezone

from apps.meteo.models import WeatherObservation, WeatherStation
from apps.mobility.models import BikeNetwork, BikeStation, BikeSnapshot
from apps.serving.models import GoldDashboardPageSummary


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
)
class DashboardPageSummaryTests(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.as_of = timezone.now()
        GoldDashboardPageSummary.objects.create(
            page='home',
            city_key='Paris',
            payload_json={
                'meteo_station_count': 321,
                'bike_network_count': 654,
                'pollution_sensor_count': 987,
                'total_observations': 888777,
                'last_meteo_update': cls.as_of.isoformat(),
                'last_mobility_update': cls.as_of.isoformat(),
                'last_pollution_update': cls.as_of.isoformat(),
                'last_update': cls.as_of.isoformat(),
            },
            as_of=cls.as_of,
        )
        GoldDashboardPageSummary.objects.create(
            page='climate',
            city_key='Paris',
            payload_json={
                'station_count': 42,
                'observation_count': 4242,
                'hot_days': 7,
                'cold_days': 3,
                'last_update': cls.as_of.isoformat(),
            },
            as_of=cls.as_of,
        )

        station = WeatherStation.objects.create(
            station_id='PARIS_GOLD_HOME',
            name='Paris Gold Home',
            city='Paris',
            latitude=48.85,
            longitude=2.35,
        )
        network = BikeNetwork.objects.create(network_id='paris_net', name='Paris Net', city='Paris')
        bike_station = BikeStation.objects.create(
            network=network,
            station_id='paris_station',
            station_name='Paris Station',
            latitude=48.86,
            longitude=2.36,
        )
        WeatherObservation.objects.create(
            station=station,
            timestamp=cls.as_of,
            temperature=21.5,
            wind_speed=4.0,
            pressure=1012.0,
            precipitation=0.0,
            visibility=10.0,
        )
        BikeSnapshot.objects.create(
            station=bike_station,
            timestamp=cls.as_of,
            bikes_available=5,
            free_slots=5,
            total_slots=10,
        )

    def test_summary_endpoint_returns_gold_payload(self):
        response = self.client.get('/api/dashboard/summary/climate/', {'city': 'Paris'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['station_count'], 42)
        self.assertEqual(payload['observation_count'], 4242)
        self.assertEqual(payload['hot_days'], 7)

    def test_dashboard_home_renders_gold_summary_values(self):
        response = self.client.get('/', {'city': 'Paris'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('888777', content)
        self.assertIn('321', content)
        self.assertIn('654', content)
