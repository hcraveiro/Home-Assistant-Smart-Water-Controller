"""Notification setup and options regression tests (no physical devices)."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ha_stub
from test_service_profiles import Flow

from custom_components.smart_water_controller.flows.config_flow_impl import SmartWaterControllerConfigFlow
from custom_components.smart_water_controller.flows.options_flow_impl import SmartWaterControllerOptionsFlowHandler
from custom_components.smart_water_controller.flows.notifications import (
    available_mobile_services, current_settings, parse_notification_settings,
)
from custom_components.smart_water_controller.const import (
    NOTIFICATIONS_ENABLED, NOTIFY_STATION_FINISHED,
    NOTIFY_CYCLE_FINISHED, NOTIFY_IRRIGATION_FAILED, NOTIFICATION_TARGETS,
)

PHONE1 = "notify.mobile_app_xiaomi_redmi_note_14_pro_henrique"
PHONE2 = "notify.mobile_app_xiaomi_redmi_note_14_pro_marta"


def fake_hass():
    return SimpleNamespace(
        services=SimpleNamespace(async_services=lambda: {
            "notify": {
                "mobile_app_xiaomi_redmi_note_14_pro_henrique": object(),
                "mobile_app_xiaomi_redmi_note_14_pro_marta": object(),
                "notify": object(),  # Not an individual mobile recipient.
            }
        })
    )


class NotificationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_disables_notifications_by_default_and_ends_with_final_step(self):
        flow = SmartWaterControllerConfigFlow()
        flow.hass = fake_hass()
        with patch("custom_components.smart_water_controller.flows.notifications.selector", return_value=lambda x: x):
            screen = await flow.async_step_soil_moisture({"use_soil_moisture": "false"})
            self.assertEqual(screen["step_id"], "notifications")
            self.assertTrue(screen["last_step"])
            self.assertFalse(screen["data_schema"]({})[NOTIFICATIONS_ENABLED])

            complete = await flow.async_step_notifications({})
            self.assertEqual(complete["type"], "create_entry")
            self.assertFalse(complete["data"][NOTIFICATIONS_ENABLED])
            self.assertEqual(complete["data"][NOTIFICATION_TARGETS], [])

    async def test_setup_enabled_requires_targets_then_saves_all_options(self):
        flow = SmartWaterControllerConfigFlow()
        flow.hass = fake_hass()
        with patch("custom_components.smart_water_controller.flows.notifications.selector", return_value=lambda x: x):
            rejected = await flow.async_step_notifications({NOTIFICATIONS_ENABLED: True, NOTIFICATION_TARGETS: []})
            self.assertEqual(rejected["errors"], {NOTIFICATION_TARGETS: "target_required"})
            self.assertEqual(rejected["step_id"], "notifications")
            complete = await flow.async_step_notifications({
                NOTIFICATIONS_ENABLED: True,
                NOTIFY_STATION_FINISHED: False,
                NOTIFY_CYCLE_FINISHED: True,
                NOTIFY_IRRIGATION_FAILED: True,
                NOTIFICATION_TARGETS: [PHONE1, PHONE2],
            })
            self.assertEqual(complete["type"], "create_entry")
            self.assertEqual(complete["data"][NOTIFICATION_TARGETS], [PHONE1, PHONE2])
            self.assertFalse(complete["data"][NOTIFY_STATION_FINISHED])

    async def test_options_can_be_edited_without_recreating_entry(self):
        entry = SimpleNamespace(data={}, options={})
        flow = SmartWaterControllerOptionsFlowHandler(entry)
        flow.hass = fake_hass()
        menu = await flow.async_step_init()
        self.assertEqual(menu["menu_options"][-1], "notifications")
        with patch("custom_components.smart_water_controller.flows.notifications.selector", return_value=lambda x: x):
            screen = await flow.async_step_notifications()
            self.assertEqual(screen["step_id"], "notifications")
            saved = await flow.async_step_notifications({
                NOTIFICATIONS_ENABLED: True,
                NOTIFY_STATION_FINISHED: True,
                NOTIFY_CYCLE_FINISHED: False,
                NOTIFY_IRRIGATION_FAILED: True,
                NOTIFICATION_TARGETS: [PHONE1],
            })
        self.assertEqual(saved["type"], "create_entry")
        self.assertTrue(saved["data"][NOTIFICATIONS_ENABLED])
        self.assertFalse(saved["data"][NOTIFY_CYCLE_FINISHED])
        self.assertEqual(saved["data"][NOTIFICATION_TARGETS], [PHONE1])

    def test_only_real_mobile_services_are_available(self):
        self.assertEqual(available_mobile_services(fake_hass()), [PHONE1, PHONE2])
        settings, errors = parse_notification_settings({
            NOTIFICATIONS_ENABLED: True, NOTIFICATION_TARGETS: ["notify.notify"],
        }, [PHONE1, PHONE2])
        self.assertEqual(errors, {NOTIFICATION_TARGETS: "invalid_targets"})
        self.assertFalse(settings.get(NOTIFICATION_TARGETS))

    def test_options_override_saved_setup_and_disabled_setting_is_respected(self):
        entry = SimpleNamespace(
            data={NOTIFICATIONS_ENABLED: True, NOTIFICATION_TARGETS: [PHONE1]},
            options={NOTIFICATIONS_ENABLED: False},
        )
        self.assertFalse(current_settings(entry)[NOTIFICATIONS_ENABLED])
        self.assertEqual(current_settings(entry)[NOTIFICATION_TARGETS], [PHONE1])

