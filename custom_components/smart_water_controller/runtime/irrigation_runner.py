"""Irrigation runner helpers for Smart Water Controller."""

from __future__ import annotations

from datetime import timedelta
import asyncio
from asyncio import sleep
import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON
from homeassistant.util import dt as dt_util

from ..helpers import planning
from .active_irrigation import ActiveIrrigationState
from ..api.controller import APIConnectionError

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class IrrigationRunner:
    """Execute irrigation operations for the coordinator."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        """Initialize irrigation runner."""
        self.coordinator = coordinator
        self.hass = coordinator.hass

    async def restore_active_irrigation(self) -> None:
        """Restore active irrigation safety for switch control method.

        When using the switch control method, watering is implemented by
        turning a station switch on and later turning it off. If Home Assistant
        restarts mid-watering, ensure the switch is turned off at the expected
        end time to avoid indefinite watering.
        """
        if not self.coordinator._is_switch_control_method():
            self.coordinator.active_irrigation = None
            self.coordinator.current_irrigation = None
            return

        if self.coordinator.active_irrigation is None:
            self.coordinator.current_irrigation = None
            return

        if self.coordinator.active_irrigation.end_at is None:
            self.coordinator.active_irrigation = None
            self.coordinator.current_irrigation = None
            await self.coordinator.save_persistent_data()
            return

        now = dt_util.now()

        if self.coordinator.active_irrigation.has_ended(now):
            _LOGGER.warning(
                "%s - Found stale active irrigation in storage (station %s). Turning off now.",
                self.coordinator.controller_mac_address,
                self.coordinator.active_irrigation.station,
            )
            try:
                await self.coordinator.api.turn_off_station_switch(
                    self.coordinator.active_irrigation.station
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "%s - Failed turning off stale station switch",
                    self.coordinator.controller_mac_address,
                    exc_info=True,
                )

            self.coordinator.active_irrigation = None
            self.coordinator.current_irrigation = None
            await self.coordinator.save_persistent_data()
            return

        remaining_seconds = int(
            (self.coordinator.active_irrigation.end_at - now).total_seconds()
        )

        if remaining_seconds <= 0:
            self.coordinator.active_irrigation = None
            self.coordinator.current_irrigation = None
            await self.coordinator.save_persistent_data()
            return

        _LOGGER.info(
            "%s - Restored active irrigation (station %s). Will stop in %s seconds.",
            self.coordinator.controller_mac_address,
            self.coordinator.active_irrigation.station,
            remaining_seconds,
        )

        self.coordinator._set_current_irrigation(
            station=self.coordinator.active_irrigation.station,
            source=self.coordinator.active_irrigation.source,
            duration_minutes=self.coordinator.active_irrigation.duration_minutes,
            start_at=self.coordinator.active_irrigation.start_at,
            end_at=self.coordinator.active_irrigation.end_at,
        )

        try:
            self.coordinator.stations[
                self.coordinator.active_irrigation.station - 1
            ].state = "Sprinkling"
        except Exception:  # pylint: disable=broad-except
            pass

        self.hass.async_create_task(
            self.stop_irrigation_after_delay(
                self.coordinator.active_irrigation.station,
                remaining_seconds,
            )
        )

    async def stop_irrigation_after_delay(self, station: int, delay_seconds: int) -> None:
        """Stop irrigation after a delay."""
        try:
            await asyncio.sleep(delay_seconds)

            if (
                self.coordinator.active_irrigation is not None
                and int(self.coordinator.active_irrigation.station) == int(station)
            ):
                await self.stop_irrigation()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception(
                "%s - Error while scheduling irrigation auto-stop",
                self.coordinator.controller_mac_address,
            )

    async def run_watering_cycle(self, scheduled_hour: str | None = None) -> None:
        """Run the scheduled watering cycle if all conditions are met."""
        _LOGGER.info(
            "%s - Running scheduled watering cycle...",
            self.coordinator.controller_mac_address,
        )

        if self.coordinator.soil_moisture_sensor:
            state = self.hass.states.get(self.coordinator.soil_moisture_sensor)

            if state and state.state not in ("unknown", "unavailable"):
                try:
                    moisture = float(state.state)

                    if moisture >= self.coordinator.soil_moisture_threshold:
                        _LOGGER.info(
                            "%s - Soil moisture is %s%%, above threshold (%s%%). Skipping watering.",
                            self.coordinator.controller_mac_address,
                            moisture,
                            self.coordinator.soil_moisture_threshold,
                        )
                        return

                    _LOGGER.debug(
                        "%s - Soil moisture is %s%%, below threshold (%s%%). Proceeding with watering.",
                        self.coordinator.controller_mac_address,
                        moisture,
                        self.coordinator.soil_moisture_threshold,
                    )
                except ValueError:
                    _LOGGER.warning(
                        "%s - Failed to parse soil moisture value: %s",
                        self.coordinator.controller_mac_address,
                        state.state,
                    )
            else:
                _LOGGER.warning(
                    "%s - Soil moisture sensor state is unknown or unavailable: %s",
                    self.coordinator.controller_mac_address,
                    state.state if state else "None",
                )

        if self.coordinator._rain_blocks_watering_now():
            _LOGGER.info(
                "%s - Watering skipped because it is raining now.",
                self.coordinator.controller_mac_address,
            )
            return

        current_month_index = dt_util.now().month - 1
        month_config = self.coordinator.schedule[current_month_index]

        if not month_config:
            _LOGGER.info(
                "%s - No configuration active for this month.",
                self.coordinator.controller_mac_address,
            )
            return

        stations = month_config.get("stations", {})
        watering_hours = self.coordinator._get_valid_watering_hours(month_config)

        if not watering_hours:
            _LOGGER.info(
                "%s - No watering hours configured for this month.",
                self.coordinator.controller_mac_address,
            )
            return

        if scheduled_hour and scheduled_hour not in watering_hours:
            _LOGGER.info(
                "%s - Scheduled slot %s is no longer in the active schedule. Skipping stale watering callback.",
                self.coordinator.controller_mac_address,
                scheduled_hour,
            )
            return

        current_slot_index = planning.get_current_slot_index(
            watering_hours,
            scheduled_hour=scheduled_hour,
            now=dt_util.now(),
            log_prefix=f"{self.coordinator.controller_mac_address} - ",
        )
        total_slots_today = len(watering_hours)

        _LOGGER.debug(
            "%s - Hours=%s scheduled_hour=%s current_slot_index=%s total_slots_today=%s",
            self.coordinator.controller_mac_address,
            watering_hours,
            scheduled_hour,
            current_slot_index,
            total_slots_today,
        )

        expected_rain_today_mm = self.coordinator.rain_total_amount_forecasted_today

        for station_key, scheduled_minutes in stations.items():
            try:
                scheduled_minutes = int(scheduled_minutes)
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "%s - Invalid scheduled minutes for %s: %s",
                    self.coordinator.controller_mac_address,
                    station_key,
                    scheduled_minutes,
                )
                continue

            if scheduled_minutes <= 0:
                continue

            station_id = int(station_key.replace("station_", "").replace("_minutes", ""))

            flow_rate = self.coordinator.water_flow_rate[station_id - 1]
            area = self.coordinator.station_areas[station_id - 1] or 1
            already_applied_mm = self.coordinator.sprinkle_total_amount_today[station_id - 1]

            plan = planning.calculate_station_slot_irrigation_plan(
                station_id=station_id,
                scheduled_minutes=scheduled_minutes,
                current_slot_index=current_slot_index,
                flow_rate=flow_rate,
                area=area,
                already_applied_mm=already_applied_mm,
                expected_rain_today_mm=expected_rain_today_mm,
            )

            if plan.mm_per_minute <= 0:
                _LOGGER.warning(
                    "%s - Invalid mm per minute for station %s: flow_rate=%s, area=%s",
                    self.coordinator.controller_mac_address,
                    station_id,
                    flow_rate,
                    area,
                )
                continue

            if plan.amount_to_apply_mm <= 0:
                _LOGGER.info(
                    "%s - Station %s does not need watering for this slot. "
                    "PlannedUntilSlot=%.2fmm, Applied=%.2fmm, ExpectedRain=%.2fmm",
                    self.coordinator.controller_mac_address,
                    station_id,
                    plan.planned_until_slot_mm,
                    plan.already_applied_mm,
                    plan.expected_rain_today_mm,
                )
                continue

            if plan.minutes_needed > 0:
                _LOGGER.info(
                    "%s - Station %s will irrigate for %s min to apply %.2fmm "
                    "(slot=%s/%s, planned_per_slot=%.2fmm, planned_until_slot=%.2fmm, "
                    "applied=%.2fmm, expected_rain=%.2fmm, mm/min=%.2f)",
                    self.coordinator.controller_mac_address,
                    station_id,
                    plan.minutes_needed,
                    plan.amount_to_apply_mm,
                    current_slot_index,
                    total_slots_today,
                    plan.planned_per_slot_mm,
                    plan.planned_until_slot_mm,
                    plan.already_applied_mm,
                    plan.expected_rain_today_mm,
                    plan.mm_per_minute,
                )

                await self.start_irrigation(
                    station_id,
                    plan.minutes_needed,
                    source="automatic",
                )

    async def start_irrigation(
        self,
        station: int,
        minutes: int | None = None,
        *,
        source: str = "manual",
    ) -> None:
        """Start irrigation on a station."""
        duration = int(
            minutes if minutes is not None else self.coordinator.irrigation_manual_duration
        )
        now = dt_util.now()
        end_at = now + timedelta(minutes=duration)

        _LOGGER.info(
            "%s - Going to start watering on station %s for %s minutes. Source=%s",
            self.coordinator.controller_mac_address,
            station,
            duration,
            source,
        )

        self.coordinator.irrigation_stop_event.clear()

        self.coordinator._set_current_irrigation(
            station=station,
            source=source,
            duration_minutes=duration,
            start_at=now,
            end_at=end_at,
        )

        try:
            if self.coordinator._is_switch_control_method():
                await self.coordinator.api.turn_on_station_switch(station)

                self.coordinator.active_irrigation = ActiveIrrigationState.create(
                    station=station,
                    source=source,
                    duration_minutes=duration,
                    station_name=self.coordinator._get_station_name(station),
                    start_at=now,
                    end_at=end_at,
                )

                await self.coordinator.save_persistent_data()

                self.coordinator.stations[station - 1].state = "Sprinkling"

                await self.coordinator.station_switch_tracker.sync()

                self.hass.async_create_task(
                    self.stop_irrigation_after_delay(
                        int(station),
                        int(duration) * 60,
                    )
                )
            else:
                await self.coordinator.api.sprinkle_station(station, duration)
                self.coordinator.stations[station - 1].state = "Sprinkling"

        except APIConnectionError:
            _LOGGER.error(
                "%s - Failed due to connection error.",
                self.coordinator.controller_mac_address,
            )
            self.coordinator._clear_current_irrigation(station)
            return

        data = await self.coordinator.async_update_all_sensors()

        if data is not None:
            self.coordinator.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                "%s - async_update_all_sensors() returned None, skipping update.",
                self.coordinator.controller_mac_address,
            )

        for _ in range(duration * 60):
            if self.coordinator.irrigation_stop_event.is_set():
                _LOGGER.info(
                    "%s - Irrigation cancellation triggered.",
                    self.coordinator.controller_mac_address,
                )
                break

            await sleep(1)

            self.coordinator.total_water_consumption += (
                self.coordinator.water_flow_rate[station - 1] / 60
            )

            flow_rate = self.coordinator.water_flow_rate[station - 1]
            area = self.coordinator.station_areas[station - 1] or 1
            mm_per_minute = flow_rate / area

            self.coordinator.sprinkle_total_amount_today[station - 1] += mm_per_minute / 60

        else:
            _LOGGER.info(
                "%s - Finished watering on station %s.",
                self.coordinator.controller_mac_address,
                station,
            )

        if self.coordinator._is_switch_control_method():
            try:
                await self.coordinator.api.turn_off_station_switch(station)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "%s - Failed turning off station switch %s",
                    self.coordinator.controller_mac_address,
                    station,
                    exc_info=True,
                )

            self.coordinator.active_irrigation = None
            await self.coordinator.save_persistent_data()

            await self.coordinator.station_switch_tracker.sync()

            switch_entity_id = self.coordinator.station_switch_tracker.get_station_switch_entity_id(
                station
            )
            switch_state = self.hass.states.get(switch_entity_id) if switch_entity_id else None

            if switch_state is None or switch_state.state != STATE_ON:
                self.coordinator.stations[station - 1].state = "Stopped"
                self.coordinator._clear_current_irrigation(station)

        else:
            self.coordinator.stations[station - 1].state = "Stopped"
            self.coordinator._clear_current_irrigation(station)

        self.coordinator.last_sprinkle = dt_util.now()

        data = await self.coordinator.async_update_all_sensors()

        if data is not None:
            self.coordinator.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                "%s - async_update_all_sensors() returned None, skipping update.",
                self.coordinator.controller_mac_address,
            )

    async def stop_irrigation(self) -> None:
        """Stop irrigation."""
        _LOGGER.info(
            "%s - Stopping watering...",
            self.coordinator.controller_mac_address,
        )

        try:
            if self.coordinator._is_switch_control_method():
                await self.coordinator.api.turn_off_all_station_switches()
            else:
                await self.coordinator.api.stop_sprinkle()
        except APIConnectionError:
            _LOGGER.error(
                "%s - Failed due to connection error.",
                self.coordinator.controller_mac_address,
            )
            return

        self.coordinator.irrigation_stop_event.set()

        if self.coordinator._is_switch_control_method():
            self.coordinator.active_irrigation = None
            await self.coordinator.save_persistent_data()

        self.coordinator._clear_current_irrigation()

        for station_id in range(1, self.coordinator.num_stations + 1):
            self.coordinator.stations[station_id - 1].state = "Stopped"

        _LOGGER.info(
            "%s - Stopped watering.",
            self.coordinator.controller_mac_address,
        )

        data = await self.coordinator.async_update_all_sensors()

        if data is not None:
            self.coordinator.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                "%s - async_update_all_sensors() returned None after stopping watering.",
                self.coordinator.controller_mac_address,
            )

    async def turn_controller_on(self) -> None:
        """Turn irrigation controller on."""
        _LOGGER.info(
            "%s - Turning irrigation controller on...",
            self.coordinator.controller_mac_address,
        )

        try:
            await self.coordinator.api.turn_on()
        except APIConnectionError:
            _LOGGER.error(
                "%s - Failed due to connection error.",
                self.coordinator.controller_mac_address,
            )
            return

        self.coordinator.controller.state = "On"

        data = await self.coordinator.async_update_all_sensors()
        self.coordinator.async_set_updated_data(data)

        _LOGGER.info(
            "%s - Irrigation controller turned on.",
            self.coordinator.controller_mac_address,
        )

    async def turn_controller_off(self) -> None:
        """Turn irrigation controller off."""
        _LOGGER.info(
            "%s - Turning irrigation controller off..",
            self.coordinator.controller_mac_address,
        )

        try:
            await self.coordinator.api.turn_off()
        except APIConnectionError:
            _LOGGER.error(
                "%s - Failed due to connection error.",
                self.coordinator.controller_mac_address,
            )
            return

        self.coordinator.controller.state = "Off"

        data = await self.coordinator.async_update_all_sensors()
        self.coordinator.async_set_updated_data(data)

        _LOGGER.info(
            "%s - Irrigation controller turned off.",
            self.coordinator.controller_mac_address,
        )