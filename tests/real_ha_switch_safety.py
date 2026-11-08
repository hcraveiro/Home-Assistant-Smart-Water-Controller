"""Explicit integration checks for real HA, not the ha_stub unittest process.

Run inside the Dev HA image with /config on PYTHONPATH. Creates an isolated
HomeAssistant object, temporary config directory and mocked services; does not
connect to the running HA API, load integrations, or contact any hardware.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, device_registry as dr
from custom_components.smart_water_controller.api.controller import SmartWaterControllerAPI
from custom_components.smart_water_controller.runtime import irrigation_runner as runtime
from custom_components.smart_water_controller.runtime.switch_tracking import StationSwitchTracker


class Coordinator:
    def __init__(self, hass, entities):
        self.hass = hass
        self.num_stations = len(entities)
        self.station_switch_entities = entities
        self.stations = [SimpleNamespace(state='Stopped') for _ in entities]
        self.controller_mac_address = 'ISOLATED-MOCK-NO-HARDWARE'
        self.pending_stop_stations = []
        self.active_irrigation = self.current_irrigation = None
        self.irrigation_manual_duration = 9
        self.water_flow_rate = [21] * len(entities)
        self.station_areas = [50] * len(entities)
        self.sprinkle_total_amount_today = [0.] * len(entities)
        self.sprinkle_duration_today_seconds = [0.] * len(entities)
        self.sprinkle_target_amount_today = [4.2] * len(entities)
        self.total_water_consumption = 0.
        self.last_sprinkle = datetime.now(timezone.utc)
        self.last_rain = self.last_sprinkle
        self.soil_moisture_sensor = None
        self.controller = SimpleNamespace(state='On', device_id='mock')
        self.irrigation_control_method = 'switch'
        self.saved = []
        self.station_switch_tracker = StationSwitchTracker(self)
        self.api = SmartWaterControllerAPI(hass, None, 10, station_switch_entities=entities)
        self.irrigation_runner = runtime.IrrigationRunner(self)

    def _is_switch_control_method(self):
        return True

    def _get_station_name(self, station):
        return f'Mock {station}'

    def _clear_current_irrigation(self, station=None):
        self.current_irrigation = None

    async def save_persistent_data(self, *, force=False):
        self.saved.append(deepcopy((self.pending_stop_stations, self.active_irrigation)))

    async def async_update_all_sensors(self):
        return []

    def async_set_updated_data(self, data):
        pass


async def main():
    with TemporaryDirectory(prefix='swc-isolated-') as config:
        hass = HomeAssistant(config)
        # Real registry, state machine, service dispatch, API and irrigation runner.
        dr.async_setup(hass)
        await dr.async_load(hass)
        registry = er.async_get(hass)
        await registry.async_load()
        rainbird = registry.async_get_or_create('switch', 'rainbird', 'isolated-zone', suggested_object_id='isolated_rainbird')
        generic = registry.async_get_or_create('switch', 'template', 'isolated-template', suggested_object_id='isolated_template')
        entities = [rainbird.entity_id, generic.entity_id]
        calls = []

        async def start(call):
            calls.append((call.domain, call.service, dict(call.data)))
            hass.states.async_set(call.data['entity_id'], 'on')

        async def stop(call):
            calls.append((call.domain, call.service, dict(call.data)))
            entity = call.data['entity_id']
            if hass.states.get(entity).state != 'unavailable':
                hass.states.async_set(entity, 'off')

        hass.services.async_register('rainbird', 'start_irrigation', start)
        hass.services.async_register('switch', 'turn_on', start)
        hass.services.async_register('switch', 'turn_off', stop)
        for entity in entities:
            hass.states.async_set(entity, 'off')
        c = Coordinator(hass, entities)
        runner = c.irrigation_runner
        try:
            task = asyncio.create_task(runner.start_irrigation(1, 9, run_seconds=300))
            async with asyncio.timeout(5):
                while hass.states.get(entities[0]).state != 'on':
                    await asyncio.sleep(.01)
            assert await runner.stop_station(1)
            assert not await task
            assert calls[0] == ('rainbird', 'start_irrigation', {'entity_id': entities[0], 'duration': 9}), calls
            assert hass.states.get(entities[0]).state == 'off'
            assert not c.pending_stop_stations
            print('PASS Rainbird: registry platform rainbird, duration=9, explicit stop during 300s session, confirmed OFF', flush=True)

            task = asyncio.create_task(runner.start_irrigation(2, 9, run_seconds=300))
            async with asyncio.timeout(5):
                while hass.states.get(entities[1]).state != 'on':
                    await asyncio.sleep(.01)
            hass.states.async_set(entities[1], 'unavailable')
            assert not await runner.stop_station(2)
            assert not await task
            assert c.pending_stop_stations == [2]
            assert c.active_irrigation is not None
            assert any(pending == [2] for pending, _ in c.saved)
            assert not await runner.start_irrigation(1, 9, run_seconds=300)
            before = len(calls)
            # Reconnection reports ON, not OFF: recovery must issue the OFF.
            hass.states.async_set(entities[1], 'on')
            await runner.handle_switch_states()
            assert ('switch', 'turn_off', {'entity_id': entities[1]}) in calls[before:]
            assert hass.states.get(entities[1]).state == 'off'
            assert not c.pending_stop_stations
            assert c.active_irrigation is None
            assert runner._session is None
            print('PASS unavailable stop: pending [2], persisted intent, new start blocked, reconnect ON -> actual recovery OFF -> pending []', flush=True)
            print(json.dumps({'calls': calls, 'cleanup': {'states': [hass.states.get(e).state for e in entities], 'pending': c.pending_stop_stations, 'active': c.active_irrigation, 'session': runner._session}}), flush=True)
        finally:
            for entity in entities:
                hass.states.async_set(entity, 'off')
            await runner.async_close()
            await hass.async_stop()


if __name__ == '__main__':
    asyncio.run(main())
