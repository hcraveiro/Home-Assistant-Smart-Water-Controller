"""Station switch tracking helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class StationSwitchTracker:
    """Track station switch state changes for switch-based irrigation control."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        """Initialize the station switch tracker."""
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._unsubscribers = []

    def clear(self) -> None:
        """Cancel all registered station switch callbacks."""
        for unsubscribe in list(self._unsubscribers):
            try:
                unsubscribe()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug(
                    "%s - Failed to cancel station switch callback.",
                    self.coordinator.controller_mac_address,
                    exc_info=True,
                )

        self._unsubscribers = []

    def get_station_switch_entity_id(self, station_id: int) -> str | None:
        """Return the switch entity configured for a station."""
        if not self.coordinator._is_switch_control_method():
            return None

        if not isinstance(self.coordinator.station_switch_entities, list):
            return None

        index = station_id - 1

        if index < 0 or index >= len(self.coordinator.station_switch_entities):
            return None

        entity_id = self.coordinator.station_switch_entities[index]

        if not entity_id:
            return None

        return str(entity_id)

    async def setup(self) -> None:
        """Track station switch changes when using switch-based irrigation control."""
        self.clear()

        if not self.coordinator._is_switch_control_method():
            _LOGGER.debug(
                "%s - Station switch tracking disabled because control method is not switch.",
                self.coordinator.controller_mac_address,
            )
            return

        if not self.coordinator.station_switch_entities:
            _LOGGER.warning(
                "%s - Switch control method is enabled but no station switches are configured.",
                self.coordinator.controller_mac_address,
            )
            return

        for station_id in range(1, self.coordinator.num_stations + 1):
            entity_id = self.get_station_switch_entity_id(station_id)

            if not entity_id:
                _LOGGER.warning(
                    "%s - No switch entity configured for station %s.",
                    self.coordinator.controller_mac_address,
                    station_id,
                )
                continue

            @callback
            def _handle_station_switch_change(
                event,
                tracked_station_id=station_id,
                tracked_entity_id=entity_id,
            ) -> None:
                """Handle station switch state changes."""
                new_state = event.data.get("new_state")
                old_state = event.data.get("old_state")

                if new_state is None:
                    return

                old_value = old_state.state if old_state else None
                new_value = new_state.state

                _LOGGER.debug(
                    "%s - Station %s switch changed: %s %s -> %s",
                    self.coordinator.controller_mac_address,
                    tracked_station_id,
                    tracked_entity_id,
                    old_value,
                    new_value,
                )

                self.hass.async_create_task(self.sync())

            unsubscribe = async_track_state_change_event(
                self.hass,
                [entity_id],
                _handle_station_switch_change,
            )

            self._unsubscribers.append(unsubscribe)

            _LOGGER.info(
                "%s - Tracking station %s switch: %s",
                self.coordinator.controller_mac_address,
                station_id,
                entity_id,
            )

        await self.sync()

    async def sync(self) -> None:
        """Synchronize station status entities from configured switch states."""
        if not self.coordinator._is_switch_control_method():
            return

        changed = False

        for station_id in range(1, self.coordinator.num_stations + 1):
            entity_id = self.get_station_switch_entity_id(station_id)

            if not entity_id:
                continue

            state = self.hass.states.get(entity_id)

            if state is None:
                _LOGGER.debug(
                    "%s - Station %s switch entity not found: %s",
                    self.coordinator.controller_mac_address,
                    station_id,
                    entity_id,
                )
                continue

            if state.state == STATE_ON:
                new_station_state = "Sprinkling"

                if self.coordinator._get_current_irrigation_station_id() is None:
                    self.coordinator._set_current_irrigation(
                        station=station_id,
                        source="external",
                        duration_minutes=None,
                        start_at=dt_util.now(),
                        end_at=None,
                    )
                    changed = True

            elif state.state == STATE_OFF:
                new_station_state = "Stopped"

                if self.coordinator._get_current_irrigation_station_id() == station_id:
                    self.coordinator._clear_current_irrigation(station_id)
                    changed = True

            else:
                _LOGGER.debug(
                    "%s - Station %s switch has unsupported state: %s=%s",
                    self.coordinator.controller_mac_address,
                    station_id,
                    entity_id,
                    state.state,
                )
                continue

            if self.coordinator.stations[station_id - 1].state != new_station_state:
                self.coordinator.stations[station_id - 1].state = new_station_state
                changed = True

                _LOGGER.info(
                    "%s - Station %s state synchronized from switch %s: %s",
                    self.coordinator.controller_mac_address,
                    station_id,
                    entity_id,
                    new_station_state,
                )

        if self.coordinator.active_irrigation is not None:
            active_station_id = self.coordinator.active_irrigation.station
            active_switch_entity_id = self.get_station_switch_entity_id(active_station_id)
            active_switch_state = (
                self.hass.states.get(active_switch_entity_id)
                if active_switch_entity_id
                else None
            )

            if active_switch_state and active_switch_state.state == STATE_OFF:
                _LOGGER.info(
                    "%s - Active irrigation station %s is now off. "
                    "Clearing persisted active irrigation.",
                    self.coordinator.controller_mac_address,
                    active_station_id,
                )
                self.coordinator.active_irrigation = None
                self.coordinator._clear_current_irrigation(active_station_id)
                await self.coordinator.save_persistent_data()
                changed = True

        if changed:
            data = await self.coordinator.async_update_all_sensors()

            if data is not None:
                self.coordinator.async_set_updated_data(data)
            else:
                _LOGGER.warning(
                    "%s - async_update_all_sensors() returned None after switch state sync.",
                    self.coordinator.controller_mac_address,
                )