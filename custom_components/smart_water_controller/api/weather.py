from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import (
    WEATHER_PROVIDER_NONE,
    WEATHER_PROVIDER_OPENWEATHERMAP,
    WEATHER_PROVIDER_PIRATEWEATHER,
)
from ..weather_providers.owm import OpenWeatherMapProvider
from ..weather_providers.pirateweather import PirateWeatherProvider
from ..errors import APIConnectionError

class WeatherAPI:
    """Weather API facade.

    This class keeps the public interface expected by the coordinator, while the
    actual implementation lives in provider-specific modules.
    """

    def __init__(
        self,
        api_key: str,
        latitude: str,
        longitude: str,
        timeout: int,
        provider: str = WEATHER_PROVIDER_OPENWEATHERMAP,
        hass: HomeAssistant | None = None,
    ) -> None:
        """Initialize the configured weather provider."""
        self._provider_name = (provider or "").strip() or WEATHER_PROVIDER_OPENWEATHERMAP
        self._provider = self._load_provider(
            provider=self._provider_name,
            api_key=api_key,
            latitude=latitude,
            longitude=longitude,
            timeout=timeout,
            hass=hass,
        )

    def _load_provider(
        self,
        *,
        provider: str,
        api_key: str,
        latitude: str,
        longitude: str,
        timeout: int,
        hass: HomeAssistant | None = None,
    ):
        """Return the provider instance for the given provider key."""
        if provider == WEATHER_PROVIDER_NONE:
            raise APIConnectionError("Weather provider is disabled")

        if provider == WEATHER_PROVIDER_OPENWEATHERMAP:
            return OpenWeatherMapProvider(
                api_key=api_key,
                latitude=latitude,
                longitude=longitude,
                timeout=timeout,
                hass=hass,
            )

        if provider == WEATHER_PROVIDER_PIRATEWEATHER:
            return PirateWeatherProvider(
                api_key=api_key,
                latitude=latitude,
                longitude=longitude,
                timeout=timeout,
                hass=hass,
            )

        raise APIConnectionError(f"Unsupported weather provider '{provider}'")

    def restore_cache(
        self,
        *,
        forecast: list[dict[str, Any]] | None,
        current: dict[str, Any] | None,
    ) -> None:
        """Hand persisted caches back to the provider.

        The caches live on the provider, not on this facade: assigning
        ``weather_api._cache_forecast`` from the outside silently did nothing.
        """
        self._provider.restore_cache(forecast=forecast, current=current)

    async def get_current_weather(self) -> Any:
        return await self._provider.get_current_weather()

    async def is_raining(self) -> dict:
        return await self._provider.is_raining()

    async def get_forecast(self) -> list:
        return await self._provider.get_forecast()

    async def will_it_rain(self) -> dict:
        return await self._provider.will_it_rain()

    async def get_total_rain_forecast_for_today(self) -> float:
        return await self._provider.get_total_rain_forecast_for_today()