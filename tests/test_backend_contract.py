"""Execution facts must not drift with editable controller configuration."""
import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import ha_stub
from custom_components.smart_water_controller.const import SOLEM_CONTROL_METHODS
from custom_components.smart_water_controller.runtime.backend import resolve_execution_contract
from custom_components.smart_water_controller.runtime import irrigation_runner as runtime
from test_irrigation_safety import Coordinator


class ContractTests(unittest.TestCase):
    def resolve(self, *, native=True, switch=False, seconds=12.345):
        return resolve_execution_contract(
            control_method=next(iter(SOLEM_CONTROL_METHODS)), is_switch=switch,
            api=SimpleNamespace(uses_native_watering_duration=lambda: native),
            duration_minutes=5, requested_seconds=seconds,
            switch_budget=10, service_budget=90,
        )

    def test_native_duration_and_frozen_confirmation(self):
        contract = self.resolve()
        self.assertEqual(contract.effective_seconds, 300)
        self.assertEqual(contract.expiry_policy, 'controller_timer')
        self.assertEqual(contract.confirmation_basis, 'service_ack_and_elapsed')
        self.assertEqual(contract.command_budget, 90)
        self.assertEqual(contract.stop_scope, 'controller')
        self.assertIn('water flow not verified', contract.completion_reason)
        with self.assertRaises(FrozenInstanceError):
            contract.confirmation_basis = 'flow_verified'

    def test_static_override_requires_stop_and_preserves_fractional_precision(self):
        contract = self.resolve(native=False)
        self.assertEqual(contract.backend_kind, 'service')
        self.assertEqual(contract.effective_seconds, 12.345)
        self.assertEqual(contract.expiry_policy, 'explicit_stop')
        self.assertEqual(self.resolve(native=False, seconds=400).effective_seconds, 300)

    def test_switch_never_inherits_native_service_duration(self):
        contract = self.resolve(switch=True)
        self.assertEqual(contract.effective_seconds, 12.345)
        self.assertEqual(contract.confirmation_basis, 'switch_state')
        self.assertEqual(contract.command_budget, 10)
        self.assertEqual(contract.stop_scope, 'station_may_stop_all_zones')


class CapturedContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_expiry_and_report_remain_native_after_mapping_change(self):
        c = Coordinator(switch=False)
        runner = c.irrigation_runner
        c.irrigation_control_method = next(iter(SOLEM_CONTROL_METHODS))
        c.irrigation_notifications = SimpleNamespace(
            mark_started=lambda station: None, report=AsyncMock(), async_close=AsyncMock(),
            failed_station=None,
        )
        try:
            task = asyncio.create_task(runner.start_irrigation(1, 5, run_seconds=.01))
            async with asyncio.timeout(1):
                while runner._session is None:
                    await asyncio.sleep(.001)
            session = runner._session
            self.assertEqual(session.contract.effective_seconds, 300)
            c.irrigation_control_method = 'service'
            c.api.uses_native_watering_duration = lambda: False
            session.deadline = asyncio.get_running_loop().time()
            session.wake.set()
            self.assertTrue(await task)
            self.assertNotIn(('service_off',), c.api.commands)
            self.assertEqual(c.irrigation_notifications.report.await_args.kwargs['reason'],
                             session.contract.completion_reason)
        finally:
            await runner.async_close()

    async def test_static_mapping_still_stops_after_mode_and_capability_change(self):
        c = Coordinator(switch=False)
        runner = c.irrigation_runner
        c.irrigation_control_method = next(iter(SOLEM_CONTROL_METHODS))
        c.api.uses_native_watering_duration = lambda: False
        c.irrigation_notifications = SimpleNamespace(
            mark_started=lambda station: None, report=AsyncMock(), async_close=AsyncMock(),
            failed_station=None,
        )
        try:
            with patch.object(runtime, 'SAMPLE_INTERVAL', .005):
                task = asyncio.create_task(runner.start_irrigation(1, 5, run_seconds=.04))
                async with asyncio.timeout(1):
                    while runner._session is None:
                        await asyncio.sleep(.001)
                contract = runner._session.contract
                self.assertEqual(contract.effective_seconds, .04)
                c.switch = True
                c.irrigation_control_method = 'switch'
                c.api.uses_native_watering_duration = lambda: True
                self.assertTrue(await task)
            self.assertIn(('service_off',), c.api.commands)
            self.assertNotIn(('off', 1), c.api.commands)
            report = c.irrigation_notifications.report.await_args
            self.assertEqual(report.args, ('station_finished',))
            self.assertEqual(report.kwargs['reason'], contract.completion_reason)
            self.assertEqual(contract.confirmation_basis, 'service_ack_and_elapsed')
        finally:
            await runner.async_close()
