"""Smart Water Controller API abstraction.

This integration can be configured to call arbitrary Home Assistant services for
its core actions (sprinkle/stop/on/off). The mapping is defined in the config
entry and interpreted here.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    WEATHER_PROVIDER,
    ACTION_SPRINKLE_STATION,
    ACTION_STOP_SPRINKLE,
    ACTION_TURN_ON,
    ACTION_TURN_OFF,
    SERVICE_ACTION_ENABLED,
    SERVICE_ACTION_PARAMS,
    SERVICE_ACTION_SERVICE,
    SERVICE_PARAM_NAME,
    SERVICE_PARAM_TYPE,
    SERVICE_PARAM_TYPE_MAC,
    SERVICE_PARAM_TYPE_STATION,
    SERVICE_PARAM_TYPE_TIME,
    SERVICE_PARAM_VALUE,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
    WEATHER_PROVIDER_OPENWEATHERMAP,
    WEATHER_PROVIDER_PIRATEWEATHER,
)

from .weather_providers.owm import OpenWeatherMapProvider
from .weather_providers.pirateweather import PirateWeatherProvider
from .errors import APIConnectionError

_LOGGER = logging.getLogger(__name__)


def _coerce_scalar(value: str) -> Any:
    """Coerce a string into int/float when appropriate."""
    if value is None:
        return None

    v = str(value).strip()
    if v == "":
        return ""

    # int
    if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
        try:
            return int(v)
        except Exception:  # pylint: disable=broad-except
            return v

    # float
    try:
        return float(v)
    except Exception:  # pylint: disable=broad-except
        return v


class SmartWaterControllerAPI:
    """Service-backed API that calls user-configured HA services."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller_mac: str | None,
        bluetooth_timeout: int,
        service_actions: dict[str, Any] | None = None,
        station_switch_entities: list[str] | None = None,
    ) -> None:
        self.hass = hass
        self.controller_mac = controller_mac
        self.bluetooth_timeout = bluetooth_timeout
        self.service_actions = service_actions or {}
        self.station_switch_entities = station_switch_entities or []

    async def connect(self) -> None:
        """Best-effort connectivity check.

        With a generic service mapping there is no guaranteed "read-only" action.
        We keep this as a no-op to avoid blocking startup.
        """
        return

    def update_mapping(
        self,
        *,
        controller_mac: str | None,
        service_actions: dict[str, Any],
        station_switch_entities: list[str] | None = None,
    ) -> None:
        """Update mapping at runtime (used when reconfiguring)."""
        self.controller_mac = controller_mac
        self.service_actions = service_actions

        if station_switch_entities is not None:
            self.station_switch_entities = station_switch_entities

    def update_station_switches(self, station_switch_entities: list[str]) -> None:
        """Update station switch entity mapping at runtime (switch control method)."""
        self.station_switch_entities = station_switch_entities

    def _get_station_switch_entity(self, station: int) -> str:
        """Return switch entity_id for the station (1-based)."""
        idx = int(station) - 1
        if idx < 0 or idx >= len(self.station_switch_entities):
            raise APIConnectionError(f"No switch entity configured for station {station}")
        entity_id = (self.station_switch_entities[idx] or "").strip()
        if not entity_id:
            raise APIConnectionError(f"No switch entity configured for station {station}")
        return entity_id

    async def turn_on_station_switch(self, station: int) -> None:
        """Turn on the switch associated with a station."""
        entity_id = self._get_station_switch_entity(station)
        try:
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
        except Exception as exc:  # pylint: disable=broad-except
            raise APIConnectionError(f"Error turning on switch '{entity_id}': {exc}") from exc

    async def turn_off_station_switch(self, station: int) -> None:
        """Turn off the switch associated with a station."""
        entity_id = self._get_station_switch_entity(station)
        try:
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
        except Exception as exc:  # pylint: disable=broad-except
            raise APIConnectionError(f"Error turning off switch '{entity_id}': {exc}") from exc

    async def turn_off_all_station_switches(self) -> None:
        """Turn off all configured station switches (best-effort)."""
        for idx, entity_id in enumerate(self.station_switch_entities, start=1):
            eid = (entity_id or "").strip()
            if not eid:
                continue
            try:
                await self.hass.services.async_call("switch", "turn_off", {"entity_id": eid}, blocking=True)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning("Failed turning off station %s switch '%s'", idx, eid, exc_info=True)

    async def _async_call_configured_service(
        self,
        *,
        action: str,
        station: int | None = None,
        minutes: int | None = None,
    ) -> None:
        """Call the configured HA service for a logical action."""

        action_cfg = self.service_actions.get(action) or {}
        if not action_cfg.get(SERVICE_ACTION_ENABLED, True):
            raise APIConnectionError(f"Action '{action}' is disabled")

        service_call = (action_cfg.get(SERVICE_ACTION_SERVICE) or "").strip()
        if not service_call or "." not in service_call:
            raise APIConnectionError(f"Action '{action}' has no valid service configured")

        domain, service = service_call.split(".", 1)

        payload: dict[str, Any] = {}
        params: list[dict[str, Any]] = action_cfg.get(SERVICE_ACTION_PARAMS, []) or []

        for p in params:
            name = (p.get(SERVICE_PARAM_NAME) or "").strip()
            if not name:
                continue

            ptype = (p.get(SERVICE_PARAM_TYPE) or "other").strip()
            configured_value = (p.get(SERVICE_PARAM_VALUE) or "")

            # If a value is explicitly configured, always send it.
            if str(configured_value).strip() != "":
                payload[name] = _coerce_scalar(str(configured_value))
                continue

            # Otherwise, fill based on type and runtime context.
            if ptype == SERVICE_PARAM_TYPE_TIME and minutes is not None:
                payload[name] = int(minutes)
            elif ptype == SERVICE_PARAM_TYPE_STATION and station is not None:
                payload[name] = int(station)
            elif ptype == SERVICE_PARAM_TYPE_MAC:
                if not self.controller_mac:
                    raise APIConnectionError("Controller MAC address is not set")
                payload[name] = self.controller_mac
            else:
                # "other" (or unknown type) with empty value means coordinator will skip.
                continue

        try:
            await self.hass.services.async_call(domain, service, payload, blocking=True)
        except HomeAssistantError as exc:
            raise APIConnectionError(str(exc)) from exc
        except Exception as exc:  # pylint: disable=broad-except
            raise APIConnectionError(f"Error calling service '{service_call}': {exc}") from exc

    async def sprinkle_station(self, station: int, minutes: int) -> None:
        """Sprinkle a station for a number of minutes."""
        await self._async_call_configured_service(
            action=ACTION_SPRINKLE_STATION,
            station=int(station),
            minutes=int(minutes),
        )

    async def stop_sprinkle(self) -> None:
        """Stop an active sprinkle."""
        await self._async_call_configured_service(action=ACTION_STOP_SPRINKLE)

    async def turn_on(self) -> None:
        """Turn controller on."""
        await self._async_call_configured_service(action=ACTION_TURN_ON)

    async def turn_off(self) -> None:
        """Turn controller off."""
        await self._async_call_configured_service(action=ACTION_TURN_OFF)


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
    ) -> None:
        """Initialize the configured weather provider."""
        self._provider_name = (provider or "").strip() or WEATHER_PROVIDER_OPENWEATHERMAP
        self._provider = self._load_provider(
            provider=self._provider_name,
            api_key=api_key,
            latitude=latitude,
            longitude=longitude,
            timeout=timeout,
        )

    def _load_provider(
        self,
        *,
        provider: str,
        api_key: str,
        latitude: str,
        longitude: str,
        timeout: int,
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
            )

        if provider == WEATHER_PROVIDER_PIRATEWEATHER:
            return PirateWeatherProvider(
                api_key=api_key,
                latitude=latitude,
                longitude=longitude,
                timeout=timeout,
            )

        raise APIConnectionError(f"Unsupported weather provider '{provider}'")

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
