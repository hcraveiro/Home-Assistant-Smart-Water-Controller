"""Config flow for Smart Water Controller Bluetooth Watering Controller."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL, CONF_SENSORS
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector

from .api import APIConnectionError, SmartWaterControllerAPI
from .util import normalize_mac_address
from .const import (
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_MIN_TIMEOUT,
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOIL_MOISTURE,
    DOMAIN,
    IRRIGATION_CONTROL_METHOD,
    IRRIGATION_CONTROL_METHOD_SERVICE,
    IRRIGATION_CONTROL_METHOD_SWITCH,
    ACTION_SPRINKLE_STATION,
    ACTION_STOP_SPRINKLE,
    ACTION_TURN_ON,
    ACTION_TURN_OFF,
    SUPPORTED_ACTIONS_IN_ORDER,
    SERVICE_ACTIONS,
    SERVICE_ACTION_ENABLED,
    SERVICE_ACTION_SERVICE,
    SERVICE_ACTION_PARAMS,
    STATION_SWITCH_ENTITIES,
    SERVICE_PARAM_NAME,
    SERVICE_PARAM_LABEL,
    SERVICE_PARAM_VALUE,
    SERVICE_PARAM_TYPE,
    SUPPORTED_PARAM_TYPES,
    SERVICE_PARAM_TYPE_MAC,
    SERVICE_PARAM_TYPE_OTHER,
    MIN_SCAN_INTERVAL,
    NUM_STATIONS,
    WEATHER_API_CACHE_DEFAULT_TIMEOUT,
    WEATHER_API_CACHE_MIN_TIMEOUT,
    WEATHER_API_CACHE_TIMEOUT,
    WEATHER_API_KEY,
    SOIL_MOISTURE_SENSOR,
    SOIL_MOISTURE_THRESHOLD,
    SPRINKLE_WITH_RAIN,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
    WEATHER_PROVIDER_OPENWEATHERMAP,
    WEATHER_PROVIDER_PIRATEWEATHER,
    USE_SOIL_MOISTURE,
    IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
    SOLEM_TOOLKIT_SERVICE_SPRINKLE,
    SOLEM_TOOLKIT_SERVICE_STOP,
    SOLEM_TOOLKIT_SERVICE_TURN_ON,
    SOLEM_TOOLKIT_SERVICE_TURN_OFF,
    SERVICE_PARAM_TYPE_STATION,
    SERVICE_PARAM_TYPE_TIME,
)

_LOGGER = logging.getLogger(__name__)


def _is_mac_address(value: str) -> bool:
    """Basic MAC address validation (AA:BB:CC:DD:EE:FF)."""
    parts = value.split(":")
    if len(parts) != 6:
        return False
    try:
        return all(len(p) == 2 and int(p, 16) >= 0 for p in parts)
    except ValueError:
        return False


def _bool_select_schema(*, default: str | None = None) -> Any:
    """Return a True/False dropdown selector schema."""
    sel: dict[str, Any] = {
        "select": {
            "options": ["false", "true"],
            "mode": "dropdown",
            "translation_key": "true_false_selector",
        }
    }
    if default is not None:
        return selector(sel)  # caller provides default at vol.Required/Optional
    return selector(sel)


class SmartWaterControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Water Controller Bluetooth Watering Controller."""

    VERSION = 1

    def __init__(self) -> None:
        self._input_data: dict[str, Any] = {}
        self._num_stations: int = 1
        self._reconfigure_entry: ConfigEntry | None = None
        self._selected_actions: list[str] = []
        self._current_action: str | None = None

    def _build_solem_toolkit_defaults(self) -> dict[str, Any]:
        """Return default SERVICE_ACTIONS configuration for Solem Toolkit."""
        return {
            ACTION_SPRINKLE_STATION: {
                SERVICE_ACTION_ENABLED: True,
                SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_SPRINKLE,
                SERVICE_ACTION_PARAMS: [
                    {
                        SERVICE_PARAM_NAME: "device_mac",
                        SERVICE_PARAM_LABEL: "MAC Address",
                        SERVICE_PARAM_VALUE: "",
                        SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                    },
                    {
                        SERVICE_PARAM_NAME: "station",
                        SERVICE_PARAM_LABEL: "Station",
                        SERVICE_PARAM_VALUE: "",
                        SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_STATION,
                    },
                    {
                        SERVICE_PARAM_NAME: "minutes",
                        SERVICE_PARAM_LABEL: "Minutes to sprinkle",
                        SERVICE_PARAM_VALUE: "",
                        SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_TIME,
                    },
                ],
            },
            ACTION_STOP_SPRINKLE: {
                SERVICE_ACTION_ENABLED: True,
                SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_STOP,
                SERVICE_ACTION_PARAMS: [
                    {
                        SERVICE_PARAM_NAME: "device_mac",
                        SERVICE_PARAM_LABEL: "MAC Address",
                        SERVICE_PARAM_VALUE: "",
                        SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                    }
                ],
            },
            ACTION_TURN_ON: {
                SERVICE_ACTION_ENABLED: True,
                SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_TURN_ON,
                SERVICE_ACTION_PARAMS: [
                    {
                        SERVICE_PARAM_NAME: "device_mac",
                        SERVICE_PARAM_LABEL: "MAC Address",
                        SERVICE_PARAM_VALUE: "",
                        SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                    }
                ],
            },
            ACTION_TURN_OFF: {
                SERVICE_ACTION_ENABLED: True,
                SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_TURN_OFF,
                SERVICE_ACTION_PARAMS: [
                    {
                        SERVICE_PARAM_NAME: "device_mac",
                        SERVICE_PARAM_LABEL: "MAC Address",
                        SERVICE_PARAM_VALUE: "",
                        SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                    }
                ],
            },
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return SmartWaterControllerOptionsFlowHandler(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the reconfigure flow using the same phased flow as initial setup."""
        config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if config_entry is None:
            return self.async_abort(reason="unknown")
    
        self._reconfigure_entry = config_entry
        # Pre-populate with existing data to allow defaults across all steps.
        self._input_data = dict(config_entry.data)
        self._num_stations = int(self._input_data.get(NUM_STATIONS, 1))
    
        # Preserve current title as default name if user didn't store CONF_NAME before
        if CONF_NAME not in self._input_data and config_entry.title:
            self._input_data[CONF_NAME] = config_entry.title
    
        # Kick off the same flow as a new setup.
        return await self.async_step_user(user_input)


    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: Choose instance name and how watering is controlled."""
        errors: dict[str, str] = {}
    
        if user_input is not None:
            # Store friendly name
            name = (user_input.get(CONF_NAME) or "").strip()
            if name:
                self._input_data[CONF_NAME] = name
            else:
                self._input_data.pop(CONF_NAME, None)
        
            method = user_input[IRRIGATION_CONTROL_METHOD]
            self._input_data[IRRIGATION_CONTROL_METHOD] = method
        
            # If Solem Toolkit selected -> prefill actions, but DO NOT start service screens yet
            if method == IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT:
                try:
                    self._input_data[SERVICE_ACTIONS] = self._build_solem_toolkit_defaults()
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Failed to prefill Solem Toolkit defaults")
                    errors["base"] = "unknown"
                    return self.async_show_form(step_id="user", data_schema=schema, errors=errors, last_step=False)
        
            # Next step is always stations (uniform across controller types)
            return await self.async_step_num_stations()

    
        default_method = self._input_data.get(IRRIGATION_CONTROL_METHOD, IRRIGATION_CONTROL_METHOD_SERVICE)
    
        default_name = (self._input_data.get(CONF_NAME) or "").strip()
        if not default_name:
            default_name = (self._input_data.get(CONTROLLER_MAC_ADDRESS) or "").strip()
    
        schema = vol.Schema(
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

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors, last_step=False)



    async def async_step_service_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Configure which HA services this integration will call."""
        errors: dict[str, str] = {}
    
        if user_input is not None:
            try:
                actions_config: dict[str, Any] = {}
                self._selected_actions = []
    
                # Validate and persist actions + service mapping
                for action in SUPPORTED_ACTIONS_IN_ORDER:
                    enabled = bool(user_input.get(f"enable_{action}", False))
                    service_call = (user_input.get(f"service_{action}") or "").strip()
    
                    if enabled:
                        if not service_call or "." not in service_call:
                            errors[f"service_{action}"] = "invalid_service"
    
                    existing_action = self._input_data.get(SERVICE_ACTIONS, {}).get(action, {})
                    existing_params = existing_action.get(SERVICE_ACTION_PARAMS, []) or []
    
                    actions_config[action] = {
                        SERVICE_ACTION_ENABLED: enabled,
                        SERVICE_ACTION_SERVICE: service_call if enabled else "",
                        SERVICE_ACTION_PARAMS: existing_params,
                    }
    
                    if enabled:
                        self._selected_actions.append(action)
    
                if not errors:
                    self._input_data[SERVICE_ACTIONS] = actions_config
    
                    # Jump to the first selected action (if any), otherwise continue.
                    if self._selected_actions:
                        return await self._async_goto_next_action_step()
    
                    return await self.async_step_num_stations()
    
            except AbortFlow:
                raise
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error while processing service configuration")
                errors["base"] = "unknown"
    
        existing_actions = self._input_data.get(SERVICE_ACTIONS, {})
    
        schema = vol.Schema(
            {
                vol.Optional(
                    f"enable_{ACTION_SPRINKLE_STATION}",
                    default=bool(existing_actions.get(ACTION_SPRINKLE_STATION, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_SPRINKLE_STATION}",
                    default=str(existing_actions.get(ACTION_SPRINKLE_STATION, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
    
                vol.Optional(
                    f"enable_{ACTION_STOP_SPRINKLE}",
                    default=bool(existing_actions.get(ACTION_STOP_SPRINKLE, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_STOP_SPRINKLE}",
                    default=str(existing_actions.get(ACTION_STOP_SPRINKLE, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
    
                vol.Optional(
                    f"enable_{ACTION_TURN_ON}",
                    default=bool(existing_actions.get(ACTION_TURN_ON, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_TURN_ON}",
                    default=str(existing_actions.get(ACTION_TURN_ON, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
    
                vol.Optional(
                    f"enable_{ACTION_TURN_OFF}",
                    default=bool(existing_actions.get(ACTION_TURN_OFF, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_TURN_OFF}",
                    default=str(existing_actions.get(ACTION_TURN_OFF, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
            }
        )
    
        return self.async_show_form(step_id="service_config", data_schema=schema, errors=errors, last_step=False)



    async def _async_goto_next_action_step(self) -> ConfigFlowResult:
        """Go to the next selected action configuration step."""
        if not self._selected_actions:
            self._current_action = None
            return await self.async_step_num_stations()
    
        self._current_action = self._selected_actions.pop(0)
        return await self.async_step_configure_action()

    async def async_step_configure_action(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure parameters for the currently selected logical action (generic step)."""
        action = self._current_action
        if action is None:
            return await self.async_step_location()
    
        if user_input is not None:
            return await self._async_handle_action_config_submit(action=action, user_input=user_input)
    
        return await self._async_show_action_form(action=action)

    async def _async_handle_action_config_submit(self, *, action: str, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Persist action config and advance to the next step."""
        errors: dict[str, str] = {}
    
        existing_action = self._input_data.get(SERVICE_ACTIONS, {}).get(action, {})
        service_call = str(existing_action.get(SERVICE_ACTION_SERVICE, "") or "").strip()
        if not service_call or "." not in service_call:
            errors["base"] = "invalid_service"
    
        params: list[dict[str, Any]] = []
        for idx in range(1, 6):
            name = (user_input.get(f"param_{idx}_name") or "").strip()
            label = (user_input.get(f"param_{idx}_label") or "").strip()
            value = (user_input.get(f"param_{idx}_value") or "").strip()
            ptype = (user_input.get(f"param_{idx}_type") or "").strip()
    
            if not name and not label and not value and not ptype:
                continue
    
            if not ptype:
                ptype = SERVICE_PARAM_TYPE_OTHER
    
            if ptype == SERVICE_PARAM_TYPE_MAC:
                if value and not _is_mac_address(value):
                    errors["base"] = "invalid_mac"
    
            params.append(
                {
                    SERVICE_PARAM_NAME: name,
                    SERVICE_PARAM_LABEL: label,
                    SERVICE_PARAM_VALUE: value if value else "",
                    SERVICE_PARAM_TYPE: ptype,
                }
            )
    
        if errors:
            return await self._async_show_action_form(action=action, errors=errors)
    
        self._input_data.setdefault(SERVICE_ACTIONS, {}).setdefault(action, {})
        self._input_data[SERVICE_ACTIONS][action].update(
            {
                SERVICE_ACTION_ENABLED: True,
                SERVICE_ACTION_SERVICE: service_call,
                SERVICE_ACTION_PARAMS: params,
            }
        )
    
        # If we still have actions to configure, continue.
        if self._selected_actions:
            return await self._async_goto_next_action_step()
    
        # All actions configured -> enforce MAC presence across enabled actions.
        mac_value: str | None = None
        for a in SUPPORTED_ACTIONS_IN_ORDER:
            cfg = self._input_data.get(SERVICE_ACTIONS, {}).get(a, {})
            if not cfg.get(SERVICE_ACTION_ENABLED):
                continue
            for p in (cfg.get(SERVICE_ACTION_PARAMS, []) or []):
                if p.get(SERVICE_PARAM_TYPE) == SERVICE_PARAM_TYPE_MAC:
                    candidate = str(p.get(SERVICE_PARAM_VALUE, "") or "").strip()
                    if candidate:
                        mac_value = candidate
                        break
            if mac_value:
                break
    
        if not mac_value:
            # No MAC provided in any enabled action -> error on current screen.
            return await self._async_show_action_form(action=action, errors={"base": "mac_required"})
    
        # Use MAC as unique_id (stable).
        await self.async_set_unique_id(normalize_mac_address(mac_value))
        if self._reconfigure_entry is None:
            self._abort_if_unique_id_configured()
    
        return await self.async_step_location()


    async def _async_show_action_form(self, *, action: str, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        """Render a parameter configuration form for a given logical action."""
        errors = errors or {}
        existing = self._input_data.get(SERVICE_ACTIONS, {}).get(action, {})
        existing_params: list[dict[str, Any]] = existing.get(SERVICE_ACTION_PARAMS, []) or []
        service_call = str(existing.get(SERVICE_ACTION_SERVICE, "") or "")
    
        schema_dict: dict[Any, Any] = {}
    
        for idx in range(1, 6):
            current = existing_params[idx - 1] if idx - 1 < len(existing_params) else {}
            schema_dict[vol.Optional(f"param_{idx}_name", default=str(current.get(SERVICE_PARAM_NAME, "") or ""))] = str
            schema_dict[vol.Optional(f"param_{idx}_label", default=str(current.get(SERVICE_PARAM_LABEL, "") or ""))] = str
            schema_dict[vol.Optional(f"param_{idx}_value", default=str(current.get(SERVICE_PARAM_VALUE, "") or ""))] = str
            schema_dict[
                vol.Optional(f"param_{idx}_type", default=str(current.get(SERVICE_PARAM_TYPE, "other") or "other"))
            ] = selector({"select": {"options": SUPPORTED_PARAM_TYPES, "mode": "dropdown"}})
    
        description = (
            f"Action: {action}\n"
            f"Service: {service_call}\n\n"
            "Fill only the parameters you want to hardcode.\n"
            "Empty values will be provided by the coordinator at runtime."
        )
    
        return self.async_show_form(
            step_id="configure_action",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={"service_description": description},
            last_step=False,
        )


    async def async_step_num_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: Select number of stations."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._input_data.update(user_input)
            self._num_stations = int(self._input_data[NUM_STATIONS])
            return await self.async_step_lawn_areas()

        default_num = int(self._input_data.get(NUM_STATIONS, 1))
        schema = vol.Schema(
            {
                vol.Required(NUM_STATIONS, default=default_num): vol.All(
                    vol.Coerce(int), vol.Clamp(min=1)
                )
            }
        )

        return self.async_show_form(step_id="num_stations", data_schema=schema, errors=errors, last_step=False)

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 6: Select controller location (zone entity)."""
        if user_input is not None:
            self._input_data.update(user_input)
            return await self.async_step_weather()

        default_zone = self._input_data.get(CONF_SENSORS)
        schema = vol.Schema(
            {
                vol.Required(CONF_SENSORS, default=default_zone): selector(
                    {"entity": {"domain": "zone"}}
                )
            }
        )

        return self.async_show_form(step_id="location", data_schema=schema, errors={}, last_step=False)

    async def async_step_lawn_areas(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 5: Configure lawn names and areas per station."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                station_names = [
                    user_input[f"station_{i}_name"].strip() for i in range(1, self._num_stations + 1)
                ]
                station_areas = [
                    user_input[f"station_{i}_area"] for i in range(1, self._num_stations + 1)
                ]
                self._input_data["station_names"] = station_names
                self._input_data["station_areas"] = station_areas
                
                self._input_data["station_names"] = station_names
                self._input_data["station_areas"] = station_areas
                
                method = self._input_data.get(IRRIGATION_CONTROL_METHOD, IRRIGATION_CONTROL_METHOD_SERVICE)
                
                # Switch-based flow: collect one switch entity per station.
                if method == IRRIGATION_CONTROL_METHOD_SWITCH:
                    return await self.async_step_station_switches()
                
                # After stations are defined, go to services configuration
                if method == IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT:
                    # Services are already prefilled; go straight to the parameter screens
                    self._selected_actions = list(SUPPORTED_ACTIONS_IN_ORDER)
                    return await self._async_goto_next_action_step()
                
                # Manual service mapping flow
                return await self.async_step_service_config()

            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Failed to process station areas")
                errors["base"] = "unknown"

        previous_names = self._input_data.get("station_names", [])
        previous_areas = self._input_data.get("station_areas", [])
        area_schema = self._build_lawn_areas_schema(previous_names, previous_areas)

        return self.async_show_form(
            step_id="lawn_areas",
            data_schema=area_schema,
            errors=errors,
            last_step=False,
        )

    async def async_step_station_switches(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 6 (switch method): Select one switch entity per station."""
        errors: dict[str, str] = {}
    
        if user_input is not None:
            switches: list[str] = []
            for i in range(1, self._num_stations + 1):
                entity_id = (user_input.get(f"station_{i}_switch") or "").strip()
                if not entity_id:
                    errors[f"station_{i}_switch"] = "required"
                switches.append(entity_id)
    
            if not errors:
                self._input_data[STATION_SWITCH_ENTITIES] = switches
                return await self.async_step_location()
    
        existing: list[str] = self._input_data.get(STATION_SWITCH_ENTITIES, []) or []
        schema_fields: dict[Any, Any] = {}
        for i in range(1, self._num_stations + 1):
            default_entity = existing[i - 1] if len(existing) >= i else ""
            schema_fields[
                vol.Required(f"station_{i}_switch", default=default_entity)
            ] = selector({"entity": {"domain": "switch"}})
    
        return self.async_show_form(
            step_id="station_switches",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
            last_step=False,
        )

    async def async_step_weather(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 7: Configure Weather provider and rain behavior."""
        if user_input is not None:
            self._input_data.update(user_input)
            return await self.async_step_soil_moisture()
    
        default_provider = self._input_data.get(
            WEATHER_PROVIDER, WEATHER_PROVIDER_NONE
        )
        default_key = self._input_data.get(WEATHER_API_KEY, "")
        default_rain = self._input_data.get(SPRINKLE_WITH_RAIN, "false")
    
        schema = vol.Schema(
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
    
        return self.async_show_form(step_id="weather", data_schema=schema, last_step=False)


    async def async_step_soil_moisture(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 8: Configure soil moisture sensor (optional)."""
        if user_input is not None:
            self._input_data.update(user_input)
    
            controller_mac = (self._input_data.get(CONTROLLER_MAC_ADDRESS) or "").strip()
            friendly_name = (self._input_data.get(CONF_NAME) or "").strip()
    
            title = friendly_name or controller_mac or "SmartWaterController"
    
            if self._reconfigure_entry is not None:
                self.hass.config_entries.async_update_entry(
                    self._reconfigure_entry,
                    data=self._input_data,
                    title=title,
                )
                await self.hass.config_entries.async_reload(self._reconfigure_entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")
    
            return self.async_create_entry(title=title, data=self._input_data)
    
        default_use = self._input_data.get(USE_SOIL_MOISTURE, "false")
        default_sensor = self._input_data.get(SOIL_MOISTURE_SENSOR)
        default_threshold = self._input_data.get(SOIL_MOISTURE_THRESHOLD, DEFAULT_SOIL_MOISTURE)
    
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
    
        return self.async_show_form(
            step_id="soil_moisture",
            data_schema=vol.Schema(schema_dict),
            errors={},
            last_step=True,
        )



    def _build_lawn_areas_schema(
        self,
        default_names: list[str] | None = None,
        default_areas: list[float] | None = None,
    ) -> vol.Schema:
        """Generate schema for lawn names and areas with optional defaults."""
        schema_dict: dict[Any, Any] = {}
        for i in range(1, self._num_stations + 1):
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


class SmartWaterControllerOptionsFlowHandler(OptionsFlow):
    """Handle options flow with a menu of configuration sections."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._options: dict[str, Any] = dict(config_entry.options)
    
        # For the "services" options flow (generic action loop)
        self._selected_actions: list[str] = []
        self._current_action: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show a menu with configurable sections."""
        method = self._config_entry.data.get(IRRIGATION_CONTROL_METHOD, IRRIGATION_CONTROL_METHOD_SERVICE)
        menu_options = ["basic_data", "num_stations", "lawn_areas"]
        if method == IRRIGATION_CONTROL_METHOD_SWITCH:
            menu_options.append("station_switches")
        else:
            menu_options.append("services")
        menu_options.extend(["weather", "soil_moisture"])

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    async def async_step_basic_data(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: basic integration options (scan interval, timeouts, etc.)."""
        if user_input is not None:
            new_options = {**self._config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self._options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=MIN_SCAN_INTERVAL)),
                vol.Required(
                    BLUETOOTH_TIMEOUT,
                    default=self._options.get(BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=BLUETOOTH_MIN_TIMEOUT)),
                vol.Required(
                    WEATHER_API_CACHE_TIMEOUT,
                    default=self._options.get(
                        WEATHER_API_CACHE_TIMEOUT,
                        WEATHER_API_CACHE_DEFAULT_TIMEOUT,
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=WEATHER_API_CACHE_MIN_TIMEOUT)),
            }
        )

        return self.async_show_form(step_id="basic_data", data_schema=data_schema)

    async def async_step_services(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: configure enabled actions + service mapping (stored in config_entry.data)."""
        errors: dict[str, str] = {}
    
        if user_input is not None:
            try:
                actions_config: dict[str, Any] = {}
                self._selected_actions = []
    
                for action in SUPPORTED_ACTIONS_IN_ORDER:
                    enabled = bool(user_input.get(f"enable_{action}", False))
                    service_call = (user_input.get(f"service_{action}") or "").strip()
    
                    if enabled:
                        if not service_call or "." not in service_call:
                            errors[f"service_{action}"] = "invalid_service"
    
                    existing_action = self._config_entry.data.get(SERVICE_ACTIONS, {}).get(action, {})
                    existing_params = existing_action.get(SERVICE_ACTION_PARAMS, []) or []
    
                    actions_config[action] = {
                        SERVICE_ACTION_ENABLED: enabled,
                        SERVICE_ACTION_SERVICE: service_call if enabled else "",
                        SERVICE_ACTION_PARAMS: existing_params,
                    }
    
                    if enabled:
                        self._selected_actions.append(action)
    
                if not errors:
                    new_data = {**self._config_entry.data, SERVICE_ACTIONS: actions_config}
                    self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
    
                    if self._selected_actions:
                        return await self._async_goto_next_action_step_services()
    
                    await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                    return self.async_create_entry(title="", data=dict(self._config_entry.options))
    
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error while processing services options")
                errors["base"] = "unknown"
    
        existing_actions = self._config_entry.data.get(SERVICE_ACTIONS, {})
    
        schema = vol.Schema(
            {
                vol.Optional(
                    f"enable_{ACTION_SPRINKLE_STATION}",
                    default=bool(existing_actions.get(ACTION_SPRINKLE_STATION, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_SPRINKLE_STATION}",
                    default=str(existing_actions.get(ACTION_SPRINKLE_STATION, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
    
                vol.Optional(
                    f"enable_{ACTION_STOP_SPRINKLE}",
                    default=bool(existing_actions.get(ACTION_STOP_SPRINKLE, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_STOP_SPRINKLE}",
                    default=str(existing_actions.get(ACTION_STOP_SPRINKLE, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
    
                vol.Optional(
                    f"enable_{ACTION_TURN_ON}",
                    default=bool(existing_actions.get(ACTION_TURN_ON, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_TURN_ON}",
                    default=str(existing_actions.get(ACTION_TURN_ON, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
    
                vol.Optional(
                    f"enable_{ACTION_TURN_OFF}",
                    default=bool(existing_actions.get(ACTION_TURN_OFF, {}).get(SERVICE_ACTION_ENABLED, True)),
                ): selector({"boolean": {}}),
                vol.Optional(
                    f"service_{ACTION_TURN_OFF}",
                    default=str(existing_actions.get(ACTION_TURN_OFF, {}).get(SERVICE_ACTION_SERVICE, "") or ""),
                ): str,
            }
        )
    
        return self.async_show_form(step_id="services", data_schema=schema, errors=errors)


    async def _async_goto_next_action_step_services(self) -> ConfigFlowResult:
        """Go to the next selected action configuration step (options flow)."""
        if not self._selected_actions:
            self._current_action = None
            return await self.async_step_location()

        self._current_action = self._selected_actions.pop(0)
        return await self.async_step_services_configure_action()
    
    async def async_step_services_configure_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options: configure params for the currently selected action (generic step)."""
        action = self._current_action
        if action is None:
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))
    
        if user_input is not None:
            return await self._async_handle_action_config_submit_services(action=action, user_input=user_input)
    
        return await self._async_show_action_form_services(action=action)

    async def _async_handle_action_config_submit_services(
        self, *, action: str, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Persist action params (options) and advance to the next step."""
        errors: dict[str, str] = {}
    
        existing_action = self._config_entry.data.get(SERVICE_ACTIONS, {}).get(action, {})
        service_call = str(existing_action.get(SERVICE_ACTION_SERVICE, "") or "").strip()
        if not service_call or "." not in service_call:
            errors["base"] = "invalid_service"
    
        params: list[dict[str, Any]] = []
        for idx in range(1, 6):
            name = (user_input.get(f"param_{idx}_name") or "").strip()
            label = (user_input.get(f"param_{idx}_label") or "").strip()
            value = (user_input.get(f"param_{idx}_value") or "").strip()
            ptype = (user_input.get(f"param_{idx}_type") or "").strip()
    
            if not name and not label and not value and not ptype:
                continue
    
            if not ptype:
                ptype = SERVICE_PARAM_TYPE_OTHER
    
            if ptype == SERVICE_PARAM_TYPE_MAC:
                if value and not _is_mac_address(value):
                    errors["base"] = "invalid_mac"
    
            params.append(
                {
                    SERVICE_PARAM_NAME: name,
                    SERVICE_PARAM_LABEL: label,
                    SERVICE_PARAM_VALUE: value if value else "",
                    SERVICE_PARAM_TYPE: ptype,
                }
            )
    
        if errors:
            return await self._async_show_action_form_services(action=action, errors=errors)
    
        # Update the action params in config_entry.data
        current_actions = dict(self._config_entry.data.get(SERVICE_ACTIONS, {}))
        current_action_cfg = dict(current_actions.get(action, {}))
        current_action_cfg[SERVICE_ACTION_PARAMS] = params
        current_actions[action] = current_action_cfg
    
        new_data = {**self._config_entry.data, SERVICE_ACTIONS: current_actions}
        self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
    
        # If more actions to configure, continue loop
        if self._selected_actions:
            return await self._async_goto_next_action_step_services()
    
        # All action params configured -> enforce MAC presence across enabled actions
        mac_value: str | None = None
        for a in SUPPORTED_ACTIONS_IN_ORDER:
            cfg = new_data.get(SERVICE_ACTIONS, {}).get(a, {})
            if not cfg.get(SERVICE_ACTION_ENABLED):
                continue
            for p in (cfg.get(SERVICE_ACTION_PARAMS, []) or []):
                if p.get(SERVICE_PARAM_TYPE) == SERVICE_PARAM_TYPE_MAC:
                    candidate = str(p.get(SERVICE_PARAM_VALUE, "") or "").strip()
                    if candidate:
                        mac_value = candidate
                        break
            if mac_value:
                break
    
        if not mac_value:
            return await self._async_show_action_form_services(action=action, errors={"base": "mac_required"})
    
        await self.hass.config_entries.async_reload(self._config_entry.entry_id)
        return self.async_create_entry(title="", data=dict(self._config_entry.options))

    async def _async_show_action_form_services(
        self, *, action: str, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Render a parameter configuration form for a given action (options flow)."""
        errors = errors or {}
        existing = self._config_entry.data.get(SERVICE_ACTIONS, {}).get(action, {})
        existing_params: list[dict[str, Any]] = existing.get(SERVICE_ACTION_PARAMS, []) or []
        service_call = str(existing.get(SERVICE_ACTION_SERVICE, "") or "")
    
        schema_dict: dict[Any, Any] = {}
    
        for idx in range(1, 6):
            current = existing_params[idx - 1] if idx - 1 < len(existing_params) else {}
            schema_dict[vol.Optional(f"param_{idx}_name", default=str(current.get(SERVICE_PARAM_NAME, "") or ""))] = str
            schema_dict[vol.Optional(f"param_{idx}_label", default=str(current.get(SERVICE_PARAM_LABEL, "") or ""))] = str
            schema_dict[vol.Optional(f"param_{idx}_value", default=str(current.get(SERVICE_PARAM_VALUE, "") or ""))] = str
            schema_dict[
                vol.Optional(f"param_{idx}_type", default=str(current.get(SERVICE_PARAM_TYPE, "other") or "other"))
            ] = selector({"select": {"options": SUPPORTED_PARAM_TYPES, "mode": "dropdown"}})
    
        description = (
            f"Action: {action}\n"
            f"Service: {service_call}\n\n"
            "Fill only the parameters you want to hardcode.\n"
            "Empty values will be provided by the coordinator at runtime."
        )
    
        return self.async_show_form(
            step_id="services_configure_action",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={"service_description": description},
        )

    async def async_step_num_stations(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: configure number of stations."""
        errors: dict[str, str] = {}
    
        current_num = int(self._config_entry.data.get(NUM_STATIONS, 1))
        current_names = list(self._config_entry.data.get("station_names", []) or [])
        current_areas = list(self._config_entry.data.get("station_areas", []) or [])
    
        if user_input is not None:
            try:
                new_num = int(user_input[NUM_STATIONS])
    
                # Resize names
                names = current_names[:new_num]
                while len(names) < new_num:
                    names.append(f"Station {len(names) + 1}")
    
                # Resize areas
                areas = current_areas[:new_num]
                while len(areas) < new_num:
                    areas.append(0)
    
                new_data = {
                    **self._config_entry.data,
                    NUM_STATIONS: new_num,
                    "station_names": names,
                    "station_areas": areas,
                }
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
                await self.hass.config_entries.async_reload(self._config_entry.entry_id)
    
                # Nice UX: jump straight to lawn areas to edit new stations
                return await self.async_step_lawn_areas()
    
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Failed to update number of stations")
                errors["base"] = "unknown"
    
        schema = vol.Schema(
            {
                vol.Required(NUM_STATIONS, default=current_num): vol.All(
                    vol.Coerce(int),
                    vol.Clamp(min=1),
                )
            }
        )
    
        return self.async_show_form(step_id="num_stations", data_schema=schema, errors=errors)

    async def async_step_lawn_areas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: configure lawn names and areas per station."""
        num_stations = int(self._config_entry.data.get(NUM_STATIONS, 1))
        previous_names = self._config_entry.data.get("station_names", [])
        previous_areas = self._config_entry.data.get("station_areas", [])

        if user_input is not None:
            station_names = [user_input[f"station_{i}_name"].strip() for i in range(1, num_stations + 1)]
            station_areas = [user_input[f"station_{i}_area"] for i in range(1, num_stations + 1)]
            new_data = {**self._config_entry.data, "station_names": station_names, "station_areas": station_areas}
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        schema = vol.Schema(
            {
                **{
                    vol.Required(
                        f"station_{i}_name",
                        default=previous_names[i - 1]
                        if previous_names and i - 1 < len(previous_names)
                        else f"Station {i}",
                        description={"translation_key": f"station_{i}_name"},
                    ): str
                    for i in range(1, num_stations + 1)
                },
                **{
                    vol.Required(
                        f"station_{i}_area",
                        default=previous_areas[i - 1]
                        if previous_areas and i - 1 < len(previous_areas)
                        else 0,
                        description={"translation_key": f"station_{i}_area"},
                    ): vol.All(vol.Coerce(float), vol.Range(min=0))
                    for i in range(1, num_stations + 1)
                },
            }
        )
        
        return self.async_show_form(step_id="lawn_areas", data_schema=schema)

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: configure Weather provider and rain behavior."""
        if user_input is not None:
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))
    
        schema = vol.Schema(
            {
                vol.Required(
                    WEATHER_PROVIDER,
                    default=self._config_entry.data.get(
                        WEATHER_PROVIDER, WEATHER_PROVIDER_NONE
                    ),
                ): selector(
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
                vol.Optional(
                    WEATHER_API_KEY,
                    default=self._config_entry.data.get(WEATHER_API_KEY, ""),
                ): str,
                vol.Required(
                    SPRINKLE_WITH_RAIN,
                    default=self._config_entry.data.get(SPRINKLE_WITH_RAIN, "false"),
                ): _bool_select_schema(),
            }
        )
    
        return self.async_show_form(step_id="weather", data_schema=schema)


    async def async_step_soil_moisture(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: configure soil moisture sensor and threshold."""
        if user_input is not None:
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        default_sensor = self._config_entry.data.get(SOIL_MOISTURE_SENSOR)
        schema_dict: dict[Any, Any] = {
            vol.Required(
                USE_SOIL_MOISTURE,
                default=self._config_entry.data.get(USE_SOIL_MOISTURE, "false"),
            ): _bool_select_schema(),
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
            vol.Optional(
                SOIL_MOISTURE_THRESHOLD,
                default=self._config_entry.data.get(SOIL_MOISTURE_THRESHOLD, DEFAULT_SOIL_MOISTURE),
            )
        ] = vol.All(vol.Coerce(float), vol.Range(min=0, max=100))

        return self.async_show_form(step_id="soil_moisture", data_schema=vol.Schema(schema_dict))

    async def async_step_station_switches(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Options: configure one switch entity per station (switch control method)."""
        num_stations = int(self._config_entry.data.get(NUM_STATIONS, 1))
        existing: list[str] = self._config_entry.data.get(STATION_SWITCH_ENTITIES, []) or []
    
        errors: dict[str, str] = {}
    
        if user_input is not None:
            switches: list[str] = []
            for i in range(1, num_stations + 1):
                entity_id = (user_input.get(f"station_{i}_switch") or "").strip()
                if not entity_id:
                    errors[f"station_{i}_switch"] = "required"
                switches.append(entity_id)
    
            if not errors:
                new_data = {**self._config_entry.data, STATION_SWITCH_ENTITIES: switches}
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
                await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                return self.async_create_entry(title="", data=dict(self._config_entry.options))
    
        schema_fields: dict[Any, Any] = {}
        for i in range(1, num_stations + 1):
            default_entity = existing[i - 1] if len(existing) >= i else ""
            schema_fields[vol.Required(f"station_{i}_switch", default=default_entity)] = selector(
                {"entity": {"domain": "switch"}}
            )
    
        return self.async_show_form(
            step_id="station_switches",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
