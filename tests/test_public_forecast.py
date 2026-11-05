"""Recorder-safe presentation without changing provider/accounting caches."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from zoneinfo import ZoneInfo

import ha_stub
from custom_components.smart_water_controller.weather_providers.public_forecast import compact_today_forecast


class PublicForecastTests(unittest.TestCase):
    def test_owm_keeps_midnight_end_block_not_tomorrows_rain(self):
        now = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
        source = []
        for hour in (18, 21, 24, 27):
            end = now.replace(hour=0) + timedelta(hours=hour)
            source.append({'dt': int(end.timestamp()), 'dt_txt': end.strftime('%Y-%m-%d %H:%M:%S'),
                           'pop': .7, 'rain': {'3h': 2}, 'main': {'padding': 'x' * 20000}})
        before = deepcopy(source)
        rows = compact_today_forecast(source, now)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]['end'], '2026-11-06T00:00:00+00:00')
        self.assertEqual(rows[-1]['start'], '2026-11-05T21:00:00+00:00')
        self.assertEqual(rows[0]['rain'], {'3h': 2.0})
        self.assertNotIn('main', rows[0])
        self.assertEqual(source, before)
        self.assertLess(len(json.dumps({'forecast': rows}).encode()), 8192)

    def test_pirateweather_keeps_legacy_fields_without_double_proration(self):
        now = datetime(2026, 11, 5, 14, tzinfo=ZoneInfo('Europe/Lisbon'))
        source = [{'dt_txt': '2026-11-05 00:00:00', 'pop': 0, 'rain': {'3h': 0}},
                  {'dt_txt': '2026-11-05 12:00:00', 'pop': .8, 'rain': {'3h': 3}},
                  {'dt_txt': '2026-11-05 15:00:00', 'pop': .2, 'rain': {'3h': 1}},
                  {'dt_txt': '2026-11-06 00:00:00', 'pop': 1, 'rain': {'3h': 20}}]
        rows = compact_today_forecast(source, now)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['rain']['3h'], 3)
        self.assertEqual(rows[0]['dt_txt'], source[1]['dt_txt'])
        self.assertEqual(rows[0]['pop'], .8)
        self.assertEqual(rows[0]['end'], '2026-11-05T15:00:00+00:00')

    def test_dst_midnight_is_local_not_utc(self):
        now = datetime(2026, 3, 29, 20, tzinfo=ZoneInfo('Europe/Lisbon'))
        rows = compact_today_forecast([
            {'dt': int(datetime(2026, 3, 30, 0, tzinfo=timezone.utc).timestamp()), 'rain': {'3h': 3}},
            {'dt': int(datetime(2026, 3, 30, 3, tzinfo=timezone.utc).timestamp()), 'rain': {'3h': 30}},
        ], now)
        self.assertEqual(len(rows), 1)  # 21–00 UTC overlaps local day until 23 UTC

    def test_recorder_bound_and_malformed_rows(self):
        now = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
        valid = {'dt': int(now.replace(hour=18).timestamp()), 'pop': .8, 'rain': {'3h': 2},
                 'dt_txt': 'x' * 100000, 'weather': ['x' * 100000]}
        invalid = [None, {}, {'dt': 'bad'}, {'dt': 1e300}, {**valid, 'pop': float('nan')}]
        rows = compact_today_forecast(invalid + [valid] * 100, now)
        self.assertEqual(len(rows), 32)
        self.assertLess(len(json.dumps({'forecast': rows}, allow_nan=False).encode()), 8192)

    def test_empty_and_old_snapshots(self):
        now = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
        self.assertEqual(compact_today_forecast([], now), [])
        self.assertEqual(compact_today_forecast([{'dt_txt': '2026-11-04 21:00:00'}], now), [])
