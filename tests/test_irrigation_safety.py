"""Regression scenarios based on overlapping sessions and unavailable controllers."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ha_stub
from custom_components.smart_water_controller.runtime import irrigation_runner as runtime
from custom_components.smart_water_controller.runtime.active_irrigation import ActiveIrrigationState
from custom_components.smart_water_controller.runtime.storage import IrrigationStorage, PersistentIrrigationState
from custom_components.smart_water_controller.runtime.schedule_state import ScheduleStateManager
from custom_components.smart_water_controller.runtime.switch_tracking import StationSwitchTracker
from custom_components.smart_water_controller.api.controller import SmartWaterControllerAPI
from custom_components.smart_water_controller.helpers import planning


class FakeStates(dict):
    def set(self, entity, state):
        self[entity] = SimpleNamespace(state=state)


class FakeAPI:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.commands = []
        self.fail_on = False
        self.fail_off = False
        self.confirm_on = True
        self.confirm_off = True
        self.global_off = True
        self.on_gate = None

    async def turn_on_station_switch(self, station, minutes=None):
        self.commands.append(('on', station, minutes))
        if self.on_gate is not None:
            await self.on_gate.wait()
        if self.fail_on:
            # A transport timeout may happen after the valve accepted ON.
            self.coordinator.set_state(station, 'unavailable')
            raise RuntimeError('connection lost after ON')
        if self.confirm_on:
            self.coordinator.set_state(station, 'on')

    async def turn_off_station_switch(self, station):
        self.commands.append(('off', station))
        if self.fail_off:
            raise RuntimeError('controller unreachable')
        if self.confirm_off:
            stations = range(1, 4) if self.global_off else [station]
            for index in stations:
                if self.coordinator.get_state(index) in ('on', 'off'):
                    self.coordinator.set_state(index, 'off')

    async def sprinkle_station(self, station, minutes):
        self.commands.append(('service_on', station, minutes))

    async def stop_sprinkle(self):
        self.commands.append(('service_off',))


class Coordinator:
    def __init__(self, switch=True):
        self.hass = SimpleNamespace(states=FakeStates(), async_create_task=asyncio.create_task)
        self.num_stations = 3
        self.station_switch_entities = ['switch.zone_1', 'switch.zone_2', 'switch.zone_3']
        self.stations = [SimpleNamespace(state='Stopped') for _ in range(3)]
        self.controller_mac_address = 'test-controller'
        self.pending_stop_stations = []
        self.active_irrigation = None
        self.current_irrigation = None
        self.irrigation_manual_duration = 5
        self.water_flow_rate = [21, 21, 21]
        self.station_areas = [50, 50, 50]
        self.sprinkle_total_amount_today = [0., 0., 0.]
        self.sprinkle_target_amount_today = [4.2, 4.2, 4.2]
        self.total_water_consumption = 0.
        self.rain_total_amount_forecasted_today = 0.
        self.last_sprinkle = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.last_rain = self.last_sprinkle
        self.soil_moisture_sensor = None
        self.switch = switch
        self.saved = []
        self.station_switch_tracker = StationSwitchTracker(self)
        self.api = FakeAPI(self)
        self.irrigation_runner = runtime.IrrigationRunner(self)
        self.schedule_state = ScheduleStateManager(self)
        self.schedule = [{'hours': ['08:00:00', '20:00:00'], 'stations': {
            f'station_{i}_minutes': 5 for i in range(1, 4)}, 'interval_days': 1} for _ in range(12)]
        for station in range(1, 4):
            self.set_state(station, 'off')

    def set_state(self, station, state):
        self.hass.states.set(self.station_switch_entities[station - 1], state)

    def get_state(self, station):
        return self.hass.states[self.station_switch_entities[station - 1]].state

    def _is_switch_control_method(self):
        return self.switch

    def _get_station_name(self, station):
        return f'Zone {station}'

    def _clear_current_irrigation(self, station=None):
        if station is None or self.current_irrigation is None or self.current_irrigation.station == station:
            self.current_irrigation = None

    async def save_persistent_data(self):
        self.saved.append(deepcopy((self.pending_stop_stations, self.active_irrigation)))

    async def async_update_all_sensors(self):
        return []

    def async_set_updated_data(self, data):
        pass

    async def check_and_schedule_watering(self):
        pass

    def _get_valid_watering_hours(self, config):
        return planning.get_valid_watering_hours(config)

    def _rain_blocks_watering_now(self):
        return False


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c = Coordinator()
        self.runner = self.c.irrigation_runner
        self.patches = [patch.object(runtime, 'CONFIRM_TIMEOUT', .04),
                        patch.object(runtime, 'SAMPLE_INTERVAL', .005),
                        patch.object(runtime, 'RETRY_INTERVAL', .01)]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        self.c.api.fail_off = False
        self.c.api.confirm_off = True
        await self.runner.async_close()
        for item in self.patches:
            item.stop()

    async def start(self, station=1, seconds=.06, **kwargs):
        task = asyncio.create_task(self.runner.start_irrigation(station, 5, run_seconds=seconds, **kwargs))
        await self.wait_for(lambda: self.runner._session is not None or task.done())
        return task

    async def wait_for(self, predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(.001)

    async def test_sequential_zones_are_not_cancelled_by_previous_cleanup(self):
        for station in (1, 2, 3):
            self.assertTrue(await self.runner.start_irrigation(station, 5, run_seconds=.025, source='automatic'))
            self.assertGreater(self.c.sprinkle_total_amount_today[station - 1], 0)
        self.assertEqual([c for c in self.c.api.commands if c[0] == 'off'],
                         [('off', 1), ('off', 2), ('off', 3)])

    async def test_replacing_same_zone_cannot_stop_new_session(self):
        old = await self.start(seconds=.04)
        await asyncio.sleep(.01)
        new = asyncio.create_task(self.runner.start_irrigation(1, 5, run_seconds=.10))
        self.assertFalse(await old)
        await asyncio.sleep(.045)
        self.assertEqual(self.c.get_state(1), 'on')
        self.assertFalse(new.done())
        self.assertTrue(await new)

    async def test_replacing_other_zone_owns_all_cleanup_before_start(self):
        old = await self.start(seconds=.04)
        new = asyncio.create_task(self.runner.start_irrigation(2, 5, run_seconds=.10))
        self.assertFalse(await old)
        await asyncio.sleep(.05)
        self.assertEqual(self.c.get_state(2), 'on')
        self.assertTrue(await new)

    async def test_unavailable_unknown_or_missing_switch_refuses_start(self):
        for value in ('unavailable', 'unknown', None):
            if value is None:
                self.c.hass.states.pop('switch.zone_1', None)
            else:
                self.c.set_state(1, value)
            with self.assertLogs(runtime._LOGGER, 'ERROR'):
                self.assertFalse(await self.runner.start_irrigation(1, 5))
            self.assertFalse(self.c.api.commands)
            self.assertIsNone(self.c.current_irrigation)
            self.assertEqual(self.c.stations[0].state, 'Unavailable')

    async def test_on_acknowledgement_is_required(self):
        self.c.api.confirm_on = False
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            self.assertFalse(await self.runner.start_irrigation(1, 5))
        self.assertIsNone(self.c.current_irrigation)
        self.assertEqual(self.c.total_water_consumption, 0)

    async def test_start_timeout_persists_stop_and_recovery_sends_off(self):
        self.c.api.fail_on = True
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            self.assertFalse(await self.runner.start_irrigation(1, 5))
        self.assertEqual(self.c.pending_stop_stations, [1])
        self.assertIsNotNone(self.c.saved[-1][1])
        self.c.set_state(1, 'on')
        self.c.api.fail_on = False
        await self.runner.handle_switch_states()
        self.assertEqual(self.c.get_state(1), 'off')
        self.assertEqual(self.c.pending_stop_stations, [])

    async def test_loss_stops_accounting_and_reconnect_forces_off(self):
        task = await self.start(seconds=.2)
        await asyncio.sleep(.015)
        self.c.set_state(1, 'unavailable')
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            await self.c.station_switch_tracker.sync()
        self.assertFalse(await task)
        amount = self.c.total_water_consumption
        await asyncio.sleep(.025)
        self.assertEqual(amount, self.c.total_water_consumption)
        self.assertEqual(self.c.pending_stop_stations, [1])
        self.assertEqual(self.c.stations[0].state, 'Unavailable')
        self.c.set_state(1, 'on')
        await self.c.station_switch_tracker.sync()
        self.assertEqual(self.c.get_state(1), 'off')
        self.assertEqual(self.c.pending_stop_stations, [])
        self.assertIsNone(self.c.active_irrigation)

    async def test_transient_loss_is_not_hidden_by_fast_reconnection(self):
        task = await self.start(seconds=.2)
        self.runner.notify_switch_change(1, 'unavailable')
        # Both notifications can queue before the async handler owns the lock.
        self.c.set_state(1, 'on')
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            await self.runner.handle_switch_states()
        self.assertFalse(await task)
        self.assertEqual(self.c.get_state(1), 'off')
        self.assertIsNone(self.runner._session)

    async def test_start_that_loses_connectivity_before_acknowledgement_is_stopped(self):
        original = self.c.api.turn_on_station_switch
        async def unstable_start(station, minutes):
            self.runner.notify_switch_change(station, 'unavailable')
            await original(station, minutes)
        with patch.object(self.c.api, 'turn_on_station_switch', side_effect=unstable_start):
            with self.assertLogs(runtime._LOGGER, 'ERROR'):
                self.assertFalse(await self.runner.start_irrigation(1, 5))
        self.assertEqual(self.c.get_state(1), 'off')
        self.assertEqual(self.c.total_water_consumption, 0)

    async def test_independent_switches_are_all_stopped_on_replacement(self):
        self.c.api.global_off = False
        old = await self.start(seconds=.04)
        new = asyncio.create_task(self.runner.start_irrigation(2, 5, run_seconds=.05))
        self.assertFalse(await old)
        self.assertTrue(await new)
        self.assertTrue(all(self.c.get_state(i) == 'off' for i in range(1, 4)))

    async def test_full_scheduled_cycle_visits_each_station(self):
        original = self.runner.start_irrigation
        async def short_start(*args, **kwargs):
            kwargs['run_seconds'] = .02
            return await original(*args, **kwargs)
        with patch.object(self.runner, 'start_irrigation', side_effect=short_start):
            await self.runner.run_watering_cycle('08:00:00')
        self.assertEqual([c[1] for c in self.c.api.commands if c[0] == 'on'], [1, 2, 3])
        self.assertTrue(all(value > 0 for value in self.c.sprinkle_total_amount_today))

    async def test_external_off_does_not_keep_counting(self):
        task = await self.start(seconds=.2)
        self.c.set_state(1, 'off')
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            await self.c.station_switch_tracker.sync()
        self.assertFalse(await task)
        amount = self.c.total_water_consumption
        await asyncio.sleep(.02)
        self.assertEqual(amount, self.c.total_water_consumption)

    async def test_failed_stop_remains_pending_even_with_stale_off_state(self):
        self.c.api.fail_off = True
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            self.assertFalse(await self.runner.stop_irrigation())
        self.assertEqual(self.c.pending_stop_stations, [1, 2, 3])
        self.assertEqual(self.c.stations[0].state, 'Stopping')
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            self.assertFalse(await self.runner.start_irrigation(1, 5))
        self.assertFalse(any(c[0] == 'on' for c in self.c.api.commands))

    async def test_service_success_without_off_confirmation_is_not_stopped(self):
        task = await self.start(seconds=.3)
        self.c.api.confirm_off = False
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            self.assertFalse(await self.runner.stop_irrigation())
        self.assertFalse(await task)
        self.assertIn(1, self.c.pending_stop_stations)
        self.assertEqual(self.c.stations[0].state, 'Stopping')

    async def test_retry_recovers_without_another_state_change(self):
        self.c.api.fail_off = True
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            await self.runner.stop_irrigation()
        self.c.api.fail_off = False
        await self.wait_for(lambda: not self.c.pending_stop_stations)

    async def test_restart_recovers_durable_stop_before_any_new_start(self):
        self.c.active_irrigation = ActiveIrrigationState.create(
            station=1, source='manual', duration_minutes=5, station_name='Zone 1')
        self.c.set_state(1, 'unavailable')
        with self.assertLogs(runtime._LOGGER, 'ERROR'):
            await self.runner.restore_active_irrigation()
        self.assertEqual(self.c.pending_stop_stations, [1])
        self.c.set_state(1, 'on')
        await self.runner.handle_switch_states()
        self.assertEqual(self.c.get_state(1), 'off')
        self.assertIsNone(self.c.active_irrigation)

    async def test_cancel_during_start_command_still_stops(self):
        self.c.api.on_gate = asyncio.Event()
        task = asyncio.create_task(self.runner.start_irrigation(1, 5))
        await self.wait_for(lambda: bool(self.c.api.commands))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn(('off', 1), self.c.api.commands)
        self.assertIsNone(self.c.active_irrigation)

    async def test_unload_cancels_workers_and_retry(self):
        task = await self.start(seconds=.2)
        await self.runner.async_close()
        self.assertFalse(await task)
        self.assertEqual(self.c.get_state(1), 'off')
        self.assertFalse(self.runner._workers)
        self.assertIsNone(self.runner._retry_task)

    async def test_external_switch_metadata_is_preserved(self):
        self.c.set_state(2, 'on')
        await self.c.station_switch_tracker.sync()
        self.assertEqual(self.c.current_irrigation.station, 2)
        self.assertEqual(self.c.current_irrigation.source, 'external')
        self.c.set_state(2, 'off')
        await self.c.station_switch_tracker.sync()
        self.assertIsNone(self.c.current_irrigation)

    async def test_service_mode_still_runs_a_single_session(self):
        self.c.switch = False
        self.assertTrue(await self.runner.start_irrigation(1, 5, run_seconds=.02))
        self.assertEqual(self.c.api.commands, [('service_on', 1, 5), ('service_off',)])

    async def test_stop_abandons_rest_of_scheduled_sequence(self):
        async def short_start(*args, **kwargs):
            kwargs['run_seconds'] = .2
            return await original(*args, **kwargs)
        original = self.runner.start_irrigation
        with patch.object(self.runner, 'start_irrigation', side_effect=short_start):
            cycle = asyncio.create_task(self.runner.run_watering_cycle('08:00:00'))
            await self.wait_for(lambda: self.runner._session is not None)
            await self.runner.stop_irrigation()
            await cycle
        self.assertEqual([c for c in self.c.api.commands if c[0] == 'on'], [('on', 1, 5)])

    async def test_manual_replacement_abandons_automatic_sequence(self):
        original = self.runner.start_irrigation
        async def short_start(*args, **kwargs):
            kwargs['run_seconds'] = .1
            return await original(*args, **kwargs)
        with patch.object(self.runner, 'start_irrigation', side_effect=short_start):
            cycle = asyncio.create_task(self.runner.run_watering_cycle('08:00:00'))
            await self.wait_for(lambda: self.runner._session is not None)
            manual = asyncio.create_task(original(3, 5, run_seconds=.04))
            await cycle
            self.assertTrue(await manual)
        self.assertEqual([c[1] for c in self.c.api.commands if c[0] == 'on'], [1, 3])

    async def test_duplicate_scheduled_cycles_are_rejected(self):
        task = await self.start(seconds=.03)
        await self.runner.run_watering_cycle('08:00:00')
        self.assertEqual(len([c for c in self.c.api.commands if c[0] == 'on']), 1)
        await task


class PlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_after_missed_morning_slot_preserves_daily_debt(self):
        c = Coordinator()
        c.sprinkle_total_amount_today = [2.1, 0, 2.09]
        now = datetime(2026, 10, 20, 10, 57, tzinfo=timezone.utc)
        with patch.object(ha_stub.dt, 'now', return_value=now):
            await c.schedule_state.set_schedule(c.schedule)
            self.assertEqual(c.sprinkle_target_amount_today, [4.2, 4.2, 4.2])
            self.assertEqual(c.remaining_sprinkle_today, [2.1, 4.2, 2.11])
            c.sprinkle_total_amount_today[1] = 2.1
            self.assertEqual(c.schedule_state.calculate_remaining_sprinkle_today(2), 2.1)
            await c.schedule_state.set_schedule(c.schedule)
            self.assertEqual(c.sprinkle_target_amount_today, [4.2, 4.2, 4.2])

    async def test_legacy_storage_migration_preserves_pending_shutdown(self):
        storage = IrrigationStorage(None, 'entry-specific', num_stations=3,
                                    station_name_resolver=str, legacy_storage_key='irrigation_None')
        state = storage._default_state()
        state.pending_stop_stations = [2]
        raw = storage._state_to_storage(state)
        storage._legacy_store.data = raw
        restored = await storage.async_load()
        self.assertEqual(restored.pending_stop_stations, [2])
        self.assertEqual(storage._store.data, raw)
        self.assertEqual(storage._legacy_store.data, raw)

    def test_slot_plan_is_capped_by_same_daily_target_as_sensors(self):
        plan = planning.calculate_station_slot_irrigation_plan(
            station_id=1, scheduled_minutes=5, current_slot_index=2,
            flow_rate=21, area=50, already_applied_mm=2.1,
            expected_rain_today_mm=0, daily_target_mm=2.1)
        self.assertEqual(plan.minutes_needed, 0)
        self.assertEqual(plan.amount_to_apply_mm, 0)

    def test_exact_slot_does_not_round_up_to_extra_minute(self):
        plan = planning.calculate_station_slot_irrigation_plan(
            station_id=1, scheduled_minutes=5, current_slot_index=2,
            flow_rate=21, area=50, already_applied_mm=2.1,
            expected_rain_today_mm=0, daily_target_mm=4.2)
        self.assertEqual(plan.minutes_needed, 5)

    def test_pending_shutdown_round_trips_with_legacy_storage(self):
        storage = IrrigationStorage(None, 'test', num_stations=3, station_name_resolver=str)
        active = ActiveIrrigationState.create(station=1, source='manual', duration_minutes=5, station_name='Zone 1')
        state = storage._default_state()
        state.active_irrigation = active
        state.pending_stop_stations = [1, 3]
        restored = storage._state_from_storage(storage._state_to_storage(state))
        self.assertEqual(restored.pending_stop_stations, [1, 3])
        self.assertEqual(restored.active_irrigation.station, 1)
        raw = storage._state_to_storage(state)
        del raw['pending_stop_stations']
        self.assertEqual(storage._state_from_storage(raw).pending_stop_stations, [])


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_rainbird_receives_requested_duration(self):
        calls = []
        async def call(*args, **kwargs):
            calls.append((args, kwargs))
        hass = SimpleNamespace(services=SimpleNamespace(async_call=call),
                               registry=SimpleNamespace(async_get=lambda _: SimpleNamespace(platform='rainbird')))
        api = SmartWaterControllerAPI(hass, None, 10, station_switch_entities=['switch.lawn'])
        await api.turn_on_station_switch(1, 9)
        self.assertEqual(calls[0][0], ('rainbird', 'start_irrigation', {'entity_id': 'switch.lawn', 'duration': 9}))
        hass.registry.async_get = lambda _: SimpleNamespace(platform='mqtt')
        await api.turn_on_station_switch(1, 9)
        self.assertEqual(calls[1][0], ('switch', 'turn_on', {'entity_id': 'switch.lawn'}))

    async def test_global_stop_does_not_swallow_partial_failures(self):
        calls = []
        async def call(domain, service, data, **kwargs):
            calls.append(data['entity_id'])
            if data['entity_id'] == 'switch.zone_1':
                raise RuntimeError('offline')
        api = SmartWaterControllerAPI(SimpleNamespace(services=SimpleNamespace(async_call=call)), None, 10,
                                      station_switch_entities=['switch.zone_1', 'switch.zone_2'])
        with self.assertLogs('custom_components.smart_water_controller.api.controller', 'ERROR'):
            with self.assertRaises(Exception):
                await api.turn_off_all_station_switches()
        self.assertEqual(calls, ['switch.zone_1', 'switch.zone_2'])


if __name__ == '__main__':
    unittest.main()
