"""Schema builders for Smart Water Controller config flows."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL, CONF_SENSORS
from homeassistant.helpers.selector import selector

from .common import _bool_select_schema
from ..const import (
    ACTION_SPRINKLE_STATION,
    ACTION_STOP_SPRINKLE,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    BLUETOOTH_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOIL_MOISTURE,
    IRRIGATION_CONTROL_METHOD,
    IRRIGATION_CONTROL_METHOD_SERVICE,
    IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
    IRRIGATION_CONTROL_METHOD_SWITCH,
    MIN_SCAN_INTERVAL,
    NUM_STATIONS,
    SERVICE_ACTION_ENABLED,
    SERVICE_ACTION_SERVICE,
    SERVICE_PARAM_LABEL,
    SERVICE_PARAM_NAME,
    SERVICE_PARAM_TYPE,
    SERVICE_PARAM_VALUE,
    SOIL_MOISTURE_SENSOR,
    SOIL_MOISTURE_THRESHOLD,
    SPRINKLE_WITH_RAIN,
    STATION_SWITCH_ENTITIES,
    SUPPORTED_PARAM_TYPES,
    USE_SOIL_MOISTURE,
    WEATHER_API_CACHE_DEFAULT_TIMEOUT,
    WEATHER_API_CACHE_MIN_TIMEOUT,
    WEATHER_API_CACHE_TIMEOUT,
    WEATHER_API_KEY,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
    WEATHER_PROVIDER_OPENWEATHERMAP,
    WEATHER_PROVIDER_PIRATEWEATHER,
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_MIN_TIMEOUT,
)


def build_user_step_schema(default_name: str, default_method: str) -> vol.Schema:
    """Build schema for the initial user step."""
    return vol.Schema(
        {
            vol.Optional(CONF_NAME, default=default_name): str,
            vol.Required(IRRIGATION_CONTROL_METHOD, default=default_method): selector(
                {
                    "select": {
                        "options": [
                            IRRIGATION_CONTROL_METHOD_SWITCH,
                            IRRIGATION_CONTROL_METHOD_SERVICE,
                            IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
                        ],
                        "mode": "dropdown",
                        "translation_key": "irrigation_control_method",
                    }
                }
            ),
        }
    )


def build_service_config_schema(existing_actions: dict[str, Any]) -> vol.Schema:
    """Build schema for service/action enablement and service mapping."""
    return vol.Schema(
        {
            vol.Optional(
                f"enable_{ACTION_SPRINKLE_STATION}",
                default=bool(
                    existing_actions.get(ACTION_SPRINKLE_STATION, {}).get(
                        SERVICE_ACTION_ENABLED, True
                    )
                ),
            ): selector({"boolean": {}}),
            vol.Optional(
                f"service_{ACTION_SPRINKLE_STATION}",
                default=str(
                    existing_actions.get(ACTION_SPRINKLE_STATION, {}).get(
                        SERVICE_ACTION_SERVICE, ""
                    )
                    or ""
                ),
            ): str,

            vol.Optional(
                f"enable_{ACTION_STOP_SPRINKLE}",
                default=bool(
                    existing_actions.get(ACTION_STOP_SPRINKLE, {}).get(
                        SERVICE_ACTION_ENABLED, True
                    )
                ),
            ): selector({"boolean": {}}),
            vol.Optional(
                f"service_{ACTION_STOP_SPRINKLE}",
                default=str(
                    existing_actions.get(ACTION_STOP_SPRINKLE, {}).get(
                        SERVICE_ACTION_SERVICE, ""
                    )
                    or ""
                ),
            ): str,

            vol.Optional(
                f"enable_{ACTION_TURN_ON}",
                default=bool(
                    existing_actions.get(ACTION_TURN_ON, {}).get(
                        SERVICE_ACTION_ENABLED, True
                    )
                ),
            ): selector({"boolean": {}}),
            vol.Optional(
                f"service_{ACTION_TURN_ON}",
                default=str(
                    existing_actions.get(ACTION_TURN_ON, {}).get(
                        SERVICE_ACTION_SERVICE, ""
                    )
                    or ""
                ),
            ): str,

            vol.Optional(
                f"enable_{ACTION_TURN_OFF}",
                default=bool(
                    existing_actions.get(ACTION_TURN_OFF, {}).get(
                        SERVICE_ACTION_ENABLED, True
                    )
                ),
            ): selector({"boolean": {}}),
            vol.Optional(
                f"service_{ACTION_TURN_OFF}",
                default=str(
                    existing_actions.get(ACTION_TURN_OFF, {}).get(
                        SERVICE_ACTION_SERVICE, ""
                    )
                    or ""
                ),
            ): str,
        }
    )


def build_action_params_schema(existing_params: list[dict[str, Any]]) -> vol.Schema:
    """Build schema for action parameter configuration."""
    schema_dict: dict[Any, Any] = {}

    for idx in range(1, 6):
        current = existing_params[idx - 1] if idx - 1 < len(existing_params) else {}

        schema_dict[
            vol.Optional(
                f"param_{idx}_name",
                default=str(current.get(SERVICE_PARAM_NAME, "") or ""),
            )
        ] = str

        schema_dict[
            vol.Optional(
                f"param_{idx}_label",
                default=str(current.get(SERVICE_PARAM_LABEL, "") or ""),
            )
        ] = str

        schema_dict[
            vol.Optional(
                f"param_{idx}_value",
                default=str(current.get(SERVICE_PARAM_VALUE, "") or ""),
            )
        ] = str

        schema_dict[
            vol.Optional(
                f"param_{idx}_type",
                default=str(current.get(SERVICE_PARAM_TYPE, "other") or "other"),
            )
        ] = selector({"select": {"options": SUPPORTED_PARAM_TYPES, "mode": "dropdown"}})

    return vol.Schema(schema_dict)


def build_num_stations_schema(default_num: int) -> vol.Schema:
    """Build schema for number of stations."""
    return vol.Schema(
        {
            vol.Required(NUM_STATIONS, default=default_num): vol.All(
                vol.Coerce(int),
                vol.Clamp(min=1),
            )
        }
    )


def build_location_schema(default_zone: str | None) -> vol.Schema:
    """Build schema for controller location."""
    return vol.Schema(
        {
            vol.Required(CONF_SENSORS, default=default_zone): selector(
                {"entity": {"domain": "zone"}}
            )
        }
    )


def build_lawn_areas_schema(
    num_stations: int,
    default_names: list[str] | None = None,
    default_areas: list[float] | None = None,
) -> vol.Schema:
    """Build schema for station names and lawn areas."""
    schema_dict: dict[Any, Any] = {}

    for i in range(1, num_stations + 1):
        schema_dict[
            vol.Required(
                f"station_{i}_name",
                default=default_names[i - 1]
                if default_names and i - 1 < len(default_names)
                else f"Station {i}",
                description={"translation_key": f"station_{i}_name"},
            )
        ] = str

        schema_dict[
            vol.Required(
                f"station_{i}_area",
                default=default_areas[i - 1]
                if default_areas and i - 1 < len(default_areas)
                else 0,
                description={"translation_key": f"station_{i}_area"},
            )
        ] = vol.All(vol.Coerce(float), vol.Range(min=0))

    return vol.Schema(schema_dict)


def build_station_switches_schema(
    num_stations: int,
    existing: list[str] | None = None,
) -> vol.Schema:
    """Build schema for station switch entity selection."""
    existing = existing or []
    schema_fields: dict[Any, Any] = {}

    for i in range(1, num_stations + 1):
        default_entity = existing[i - 1] if len(existing) >= i else ""
        schema_fields[
            vol.Required(f"station_{i}_switch", default=default_entity)
        ] = selector({"entity": {"domain": "switch"}})

    return vol.Schema(schema_fields)


def build_weather_schema(
    default_provider: str,
    default_key: str,
    default_rain: str,
) -> vol.Schema:
    """Build schema for weather provider configuration."""
    return vol.Schema(
        {
            vol.Required(WEATHER_PROVIDER, default=default_provider): selector(
                {
                    "select": {
                        "options": [
                            WEATHER_PROVIDER_NONE,
                            WEATHER_PROVIDER_OPENWEATHERMAP,
                            WEATHER_PROVIDER_PIRATEWEATHER,
                        ],
                        "mode": "dropdown",
                        "translation_key": "weather_provider",
                    }
                }
            ),
            vol.Optional(WEATHER_API_KEY, default=default_key): str,
            vol.Required(SPRINKLE_WITH_RAIN, default=default_rain): _bool_select_schema(),
        }
    )


def build_soil_moisture_schema(
    default_use: str,
    default_sensor: str | None,
    default_threshold: float,
) -> vol.Schema:
    """Build schema for soil moisture configuration."""
    schema_dict: dict[Any, Any] = {
        vol.Required(USE_SOIL_MOISTURE, default=default_use): _bool_select_schema(),
    }

    if default_sensor:
        schema_dict[vol.Optional(SOIL_MOISTURE_SENSOR, default=default_sensor)] = selector(
            {"entity": {"domain": "sensor", "device_class": "humidity"}}
        )
    else:
        schema_dict[vol.Optional(SOIL_MOISTURE_SENSOR)] = selector(
            {"entity": {"domain": "sensor", "device_class": "humidity"}}
        )

    schema_dict[
        vol.Optional(SOIL_MOISTURE_THRESHOLD, default=default_threshold)
    ] = vol.All(vol.Coerce(float), vol.Range(min=0, max=100))

    return vol.Schema(schema_dict)


def build_basic_data_schema(options: dict[str, Any]) -> vol.Schema:
    """Build schema for basic options data."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Clamp(min=MIN_SCAN_INTERVAL)),
            vol.Required(
                BLUETOOTH_TIMEOUT,
                default=options.get(BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Clamp(min=BLUETOOTH_MIN_TIMEOUT)),
            vol.Required(
                WEATHER_API_CACHE_TIMEOUT,
                default=options.get(
                    WEATHER_API_CACHE_TIMEOUT,
                    WEATHER_API_CACHE_DEFAULT_TIMEOUT,
                ),
            ): vol.All(vol.Coerce(int), vol.Clamp(min=WEATHER_API_CACHE_MIN_TIMEOUT)),
        }
    )