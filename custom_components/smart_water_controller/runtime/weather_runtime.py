"""Weather runtime helpers for Smart Water Controller."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_SENSORS
from homeassistant.util import dt as dt_util

from ..api.weather import WeatherAPI
from ..const import (
    MAX_RAIN_SAMPLE_INTERVAL_FACTOR,
    MIN_SIGNIFICANT_RAIN_MM,
    WEATHER_API_KEY,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
)
from ..weather_providers.common import rain_rate_from_owm_payload
from ..errors import APIConnectionError

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class WeatherRuntime:
    """Manage weather provider configuration and runtime updates."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        """Initialize weather runtime."""
        self.coordinator = coordinator
        self.hass = coordinator.hass

        # Rain is integrated over the time really elapsed between two weather
        # samples, so we need to remember when the previous one happened.
        self._last_rain_sample_at: datetime | None = None
        self._rain_rate_mm_per_hour = 0.0
        self._current_weather_fresh = False
        # Last successfully fetched forecast total, kept so a provider outage
        # does not wipe the expected rain used by the irrigation planner.
        self._forecast_total_mm = 0.0
        self.coordinator.weather_forecast_available = False
        self._rain_stop_task: asyncio.Task | None = None

    def configure(self) -> None:
        """Configure weather integration based on the selected provider."""
        provider = self.coordinator.config_entry.data.get(
            WEATHER_PROVIDER,
            WEATHER_PROVIDER_NONE,
        )

        self.coordinator.weather_api_key = (
            self.coordinator.config_entry.data.get(WEATHER_API_KEY) or ""
        ).strip()

        zone_entity_id = self.coordinator.config_entry.data.get(CONF_SENSORS)
        zone_state = self.hass.states.get(zone_entity_id) if zone_entity_id else None

        self.coordinator.latitude = None
        self.coordinator.longitude = None

        if zone_state:
            self.coordinator.latitude = zone_state.attributes.get("latitude")
            self.coordinator.longitude = zone_state.attributes.get("longitude")

        if provider == WEATHER_PROVIDER_NONE:
            self.coordinator.weather_api = None
            return

        if (
            not self.coordinator.weather_api_key
            or not self.coordinator.latitude
            or not self.coordinator.longitude
        ):
            self.coordinator.weather_api = None
            return

        self.coordinator.weather_api = WeatherAPI(
            self.coordinator.weather_api_key,
            self.coordinator.latitude,
            self.coordinator.longitude,
            self.coordinator.weather_api_timeout,
            provider=provider,
            hass=self.hass,
        )

    def restore_cache(self) -> None:
        """Restore weather API cache from persisted coordinator state."""
        # rain_total_amount_forecasted_today was persisted as
        # "expected rain still to come" + "rain already fallen today".
        self._forecast_total_mm = max(
            0.0,
            float(self.coordinator.rain_total_amount_forecasted_today or 0)
            - float(self.coordinator.rain_total_amount_today or 0),
        )

        if not self.coordinator.weather_api:
            return

        self.coordinator.weather_api.restore_cache(
            forecast=self.coordinator.will_it_rain_today_forecast,
            current=self.coordinator.is_raining_now_json,
        )

    def reset_daily_state(self) -> None:
        """Forget the cached forecast total at the daily reset."""
        self._forecast_total_mm = 0.0

    def set_forecast_total(self, total_mm: float) -> None:
        """Record a successfully fetched daily forecast for outage fallback."""
        self._forecast_total_mm = max(0.0, float(total_mm or 0))

    async def refresh(self) -> None:
        """Refresh weather-related runtime state."""
        if self.coordinator.weather_api:
            await self._refresh_from_provider()
        else:
            self.coordinator.will_it_rain_today = False
            self.coordinator.will_it_rain_today_forecast = []
            self.coordinator.is_raining_now = False
            self.coordinator.is_raining_now_json = {}
            self._rain_rate_mm_per_hour = 0.0
            self._current_weather_fresh = False
            self._forecast_total_mm = 0.0
            self.coordinator.weather_forecast_available = True

        self._accumulate_rain(self._elapsed_rain_minutes())

        if (
            self._current_weather_fresh
            and self.coordinator.is_raining_now
            and not self.coordinator.sprinkle_with_rain
        ):
            self._stop_irrigation_for_rain()

        self.coordinator.rain_total_amount_forecasted_today = (
            self._forecast_total_mm + self.coordinator.rain_total_amount_today
        )

    @staticmethod
    def _weather_log_level(error: Exception) -> int:
        """Avoid warning spam while PirateWeather is already cooling down."""
        if isinstance(error, APIConnectionError) and "PirateWeather HTTP 429 cooldown active" in str(error):
            return logging.DEBUG
        return logging.WARNING

    async def _refresh_from_provider(self) -> None:
        """Read the provider, keeping the last known values on failure.

        A weather outage must not break irrigation bookkeeping: these calls used
        to propagate out of ``async_update_all_sensors()``, which wiped the whole
        entity data set and aborted the post-irrigation state update.
        """
        weather_api = self.coordinator.weather_api
        self._current_weather_fresh = False

        try:
            will_it_rain_result = await weather_api.will_it_rain()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.log(
                self._weather_log_level(err),
                "%s - Could not refresh the rain forecast (%s). Keeping the last known values.",
                self.coordinator.controller_mac_address,
                err,
            )
        else:
            self.coordinator.will_it_rain_today = will_it_rain_result.get(
                "will_rain",
                False,
            )
            self.coordinator.will_it_rain_today_forecast = (
                will_it_rain_result.get("forecast", []) or []
            )

        try:
            is_raining_result = await weather_api.is_raining()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.log(
                self._weather_log_level(err),
                "%s - Could not read the current weather (%s). Pausing rain accumulation.",
                self.coordinator.controller_mac_address,
                err,
            )
            # Without a fresh reading we do not know how hard it rains, so stop
            # integrating instead of extrapolating the last known rate.
            self._rain_rate_mm_per_hour = 0.0
        else:
            self._current_weather_fresh = True
            self.coordinator.is_raining_now = is_raining_result.get("is_raining", False)
            self.coordinator.is_raining_now_json = (
                is_raining_result.get("current", {}) or {}
            )
            self._rain_rate_mm_per_hour = self._read_rain_rate(is_raining_result)

        try:
            self._forecast_total_mm = await weather_api.get_total_rain_forecast_for_today()
            self.coordinator.weather_forecast_available = True
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.log(
                self._weather_log_level(err),
                "%s - Could not refresh the expected rain for today (%s). "
                "Keeping the last known value of %.2fmm.",
                self.coordinator.controller_mac_address,
                err,
                self._forecast_total_mm,
            )

    def _read_rain_rate(self, is_raining_result: dict) -> float:
        """Return the current rain rate in mm/h reported by the provider."""
        rate = is_raining_result.get("rain_rate_mm_per_hour")

        if rate is not None:
            try:
                return max(0.0, float(rate))
            except (TypeError, ValueError):
                pass

        # Fallback for payloads that only carry the OpenWeatherMap shape.
        return rain_rate_from_owm_payload(is_raining_result.get("current"))

    def _elapsed_rain_minutes(self) -> float:
        """Return the minutes elapsed since the previous weather sample.

        Rain used to be integrated assuming exactly one poll interval had passed.
        But ``refresh()`` also runs outside the polling tick (start and end of an
        irrigation, stop, schedule change, controller on/off), so every one of
        those added a full interval of phantom rain to the daily total, which in
        turn suppressed watering.
        """
        now = dt_util.now()
        poll_minutes = max(0.0, float(self.coordinator.poll_interval) / 60)
        previous = self._last_rain_sample_at
        self._last_rain_sample_at = now

        if previous is None:
            # No elapsed observation exists yet; do not invent a full interval.
            return 0.0

        elapsed = (now - previous).total_seconds() / 60

        if elapsed <= 0:
            return 0.0

        return min(elapsed, poll_minutes * MAX_RAIN_SAMPLE_INTERVAL_FACTOR)

    def _accumulate_rain(self, elapsed_minutes: float) -> None:
        """Add the rain fallen during the elapsed period to today's totals."""
        if not self.coordinator.is_raining_now or elapsed_minutes <= 0:
            return

        self.coordinator.has_rained_today = True
        self.coordinator.rain_time_today += elapsed_minutes
        self.coordinator.rain_total_amount_today += (
            self._rain_rate_mm_per_hour * elapsed_minutes / 60
        )

        # Only meaningful rain counts as a watering event able to postpone the
        # following days through the schedule interval. Any trace of drizzle used
        # to reach this point and block irrigation for "interval_days".
        if (
            self._rain_rate_mm_per_hour > 0
            and self.coordinator.rain_total_amount_today >= MIN_SIGNIFICANT_RAIN_MM
        ):
            self.coordinator.last_rain = dt_util.now()

    def _stop_irrigation_for_rain(self) -> None:
        """Stop a running irrigation because it started raining."""
        if self._rain_stop_task is not None and not self._rain_stop_task.done():
            return

        if not any(
            station.state == "Sprinkling" for station in self.coordinator.stations
        ):
            return

        _LOGGER.info(
            "%s - It is raining and watering with rain is disabled. Stopping irrigation.",
            self.coordinator.controller_mac_address,
        )

        # Scheduled rather than awaited: stop_irrigation() refreshes all sensors,
        # which calls straight back into refresh(). Letting the current refresh
        # finish first keeps that recursion out of the rain accounting.
        self._rain_stop_task = self.hass.async_create_task(
            self.coordinator.stop_irrigation()
        )
