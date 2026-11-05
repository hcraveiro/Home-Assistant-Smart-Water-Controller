"""Temporal accounting regressions using real DST-aware local timezones."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ha_stub  # noqa: F401
from custom_components.smart_water_controller.errors import APIConnectionError
from custom_components.smart_water_controller.weather_providers import owm, pirateweather
from custom_components.smart_water_controller.weather_providers.intervals import (
    RainInterval, remaining_day, remaining_rain,
)

UTC = timezone.utc
LISBON = ZoneInfo("Europe/Lisbon")


class IntervalTests(unittest.TestCase):
    def test_dst_calendar_days_have_real_elapsed_lengths(self):
        for month, day, hours in [(3, 29, 23), (10, 25, 25)]:
            now = datetime(2026, month, day, tzinfo=LISBON)
            start, end = remaining_day(now)
            self.assertEqual((end - start).total_seconds() / 3600, hours)
            intervals = [RainInterval(start, end, hours)]
            self.assertEqual(remaining_rain(intervals, now), hours)

    def test_repeated_hour_uses_elapsed_time(self):
        start = datetime(2026, 10, 25, 0, tzinfo=UTC)
        interval = RainInterval(start, start + timedelta(hours=3), 3)
        now = datetime(2026, 10, 25, 1, 30, tzinfo=LISBON, fold=1)
        self.assertEqual(remaining_rain([interval], now), 1.5)


class ProviderIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def test_owm_numeric_utc_preceding_interval_and_cache(self):
        now = datetime(2026, 7, 10, 14, tzinfo=LISBON)  # 13 UTC
        end = datetime(2026, 7, 10, 15, tzinfo=UTC)
        item = {"dt": end.timestamp(), "dt_txt": "2026-07-10 15:00:00",
                "rain": {"3h": 3}, "pop": 0.8}
        with patch.object(owm.dt_util, "now", return_value=now):
            provider = owm.OpenWeatherMapProvider("k", "1", "2", 30)
            with patch.object(owm, "fetch_json", return_value={"list": [item]}) as fetch:
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 2)
                self.assertTrue((await provider.will_it_rain())["will_rain"])
                self.assertEqual((await provider.get_forecast())[0], item)
                fetch.assert_called_once()
                # Cached raw data must be integrated against the new current instant.
                with patch.object(owm.dt_util, "now", return_value=now + timedelta(minutes=15)):
                    self.assertEqual(await provider.get_total_rain_forecast_for_today(), 1.75)

    async def test_owm_utc_next_date_interval_includes_local_evening(self):
        now = datetime(2026, 7, 10, 23, 30, tzinfo=LISBON)
        end = datetime(2026, 7, 11, 0, tzinfo=UTC)
        with patch.object(owm.dt_util, "now", return_value=now):
            provider = owm.OpenWeatherMapProvider("k", "1", "2", 5)
            with patch.object(owm, "fetch_json", return_value={"list": [
                {"dt": end.timestamp(), "dt_txt": "2026-07-11 00:00:00", "rain": {"3h": 6}}
            ]}):
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 1)

    async def test_owm_past_preceding_interval_is_not_future_rain(self):
        now = datetime(2026, 7, 10, 16, tzinfo=LISBON)
        with patch.object(owm.dt_util, "now", return_value=now):
            provider = owm.OpenWeatherMapProvider("k", "1", "2", 5)
            with patch.object(owm, "fetch_json", return_value={"list": [
                {"dt": now.timestamp(), "dt_txt": "ignored", "rain": {"3h": 9}, "pop": 1}
            ]}):
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 0)
                self.assertFalse((await provider.will_it_rain())["will_rain"])

    async def test_pirate_partial_bucket_is_not_prorated_twice(self):
        now = datetime(2026, 7, 10, 14, tzinfo=LISBON)
        payload = {"hourly": {"data": [
            {"time": now.timestamp(), "precipIntensity": 3, "precipProbability": 0.8}
        ]}, "currently": {}}
        with patch.object(pirateweather.dt_util, "now", return_value=now):
            provider = pirateweather.PirateWeatherProvider("k", "1", "2", 60)
            with patch.object(pirateweather, "fetch_json", return_value=payload) as fetch:
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 3)
                self.assertTrue((await provider.will_it_rain())["will_rain"])
                await provider.get_current_weather()
                with patch.object(pirateweather.dt_util, "now", return_value=now + timedelta(minutes=30)):
                    self.assertEqual(await provider.get_total_rain_forecast_for_today(), 1.5)
                fetch.assert_called_once()

    async def test_pirate_midnight_clips_hour(self):
        now = datetime(2026, 7, 10, 23, 45, tzinfo=LISBON)
        with patch.object(pirateweather.dt_util, "now", return_value=now):
            provider = pirateweather.PirateWeatherProvider("k", "1", "2", 5)
            payload = {"hourly": {"data": [{"time": now.timestamp(), "precipIntensity": 4}]}}
            with patch.object(pirateweather, "fetch_json", return_value=payload):
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 1)

    async def test_pirate_missing_hourly_is_unavailable_but_explicit_empty_is_valid(self):
        for payload in [{}, {"hourly": {}}, {"hourly": {"data": None}}]:
            provider = pirateweather.PirateWeatherProvider("k", "1", "2", 5)
            with patch.object(pirateweather, "fetch_json", return_value=payload):
                with self.assertRaises(APIConnectionError):
                    await provider.get_total_rain_forecast_for_today()
                self.assertIsNone(provider._last_forecast_fetch_time)
        for hourly in [[], [{"time": datetime.now(UTC).timestamp()}]]:
            provider = pirateweather.PirateWeatherProvider("k", "1", "2", 5)
            with patch.object(pirateweather, "fetch_json", return_value={"hourly": {"data": hourly}}):
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 0)

    async def test_pirate_dst_days_integrate_every_real_hour(self):
        for month, day, hours in [(3, 29, 23), (10, 25, 25)]:
            now = datetime(2026, month, day, tzinfo=LISBON)
            start, _ = remaining_day(now)
            payload = {"hourly": {"data": [
                {"time": (start + timedelta(hours=i)).timestamp(), "precipIntensity": 1}
                for i in range(hours + 1)
            ]}}
            with patch.object(pirateweather.dt_util, "now", return_value=now):
                provider = pirateweather.PirateWeatherProvider("k", "1", "2", 60)
                with patch.object(pirateweather, "fetch_json", return_value=payload):
                    self.assertEqual(await provider.get_total_rain_forecast_for_today(), hours)
                    blocks = await provider.get_forecast()
                    self.assertEqual(sum(b["rain"]["3h"] for b in blocks), hours)

    async def test_day_rollover_refreshes_both_providers_even_with_long_timeout(self):
        now = datetime(2026, 7, 10, 23, 59, tzinfo=LISBON)
        for module, cls, payload in [
            (owm, owm.OpenWeatherMapProvider, {"list": [{"dt": now.timestamp()}]}),
            (pirateweather, pirateweather.PirateWeatherProvider, {"hourly": {"data": []}}),
        ]:
            with patch.object(module.dt_util, "now", return_value=now):
                provider = cls("k", "1", "2", 60)
                with patch.object(module, "fetch_json", return_value=payload) as fetch:
                    await provider.get_forecast()
                    with patch.object(module.dt_util, "now", return_value=now + timedelta(minutes=2)):
                        await provider.get_forecast()
                    self.assertEqual(fetch.call_count, 2)

    async def test_restored_display_cache_requires_refresh(self):
        for module, cls, payload in [
            (owm, owm.OpenWeatherMapProvider, {"list": [{"dt": datetime.now(UTC).timestamp()}]}),
            (pirateweather, pirateweather.PirateWeatherProvider, {"hourly": {"data": []}}),
        ]:
            provider = cls("k", "1", "2", 5)
            provider.restore_cache(forecast=[{"dt_txt": "old", "rain": {"3h": 100}}], current=None)
            with patch.object(module, "fetch_json", return_value=payload) as fetch:
                self.assertEqual(await provider.get_total_rain_forecast_for_today(), 0)
                fetch.assert_called_once()
