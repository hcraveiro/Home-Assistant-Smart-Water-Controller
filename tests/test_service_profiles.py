"""Preset routing and flow regressions with real service payload construction."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import ha_stub
import voluptuous as vol
from homeassistant import const as ha_const

ha_const.CONF_NAME = "name"
ha_const.CONF_SCAN_INTERVAL = "scan_interval"


class Flow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


def selector(config):
    """Fake HA selectors only; actual voluptuous schemas are tested unchanged."""
    if "select" in config:
        if config["select"].get("custom_value"):
            return str
        return vol.In(config["select"]["options"])
    return lambda value: value


ha_stub.module("homeassistant.config_entries", ConfigEntry=object, ConfigFlow=Flow,
               ConfigFlowResult=dict, OptionsFlow=Flow)
ha_stub.module("homeassistant.data_entry_flow", AbortFlow=type("AbortFlow", (Exception,), {}))
ha_stub.module("homeassistant.helpers.selector", selector=selector)

from custom_components.smart_water_controller.const import (
    IRRIGATION_CONTROL_METHOD, IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
    IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR, NUM_STATIONS, SERVICE_ACTIONS,
)
from custom_components.smart_water_controller.flows.service_actions import build_solem_toolkit_defaults
from custom_components.smart_water_controller.flows.config_flow_impl import SmartWaterControllerConfigFlow
from custom_components.smart_water_controller.flows.options_flow_impl import SmartWaterControllerOptionsFlowHandler
from custom_components.smart_water_controller.flows.schemas import build_user_step_schema, build_num_stations_schema
from custom_components.smart_water_controller.api.controller import SmartWaterControllerAPI


class ServiceProfiles(unittest.IsolatedAsyncioTestCase):
    async def test_both_presets_send_correct_model_mac_and_dynamic_duration(self):
        for model in ("bl_ip", "bl_nr"):
            with self.subTest(model=model):
                call = AsyncMock()
                api = SmartWaterControllerAPI(
                    SimpleNamespace(services=SimpleNamespace(async_call=call)),
                    "AA:BB:CC:DD:EE:FF", 27, build_solem_toolkit_defaults(model),
                )
                await api.turn_on()
                station = 1 if model == "bl_nr" else 2
                await api.sprinkle_station(station, 20)
                await api.stop_sprinkle()
                await api.turn_off()
                expected = ["turn_on", "sprinkle_station_x_for_y_minutes", "stop_manual_sprinkle", "turn_off_permanent"]
                for index, service in enumerate(expected):
                    args = call.await_args_list[index]
                    self.assertEqual(args.args[:2], ("solem_toolkit", service))
                    payload = {"device_mac": "AA:BB:CC:DD:EE:FF", "model": model, "bluetooth_timeout": 27}
                    if index == 1:
                        payload.update(station=station, minutes=20)
                    self.assertEqual(args.args[2], payload)
                    self.assertTrue(args.kwargs["blocking"])

    async def test_legacy_bl_ip_mapping_without_model_still_works(self):
        actions = build_solem_toolkit_defaults()
        for action in actions.values():
            action["params"] = [p for p in action["params"] if p["name"] != "model"]
        call = AsyncMock()
        api = SmartWaterControllerAPI(SimpleNamespace(services=SimpleNamespace(async_call=call)),
                                      "AA:BB:CC:DD:EE:FF", 15, actions)
        await api.sprinkle_station(2, 5)
        self.assertEqual(call.await_args.args[2]["station"], 2)
        self.assertNotIn("model", call.await_args.args[2])

    async def test_custom_service_does_not_receive_toolkit_parameters(self):
        actions = {"stop_sprinkle": {"service": "script.stop_water", "enabled": True, "params": []}}
        call = AsyncMock()
        api = SmartWaterControllerAPI(SimpleNamespace(services=SimpleNamespace(async_call=call)), None, 20, actions)
        await api.stop_sprinkle()
        call.assert_awaited_once_with("script", "stop_water", {}, blocking=True)

    async def test_explicit_timeout_is_not_overwritten(self):
        actions = build_solem_toolkit_defaults("bl_nr")
        actions["turn_on"]["params"].append({"name": "bluetooth_timeout", "type": "other", "value": "30"})
        call = AsyncMock()
        api = SmartWaterControllerAPI(SimpleNamespace(services=SimpleNamespace(async_call=call)), "AA:BB:CC:DD:EE:FF", 15, actions)
        await api.turn_on()
        self.assertEqual(call.await_args.args[2]["bluetooth_timeout"], 30)

    async def test_each_setup_profile_prefills_actions(self):
        for method, model in [(IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT, "bl_ip"), (IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR, "bl_nr")]:
            flow = SmartWaterControllerConfigFlow()
            flow.hass = SimpleNamespace(services=SimpleNamespace(async_services=lambda: {}))
            result = await flow.async_step_user({IRRIGATION_CONTROL_METHOD: method})
            self.assertEqual(result["step_id"], "num_stations")
            for action in flow._input_data[SERVICE_ACTIONS].values():
                self.assertIn(model, [p["value"] for p in action["params"]])
            await flow.async_step_num_stations({NUM_STATIONS: 1})
            result = await flow.async_step_lawn_areas({"station_1_name": "Garden", "station_1_area": 10})
            self.assertEqual(result["step_id"], "service_config")
            defaults = result["data_schema"]({})
            self.assertEqual(defaults["service_sprinkle_station"], "solem_toolkit.sprinkle_station_x_for_y_minutes")
            result = await flow.async_step_service_config(defaults)
            self.assertEqual(result["step_id"], "configure_action")

    async def test_reconfigure_same_preset_preserves_customized_mapping(self):
        flow = SmartWaterControllerConfigFlow()
        actions = build_solem_toolkit_defaults()
        actions["turn_on"]["params"][0]["value"] = "AA:BB:CC:DD:EE:FF"
        flow._input_data = {IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT, SERVICE_ACTIONS: deepcopy(actions)}
        await flow.async_step_user({IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT})
        self.assertEqual(flow._input_data[SERVICE_ACTIONS], actions)
        await flow.async_step_user({IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR})
        for action in flow._input_data[SERVICE_ACTIONS].values():
            self.assertIn("bl_nr", [p["value"] for p in action["params"]])

    async def test_bl_nr_single_station_guard_in_setup_and_options(self):
        flow = SmartWaterControllerConfigFlow()
        flow._input_data = {IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR}
        result = await flow.async_step_num_stations({NUM_STATIONS: 2})
        self.assertEqual(result["errors"], {"base": "bl_nr_single_station"})
        self.assertEqual(flow._num_stations, 1)
        entry = SimpleNamespace(data={IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR}, options={})
        options = SmartWaterControllerOptionsFlowHandler(entry)
        options.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))
        result = await options.async_step_num_stations({NUM_STATIONS: 2})
        self.assertEqual(result["errors"], {"base": "bl_nr_single_station"})
        options.hass.config_entries.async_update_entry.assert_not_called()

    async def test_other_profiles_keep_configurable_station_count(self):
        for method in (IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT, "service", "switch"):
            flow = SmartWaterControllerConfigFlow()
            flow._input_data = {IRRIGATION_CONTROL_METHOD: method}
            result = await flow.async_step_num_stations({NUM_STATIONS: 4})
            self.assertEqual(result["step_id"], "lawn_areas")
            self.assertEqual(flow._num_stations, 4)

    def test_selector_and_station_schema_compatibility(self):
        schema = build_user_step_schema("", "service")
        for method in ("switch", "service", "solem_toolkit", "solem_toolkit_bl_nr"):
            self.assertEqual(schema({IRRIGATION_CONTROL_METHOD: method})[IRRIGATION_CONTROL_METHOD], method)
        self.assertEqual(build_num_stations_schema(2)({NUM_STATIONS: 2})[NUM_STATIONS], 2)
        self.assertEqual(build_num_stations_schema(1)({NUM_STATIONS: 3})[NUM_STATIONS], 3)
        self.assertEqual(build_num_stations_schema(1, 1)({NUM_STATIONS: 1})[NUM_STATIONS], 1)
        with self.assertRaises(vol.Invalid):
            build_num_stations_schema(1, 1)({NUM_STATIONS: 2})

    def test_presets_are_independent_and_reject_unknown_models(self):
        first = build_solem_toolkit_defaults("bl_nr")
        first["turn_on"]["params"][0]["value"] = "changed"
        self.assertEqual(build_solem_toolkit_defaults("bl_nr")["turn_on"]["params"][0]["value"], "")
        with self.assertRaises(ValueError):
            build_solem_toolkit_defaults("unknown")


    async def test_reconfigure_stages_changes_without_mutating_saved_entry(self):
        actions = build_solem_toolkit_defaults()
        entry = SimpleNamespace(data={IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
                                      SERVICE_ACTIONS: actions}, title="Solem")
        flow = SmartWaterControllerConfigFlow()
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda key: entry))
        flow.context = {"entry_id": "test"}
        await flow.async_step_reconfigure()
        flow._input_data[SERVICE_ACTIONS]["turn_on"]["params"][0]["value"] = "changed"
        self.assertEqual(entry.data[SERVICE_ACTIONS]["turn_on"]["params"][0]["value"], "")

    async def test_reconfigure_keeps_disabled_preset_actions_disabled(self):
        flow = SmartWaterControllerConfigFlow()
        flow.hass = SimpleNamespace(services=SimpleNamespace(async_services=lambda: {}))
        actions = build_solem_toolkit_defaults()
        actions["sprinkle_station"]["enabled"] = False
        flow._input_data = {IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
                            SERVICE_ACTIONS: actions}
        await flow.async_step_user({IRRIGATION_CONTROL_METHOD: IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT})
        result = await flow.async_step_lawn_areas({"station_1_name": "Garden", "station_1_area": 10})
        self.assertEqual(result["step_id"], "service_config")
        defaults = result["data_schema"]({})
        self.assertFalse(defaults["enable_sprinkle_station"])
        result = await flow.async_step_service_config(defaults)
        self.assertEqual(result["step_id"], "configure_action")
        self.assertNotEqual(flow._current_action, "sprinkle_station")
        self.assertFalse(flow._input_data[SERVICE_ACTIONS]["sprinkle_station"]["enabled"])
