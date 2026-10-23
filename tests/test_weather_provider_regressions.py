"""Regression tests for weather HTTP failures and OpenWeatherMap rain units."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ha_stub  # noqa: F401
from custom_components.smart_water_controller.errors import APIConnectionError
from custom_components.smart_water_controller.weather_providers import common


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


if __name__ == "__main__":
    unittest.main()
