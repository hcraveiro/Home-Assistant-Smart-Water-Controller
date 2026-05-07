"""Irrigation scheduling helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class IrrigationScheduler:
    """Manage daily and slot-based irrigation scheduling."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        """Initialize irrigation scheduler."""
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._watering_unsubscribers = []
        self._daily_unsubscribers = []

    def clear_watering_callbacks(self) -> None:
        """Cancel all currently scheduled watering callbacks."""
        for unsubscribe in list(self._watering_unsubscribers):
            try:
                unsubscribe()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug(
                    "%s - Failed to cancel a scheduled watering callback.",
                    self.coordinator.controller_mac_address,
                    exc_info=True,
                )

        self._watering_unsubscribers = []

    def clear_daily_callbacks(self) -> None:
        """Cancel all daily scheduled callbacks."""
        for unsubscribe in list(self._daily_unsubscribers):
            try:
                unsubscribe()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug(
                    "%s - Failed to cancel a daily scheduled callback.",
                    self.coordinator.controller_mac_address,
                    exc_info=True,
                )

        self._daily_unsubscribers = []

    def clear(self) -> None:
        """Cancel all scheduler callbacks."""
        self.clear_watering_callbacks()
        self.clear_daily_callbacks()

    async def setup_daily_tasks(self) -> None:
        """Create daily scheduled tasks."""
        self.clear_daily_callbacks()

        _LOGGER.info(
            "%s - Scheduling daily irrigation tasks.",
            self.coordinator.controller_mac_address,
        )

        @callback
        def _run_daily_reset(_now) -> None:
            """Run the daily reset task."""
            self.hass.async_create_task(
                self.coordinator.reset_rain_sprinkle_indicators()
            )

        @callback
        def _run_daily_schedule_check(_now) -> None:
            """Run the daily watering schedule check."""
            self.hass.async_create_task(
                self.check_and_schedule_watering()
            )

        self._daily_unsubscribers.append(
            async_track_time_change(
                self.hass,
                _run_daily_reset,
                hour=0,
                minute=0,
                second=0,
            )
        )

        self._daily_unsubscribers.append(
            async_track_time_change(
                self.hass,
                _run_daily_schedule_check,
                hour=0,
                minute=1,
                second=0,
            )
        )

        _LOGGER.info(
            "%s - Daily irrigation tasks scheduled.",
            self.coordinator.controller_mac_address,
        )

    async def check_and_schedule_watering(self, *_args) -> None:
        """Check if watering can run today and schedule future watering slots."""
        _LOGGER.info(
            "%s - Checking and scheduling watering times.",
            self.coordinator.controller_mac_address,
        )

        self.clear_watering_callbacks()

        if not self.coordinator.schedule:
            _LOGGER.warning(
                "%s - Schedule not initialized, skipping watering check.",
                self.coordinator.controller_mac_address,
            )
            return

        now = dt_util.now()
        today = now.date()
        current_month_index = today.month - 1

        month_config = self.coordinator.schedule[current_month_index]

        if not month_config:
            _LOGGER.info(
                "%s - No configuration active for this month.",
                self.coordinator.controller_mac_address,
            )
            return

        interval_days = month_config.get("interval_days", 2)

        if self.coordinator._recent_event_blocks_today(interval_days):
            return

        future_slots = self.coordinator._get_future_watering_slots_for_day(
            today,
            month_config,
        )

        if not future_slots:
            _LOGGER.info(
                "%s - No remaining watering slots for today.",
                self.coordinator.controller_mac_address,
            )
            return

        if not self.coordinator.needs_watering_today():
            _LOGGER.info(
                "%s - No station currently needs watering, "
                "but future watering slots will still be scheduled because the forecast may change.",
                self.coordinator.controller_mac_address,
            )

        for hour, watering_datetime in future_slots:
            delay = (watering_datetime - now).total_seconds()

            if delay <= 0:
                continue

            @callback
            def _run_scheduled_watering(_now, scheduled_hour=hour) -> None:
                """Run a scheduled watering slot."""
                self.hass.async_create_task(
                    self.coordinator.run_watering_cycle(
                        scheduled_hour=scheduled_hour,
                    )
                )

            unsubscribe = async_call_later(
                self.hass,
                delay,
                _run_scheduled_watering,
            )

            self._watering_unsubscribers.append(unsubscribe)

            _LOGGER.info(
                "%s - Watering slot scheduled for %s (scheduled slot: %s).",
                self.coordinator.controller_mac_address,
                watering_datetime,
                hour,
            )

        _LOGGER.debug(
            "%s - Scheduled watering slots.",
            self.coordinator.controller_mac_address,
        )