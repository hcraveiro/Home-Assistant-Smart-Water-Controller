"""Task ownership regressions without cross-importing other test modules."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import ha_stub
from custom_components.smart_water_controller.runtime.irrigation_notifications import IrrigationNotifications


class NotificationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(data={'name': 'Garden'}, options={}),
            hass=SimpleNamespace(async_create_task=asyncio.create_task),
            _is_switch_control_method=lambda: False,
            _get_station_name=lambda station: f'Zone {station}',
            stations=[SimpleNamespace(state='Stopped') for _ in range(2)],
            station_irrigation_results={},
            save_persistent_data=AsyncMock(),
            irrigation_runner=SimpleNamespace(_publish=AsyncMock()),
        )
        self.manager = IrrigationNotifications(self.coordinator)

    async def asyncTearDown(self):
        await self.manager.async_close()

    async def test_cancelled_predecessor_cannot_clear_replacement_handle(self):
        await self.manager.report('irrigation_failed', station=1)
        first = self.manager._failure_task
        await asyncio.sleep(0)
        await self.manager.report('irrigation_failed', station=2)
        replacement = self.manager._failure_task
        await asyncio.sleep(0)
        self.assertTrue(first.cancelled())
        self.assertIs(self.manager._failure_task, replacement)
        self.assertFalse(replacement.done())
        await self.manager.async_close()
        self.assertTrue(replacement.cancelled())
        self.assertIsNone(self.manager._failure_task)

    async def test_mark_started_cancels_transient_failure_without_losing_next_timer(self):
        await self.manager.report('irrigation_failed', station=1)
        first = self.manager._failure_task
        await asyncio.sleep(0)
        self.manager.mark_started(1)
        await self.manager.report('irrigation_failed', station=2)
        replacement = self.manager._failure_task
        await asyncio.sleep(0)
        self.assertTrue(first.cancelled())
        self.assertIs(self.manager._failure_task, replacement)
        self.assertEqual(self.manager.failed_station, 2)

    async def test_close_is_idempotent_and_waits_for_task_disposal(self):
        await self.manager.report('irrigation_failed', station=1)
        task = self.manager._failure_task
        await asyncio.sleep(0)
        await self.manager.async_close()
        await self.manager.async_close()
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
