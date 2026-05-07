"""Config flow implementation for Smart Water Controller."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_SENSORS
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow

from ..helpers.util import normalize_mac_address
from ..const import (
    ACTION_SPRINKLE_STATION,
    ACTION_STOP_SPRINKLE,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SOIL_MOISTURE,
    DOMAIN,
    IRRIGATION_CONTROL_METHOD,
    IRRIGATION_CONTROL_METHOD_SERVICE,
    IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
    IRRIGATION_CONTROL_METHOD_SWITCH,
    NUM_STATIONS,
    SERVICE_ACTIONS,
    SERVICE_ACTION_ENABLED,
    SERVICE_ACTION_PARAMS,
    SERVICE_ACTION_SERVICE,
    SERVICE_PARAM_TYPE_MAC,
    SOIL_MOISTURE_SENSOR,
    SOIL_MOISTURE_THRESHOLD,
    SPRINKLE_WITH_RAIN,
    STATION_SWITCH_ENTITIES,
    SUPPORTED_ACTIONS_IN_ORDER,
    USE_SOIL_MOISTURE,
    WEATHER_API_KEY,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
)
from .schemas import (
    build_action_params_schema,
    build_lawn_areas_schema,
    build_location_schema,
    build_num_stations_schema,
    build_service_config_schema,
    build_soil_moisture_schema,
    build_station_switches_schema,
    build_user_step_schema,
    build_weather_schema,
)
from .service_actions import (
    build_action_description,
    build_solem_toolkit_defaults,
    find_first_mac_in_enabled_actions,
    parse_action_params_form,
    parse_service_config_form,
)

_LOGGER = logging.getLogger(__name__)


class SmartWaterControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Water Controller."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._input_data: dict[str, Any] = {}
        self._num_stations: int = 1
        self._reconfigure_entry: ConfigEntry | None = None
        self._selected_actions: list[str] = []
        self._current_action: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Get the options flow for this handler."""
        from .options_flow_impl import SmartWaterControllerOptionsFlowHandler

        return SmartWaterControllerOptionsFlowHandler(config_entry)

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Start the reconfigure flow using the same phased flow as initial setup."""
        config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if config_entry is None:
            return self.async_abort(reason="unknown")

        self._reconfigure_entry = config_entry
        self._input_data = dict(config_entry.data)
        self._num_stations = int(self._input_data.get(NUM_STATIONS, 1))

        if CONF_NAME not in self._input_data and config_entry.title:
            self._input_data[CONF_NAME] = config_entry.title

        return await self.async_step_user(user_input)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step 1: Choose instance name and irrigation control method."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            if name:
                self._input_data[CONF_NAME] = name
            else:
                self._input_data.pop(CONF_NAME, None)

            method = user_input[IRRIGATION_CONTROL_METHOD]
            self._input_data[IRRIGATION_CONTROL_METHOD] = method

            if method == IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT:
                try:
                    self._input_data[SERVICE_ACTIONS] = build_solem_toolkit_defaults()
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Failed to prefill Solem Toolkit defaults")
                    errors["base"] = "unknown"
                    default_method = self._input_data.get(
                        IRRIGATION_CONTROL_METHOD,
                        IRRIGATION_CONTROL_METHOD_SERVICE,
                    )
                    default_name = (self._input_data.get(CONF_NAME) or "").strip()
                    if not default_name:
                        default_name = (
                            self._input_data.get(CONTROLLER_MAC_ADDRESS) or ""
                        ).strip()

                    return self.async_show_form(
                        step_id="user",
                        data_schema=build_user_step_schema(default_name, default_method),
                        errors=errors,
                        last_step=False,
                    )

            return await self.async_step_num_stations()

        default_method = self._input_data.get(
            IRRIGATION_CONTROL_METHOD,
            IRRIGATION_CONTROL_METHOD_SERVICE,
        )

        default_name = (self._input_data.get(CONF_NAME) or "").strip()
        if not default_name:
            default_name = (self._input_data.get(CONTROLLER_MAC_ADDRESS) or "").strip()

        return self.async_show_form(
            step_id="user",
            data_schema=build_user_step_schema(default_name, default_method),
            errors=errors,
            last_step=False,
        )

    async def async_step_service_config(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Configure which HA services this integration will call."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                actions_config, selected_actions, errors = parse_service_config_form(
                    self._input_data.get(SERVICE_ACTIONS, {}),
                    user_input,
                )

                if not errors:
                    self._input_data[SERVICE_ACTIONS] = actions_config
                    self._selected_actions = selected_actions

                    if self._selected_actions:
                        return await self._async_goto_next_action_step()

                    return await self.async_step_location()

            except AbortFlow:
                raise
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error while processing service configuration")
                errors["base"] = "unknown"

        existing_actions = self._input_data.get(SERVICE_ACTIONS, {})

        return self.async_show_form(
            step_id="service_config",
            data_schema=build_service_config_schema(existing_actions),
            errors=errors,
            last_step=False,
        )

    async def _async_goto_next_action_step(self) -> ConfigFlowResult:
        """Go to the next selected action configuration step."""
        if not self._selected_actions:
            self._current_action = None
            return await self.async_step_location()

        self._current_action = self._selected_actions.pop(0)
        return await self.async_step_configure_action()

    async def async_step_configure_action(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure parameters for the currently selected logical action."""
        action = self._current_action
        if action is None:
            return await self.async_step_location()

        if user_input is not None:
            return await self._async_handle_action_config_submit(
                action=action,
                user_input=user_input,
            )

        return await self._async_show_action_form(action=action)

    async def _async_handle_action_config_submit(
        self,
        *,
        action: str,
        user_input: dict[str, Any],
    ) -> ConfigFlowResult:
        """Persist action config and advance to the next step."""
        errors: dict[str, str] = {}

        existing_action = self._input_data.get(SERVICE_ACTIONS, {}).get(action, {})
        service_call = str(existing_action.get(SERVICE_ACTION_SERVICE, "") or "").strip()
        if not service_call or "." not in service_call:
            errors["base"] = "invalid_service"

        params, param_errors = parse_action_params_form(user_input)
        errors.update(param_errors)

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

        if self._selected_actions:
            return await self._async_goto_next_action_step()

        mac_value = find_first_mac_in_enabled_actions(
            self._input_data.get(SERVICE_ACTIONS, {})
        )

        if not mac_value:
            return await self._async_show_action_form(
                action=action,
                errors={"base": "mac_required"},
            )

        await self.async_set_unique_id(normalize_mac_address(mac_value))
        if self._reconfigure_entry is None:
            self._abort_if_unique_id_configured()

        return await self.async_step_location()

    async def _async_show_action_form(
        self,
        *,
        action: str,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Render a parameter configuration form for a given logical action."""
        errors = errors or {}
        existing = self._input_data.get(SERVICE_ACTIONS, {}).get(action, {})
        existing_params: list[dict[str, Any]] = existing.get(SERVICE_ACTION_PARAMS, []) or []
        service_call = str(existing.get(SERVICE_ACTION_SERVICE, "") or "")

        description = build_action_description(action, service_call)

        return self.async_show_form(
            step_id="configure_action",
            data_schema=build_action_params_schema(existing_params),
            errors=errors,
            description_placeholders={"service_description": description},
            last_step=False,
        )

    async def async_step_num_stations(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Select number of stations."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._input_data.update(user_input)
            self._num_stations = int(self._input_data[NUM_STATIONS])
            return await self.async_step_lawn_areas()

        default_num = int(self._input_data.get(NUM_STATIONS, 1))

        return self.async_show_form(
            step_id="num_stations",
            data_schema=build_num_stations_schema(default_num),
            errors=errors,
            last_step=False,
        )

    async def async_step_location(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Select controller location."""
        if user_input is not None:
            self._input_data.update(user_input)
            return await self.async_step_weather()

        default_zone = self._input_data.get(CONF_SENSORS)

        return self.async_show_form(
            step_id="location",
            data_schema=build_location_schema(default_zone),
            errors={},
            last_step=False,
        )

    async def async_step_lawn_areas(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Configure lawn names and areas per station."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                station_names = [
                    user_input[f"station_{i}_name"].strip()
                    for i in range(1, self._num_stations + 1)
                ]
                station_areas = [
                    user_input[f"station_{i}_area"]
                    for i in range(1, self._num_stations + 1)
                ]

                self._input_data["station_names"] = station_names
                self._input_data["station_areas"] = station_areas

                method = self._input_data.get(
                    IRRIGATION_CONTROL_METHOD,
                    IRRIGATION_CONTROL_METHOD_SERVICE,
                )

                if method == IRRIGATION_CONTROL_METHOD_SWITCH:
                    return await self.async_step_station_switches()

                if method == IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT:
                    self._selected_actions = list(SUPPORTED_ACTIONS_IN_ORDER)
                    return await self._async_goto_next_action_step()

                return await self.async_step_service_config()

            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Failed to process station areas")
                errors["base"] = "unknown"

        previous_names = self._input_data.get("station_names", [])
        previous_areas = self._input_data.get("station_areas", [])

        return self.async_show_form(
            step_id="lawn_areas",
            data_schema=build_lawn_areas_schema(
                self._num_stations,
                previous_names,
                previous_areas,
            ),
            errors=errors,
            last_step=False,
        )

    async def async_step_station_switches(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Select one switch entity per station."""
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

        return self.async_show_form(
            step_id="station_switches",
            data_schema=build_station_switches_schema(self._num_stations, existing),
            errors=errors,
            last_step=False,
        )

    async def async_step_weather(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Configure weather provider and rain behavior."""
        if user_input is not None:
            self._input_data.update(user_input)
            return await self.async_step_soil_moisture()

        default_provider = self._input_data.get(WEATHER_PROVIDER, WEATHER_PROVIDER_NONE)
        default_key = self._input_data.get(WEATHER_API_KEY, "")
        default_rain = self._input_data.get(SPRINKLE_WITH_RAIN, "false")

        return self.async_show_form(
            step_id="weather",
            data_schema=build_weather_schema(
                default_provider,
                default_key,
                default_rain,
            ),
            last_step=False,
        )

    async def async_step_soil_moisture(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step: Configure optional soil moisture sensor."""
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
        default_threshold = self._input_data.get(
            SOIL_MOISTURE_THRESHOLD,
            DEFAULT_SOIL_MOISTURE,
        )

        return self.async_show_form(
            step_id="soil_moisture",
            data_schema=build_soil_moisture_schema(
                default_use,
                default_sensor,
                default_threshold,
            ),
            errors={},
            last_step=True,
        )