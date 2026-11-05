"""Real HA Store/coordinator/registries; no devices or network integrations.

Platform forwarding is mocked deliberately: these tests cover the integration
boundary, not UI rendering, Solem BLE or physical watering.
"""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smart_water_controller import async_setup_entry, async_unload_entry
from custom_components.smart_water_controller.const import DOMAIN
from custom_components.smart_water_controller.coordinator import SmartWaterControllerCoordinator


def make_entry(hass: HomeAssistant, title='Garden'):
    entry = MockConfigEntry(domain=DOMAIN, title=title, unique_id=None, data={
        'name': title, 'irrigation_control_method': 'switch', 'num_stations': 1,
        'station_names': ['Test zone'], 'station_areas': [10],
        'station_switch_entities': ['switch.swc_test_zone'], 'weather_provider': 'none',
    })
    entry.add_to_hass(hass)
    # This suite invokes root setup directly so optional controller dependencies
    # cannot load real network/BLE integrations. Match HA's setup lifecycle state.
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    hass.states.async_set('switch.swc_test_zone', 'off')
    return entry


@pytest.mark.asyncio
async def test_real_service_registry_multi_entry_and_unload_refusal(hass: HomeAssistant):
    first = make_entry(hass)
    with patch.object(hass.config_entries, 'async_forward_entry_setups', new_callable=AsyncMock), \
         patch.object(hass.config_entries, 'async_unload_platforms', new_callable=AsyncMock, return_value=True) as unload:
        assert await async_setup_entry(hass, first)
        second = make_entry(hass)
        assert await async_setup_entry(hass, second)
        first_service = f'set_irrigation_schedule_controller_{first.entry_id}'
        second_service = f'set_irrigation_schedule_controller_{second.entry_id}'
        assert hass.services.has_service(DOMAIN, first_service)
        assert hass.services.has_service(DOMAIN, second_service)
        assert not hass.services.has_service(DOMAIN, 'set_irrigation_schedule_garden')
        unload.return_value = False
        assert not await async_unload_entry(hass, first)
        assert not first.runtime_data.coordinator._closed
        assert hass.services.has_service(DOMAIN, first_service)
        unload.return_value = True
        assert await async_unload_entry(hass, second)
        assert not hass.services.has_service(DOMAIN, second_service)
        assert hass.services.has_service(DOMAIN, first_service)
        assert second.entry_id not in hass.data[DOMAIN]
        assert await async_unload_entry(hass, first)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_real_coordinator_first_refresh_failure_rolls_back(hass: HomeAssistant):
    entry = make_entry(hass)
    with patch.object(SmartWaterControllerCoordinator, 'async_update_data', side_effect=UpdateFailed('snapshot failed')), \
         patch.object(hass.config_entries, 'async_unload_platforms', new_callable=AsyncMock, return_value=True):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert entry.runtime_data.coordinator._closed
    assert not entry.runtime_data.services
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_real_atomic_store_round_trip(hass: HomeAssistant):
    from custom_components.smart_water_controller.runtime.storage import IrrigationStorage
    storage = IrrigationStorage(hass, 'swc_test_atomic', num_stations=1,
                                station_name_resolver=lambda station: 'Test zone')
    state = storage._default_state()
    state.pending_stop_stations = [1]
    await storage.async_save(state, force=True)
    restored = IrrigationStorage(hass, 'swc_test_atomic', num_stations=1,
                                 station_name_resolver=lambda station: 'Test zone')
    assert (await restored.async_load()).pending_stop_stations == [1]
