"""OpenWeatherMap provider implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import as_local
from homeassistant.util import dt as dt_util

from ..errors import APIConnectionError
from ..const import OPEN_WEATHER_MAP_CURRENT_URL, OPEN_WEATHER_MAP_FORECAST_URL
from .common import fetch_json, rain_rate_from_owm_payload
from .intervals import RainInterval, remaining_probability, remaining_rain

_LOGGER = logging.getLogger(__name__)


class OpenWeatherMapProvider:
    """Keep OWM display dictionaries separate from UTC rain accounting."""

    def __init__(
        self, api_key: str, latitude: str, longitude: str, timeout: int,
        hass: HomeAssistant | None = None,
    ) -> None:
        self.api_key = api_key
        self.latitude = latitude
        self.longitude = longitude
        self.timeout = timeout
        self.hass = hass
        self._cache_forecast: list[dict[str, Any]] | None = None
        self._forecast_intervals: list[RainInterval] = []
        self._cache_current: dict[str, Any] | None = None
        self._last_forecast_fetch_time = None
        self.last_forecast_date = dt_util.now().date()
        self._last_current_fetch_time = None

    def restore_cache(
        self, *, forecast: list[dict[str, Any]] | None,
        current: dict[str, Any] | None,
    ) -> None:
        """Restore display data; refresh before using it for accounting."""
        if forecast:
            self._cache_forecast = list(forecast)
        self._forecast_intervals = []
        self._last_forecast_fetch_time = None
        if current:
            self._cache_current = dict(current)

    async def get_current_weather(self) -> Any:
        now = dt_util.now()
        if (
            self._cache_current and self._last_current_fetch_time
            and now - self._last_current_fetch_time < timedelta(minutes=self.timeout)
        ):
            _LOGGER.debug("Returning cached current weather.")
            return self._cache_current
        weather_url = (
            f"{OPEN_WEATHER_MAP_CURRENT_URL}appid={self.api_key}&lat={self.latitude}&lon={self.longitude}"
        )
        _LOGGER.debug("Getting OpenWeatherMap current weather")
        data = await fetch_json(weather_url, "Current Weather data", hass=self.hass)
        if "dt" in data:
            data["dt_txt"] = as_local(
                datetime.fromtimestamp(data["dt"], tz=timezone.utc)
            ).strftime("%Y-%m-%d %H:%M:%S")
        self._cache_current = data
        self._last_current_fetch_time = now
        return data

    async def is_raining(self) -> dict:
        current_weather = await self.get_current_weather()
        return {
            "is_raining": "rain" in current_weather,
            "current": current_weather,
            "rain_rate_mm_per_hour": rain_rate_from_owm_payload(current_weather),
        }

    async def get_forecast(self) -> list:
        """Fetch all forecast slots, including the slot ending after local midnight.

        OWM's numeric dt is UTC and its rain.3h belongs to the preceding three
        hours. dt_txt is retained verbatim for display compatibility, never parsed
        as a local instant. A full response avoids local-hour/UTC cnt truncation.
        Missing or empty forecasts are unavailable, not successful dry forecasts.
        """
        now = dt_util.now()
        if (
            self._cache_forecast is not None and self._last_forecast_fetch_time
            and self._last_forecast_fetch_time.date() == now.date()
            and now - self._last_forecast_fetch_time < timedelta(minutes=self.timeout)
        ):
            return self._cache_forecast
        weather_url = (
            f"{OPEN_WEATHER_MAP_FORECAST_URL}&appid={self.api_key}&lat={self.latitude}&lon={self.longitude}"
        )
        try:
            data = await fetch_json(weather_url, "Forecast Weather data", hass=self.hass)
            forecasts = data.get("list")
            if not isinstance(forecasts, list) or not forecasts:
                raise ValueError("Missing or empty OWM forecast list")
            intervals = []
            for item in forecasts:
                end = datetime.fromtimestamp(item["dt"], tz=timezone.utc)
                intervals.append(RainInterval(
                    end - timedelta(hours=3), end,
                    float((item.get("rain") or {}).get("3h") or 0.0),
                    float(item.get("pop") or 0.0),
                ))
        except Exception as exc:
            raise APIConnectionError(f"Error processing Forecast Weather data: {exc}") from exc
        # Publish display and accounting caches atomically only after validation.
        self._cache_forecast = list(forecasts)
        self._forecast_intervals = intervals
        self._last_forecast_fetch_time = now
        self.last_forecast_date = now.date()
        return self._cache_forecast

    async def will_it_rain(self) -> dict:
        forecast = await self.get_forecast()
        return {
            "will_rain": remaining_probability(self._forecast_intervals, dt_util.now()),
            "forecast": forecast,
        }

    async def get_total_rain_forecast_for_today(self) -> float:
        """Integrate the remaining local day using elapsed UTC interval overlap."""
        await self.get_forecast()
        return remaining_rain(self._forecast_intervals, dt_util.now())
