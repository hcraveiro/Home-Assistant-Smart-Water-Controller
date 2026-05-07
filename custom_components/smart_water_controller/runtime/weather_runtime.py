"""Weather runtime helpers for Smart Water Controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_SENSORS
from homeassistant.util import dt as dt_util

from ..api.weather import WeatherAPI
from ..const import (
    WEATHER_API_KEY,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
)

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class WeatherRuntime:
    """Manage weather provider configuration and runtime updates."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        """Initialize weather runtime."""
        self.coordinator = coordinator
        self.hass = coordinator.hass

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
        )

    def restore_cache(self) -> None:
        """Restore weather API cache from persisted coordinator state."""
        if not self.coordinator.weather_api:
            return

        self.coordinator.weather_api._cache_forecast = (
            self.coordinator.will_it_rain_today_forecast
        )
        self.coordinator.weather_api._cache_current = self.coordinator.is_raining_now_json

    async def refresh(self) -> None:
        """Refresh weather-related runtime state."""
        if self.coordinator.weather_api:
            will_it_rain_result = await self.coordinator.weather_api.will_it_rain()
            self.coordinator.will_it_rain_today = will_it_rain_result.get(
                "will_rain",
                False,
            )
            self.coordinator.will_it_rain_today_forecast = (
                will_it_rain_result.get("forecast", []) or []
            )

            is_raining_result = await self.coordinator.weather_api.is_raining()
            self.coordinator.is_raining_now = is_raining_result.get("is_raining", False)
            self.coordinator.is_raining_now_json = (
                is_raining_result.get("current", {}) or {}
            )
        else:
            self.coordinator.will_it_rain_today = False
            self.coordinator.will_it_rain_today_forecast = []
            self.coordinator.is_raining_now = False
            self.coordinator.is_raining_now_json = {}

        if self.coordinator.is_raining_now:
            self.coordinator.has_rained_today = True
            self.coordinator.last_rain = dt_util.now()
            self.coordinator.rain_time_today += self.coordinator.poll_interval / 60
            self.coordinator.rain_total_amount_today += await self.calculate_rain_amount()

            if not self.coordinator.sprinkle_with_rain:
                for station_id in range(1, self.coordinator.num_stations + 1):
                    if self.coordinator.stations[station_id - 1].state == "Sprinkling":
                        await self.coordinator.stop_irrigation()
                        break

        if self.coordinator.weather_api:
            self.coordinator.rain_total_amount_forecasted_today = (
                await self.coordinator.weather_api.get_total_rain_forecast_for_today()
            ) + self.coordinator.rain_total_amount_today
        else:
            self.coordinator.rain_total_amount_forecasted_today = (
                self.coordinator.rain_total_amount_today
            )

    async def calculate_rain_amount(self) -> float:
        """Calculate rain amount for the current polling interval."""
        if "rain" not in self.coordinator.is_raining_now_json:
            return 0.0

        rain_data = self.coordinator.is_raining_now_json["rain"]

        for key, rain_amount in rain_data.items():
            if key.endswith("h") and key[:-1].isdigit():
                hours = int(key[:-1])
                minutes = hours * 60
                return (rain_amount / minutes) * (self.coordinator.poll_interval / 60)

        return 0.0