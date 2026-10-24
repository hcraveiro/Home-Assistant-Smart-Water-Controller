"""Regression tests for weather HTTP failures and OpenWeatherMap rain units."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ha_stub  # noqa: F401
from custom_components.smart_water_controller.errors import APIConnectionError
from custom_components.smart_water_controller.weather_providers import common, pirateweather, owm


class FakeResponse:
    def __init__(self, status=200, payload=None, error=None):
        self.status = status
        self.payload = {"rain": 1} if payload is None else payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload

    async def text(self):
        return "provider rejected the request"


class FakeRequest:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.response

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeRequest(self.response, self.error)


class WeatherHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_401_is_rejected_not_interpreted_as_weather(self):
        with self.assertRaisesRegex(APIConnectionError, "HTTP 401"):
            await common._decode_json(FakeResponse(status=401), "Current weather")

    async def test_http_503_is_rejected(self):
        with self.assertRaisesRegex(APIConnectionError, "HTTP 503"):
            await common._decode_json(FakeResponse(status=503), "Forecast")

    async def test_invalid_json_has_domain_specific_error(self):
        with self.assertRaisesRegex(APIConnectionError, "JSON format invalid"):
            await common._decode_json(
                FakeResponse(error=ValueError("invalid response")), "Forecast"
            )

    async def test_successful_json_is_returned(self):
        payload = {"rain": {"1h": 0.6}}
        self.assertEqual(
            await common._decode_json(FakeResponse(payload=payload), "Current"),
            payload,
        )

    async def test_shared_ha_session_and_timeout_are_used(self):
        session = FakeSession(FakeResponse(payload={"ok": True}))
        hass = object()
        with patch.object(common, "async_get_clientsession", return_value=session) as factory:
            result = await common.fetch_json("https://example.invalid/weather", "Current", hass=hass)
        self.assertEqual(result, {"ok": True})
        factory.assert_called_once_with(hass)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["timeout"].total, common.WEATHER_REQUEST_TIMEOUT)

    async def test_transport_failure_is_wrapped(self):
        session = FakeSession(error=TimeoutError("no response"))
        with patch.object(common, "async_get_clientsession", return_value=session):
            with self.assertLogs(common._LOGGER, "ERROR"):
                with self.assertRaisesRegex(APIConnectionError, "no response"):
                    await common.fetch_json("https://example.invalid", "Forecast", hass=object())


class WeatherRainUnitsTests(unittest.TestCase):
    def test_shortest_available_window_is_preferred(self):
        self.assertAlmostEqual(
            common.rain_rate_from_owm_payload({"rain": {"3h": 6, "1h": 0.5}}), 0.5
        )

    def test_three_hour_total_is_converted_to_hourly_rate(self):
        self.assertAlmostEqual(
            common.rain_rate_from_owm_payload({"rain": {"3h": 1.5}}), 0.5
        )

    def test_malformed_keys_and_values_are_ignored(self):
        self.assertAlmostEqual(
            common.rain_rate_from_owm_payload(
                {"rain": {"0h": 4, "bad": 3, "1h": "broken", "3h": 3}}
            ), 1.0
        )

    def test_missing_precipitation_means_zero(self):
        self.assertEqual(common.rain_rate_from_owm_payload(None), 0.0)
        self.assertEqual(common.rain_rate_from_owm_payload({}), 0.0)



class RateLimitAndCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_pirateweather_throttles_after_429_for_both_paths(self):
        provider = pirateweather.PirateWeatherProvider("SECRET-PIRATE-KEY", "38", "-8", 5)
        with patch.object(pirateweather, "fetch_json", side_effect=APIConnectionError("HTTP 429")) as fetch:
            with self.assertRaises(APIConnectionError):
                await provider.get_current_weather()
            with self.assertRaisesRegex(APIConnectionError, "cooldown"):
                await provider.get_forecast()
            self.assertEqual(fetch.call_count, 1)

    async def test_pirateweather_cooldown_suppresses_repeated_network_requests(self):
        provider = pirateweather.PirateWeatherProvider("SECRET-PIRATE-KEY", "38", "-8", 5)
        with patch.object(pirateweather, "fetch_json", side_effect=APIConnectionError("HTTP 429")) as fetch:
            with self.assertRaises(APIConnectionError):
                await provider.get_forecast()
            for _ in range(5):
                with self.assertRaisesRegex(APIConnectionError, "cooldown"):
                    await provider.get_current_weather()
                with self.assertRaisesRegex(APIConnectionError, "cooldown"):
                    await provider.get_forecast()
        self.assertEqual(fetch.call_count, 1)

    async def test_pirateweather_non_rate_limit_failure_does_not_start_cooldown(self):
        provider = pirateweather.PirateWeatherProvider("SECRET-PIRATE-KEY", "38", "-8", 5)
        with patch.object(pirateweather, "fetch_json", side_effect=APIConnectionError("HTTP 503")) as fetch:
            for _ in range(2):
                with self.assertRaises(APIConnectionError):
                    await provider.get_current_weather()
        self.assertEqual(fetch.call_count, 2)

    async def test_current_and_forecast_share_one_http_request(self):
        provider = pirateweather.PirateWeatherProvider("SECRET", "38", "-8", 5)
        payload = {"currently": {"precipIntensity": 0}, "hourly": {"data": []}}
        with patch.object(pirateweather, "fetch_json", return_value=payload) as fetch:
            await provider.get_current_weather()
            await provider.get_forecast()
            await provider.get_total_rain_forecast_for_today()
            await provider.is_raining()
        self.assertEqual(fetch.call_count, 1)

    async def test_parallel_current_and_forecast_share_one_http_request(self):
        import asyncio
        provider = pirateweather.PirateWeatherProvider("SECRET", "38", "-8", 5)
        payload = {"currently": {"precipIntensity": 0}, "hourly": {"data": []}}
        with patch.object(pirateweather, "fetch_json", return_value=payload) as fetch:
            await asyncio.gather(provider.get_current_weather(), provider.get_forecast())
        self.assertEqual(fetch.call_count, 1)

    async def test_pirateweather_cooldown_is_not_logged_as_error(self):
        provider = pirateweather.PirateWeatherProvider("SECRET", "38", "-8", 5)
        provider._rate_limited_until = pirateweather.dt_util.now() + pirateweather.timedelta(minutes=20)
        with patch.object(pirateweather, "fetch_json") as fetch:
            with self.assertLogs(pirateweather._LOGGER, "DEBUG") as logs:
                with self.assertRaises(APIConnectionError):
                    await provider.get_forecast()
        fetch.assert_not_called()
        self.assertTrue(any("cooldown" in line for line in logs.output))
        self.assertFalse(any("ERROR:" in line for line in logs.output))

    async def test_pirateweather_does_not_log_secret_url(self):
        provider = pirateweather.PirateWeatherProvider("SECRET-PIRATE-KEY", "38", "-8", 5)
        with patch.object(pirateweather, "fetch_json", return_value={"currently": {}}):
            with self.assertLogs(pirateweather._LOGGER, "DEBUG") as logs:
                await provider.get_current_weather()
        self.assertNotIn("SECRET-PIRATE-KEY", " ".join(logs.output))

    async def test_openweathermap_does_not_log_secret_url(self):
        provider = owm.OpenWeatherMapProvider("SECRET-OWM-KEY", "38", "-8", 5)
        with patch.object(owm, "fetch_json", return_value={"weather": []}):
            with self.assertLogs(owm._LOGGER, "DEBUG") as logs:
                await provider.get_current_weather()
        self.assertNotIn("SECRET-OWM-KEY", " ".join(logs.output))

if __name__ == "__main__":
    unittest.main()
