"""Schedule local watering slots as absolute instants with owned callbacks."""
from __future__ import annotations

from datetime import timedelta, timezone
import logging
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_change
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class IrrigationScheduler:
    """Own daily maintenance and a bounded, cross-midnight slot lookahead."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._watering_unsubscribers = []
        self._daily_unsubscribers = []
        self._closed = False

    def _clear_callbacks(self, callbacks) -> None:
        for unsubscribe in list(callbacks):
            try:
                unsubscribe()
            except Exception:
                _LOGGER.debug("Could not cancel irrigation callback", exc_info=True)
        callbacks.clear()

    def clear_watering_callbacks(self) -> None:
        self._clear_callbacks(self._watering_unsubscribers)

    def clear_daily_callbacks(self) -> None:
        self._clear_callbacks(self._daily_unsubscribers)

    def clear(self) -> None:
        self._closed = True
        self.clear_watering_callbacks()
        self.clear_daily_callbacks()

    async def setup_daily_tasks(self) -> None:
        self.clear_daily_callbacks()
        if self._closed:
            return

        @callback
        def _run_daily_reset(_now) -> None:
            if not self._closed:
                self.coordinator.async_create_task(self.coordinator.async_ensure_current_day())

        @callback
        def _run_daily_schedule_check(_now) -> None:
            if not self._closed:
                self.coordinator.async_create_task(self.check_and_schedule_watering())

        self._daily_unsubscribers.extend([
            async_track_time_change(self.hass, _run_daily_reset, hour=0, minute=0, second=0),
            async_track_time_change(self.hass, _run_daily_schedule_check, hour=0, minute=1, second=0),
        ])

    async def _dispatch_slot(self, hour: str, slot_day) -> None:
        if self._closed or dt_util.now().date() != slot_day:
            return
        # Both the midnight callback and a midnight watering use the same
        # idempotent coordinator reset. Weather I/O must finish before planning.
        await self.coordinator.async_ensure_current_day()
        if self._closed:
            return
        now = dt_util.now()
        if now.date() != slot_day:
            return
        schedule = self.coordinator.schedule
        if not schedule or len(schedule) < now.month:
            return
        config = schedule[now.month - 1] or {}
        if hour not in self.coordinator._get_valid_watering_hours(config):
            return
        if self.coordinator._recent_event_blocks_today(config.get("interval_days", 2)):
            return
        await self.coordinator.run_watering_cycle(scheduled_hour=hour)

    async def check_and_schedule_watering(self, *_args) -> None:
        """Schedule today and tomorrow; eligibility is checked when a slot fires.

        The lookahead includes midnight even though the daily rebuild happens
        at 00:01. Restarted entries do not replay missed slots. Local times that
        do not exist on a spring DST jump are skipped; autumn repeats run once.
        """
        self.clear_watering_callbacks()
        if self._closed or not self.coordinator.schedule:
            return
        now = dt_util.now()
        for day in (now.date(), now.date() + timedelta(days=1)):
            if len(self.coordinator.schedule) < day.month:
                continue
            config = self.coordinator.schedule[day.month - 1]
            if not config:
                continue
            future_slots = self.coordinator._get_future_watering_slots_for_day(day, config)
            for hour, watering_datetime in future_slots:
                instant = watering_datetime.astimezone(timezone.utc)
                if instant.timestamp() <= now.timestamp():
                    continue

                @callback
                def _run_scheduled_watering(_now, scheduled_hour=hour, slot_day=day) -> None:
                    if not self._closed:
                        self.coordinator.async_create_task(self._dispatch_slot(scheduled_hour, slot_day))

                unsubscribe = async_track_point_in_utc_time(self.hass, _run_scheduled_watering, instant)
                self._watering_unsubscribers.append(unsubscribe)
                _LOGGER.debug("%s - Watering slot %s scheduled for %s",
                              self.coordinator.controller_mac_address, hour, instant)
