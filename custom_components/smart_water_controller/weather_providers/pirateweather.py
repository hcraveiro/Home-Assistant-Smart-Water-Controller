"""PirateWeather provider implementation.

This provider adapts PirateWeather (Dark Sky compatible) data into the same
shape returned by the OpenWeatherMap provider, so the coordinator can remain
unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import as_local

from ..const import PIRATE_WEATHER_URL
from ..errors import APIConnectionError
from .common import FORECAST_BLOCK_HOURS, fetch_json
from .intervals import RainInterval, remaining_probability, remaining_rain

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

    def __init__(
        self,
        api_key: str,
        latitude: str,
        longitude: str,
        timeout: int,
        hass: HomeAssistant | None = None,
    ) -> None:
        self.api_key = api_key
        self.latitude = latitude
        self.longitude = longitude
        self.timeout = timeout
        self.hass = hass

        self._cache_current: dict[str, Any] | None = None
        self._cache_forecast: list[dict[str, Any]] | None = None
        self._forecast_intervals: list[RainInterval] = []
        self._last_current_fetch_time = None
        self._last_forecast_fetch_time = None
        self.last_forecast_date = dt_util.now().date()
        self._rate_limited_until = None
        self._shared_payload = None
        self._shared_payload_time = None
        self._request_lock = asyncio.Lock()

    def restore_cache(
        self,
        *,
        forecast: list[dict[str, Any]] | None,
        current: dict[str, Any] | None,
    ) -> None:
        """Restore caches persisted by the coordinator across restarts."""
        if forecast:
            self._cache_forecast = list(forecast)
        # Display buckets cannot reconstruct hourly coverage after a restart.
        self._forecast_intervals = []
        self._last_forecast_fetch_time = None

        if current:
            self._cache_current = dict(current)

    def _build_url(self) -> str:
        # PirateWeather uses: /forecast/<APIKEY>/<lat>,<lon>?units=si
        return f"{PIRATE_WEATHER_URL}{self.api_key}/{self.latitude},{self.longitude}?units=si"

    def _check_rate_limit(self) -> None:
        """Skip requests during the cooldown imposed after HTTP 429."""
        if self._rate_limited_until and dt_util.now() < self._rate_limited_until:
            raise APIConnectionError("PirateWeather HTTP 429 cooldown active")

    def _record_rate_limit(self, error: Exception) -> None:
        """Apply a shared cooldown to current and forecast requests."""
        if "HTTP 429" in str(error):
            self._rate_limited_until = dt_util.now() + timedelta(minutes=30)
            _LOGGER.warning("PirateWeather rate limited; pausing API requests for 30 minutes")

    async def _get_shared_payload(self) -> dict[str, Any]:
        """Reuse one PirateWeather response for current conditions and forecasts."""
        async with self._request_lock:
            now = dt_util.now()
            cache_minutes = max(15, self.timeout)
            if (
                self._shared_payload is not None
                and self._shared_payload_time is not None
                and self._shared_payload_time.date() == now.date()
                and now - self._shared_payload_time < timedelta(minutes=cache_minutes)
            ):
                return self._shared_payload

            self._check_rate_limit()
            try:
                payload = await fetch_json(
                    self._build_url(), "PirateWeather data", hass=self.hass
                )
            except APIConnectionError as exc:
                self._record_rate_limit(exc)
                raise

            self._shared_payload = payload
            self._shared_payload_time = now
            return payload

    async def get_current_weather(self) -> Any:
        now = dt_util.now()

        if (
            self._cache_current
            and self._last_current_fetch_time
            and now - self._last_current_fetch_time < timedelta(minutes=max(15, self.timeout))
        ):
            _LOGGER.debug("Returning cached current data.")
            return self._cache_current

        data = await self._get_shared_payload()
        _LOGGER.debug("PirateWeather raw data: %s", data)

        # Normalise similar to OWM: store a local dt_txt for 'currently.time'
        currently = data.get("currently") or {}
        if "time" in currently:
            utc_dt = datetime.fromtimestamp(currently["time"], tz=timezone.utc)
            local_dt = as_local(utc_dt)
            currently["dt_txt"] = local_dt.strftime("%Y-%m-%d %H:%M:%S")

        self._cache_current = data
        self._last_current_fetch_time = now

        return self._cache_current

    async def is_raining(self) -> dict:
        data = await self.get_current_weather()

        # Dark Sky style:
        # - precipIntensity is in mm/h in SI units
        # - consider raining if intensity > 0
        intensity = self._current_rain_rate(data)

        return {
            "is_raining": intensity > 0.0,
            "current": data,
            "rain_rate_mm_per_hour": intensity,
        }

    @staticmethod
    def _current_rain_rate(payload: dict[str, Any] | None) -> float:
        """Return the current precipitation rate in mm/h."""
        currently = (payload or {}).get("currently") or {}

        try:
            return float(currently.get("precipIntensity") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    async def get_forecast(self) -> list:
        """Return forecast blocks for today in 3-hour buckets.

        The coordinator expects OWM-like entries for each block:
        - dt_txt as local string for the block start (00,03,06,...)
        - rain["3h"] total mm in that 3h window
        - pop probability (max pop across hours in the block)
        """
        now = dt_util.now()

        if (
            self._cache_forecast is not None
            and self._last_forecast_fetch_time
            and self._last_forecast_fetch_time.date() == now.date()
            and now - self._last_forecast_fetch_time < timedelta(minutes=max(15, self.timeout))
        ):
            _LOGGER.debug("Returning cached forecast data.")
            return self._cache_forecast

        # Build a replacement snapshot before publishing either cache.
        try:
            data = await self._get_shared_payload()
            hourly = (data.get("hourly") or {}).get("data")
            # An explicitly empty list is a valid provider no-forecast response;
            # absent/null hourly data must not silently become dry weather.
            if not isinstance(hourly, list):
                raise ValueError("Missing PirateWeather hourly forecast data")
            intervals = []
            for item in hourly:
                start = datetime.fromtimestamp(item["time"], tz=timezone.utc)
                intervals.append(RainInterval(
                    start, start + timedelta(hours=1),
                    float(item.get("precipIntensity") or 0.0),
                    float(item.get("precipProbability") or 0.0),
                ))
        except Exception as exc:  # pylint: disable=broad-except
            if "PirateWeather HTTP 429 cooldown active" in str(exc):
                _LOGGER.debug("PirateWeather forecast skipped during rate-limit cooldown.")
            else:
                _LOGGER.error("Error processing PirateWeather forecast: %s", exc)
            raise APIConnectionError(
                f"Error processing PirateWeather forecast: {exc}"
            ) from exc

        # We build blocks for 00,03,...,21 for *today*.
        block_starts = FORECAST_BLOCK_HOURS

        blocks: list[dict[str, Any]] = []
        for start_hour in block_starts:
            block_start_local = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            block_end_local = block_start_local + timedelta(hours=3)

            # Collect hourly items that overlap this block and are for today
            mm_sum = 0.0
            pop_max = 0.0

            for interval in intervals:
                fraction = interval.overlap_fraction(block_start_local, block_end_local)
                mm_sum += interval.millimetres * fraction
                if fraction > 0:
                    pop_max = max(pop_max, interval.probability)

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
        self._forecast_intervals = intervals
        self._last_forecast_fetch_time = now
        self.last_forecast_date = now.date()
        return self._cache_forecast

    async def will_it_rain(self) -> dict:
        forecast = await self.get_forecast()
        will_rain = remaining_probability(self._forecast_intervals, dt_util.now())
        return {"will_rain": will_rain, "forecast": forecast}

    async def get_total_rain_forecast_for_today(self) -> float:
        """Integrate hourly intervals directly, never prorate display buckets."""
        await self.get_forecast()
        return remaining_rain(self._forecast_intervals, dt_util.now())
