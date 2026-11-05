"""Lifecycle tests against production modules with a controlled HA boundary.

Not real Home Assistant integration tests. Run in a subprocess to avoid changing
other tests' shared HA module boundary.
"""
import subprocess
import sys
import unittest
from pathlib import Path


class EntryLifecycleTests(unittest.TestCase):
    def test_owned_runtime_and_snapshot_failure(self):
        script = r'''
import asyncio
import importlib
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import ha_stub as h
h.module('homeassistant.config_entries', ConfigEntry=object)
import homeassistant.helpers.event as event
event.async_call_later = lambda *args, **kwargs: lambda: None
event.async_track_point_in_utc_time = lambda *args, **kwargs: lambda: None
event.async_track_time_change = lambda *args, **kwargs: lambda: None
class Coordinator:
    def __init__(self, *args, **kwargs): pass
class UpdateFailed(Exception): pass
h.module('homeassistant.helpers.update_coordinator', DataUpdateCoordinator=Coordinator, UpdateFailed=UpdateFailed)
# The production coordinator, helpers and runtime classes are loaded unchanged.
from custom_components.smart_water_controller.coordinator import SmartWaterControllerCoordinator as C
from custom_components.smart_water_controller.helpers.util import get_controller_service_prefix
assert get_controller_service_prefix(controller_mac=None, controller_name='Garden', entry_id='abc') == 'controller_abc'
assert get_controller_service_prefix(controller_mac='AA:BB:CC:DD:EE:FF', controller_name='Garden', entry_id='abc') == 'aa_bb_cc_dd_ee_ff'
async def test():
    c = C.__new__(C)
    c.data = [{'retained': True}]
    c.async_update_all_sensors = AsyncMock(side_effect=ValueError('broken'))
    try: await c.async_update_data()
    except UpdateFailed: pass
    else: raise AssertionError('failure swallowed')
    assert c.data == [{'retained': True}]
    from homeassistant.util import dt as dt_util
    c._daily_reset_lock = asyncio.Lock()
    c.last_reset = None
    async def reset():
        await asyncio.sleep(0)
        c.last_reset = dt_util.now()
    c.reset_rain_sprinkle_indicators = AsyncMock(side_effect=reset)
    await asyncio.gather(c.async_ensure_current_day(), c.async_ensure_current_day())
    c.reset_rain_sprinkle_indicators.assert_awaited_once()
    c._closed = False
    c._tasks = set()
    c.hass = SimpleNamespace(async_create_task=asyncio.create_task)
    c.irrigation_scheduler = SimpleNamespace(clear=Mock())
    c.station_switch_tracker = SimpleNamespace(clear=Mock())
    c.irrigation_runner = SimpleNamespace(async_close=AsyncMock())
    task = c.create_task(asyncio.Event().wait())
    await c.async_close()
    assert task.cancelled() and not c._tasks
    c.irrigation_runner.async_close.assert_awaited_once()
asyncio.run(test())
# Execute the real root lifecycle module against the same controlled boundary.
import homeassistant.const as const
const.Platform = SimpleNamespace(BINARY_SENSOR='binary_sensor', SENSOR='sensor', NUMBER='number', BUTTON='button')
import homeassistant.core as core
core.ServiceCall = object
import homeassistant.exceptions as exceptions
exceptions.ConfigEntryNotReady = RuntimeError
h.module('homeassistant.helpers.device_registry', DeviceEntry=object)
h.module('homeassistant.helpers.service', async_set_service_schema=lambda *args: None)
h.module('homeassistant.util.yaml', load_yaml=lambda *args: {})
name = 'custom_components.smart_water_controller'
spec = importlib.util.spec_from_file_location(name, h.ROOT / 'custom_components/smart_water_controller/__init__.py')
root = importlib.util.module_from_spec(spec)
import sys
sys.modules[name] = root
spec.loader.exec_module(root)
async def lifecycle():
    c = SimpleNamespace(async_close=AsyncMock())
    runtime = root.RuntimeData(c, Mock(), ['owned'])
    entry = SimpleNamespace(entry_id='abc', runtime_data=runtime)
    hass = SimpleNamespace(data={root.DOMAIN: {'abc': c}}, services=SimpleNamespace(async_remove=Mock()),
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry],
            async_unload_platforms=AsyncMock(return_value=False)))
    assert not await root.async_unload_entry(hass, entry)
    c.async_close.assert_not_awaited()
    assert hass.data[root.DOMAIN]['abc'] is c
    hass.config_entries.async_unload_platforms.return_value = True
    assert await root.async_unload_entry(hass, entry)
    c.async_close.assert_awaited_once()
    assert 'abc' not in hass.data[root.DOMAIN]
    hass.services.async_remove.assert_called_once_with(root.DOMAIN, 'owned')
asyncio.run(lifecycle())

# Exercise service ownership and setup rollback through the real setup function.
async def setup_ownership():
    entries = []
    handlers = {}
    class Services:
        def has_service(self, domain, name): return (domain, name) in handlers
        def async_register(self, domain, name, handler, **kwargs): handlers[(domain, name)] = handler
        def async_remove(self, domain, name): handlers.pop((domain, name), None)
    registry = SimpleNamespace(async_get_or_create=Mock())
    root.dr.async_get = lambda hass: registry
    root.dr.CONNECTION_NETWORK_MAC = 'mac'
    root._async_describe_services = AsyncMock()
    class SetupCoordinator:
        def __init__(self, hass, entry):
            self.controller_mac_address = None
            self.controller_service_prefix = get_controller_service_prefix(
                controller_mac=None, controller_name=entry.title, entry_id=entry.entry_id)
            self.data = [{'ready': True}]
            self.async_init = AsyncMock()
            self.async_config_entry_first_refresh = AsyncMock()
            self.async_close = AsyncMock()
            self.async_set_schedule = AsyncMock()
            self.async_clear_schedule = AsyncMock()
    root.SmartWaterControllerCoordinator = SetupCoordinator
    manager = SimpleNamespace(async_entries=lambda domain: entries,
        async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True))
    hass = SimpleNamespace(data={}, services=Services(), config_entries=manager)
    def entry(entry_id, title='Garden'):
        value = SimpleNamespace(entry_id=entry_id, title=title, unique_id=None,
            data={'name': title}, runtime_data=None, add_update_listener=lambda callback: Mock())
        entries.append(value)
        return value
    first = entry('first')
    assert await root.async_setup_entry(hass, first)
    assert (root.DOMAIN, 'set_irrigation_schedule_garden') in handlers
    second = entry('second')
    assert await root.async_setup_entry(hass, second)
    assert (root.DOMAIN, 'set_irrigation_schedule_garden') not in handlers
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_first') in handlers
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_second') in handlers
    await root.async_unload_entry(hass, second)
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_first') in handlers
    assert (root.DOMAIN, 'set_irrigation_schedule_garden') in handlers
    third = entry('third', 'Garden')
    manager.async_forward_entry_setups.side_effect = RuntimeError('platform setup failed')
    try: await root.async_setup_entry(hass, third)
    except RuntimeError: pass
    else: raise AssertionError('setup failure swallowed')
    third.runtime_data.coordinator.async_close.assert_awaited_once()
    assert 'third' not in hass.data[root.DOMAIN]
    assert not any('third' in name for domain, name in handlers)
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_first') in handlers
    assert (root.DOMAIN, 'set_irrigation_schedule_garden') in handlers
    manager.async_forward_entry_setups.side_effect = None
    # Failed/unloaded configured duplicates do not suppress the active alias.
    reserved = entry('reserved', 'controller_xyz')
    assert await root.async_setup_entry(hass, reserved)
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_xyz') not in handlers
    canonical = entry('xyz', 'Other')
    assert await root.async_setup_entry(hass, canonical)
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_xyz') in handlers
    await root.async_unload_entry(hass, reserved)
    assert (root.DOMAIN, 'set_irrigation_schedule_controller_xyz') in handlers
    # Reconciliation must neither replace nor delete externally owned aliases.
    external_alias = object()
    alias_key = (root.DOMAIN, 'clear_irrigation_schedule_external')
    handlers[alias_key] = external_alias
    external_entry = entry('external', 'External')
    assert await root.async_setup_entry(hass, external_entry)
    await root.async_unload_entry(hass, external_entry)
    assert handlers[alias_key] is external_alias
    fourth = entry('fourth', 'Fourth')
    external = object()
    key = (root.DOMAIN, 'set_irrigation_schedule_controller_fourth')
    handlers[key] = external
    try: await root.async_setup_entry(hass, fourth)
    except RuntimeError: pass
    else: raise AssertionError('service collision silently took ownership')
    assert handlers[key] is external
    assert 'fourth' not in hass.data[root.DOMAIN]
    await root.async_unload_entry(hass, first)
asyncio.run(setup_ownership())
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
