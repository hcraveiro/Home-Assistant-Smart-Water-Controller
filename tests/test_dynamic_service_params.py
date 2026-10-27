"""Expandable service parameter editing, prefills and validation regressions."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

# Reuse the HA flow boundary used by profile tests; production schemas stay real.
from test_service_profiles import Flow
from custom_components.smart_water_controller.const import SERVICE_ACTIONS
from custom_components.smart_water_controller.flows.service_actions import (
    build_solem_toolkit_defaults, parse_action_params_form,
)
from custom_components.smart_water_controller.flows.schemas import build_action_params_schema
from custom_components.smart_water_controller.flows.config_flow_impl import SmartWaterControllerConfigFlow
from custom_components.smart_water_controller.flows.options_flow_impl import SmartWaterControllerOptionsFlowHandler


class DynamicServiceParameters(unittest.IsolatedAsyncioTestCase):
    def test_more_than_five_parameters_round_trip_and_removal(self):
        rows = [{"name": f"field_{i}", "label": "", "value": str(i), "type": "other"} for i in range(9)]
        parsed, errors = parse_action_params_form({"params": rows})
        self.assertEqual(errors, {})
        self.assertEqual(parsed, rows)
        self.assertEqual(build_action_params_schema(parsed)({})["params"], rows)
        edited = rows[:2] + rows[3:]
        parsed, errors = parse_action_params_form({"params": edited})
        self.assertEqual(parsed, edited)
        self.assertEqual(errors, {})
        self.assertEqual(parse_action_params_form({"params": []}), ([], {}))

    def test_both_solem_profiles_prefill_all_existing_values(self):
        for model in ("bl_ip", "bl_nr"):
            actions = build_solem_toolkit_defaults(model)
            for action in actions.values():
                rows = action["params"]
                rendered = build_action_params_schema(rows)({})["params"]
                self.assertEqual(rendered, rows)
                self.assertEqual(parse_action_params_form({"params": rendered}), (rows, {}))
                self.assertIn({"name": "model", "label": "Solem model", "value": model, "type": "other"}, rendered)
            start = actions["sprinkle_station"]["params"]
            self.assertEqual(next(p for p in start if p["name"] == "station")["value"], "")
            self.assertEqual(next(p for p in start if p["name"] == "minutes")["value"], "")

    def test_schema_uses_native_expandable_object_list(self):
        with patch("custom_components.smart_water_controller.flows.schemas.selector", return_value=lambda x: x) as selector:
            build_action_params_schema([])
        config = selector.call_args.args[0]["object"]
        self.assertTrue(config["multiple"])
        self.assertEqual(set(config["fields"]), {"name", "label", "value", "type"})
        self.assertTrue(config["fields"]["name"]["required"])

    def test_invalid_records_and_duplicate_names_are_rejected(self):
        cases = [None, {}, "bad", [None], [{"name": ""}],
                 [{"name": "x", "type": "unknown"}], [{"name": "x", "value": {}}],
                 [{"name": "x"}, {"name": " x "}]]
        for rows in cases:
            with self.subTest(rows=rows):
                _params, errors = parse_action_params_form({"params": rows})
                self.assertIn("base", errors)
        self.assertEqual(parse_action_params_form({"params": [{"name": "x"}, {"name": "x"}]})[1],
                         {"base": "duplicate_parameter"})

    def test_mac_validation_and_numeric_zero_are_preserved(self):
        rows = [{"name": "device_mac", "type": "mac_address", "value": "bad"}]
        self.assertEqual(parse_action_params_form({"params": rows})[1], {"base": "invalid_mac"})
        rows[0]["value"] = "AA:BB:CC:DD:EE:FF"
        self.assertEqual(parse_action_params_form({"params": rows})[1], {})
        rows = [{"name": "counter", "value": 0}]
        self.assertEqual(parse_action_params_form({"params": rows})[0][0]["value"], "0")

    async def test_invalid_input_retains_rows_in_setup_and_options(self):
        rows = [{"name": "device_mac", "label": "MAC", "type": "mac_address", "value": "bad"}]
        actions = build_solem_toolkit_defaults("bl_nr")
        before = deepcopy(actions)
        flow = SmartWaterControllerConfigFlow()
        flow._input_data = {SERVICE_ACTIONS: actions}
        result = await flow._async_handle_action_config_submit(action="turn_on", user_input={"params": rows})
        self.assertEqual(result["errors"], {"base": "invalid_mac"})
        self.assertEqual(result["data_schema"]({})["params"], rows)
        self.assertEqual(actions, before)
        entry = SimpleNamespace(data={SERVICE_ACTIONS: deepcopy(actions)}, options={})
        options = SmartWaterControllerOptionsFlowHandler(entry)
        update = Mock()
        options.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
        result = await options._async_handle_action_config_submit_services(action="turn_on", user_input={"params": rows})
        self.assertEqual(result["errors"], {"base": "invalid_mac"})
        self.assertEqual(result["data_schema"]({})["params"], rows)
        update.assert_not_called()
        self.assertEqual(entry.data[SERVICE_ACTIONS], before)
