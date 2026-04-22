"""Tests légers : contrats documentés + helpers MinIO (mock)."""

from unittest.mock import patch

from django.test import SimpleTestCase


class ContractsModuleTests(SimpleTestCase):
    def test_gold_models_registered(self):
        from apps.serving import models

        self.assertTrue(hasattr(models, 'GoldMobilityStationLatest'))
        self.assertTrue(hasattr(models, 'GoldAirSeriesDay'))


class LakeReaderTests(SimpleTestCase):
    @patch('apps.utils.lake_readers.list_objects', return_value=[])
    def test_load_latest_empty(self, _mock_list):
        from apps.utils.lake_readers import load_latest_parquet_under_prefix

        df, key = load_latest_parquet_under_prefix('urbanhub-analytics', 'mobility/')
        self.assertTrue(df.empty)
        self.assertIsNone(key)

    @patch('apps.utils.lake_readers.download_from_lake')
    @patch('apps.utils.lake_readers.list_objects', return_value=['a/x_1.parquet', 'a/x_2.parquet'])
    def test_load_latest_picks_last_key(self, _mock_list, mock_dl):
        import pandas as pd

        from apps.utils.lake_readers import load_latest_parquet_under_prefix

        mock_dl.return_value = pd.DataFrame({'v': [1]})
        df, key = load_latest_parquet_under_prefix('urbanhub-analytics', 'a/')
        mock_dl.assert_called_once_with('urbanhub-analytics', 'a/x_2.parquet')
        self.assertEqual(key, 'a/x_2.parquet')
        self.assertEqual(len(df), 1)
