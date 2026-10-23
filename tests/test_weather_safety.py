"""Regression tests for weather sampling and failed provider readings."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ha_stub  # noqa: F401 - installs minimal Home Assistant interfaces
from custom_components.smart_water_controller.runtime import weather_runtime


class BrokenWeatherAPI:
    async def will_it_rain(self):
        raise RuntimeError("forecast unavailable")

    async def is_raining(self):
        raise RuntimeError("current weather unavailable")

    async def get_total_rain_forecast_for_today(self):
        raise RuntimeError("rain total unavailable")


def make_coordinator(api=None):
    return SimpleNamespace(
        hass=SimpleNamespace(async_create_task=lambda coro: None),
        weather_api=api,
        controller_mac_address="test-controller",
        poll_interval=60,
        is_raining_now=False,
        sprinkle_with_rain=False,
        stations=[SimpleNamespace(state="Sprinkling")],
        rain_total_amount_today=0.0,
        rain_time_today=0.0,
        has_rained_today=False,
        last_rain=None,
        rain_total_amount_forecasted_today=0.0,
    )


class RainSamplingTests(unittest.TestCase):
    def test_first_sample_does_not_invent_elapsed_rain(self):
        coordinator = make_coordinator()
        runtime = weather_runtime.WeatherRuntime(coordinator)
        runtime._rain_rate_mm_per_hour = 6.0
        coordinator.is_raining_now = True

        self.assertEqual(runtime._elapsed_rain_minutes(), 0.0)
        runtime._accumulate_rain(0.0)
        self.assertEqual(coordinator.rain_total_amount_today, 0.0)

    def test_last_rain_does_not_advance_without_new_precipitation(self):
        coordinator = make_coordinator()
        runtime = weather_runtime.WeatherRuntime(coordinator)
        coordinator.is_raining_now = True
        coordinator.rain_total_amount_today = 1.2
        previous_rain = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)
        coordinator.last_rain = previous_rain
        runtime._rain_rate_mm_per_hour = 0.0

        runtime._accumulate_rain(1.0)
        self.assertEqual(coordinator.last_rain, previous_rain)
        self.assertEqual(coordinator.rain_total_amount_today, 1.2)

    def test_elapsed_time_is_capped(self):
        coordinator = make_coordinator()
        runtime = weather_runtime.WeatherRuntime(coordinator)
        first = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)
        with patch.object(weather_runtime.dt_util, "now", return_value=first):
            self.assertEqual(runtime._elapsed_rain_minutes(), 0.0)
        with patch.object(weather_runtime.dt_util, "now", return_value=first + timedelta(hours=1)):
            self.assertEqual(runtime._elapsed_rain_minutes(), 2.0)


class ProviderFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_rain_does_not_stop_active_irrigation(self):
        coordinator = make_coordinator(BrokenWeatherAPI())
        coordinator.is_raining_now = True
        coordinator.rain_total_amount_today = 1.1
        runtime = weather_runtime.WeatherRuntime(coordinator)
        runtime._rain_rate_mm_per_hour = 3.0
        calls = []
        runtime._stop_irrigation_for_rain = lambda: calls.append("stop")

        with self.assertLogs(weather_runtime._LOGGER, level="WARNING"):
            await runtime.refresh()

        self.assertFalse(runtime._current_weather_fresh)
        self.assertEqual(calls, [])
        self.assertEqual(coordinator.rain_total_amount_today, 1.1)


if __name__ == "__main__":
    unittest.main()
