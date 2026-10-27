"""Custom action setup without MAC and searchable preset action selection."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_service_profiles import Flow
from custom_components.smart_water_controller.const import (
    IRRIGATION_CONTROL_METHOD, IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT,
    IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR, SERVICE_ACTIONS,
)
from custom_components.smart_water_controller.flows.config_flow_impl import SmartWaterControllerConfigFlow
from custom_components.smart_water_controller.flows.options_flow_impl import SmartWaterControllerOptionsFlowHandler
from custom_components.smart_water_controller.flows.service_actions import (
    available_service_actions, build_solem_toolkit_defaults,
)
from custom_components.smart_water_controller.flows.schemas import build_service_config_schema


PARAMS = [{"name": "entity_id", "label": "Valve", "type": "other", "value": "switch.garden"}]


def services():
    return SimpleNamespace(async_services=lambda: {"switch": {"turn_on": {}, "turn_off": {}}, "script": {"water": {}}})


class CustomServiceSetup(unittest.IsolatedAsyncioTestCase):
    async def test_custom_start_stop_setup_finishes_without_mac(self):
        flow = SmartWaterControllerConfigFlow()
        flow.hass = SimpleNamespace(services=services())
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = Mock()
        await flow.async_step_user({IRRIGATION_CONTROL_METHOD: "service"})
        await flow.async_step_num_stations({"num_stations": 1})
        await flow.async_step_lawn_areas({"station_1_name": "Drip", "station_1_area": 10})
        result = await flow.async_step_service_config({
            "enable_sprinkle_station": True, "service_sprinkle_station": "switch.turn_on",
            "enable_stop_sprinkle": True, "service_stop_sprinkle": "switch.turn_off",
        })
        self.assertEqual(result["step_id"], "configure_action")
        result = await flow.async_step_configure_action({"params": deepcopy(PARAMS)})
        self.assertEqual(result["step_id"], "configure_action")
        result = await flow.async_step_configure_action({"params": deepcopy(PARAMS)})
        self.assertEqual(result["step_id"], "location")
        flow.async_set_unique_id.assert_not_awaited()
        flow._abort_if_unique_id_configured.assert_not_called()
        self.assertFalse(flow._input_data[SERVICE_ACTIONS]["turn_on"]["enabled"])
        self.assertFalse(flow._input_data[SERVICE_ACTIONS]["turn_off"]["enabled"])

    async def test_custom_options_save_without_mac(self):
        entry = SimpleNamespace(entry_id="custom", options={}, unique_id=None, data={
            IRRIGATION_CONTROL_METHOD: "service", SERVICE_ACTIONS: {
                "stop_sprinkle": {"enabled": True, "service": "switch.turn_off", "params": []},
            },
        })
        flow = SmartWaterControllerOptionsFlowHandler(entry)
        update = Mock()
        reload = AsyncMock()
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update, async_reload=reload))
        result = await flow._async_handle_action_config_submit_services(action="stop_sprinkle", user_input={"params": PARAMS})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(update.call_args.kwargs["data"][SERVICE_ACTIONS]["stop_sprinkle"]["params"], PARAMS)
        reload.assert_awaited_once_with("custom")

    async def test_both_solem_presets_still_require_mac_in_setup_and_options(self):
        for method in (IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT, IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR):
            with self.subTest(method=method):
                actions = build_solem_toolkit_defaults("bl_nr" if method.endswith("bl_nr") else "bl_ip")
                flow = SmartWaterControllerConfigFlow()
                flow._input_data = {IRRIGATION_CONTROL_METHOD: method, SERVICE_ACTIONS: deepcopy(actions)}
                result = await flow._async_handle_action_config_submit(action="turn_off", user_input={"params": actions["turn_off"]["params"]})
                self.assertEqual(result["errors"], {"base": "mac_required"})
                entry = SimpleNamespace(data=deepcopy(flow._input_data), options={})
                options = SmartWaterControllerOptionsFlowHandler(entry)
                update = Mock()
                options.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
                result = await options._async_handle_action_config_submit_services(action="turn_off", user_input={"params": actions["turn_off"]["params"]})
                self.assertEqual(result["errors"], {"base": "mac_required"})
                update.assert_not_called()

    async def test_custom_reconfiguration_preserves_identity_when_no_mac_entered(self):
        entry = SimpleNamespace(data={}, unique_id="previous-id")
        flow = SmartWaterControllerConfigFlow()
        flow._reconfigure_entry = entry
        flow._input_data = {IRRIGATION_CONTROL_METHOD: "service", SERVICE_ACTIONS: {
            "stop_sprinkle": {"enabled": True, "service": "script.water", "params": []},
        }}
        flow.async_set_unique_id = AsyncMock()
        result = await flow._async_handle_action_config_submit(action="stop_sprinkle", user_input={"params": []})
        self.assertEqual(result["step_id"], "location")
        flow.async_set_unique_id.assert_not_awaited()
        self.assertEqual(entry.unique_id, "previous-id")

    def test_autocomplete_lists_registered_and_saved_unavailable_actions(self):
        registered = available_service_actions(SimpleNamespace(services=services()))
        self.assertEqual(registered, ["script.water", "switch.turn_off", "switch.turn_on"])
        existing = {"stop_sprinkle": {"service": "legacy.stop", "enabled": True}}
        with patch("custom_components.smart_water_controller.flows.schemas.selector", return_value=lambda x: x) as selector:
            schema = build_service_config_schema(existing, registered)
        config = next(c.args[0]["select"] for c in selector.call_args_list if "select" in c.args[0])
        self.assertTrue(config["custom_value"])
        self.assertEqual(config["mode"], "dropdown")
        self.assertIn("legacy.stop", config["options"])
        self.assertIn("switch.turn_on", config["options"])
        self.assertEqual(schema({})["service_stop_sprinkle"], "legacy.stop")

    async def test_options_use_same_searchable_action_selector(self):
        entry = SimpleNamespace(data={SERVICE_ACTIONS: build_solem_toolkit_defaults()}, options={})
        flow = SmartWaterControllerOptionsFlowHandler(entry)
        flow.hass = SimpleNamespace(services=services())
        with patch("custom_components.smart_water_controller.flows.schemas.selector", return_value=lambda x: x) as selector:
            result = await flow.async_step_services()
        self.assertEqual(result["step_id"], "services")
        config = next(c.args[0]["select"] for c in selector.call_args_list if "select" in c.args[0])
        self.assertIn("switch.turn_on", config["options"])
        self.assertIn("solem_toolkit.turn_on", config["options"])
        self.assertEqual(result["data_schema"]({})["service_turn_on"], "solem_toolkit.turn_on")
