"""Options wizards commit only fully validated transactions."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock

from test_service_profiles import Flow  # install HA test stubs
from custom_components.smart_water_controller.flows.options_flow_impl import SmartWaterControllerOptionsFlowHandler
from custom_components.smart_water_controller.flows.service_actions import build_solem_toolkit_defaults


class OptionsTransactions(unittest.IsolatedAsyncioTestCase):
    def flow(self, data):
        entry = SimpleNamespace(data=deepcopy(data), options={}, unique_id="AA:BB:CC:DD:EE:FF")
        flow = SmartWaterControllerOptionsFlowHandler(entry)
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock(), async_reload=AsyncMock()))
        return flow, entry

    async def test_reconfigure_mac_mismatch_preserves_pending(self):
        from custom_components.smart_water_controller.flows.config_flow_impl import SmartWaterControllerConfigFlow
        flow = SmartWaterControllerConfigFlow()
        flow._reconfigure_entry = SimpleNamespace(unique_id="AA:BB:CC:DD:EE:FF")
        flow._input_data = {"irrigation_control_method": "solem_toolkit", "service_actions": build_solem_toolkit_defaults()}
        before = deepcopy(flow._input_data)
        result = await flow._async_handle_action_config_submit(action="stop_sprinkle", user_input={"params": [
            {"name": "device_mac", "type": "mac_address", "value": "11:22:33:44:55:66"},
        ]})
        self.assertEqual(result["errors"], {"base": "mac_mismatch"})
        self.assertEqual(flow._input_data, before)

    async def test_station_count_cancel_and_final_commit(self):
        flow, entry = self.flow({"num_stations": 1, "station_names": ["Old"], "station_areas": [10]})
        original = deepcopy(entry.data)
        await flow.async_step_num_stations({"num_stations": 2})
        self.assertEqual(entry.data, original)
        flow.hass.config_entries.async_update_entry.assert_not_called()
        result = await flow.async_step_lawn_areas({"station_1_name": "A", "station_1_area": 5, "station_2_name": "B", "station_2_area": 6})
        self.assertEqual(result["type"], "create_entry")
        flow.hass.config_entries.async_update_entry.assert_called_once()
        self.assertEqual(flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]["num_stations"], 2)
        flow.hass.config_entries.async_reload.assert_not_awaited()

    async def test_service_wizard_staged_and_mac_error_is_atomic(self):
        flow, entry = self.flow({"irrigation_control_method": "solem_toolkit", "service_actions": build_solem_toolkit_defaults()})
        original = deepcopy(entry.data)
        await flow.async_step_services({"enable_sprinkle_station": True, "service_sprinkle_station": "solem_toolkit.sprinkle", "enable_stop_sprinkle": True, "service_stop_sprinkle": "solem_toolkit.stop"})
        pending = deepcopy(flow._data)
        bad = {"params": [{"name": "device_mac", "type": "mac_address", "value": "11:22:33:44:55:66"}]}
        result = await flow.async_step_services_configure_action(bad)
        self.assertEqual(result["errors"], {"base": "mac_mismatch"})
        self.assertEqual(flow._data, pending)
        self.assertEqual(entry.data, original)
        flow.hass.config_entries.async_update_entry.assert_not_called()
        await flow.async_step_services_configure_action({"params": []})
        flow.hass.config_entries.async_update_entry.assert_not_called()
        await flow.async_step_services_configure_action({"params": []})
        flow.hass.config_entries.async_update_entry.assert_called_once()
        flow.hass.config_entries.async_reload.assert_not_awaited()
