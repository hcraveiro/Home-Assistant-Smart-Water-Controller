"""Persist irrigation outcomes and optionally notify selected mobile devices."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.util import dt as dt_util

from ..const import (
    NOTIFICATIONS_ENABLED,
    NOTIFY_STATION_FINISHED,
    NOTIFY_CYCLE_FINISHED,
    NOTIFY_IRRIGATION_FAILED,
    NOTIFY_MANUAL_STOP,
    NOTIFICATION_TARGETS,
    NOTIFICATION_DEFAULTS,
)

_LOGGER = logging.getLogger(__name__)

EVENT_OPTIONS = {
    "station_finished": NOTIFY_STATION_FINISHED,
    "cycle_finished": NOTIFY_CYCLE_FINISHED,
    "irrigation_failed": NOTIFY_IRRIGATION_FAILED,
    "station_stopped": NOTIFY_MANUAL_STOP,
    "cycle_stopped": NOTIFY_MANUAL_STOP,
    "all_stopped": NOTIFY_MANUAL_STOP,
}


class IrrigationNotifications:
    """Report final outcomes independently of the active irrigation session."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.failed_station: int | None = None
        self._failure_task: asyncio.Task | None = None

    @property
    def settings(self) -> dict[str, Any]:
        entry = self.coordinator.config_entry
        return {**NOTIFICATION_DEFAULTS, **{
            key: value for source in (entry.data, entry.options)
            for key, value in source.items() if key in NOTIFICATION_DEFAULTS
        }}

    @property
    def controller_name(self) -> str:
        entry = self.coordinator.config_entry
        return (entry.data.get(CONF_NAME) or entry.title or "Controller").strip()

    def station_name(self, station: int) -> str:
        return self.coordinator._get_station_name(station) or f"Station {station}"

    async def report(
        self, event: str, *, station: int | None = None,
        source: str = "manual", reason: str | None = None,
    ) -> None:
        """Record an outcome, update sensors and send selected event notifications."""
        timestamp = dt_util.now().isoformat()
        station_name = self.station_name(station) if station is not None else None
        result = {
            "result": ("failed" if event == "irrigation_failed"
                       else "cancelled" if event in ("station_stopped", "cycle_stopped", "all_stopped")
                       else "completed"),
            "event": event,
            "source": source,
            "station": station,
            "station_name": station_name,
            "controller": self.controller_name,
            "time": timestamp,
            "reason": reason,
        }
        self.coordinator.last_irrigation_result = result
        if station is not None:
            self.coordinator.station_irrigation_results[str(station)] = result
        if event == "irrigation_failed":
            self.coordinator.last_irrigation_failure = result
            if station is not None:
                self.failed_station = station
                if not self.coordinator._is_switch_control_method():
                    self.coordinator.stations[station - 1].state = "Failed"
                if self._failure_task is not None:
                    self._failure_task.cancel()
                self._failure_task = self.coordinator.hass.async_create_task(
                    self._clear_failure_after_delay(station)
                )

        await self.coordinator.save_persistent_data()
        await self.coordinator.irrigation_runner._publish()
        await self._notify(event, station_name, source)

    async def _clear_failure_after_delay(self, station: int) -> None:
        """Briefly show Failed, then return to Idle/Stopped without losing history."""
        try:
            await asyncio.sleep(12)
            if self.failed_station == station:
                self.failed_station = None
                if not self.coordinator._is_switch_control_method():
                    if self.coordinator.stations[station - 1].state == "Failed":
                        self.coordinator.stations[station - 1].state = "Stopped"
                await self.coordinator.irrigation_runner._publish()
        except asyncio.CancelledError:
            raise
        finally:
            self._failure_task = None

    def mark_started(self, station: int) -> None:
        """Clear an old transient failure when a new attempt starts."""
        if self.failed_station == station:
            self.failed_station = None
            if self._failure_task is not None:
                self._failure_task.cancel()
                self._failure_task = None

    async def async_close(self) -> None:
        """Cancel timer tasks when HA unloads this integration."""
        if self._failure_task is not None:
            self._failure_task.cancel()
            self._failure_task = None

    async def _notify(self, event: str, station_name: str | None, source: str) -> None:
        settings = self.settings
        if not settings[NOTIFICATIONS_ENABLED] or not settings.get(EVENT_OPTIONS[event], False):
            return
        # Custom integrations ship translations/en.json. Home Assistant resolves
        # the common catalog in its configured language with English fallback.
        from homeassistant.helpers.translation import async_get_translations

        keys = {
            "station_finished": "notification_station_finished",
            "cycle_finished": (
                "notification_cycle_finished_scheduled"
                if source == "automatic" else "notification_cycle_finished_manual"
            ),
            "irrigation_failed": "notification_irrigation_failed",
            "station_stopped": "notification_station_stopped",
            "cycle_stopped": (
                "notification_cycle_stopped_scheduled"
                if source == "automatic" else "notification_cycle_stopped_manual"
            ),
            "all_stopped": "notification_all_stopped",
        }
        domain = "smart_water_controller"
        language = getattr(getattr(self.coordinator.hass, "config", None), "language", "en")
        try:
            catalog = await async_get_translations(
                self.coordinator.hass, language, "common", {domain}
            )
            prefix = f"component.{domain}.common."
            placeholders = {
                "station": station_name or self.controller_name,
                "controller": self.controller_name,
            }
            message = catalog[f"{prefix}{keys[event]}"].format(**placeholders)
            title = catalog[f"{prefix}notification_title"]
        except (KeyError, ValueError, TypeError):
            _LOGGER.exception("Irrigation notification translation is unavailable or invalid.")
            return
        except Exception:
            _LOGGER.exception("Could not load irrigation notification translations.")
            return

        for service_id in dict.fromkeys(settings.get(NOTIFICATION_TARGETS, [])):
            if not isinstance(service_id, str) or not service_id.startswith("notify.mobile_app_"):
                continue
            service_name = service_id.split(".", 1)[1]
            if not self.coordinator.hass.services.has_service("notify", service_name):
                _LOGGER.warning("Notification recipient %s is unavailable.", service_id)
                continue
            try:
                await self.coordinator.hass.services.async_call(
                    "notify", service_name,
                    {"title": title, "message": message},
                    blocking=False,
                )
            except Exception:
                _LOGGER.exception("Could not send irrigation notification to %s.", service_id)
