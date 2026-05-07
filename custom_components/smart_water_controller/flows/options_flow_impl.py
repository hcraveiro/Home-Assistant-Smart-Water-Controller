"""Options flow implementation for Smart Water Controller."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow

from ..const import (
    DEFAULT_SOIL_MOISTURE,
    IRRIGATION_CONTROL_METHOD,
    IRRIGATION_CONTROL_METHOD_SERVICE,
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
    build_basic_data_schema,
    build_lawn_areas_schema,
    build_num_stations_schema,
    build_service_config_schema,
    build_soil_moisture_schema,
    build_station_switches_schema,
    build_weather_schema,
)
from .service_actions import (
    build_action_description,
    find_first_mac_in_enabled_actions,
    parse_action_params_form,
    parse_service_config_form,
)

_LOGGER = logging.getLogger(__name__)


class SmartWaterControllerOptionsFlowHandler(OptionsFlow):
    """Handle options flow with a menu of configuration sections."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._options: dict[str, Any] = dict(config_entry.options)
        self._selected_actions: list[str] = []
        self._current_action: str | None = None

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show a menu with configurable sections."""
        method = self._config_entry.data.get(
            IRRIGATION_CONTROL_METHOD,
            IRRIGATION_CONTROL_METHOD_SERVICE,
        )
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

    async def async_step_basic_data(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure scan interval and runtime timeouts."""
        if user_input is not None:
            new_options = {**self._config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="basic_data",
            data_schema=build_basic_data_schema(self._options),
        )

    async def async_step_services(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure enabled actions and service mapping."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                actions_config, selected_actions, errors = parse_service_config_form(
                    self._config_entry.data.get(SERVICE_ACTIONS, {}),
                    user_input,
                )

                if not errors:
                    new_data = {**self._config_entry.data, SERVICE_ACTIONS: actions_config}
                    self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)

                    self._selected_actions = selected_actions

                    if self._selected_actions:
                        return await self._async_goto_next_action_step_services()

                    await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                    return self.async_create_entry(
                        title="",
                        data=dict(self._config_entry.options),
                    )

            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error while processing services options")
                errors["base"] = "unknown"

        existing_actions = self._config_entry.data.get(SERVICE_ACTIONS, {})

        return self.async_show_form(
            step_id="services",
            data_schema=build_service_config_schema(existing_actions),
            errors=errors,
        )

    async def _async_goto_next_action_step_services(self) -> ConfigFlowResult:
        """Go to the next selected action configuration step."""
        if not self._selected_actions:
            self._current_action = None
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        self._current_action = self._selected_actions.pop(0)
        return await self.async_step_services_configure_action()

    async def async_step_services_configure_action(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure params for the currently selected action."""
        action = self._current_action
        if action is None:
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        if user_input is not None:
            return await self._async_handle_action_config_submit_services(
                action=action,
                user_input=user_input,
            )

        return await self._async_show_action_form_services(action=action)

    async def _async_handle_action_config_submit_services(
        self,
        *,
        action: str,
        user_input: dict[str, Any],
    ) -> ConfigFlowResult:
        """Persist action params and advance to the next step."""
        errors: dict[str, str] = {}

        existing_action = self._config_entry.data.get(SERVICE_ACTIONS, {}).get(action, {})
        service_call = str(existing_action.get(SERVICE_ACTION_SERVICE, "") or "").strip()
        if not service_call or "." not in service_call:
            errors["base"] = "invalid_service"

        params, param_errors = parse_action_params_form(user_input)
        errors.update(param_errors)

        if errors:
            return await self._async_show_action_form_services(action=action, errors=errors)

        current_actions = dict(self._config_entry.data.get(SERVICE_ACTIONS, {}))
        current_action_cfg = dict(current_actions.get(action, {}))
        current_action_cfg[SERVICE_ACTION_PARAMS] = params
        current_actions[action] = current_action_cfg

        new_data = {**self._config_entry.data, SERVICE_ACTIONS: current_actions}
        self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)

        if self._selected_actions:
            return await self._async_goto_next_action_step_services()

        mac_value = find_first_mac_in_enabled_actions(
            new_data.get(SERVICE_ACTIONS, {})
        )

        if not mac_value:
            return await self._async_show_action_form_services(
                action=action,
                errors={"base": "mac_required"},
            )

        await self.hass.config_entries.async_reload(self._config_entry.entry_id)
        return self.async_create_entry(title="", data=dict(self._config_entry.options))

    async def _async_show_action_form_services(
        self,
        *,
        action: str,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Render a parameter configuration form for a given action."""
        errors = errors or {}
        existing = self._config_entry.data.get(SERVICE_ACTIONS, {}).get(action, {})
        existing_params: list[dict[str, Any]] = existing.get(SERVICE_ACTION_PARAMS, []) or []
        service_call = str(existing.get(SERVICE_ACTION_SERVICE, "") or "")

        description = build_action_description(action, service_call)

        return self.async_show_form(
            step_id="services_configure_action",
            data_schema=build_action_params_schema(existing_params),
            errors=errors,
            description_placeholders={"service_description": description},
        )

    async def async_step_num_stations(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure number of stations."""
        errors: dict[str, str] = {}

        current_num = int(self._config_entry.data.get(NUM_STATIONS, 1))
        current_names = list(self._config_entry.data.get("station_names", []) or [])
        current_areas = list(self._config_entry.data.get("station_areas", []) or [])

        if user_input is not None:
            try:
                new_num = int(user_input[NUM_STATIONS])

                names = current_names[:new_num]
                while len(names) < new_num:
                    names.append(f"Station {len(names) + 1}")

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

                return await self.async_step_lawn_areas()

            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Failed to update number of stations")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="num_stations",
            data_schema=build_num_stations_schema(current_num),
            errors=errors,
        )

    async def async_step_lawn_areas(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure lawn names and areas per station."""
        num_stations = int(self._config_entry.data.get(NUM_STATIONS, 1))
        previous_names = self._config_entry.data.get("station_names", [])
        previous_areas = self._config_entry.data.get("station_areas", [])

        if user_input is not None:
            station_names = [
                user_input[f"station_{i}_name"].strip()
                for i in range(1, num_stations + 1)
            ]
            station_areas = [
                user_input[f"station_{i}_area"]
                for i in range(1, num_stations + 1)
            ]

            new_data = {
                **self._config_entry.data,
                "station_names": station_names,
                "station_areas": station_areas,
            }
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        return self.async_show_form(
            step_id="lawn_areas",
            data_schema=build_lawn_areas_schema(
                num_stations,
                previous_names,
                previous_areas,
            ),
        )

    async def async_step_weather(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure weather provider and rain behavior."""
        if user_input is not None:
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        return self.async_show_form(
            step_id="weather",
            data_schema=build_weather_schema(
                self._config_entry.data.get(WEATHER_PROVIDER, WEATHER_PROVIDER_NONE),
                self._config_entry.data.get(WEATHER_API_KEY, ""),
                self._config_entry.data.get(SPRINKLE_WITH_RAIN, "false"),
            ),
        )

    async def async_step_soil_moisture(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure soil moisture sensor and threshold."""
        if user_input is not None:
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        return self.async_show_form(
            step_id="soil_moisture",
            data_schema=build_soil_moisture_schema(
                self._config_entry.data.get(USE_SOIL_MOISTURE, "false"),
                self._config_entry.data.get(SOIL_MOISTURE_SENSOR),
                self._config_entry.data.get(
                    SOIL_MOISTURE_THRESHOLD,
                    DEFAULT_SOIL_MOISTURE,
                ),
            ),
        )

    async def async_step_station_switches(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Options: configure one switch entity per station."""
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

        return self.async_show_form(
            step_id="station_switches",
            data_schema=build_station_switches_schema(num_stations, existing),
            errors=errors,
        )