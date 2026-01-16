"""PirateWeather provider implementation.

This provider adapts PirateWeather (Dark Sky compatible) data into the same
shape returned by the OpenWeatherMap provider, so the coordinator can remain
unchanged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from homeassistant.util import dt as dt_util
from homeassistant.util.dt import as_local

from ..const import PIRATE_WEATHER_URL
from ..errors import APIConnectionError

_LOGGER = logging.getLogger(__name__)


class PirateWeatherProvider:
    """PirateWeather provider.

    Output format matches the OpenWeatherMap provider:
    - get_current_weather(): raw provider json (dict)
    - is_raining(): {"is_raining": bool, "current": json}
    - get_forecast(): list of dicts with keys:
        - dt_txt (local time string "YYYY-MM-DD HH:MM:SS")
        - pop (0..1)
        - rain: {"3h": mm_in_block}
    """

    def __init__(self, api_key: str, latitude: str, longitude: str, timeout: int) -> None:
        self.api_key = api_key
        self.latitude = latitude
        self.longitude = longitude
        self.timeout = timeout

        self._cache_current: dict[str, Any] | None = None
        self._cache_forecast: list[dict[str, Any]] | None = None
        self._last_current_fetch_time = None
        self._last_forecast_fetch_time = None
        self.last_forecast_date = dt_util.now().date()

    def _build_url(self) -> str:
        # PirateWeather uses: /forecast/<APIKEY>/<lat>,<lon>?units=si
        return f"{PIRATE_WEATHER_URL}{self.api_key}/{self.latitude},{self.longitude}?units=si"

    async def get_current_weather(self) -> Any:
        now = dt_util.now()

        if (
            self._cache_current
            and self._last_current_fetch_time
            and now - self._last_current_fetch_time < timedelta(minutes=self.timeout)
        ):
            _LOGGER.debug("Returning cached current data.")
            return self._cache_current

        url = self._build_url()
        _LOGGER.debug("Getting PirateWeather current at: %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                try:
                    data = await response.json()
                    _LOGGER.debug("PirateWeather raw data: %s", data)

                    # Normalise similar to OWM: store a local dt_txt for 'currently.time'
                    currently = data.get("currently") or {}
                    if "time" in currently:
                        utc_dt = datetime.fromtimestamp(currently["time"], tz=timezone.utc)
                        local_dt = as_local(utc_dt)
                        currently["dt_txt"] = local_dt.strftime("%Y-%m-%d %H:%M:%S")

                    self._cache_current = data
                    self._last_current_fetch_time = now
                except Exception as exc:  # pylint: disable=broad-except
                    _LOGGER.error("Error processing PirateWeather current JSON!", exc_info=True)
                    raise APIConnectionError(
                        "Error processing PirateWeather current JSON!"
                    ) from exc

        return self._cache_current

    async def is_raining(self) -> dict:
        data = await self.get_current_weather()
        currently = data.get("currently") or {}

        # Dark Sky style:
        # - precipIntensity is in mm/h in SI units
        # - consider raining if intensity > 0
        intensity = float(currently.get("precipIntensity") or 0.0)
        is_raining = intensity > 0.0

        return {"is_raining": is_raining, "current": data}

    async def get_forecast(self) -> list:
        """Return forecast blocks for today in 3-hour buckets.

        The coordinator expects OWM-like entries for each block:
        - dt_txt as local string for the block start (00,03,06,...)
        - rain["3h"] total mm in that 3h window
        - pop probability (max pop across hours in the block)
        """
        now = dt_util.now()

        if (
            self._cache_forecast
            and self._last_forecast_fetch_time
            and now - self._last_forecast_fetch_time < timedelta(minutes=self.timeout)
        ):
            _LOGGER.debug("Returning cached forecast data.")
            return self._cache_forecast

        # Reset per-day similar to OWM behaviour (preserve 00:00 block if present)
        temp_cache = self._cache_forecast.copy() if self._cache_forecast else []

        if self.last_forecast_date != now.date():
            _LOGGER.debug("Day changed, resetting PirateWeather forecast cache...")
            self._cache_forecast = []
            self.last_forecast_date = now.date()

        url = self._build_url()
        _LOGGER.debug("Getting PirateWeather forecast at: %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                try:
                    data = await response.json()
                    hourly = (data.get("hourly") or {}).get("data") or []
                except Exception as exc:  # pylint: disable=broad-except
                    _LOGGER.error("Error processing PirateWeather forecast JSON!", exc_info=True)
                    if not self._cache_forecast:
                        self._cache_forecast = temp_cache
                    raise APIConnectionError(
                        "Error processing PirateWeather forecast JSON!"
                    ) from exc

        today = now.date()
        # We build blocks for 00,03,...,21 for *today*.
        block_starts = [0, 3, 6, 9, 12, 15, 18, 21]

        blocks: list[dict[str, Any]] = []
        for start_hour in block_starts:
            block_start_local = dt_util.now().replace(hour=start_hour, minute=0, second=0, microsecond=0)
            # Ensure block_start_local refers to today
            block_start_local = block_start_local.replace(year=today.year, month=today.month, day=today.day)
            block_end_local = block_start_local + timedelta(hours=3)

            # Collect hourly items that overlap this block and are for today
            mm_sum = 0.0
            pop_max = 0.0

            for h in hourly:
                if "time" not in h:
                    continue

                utc_dt = datetime.fromtimestamp(h["time"], tz=timezone.utc)
                local_dt = as_local(utc_dt)

                if local_dt.date() != today:
                    continue

                if not (block_start_local <= local_dt < block_end_local):
                    continue

                # precipIntensity in mm/h (SI). For 1 hour, approximate mm = intensity * 1h.
                intensity = float(h.get("precipIntensity") or 0.0)
                mm_sum += intensity

                pop = float(h.get("precipProbability") or 0.0)
                if pop > pop_max:
                    pop_max = pop

            dt_txt = block_start_local.strftime("%Y-%m-%d %H:%M:%S")
            blocks.append(
                {
                    "dt_txt": dt_txt,
                    "pop": pop_max,
                    "rain": {"3h": mm_sum},
                }
            )

        # Keep only blocks from current hour onwards, plus 00h block (same behaviour as OWM code)
        current_hour = now.hour
        current_block = max([h for h in block_starts if h <= current_hour])
        today_str = now.strftime("%Y-%m-%d")

        filtered = []
        for b in blocks:
            b_date, b_time = b["dt_txt"].split(" ")
            b_hour = int(b_time.split(":")[0])
            if b_date == today_str and b_hour >= current_block:
                filtered.append(b)

        # Always include the 00h block if present (useful when day rolls)
        for b in blocks:
            if b["dt_txt"].endswith("00:00:00"):
                if b not in filtered:
                    filtered.append(b)
                break

        # Sort by dt_txt
        filtered.sort(key=lambda x: x["dt_txt"])

        self._cache_forecast = filtered
        self._last_forecast_fetch_time = now
        return self._cache_forecast

    async def will_it_rain(self) -> dict:
        forecast = await self.get_forecast()
        will_rain = any((item.get("pop") or 0.0) > 0.50 for item in forecast)
        return {"will_rain": will_rain, "forecast": forecast}

    async def get_total_rain_forecast_for_today(self) -> float:
        """Sum remaining rain (mm) for today, prorating the current 3h block."""
        forecast = await self.get_forecast()

        now = dt_util.now()
        current_time = now.hour * 60 + now.minute
        today_str = now.strftime("%Y-%m-%d")
        total_rain_mm = 0.0

        for item in forecast:
            dt_txt = item.get("dt_txt")
            if not dt_txt:
                continue

            forecast_date, forecast_hour_minute = dt_txt.split(" ")
            forecast_hour = int(forecast_hour_minute.split(":")[0])

            if forecast_date != today_str:
                continue

            rain_mm = float(((item.get("rain") or {}).get("3h") or 0.0))

            forecast_start_minute = forecast_hour * 60
            forecast_end_minute = forecast_start_minute + 180

            if forecast_end_minute <= current_time:
                continue

            if forecast_start_minute <= current_time < forecast_end_minute:
                remaining_minutes = forecast_end_minute - current_time
                rain_mm = (remaining_minutes / 180) * rain_mm

            total_rain_mm += rain_mm

        return total_rain_mm
