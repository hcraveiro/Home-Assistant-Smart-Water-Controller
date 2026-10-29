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

                runner = self.coordinator.irrigation_runner
                active_session = runner._session
                if (active_session is not None
                        and active_session.irrigation.station == tracked_station_id
                        and old_value == STATE_ON and new_value != STATE_ON):
                    _LOGGER.warning(
                        "%s - Active station %s switch %s changed %s -> %s; source=%s. Session interruption will be processed.",
                        self.coordinator.controller_mac_address, tracked_station_id,
                        tracked_entity_id, old_value, new_value, active_session.irrigation.source,
                    )
                runner.notify_switch_change(tracked_station_id, new_value)
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

        await self.coordinator.irrigation_runner.handle_switch_states()
        # The runner owns active-session and pending-stop lifecycle. Switch
        # observation must never discard the durable shutdown obligation.
        await self.coordinator.irrigation_runner._publish()
