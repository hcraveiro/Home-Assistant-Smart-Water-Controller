"""Serialized irrigation sessions and persistent, confirmed switch shutdown."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.util import dt as dt_util

from ..errors import APIConnectionError
from ..helpers import planning
from .active_irrigation import ActiveIrrigationState

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)
CONFIRM_TIMEOUT = 10.0
RETRY_INTERVAL = 5.0
SAMPLE_INTERVAL = 1.0


@dataclass(eq=False)
class _Session:
    """One operation owns its cancellation, deadline and cleanup."""

    irrigation: ActiveIrrigationState
    deadline: float
    last_sample: float
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    interrupted: bool = False


class IrrigationRunner:
    """Own all hardware transitions; obsolete workers never send commands."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._lock = asyncio.Lock()
        self._session: _Session | None = None
        self._generation = 0
        self._cycle_running = False
        self._retry_task: asyncio.Task | None = None
        self._workers: set[asyncio.Task] = set()
        self._closing = False
        self._starting_station: int | None = None
        self._start_interrupted = False
        self._unavailable_logged: set[int] = set()
        # Whether this run has already told the controller to turn on. See the
        # comment in start_irrigation(); reset by turn_controller_off().
        self._controller_on_confirmed = False

    def _switch_state(self, station: int) -> str | None:
        entity_id = self.coordinator.station_switch_tracker.get_station_switch_entity_id(station)
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.state if state else None

    def _all_stations(self) -> list[int]:
        return list(range(1, self.coordinator.num_stations + 1))

    async def _publish(self) -> None:
        if self.coordinator._is_switch_control_method():
            for station in self._all_stations():
                state = self._switch_state(station)
                if state not in (STATE_ON, STATE_OFF):
                    status = "Unavailable"
                elif station in self.coordinator.pending_stop_stations:
                    status = "Stopping"
                else:
                    status = "Sprinkling" if state == STATE_ON else "Stopped"
                self.coordinator.stations[station - 1].state = status
            if self._session is None and not self.coordinator.pending_stop_stations and self._starting_station is None:
                external_station = next((i for i in self._all_stations() if self._switch_state(i) == STATE_ON), None)
                if external_station is not None and self.coordinator.current_irrigation is None:
                    self.coordinator.current_irrigation = ActiveIrrigationState.create(
                        station=external_station, source="external", duration_minutes=None,
                        station_name=self.coordinator._get_station_name(external_station),
                    )
                elif external_station is None:
                    self.coordinator._clear_current_irrigation()
        data = await self.coordinator.async_update_all_sensors()
        if data is not None:
            self.coordinator.async_set_updated_data(data)

    async def _confirm_switch(self, station: int, expected: str) -> bool:
        """Wait for an acknowledgement, never treating missing state as OFF."""
        deadline = asyncio.get_running_loop().time() + CONFIRM_TIMEOUT
        while True:
            state = self._switch_state(station)
            if state == expected:
                return True
            if state not in (STATE_ON, STATE_OFF):
                return False
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.1)

    def _invalidate_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.wake.set()

    def _ensure_retry(self) -> None:
        if not self._closing and (self._retry_task is None or self._retry_task.done()):
            self._retry_task = self.hass.async_create_task(self._retry_pending_stops())

    async def _retry_pending_stops(self) -> None:
        try:
            while self.coordinator.pending_stop_stations and not self._closing:
                await asyncio.sleep(RETRY_INTERVAL)
                async with self._lock:
                    await self._attempt_pending_stops_locked()
                    await self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("%s - Pending shutdown retry failed; shutdown remains persisted.",
                              self.coordinator.controller_mac_address)
        finally:
            self._retry_task = None
            if self.coordinator.pending_stop_stations and not self._closing:
                self._ensure_retry()

    async def _attempt_pending_stops_locked(self) -> bool:
        """Only a successful command AND an OFF state discharge a stop intent."""
        for station in list(self.coordinator.pending_stop_stations):
            if self._switch_state(station) not in (STATE_ON, STATE_OFF):
                if station not in self._unavailable_logged:
                    _LOGGER.error("%s - Stop pending for unavailable station %s (%s).",
                                  self.coordinator.controller_mac_address, station,
                                  self.coordinator.station_switch_tracker.get_station_switch_entity_id(station))
                    self._unavailable_logged.add(station)
                continue
            self._unavailable_logged.discard(station)
            try:
                async with asyncio.timeout(CONFIRM_TIMEOUT):
                    await self.coordinator.api.turn_off_station_switch(station)
                    if not await self._confirm_switch(station, STATE_OFF):
                        raise APIConnectionError("Switch did not confirm OFF")
            except Exception as exc:
                _LOGGER.error("%s - Stop not confirmed for station %s (%s): %s; will retry.",
                              self.coordinator.controller_mac_address, station,
                              self.coordinator.station_switch_tracker.get_station_switch_entity_id(station), exc)
                continue
            self.coordinator.pending_stop_stations.remove(station)
            # Persist each confirmed stop without losing any unresolved stations.
            await self.coordinator.save_persistent_data()

        if not self.coordinator.pending_stop_stations:
            self.coordinator.active_irrigation = None
            self.coordinator._clear_current_irrigation()
        else:
            self._ensure_retry()
        await self.coordinator.save_persistent_data()
        return not self.coordinator.pending_stop_stations

    async def _stop_locked(self, stations: list[int], reason: str) -> bool:
        """Invalidate ownership before awaiting hardware or storage."""
        self._invalidate_session()
        _LOGGER.info("%s - Stopping irrigation: %s", self.coordinator.controller_mac_address, reason)
        if self.coordinator._is_switch_control_method():
            self.coordinator.pending_stop_stations = sorted(
                set(self.coordinator.pending_stop_stations) | set(stations)
            )
            # Persist BEFORE attempting I/O; failures or restart must not lose the stop.
            await self.coordinator.save_persistent_data()
            stopped = await self._attempt_pending_stops_locked()
        else:
            try:
                await self.coordinator.api.stop_sprinkle()
            except Exception:
                _LOGGER.exception("%s - Controller stop failed.", self.coordinator.controller_mac_address)
                return False
            self.coordinator.active_irrigation = None
            self.coordinator._clear_current_irrigation()
            for station in self.coordinator.stations:
                station.state = "Stopped"
            stopped = True
        await self._publish()
        return stopped

    async def restore_active_irrigation(self) -> None:
        """Stop interrupted sessions on startup; never resume an uncertain valve."""
        if not self.coordinator._is_switch_control_method():
            return
        active = self.coordinator.active_irrigation
        if active is not None or self.coordinator.pending_stop_stations:
            async with self._lock:
                await self._stop_locked([active.station] if active else [], "recovering interrupted session")

    def notify_switch_change(self, station: int, state: str | None) -> None:
        """Latch loss/OFF immediately, even if a quick ON follows before the lock."""
        if self._starting_station == station and state not in (STATE_ON, STATE_OFF):
            self._start_interrupted = True
        session = self._session
        if session is not None and session.irrigation.station == station and state != STATE_ON:
            session.interrupted = True
            session.wake.set()

    async def handle_switch_states(self) -> None:
        """React immediately to loss, external OFF and recovery notifications."""
        if self._closing or not self.coordinator._is_switch_control_method():
            return
        async with self._lock:
            if self.coordinator.pending_stop_stations:
                await self._attempt_pending_stops_locked()
            session = self._session
            if session is not None:
                station = session.irrigation.station
                state = self._switch_state(station)
                if state != STATE_ON or session.interrupted:
                    self._generation += 1
                    _LOGGER.error("%s - Station %s (%s) changed to %s during irrigation; stopping session. source=%s, interrupted=%s; remaining scheduled stations will be skipped.",
                                  self.coordinator.controller_mac_address, station,
                                  self.coordinator.station_switch_tracker.get_station_switch_entity_id(station), state,
                                  session.irrigation.source, session.interrupted)
                    await self._stop_locked([station], "switch no longer confirms ON")
                else:
                    session.wake.set()

    async def run_watering_cycle(self, scheduled_hour: str | None = None) -> None:
        """Reject overlapping cycles and abandon a sequence replaced manually."""
        if self._closing or self._cycle_running or self._session is not None:
            _LOGGER.warning("%s - Skipping overlapping scheduled cycle.", self.coordinator.controller_mac_address)
            return
        self._cycle_running = True
        generation = self._generation
        try:
            await self._run_watering_cycle(scheduled_hour, generation)
        finally:
            self._cycle_running = False

    async def _run_watering_cycle(self, scheduled_hour: str | None, generation: int) -> None:
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

        if getattr(self.coordinator, "weather_api", None) and not getattr(self.coordinator, "weather_forecast_available", True):
            _LOGGER.warning(
                "%s - Today's weather forecast is unavailable; proceeding with scheduled watering without a forecast rain deduction.",
                self.coordinator.controller_mac_address,
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
                daily_target_mm=self.coordinator.sprinkle_target_amount_today[station_id - 1],
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

                if generation != self._generation:
                    _LOGGER.warning("%s - Scheduled watering cancelled before station %s: generation changed (%s -> %s); remaining stations skipped.",
                                    self.coordinator.controller_mac_address, station_id, generation, self._generation)
                    return
                completed = await self.start_irrigation(
                    station_id,
                    plan.minutes_needed,
                    source="automatic",
                    expected_generation=generation,
                    run_seconds=plan.amount_to_apply_mm / plan.mm_per_minute * 60,
                )
                if not completed:
                    _LOGGER.warning("%s - Scheduled watering aborted at station %s: incomplete irrigation; remaining stations skipped. switch_state=%s, pending_stops=%s.",
                                    self.coordinator.controller_mac_address, station_id, self._switch_state(station_id), self.coordinator.pending_stop_stations)
                    return

    async def start_irrigation(
        self, station: int, minutes: int | None = None, *, source: str = "manual",
        expected_generation: int | None = None, run_seconds: float | None = None,
    ) -> bool:
        """Serialize replacement and start; wait for this session only."""
        duration = int(minutes if minutes is not None else self.coordinator.irrigation_manual_duration)
        seconds = duration * 60 if run_seconds is None else min(duration * 60, run_seconds)
        if duration <= 0 or seconds <= 0 or station not in self._all_stations():
            raise ValueError("Station and duration must be valid positive values")
        async with self._lock:
            if self._closing or (expected_generation is not None and expected_generation != self._generation):
                return False
            if source != "automatic":
                self._generation += 1
            if self.coordinator.pending_stop_stations:
                await self._attempt_pending_stops_locked()
                if self.coordinator.pending_stop_stations:
                    _LOGGER.error("%s - Start refused: controller shutdown is still pending.",
                                  self.coordinator.controller_mac_address)
                    return False
            if self._session is not None or self.coordinator.current_irrigation is not None:
                if not await self._stop_locked(self._all_stations(), "replacing previous session"):
                    return False
            if self.coordinator._is_switch_control_method():
                # Other zones must be known too: a disconnected controller may
                # still be watering and a Rain Bird OFF affects all its zones.
                for station_id in self._all_stations():
                    state = self._switch_state(station_id)
                    if state not in (STATE_ON, STATE_OFF):
                        _LOGGER.error("%s - Start refused: station %s switch %s is %s.",
                                      self.coordinator.controller_mac_address, station_id,
                                      self.coordinator.station_switch_tracker.get_station_switch_entity_id(station_id), state)
                        await self._publish()
                        return False
                if any(self._switch_state(i) == STATE_ON for i in self._all_stations()):
                    if not await self._stop_locked(self._all_stations(), "stopping externally active valves"):
                        return False

            now = dt_util.now()
            irrigation = ActiveIrrigationState.create(
                station=station, source=source, duration_minutes=duration,
                station_name=self.coordinator._get_station_name(station), start_at=now,
                end_at=now + timedelta(seconds=seconds),
            )
            # Store the start intent first: a timeout can occur after the
            # controller has accepted ON, so even failed starts need shutdown.
            self.coordinator.active_irrigation = irrigation
            await self.coordinator.save_persistent_data()
            self._starting_station = station
            self._start_interrupted = False
            try:
                if self.coordinator._is_switch_control_method():
                    async with asyncio.timeout(CONFIRM_TIMEOUT):
                        await self.coordinator.api.turn_on_station_switch(station, duration)
                        if not await self._confirm_switch(station, STATE_ON) or self._start_interrupted:
                            raise APIConnectionError("Switch did not confirm ON")
                else:
                    # A Solem controller only waters when its own master switch
                    # is on, but it acknowledges a sprinkle over BLE either way:
                    # ack received, no error, no water. And the On/Off state
                    # this integration keeps in memory defaults to "On" at every
                    # Home Assistant restart, so it cannot be used to find out.
                    #
                    # So turn_on() goes out once per run before the first
                    # watering: it is idempotent and harmless if the controller
                    # is already on. Sending it before every command instead
                    # would mean two BLE connections per command on a
                    # single-user link, which is what made commands fail in the
                    # first place.
                    if not self._controller_on_confirmed:
                        try:
                            await self.coordinator.api.turn_on()
                        except APIConnectionError as exc:
                            # Not fatal: the controller may well be on already.
                            # Watering anyway is the lesser risk, and the log
                            # says which of the two cases this was.
                            _LOGGER.warning(
                                "%s - Could not confirm the controller is on before watering station %s (%s); watering anyway.",
                                self.coordinator.controller_mac_address, station, exc,
                            )
                        else:
                            self.coordinator.controller.state = "On"
                            self._controller_on_confirmed = True
                    await self.coordinator.api.sprinkle_station(station, duration)
            except asyncio.CancelledError:
                await self._stop_locked([station], "start task cancelled")
                raise
            except Exception as exc:
                _LOGGER.error("%s - Station %s start failed: %s", self.coordinator.controller_mac_address, station, exc)
                await self._stop_locked([station], "start was not confirmed")
                return False
            finally:
                self._starting_station = None

            started = asyncio.get_running_loop().time()
            irrigation.start_at = dt_util.now()
            irrigation.end_at = irrigation.start_at + timedelta(seconds=seconds)
            session = _Session(irrigation, started + seconds, started)
            self._session = session
            self.coordinator.current_irrigation = irrigation
            self.coordinator.stations[station - 1].state = "Sprinkling"
            _LOGGER.info("%s - Started session on station %s for %s minutes. Source=%s",
                         self.coordinator.controller_mac_address, station, duration, source)
            # Track the single worker for unload; no independent auto-stop timer.
            worker = self.hass.async_create_task(self._run_session(session))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
            await self._publish()
        return await asyncio.shield(worker)

    async def _run_session(self, session: _Session) -> bool:
        """Account confirmed ON time and stop only while this session owns I/O."""
        station = session.irrigation.station
        try:
            while True:
                remaining = max(0.0, session.deadline - asyncio.get_running_loop().time())
                try:
                    await asyncio.wait_for(session.wake.wait(), min(SAMPLE_INTERVAL, remaining))
                except TimeoutError:
                    pass
                async with self._lock:
                    session.wake.clear()
                    if self._session is not session:
                        return False
                    if self.coordinator._is_switch_control_method() and (self._switch_state(station) != STATE_ON or session.interrupted):
                        _LOGGER.warning("%s - Station %s unexpectedly interrupted: switch=%s, state=%s, latched_interruption=%s, source=%s, remaining_seconds=%.1f; aborting sequence.",
                                        self.coordinator.controller_mac_address, station,
                                        self.coordinator.station_switch_tracker.get_station_switch_entity_id(station),
                                        self._switch_state(station), session.interrupted, session.irrigation.source,
                                        max(0.0, session.deadline - asyncio.get_running_loop().time()))
                        self._generation += 1
                        await self._stop_locked([station], "switch stopped or became unavailable")
                        return False
                    now = min(asyncio.get_running_loop().time(), session.deadline)
                    elapsed = max(0.0, now - session.last_sample)
                    session.last_sample = now
                    liters = self.coordinator.water_flow_rate[station - 1] * elapsed / 60
                    area = self.coordinator.station_areas[station - 1] or 1
                    self.coordinator.total_water_consumption += liters
                    self.coordinator.sprinkle_total_amount_today[station - 1] += liters / area
                    if now >= session.deadline:
                        self.coordinator.last_sprinkle = dt_util.now()
                        return await self._stop_locked([station], "session duration completed")
                    self.coordinator.last_sprinkle = dt_util.now()
        except asyncio.CancelledError:
            async with self._lock:
                if self._session is session:
                    await self._stop_locked([station], "session worker cancelled")
            raise
        except Exception:
            _LOGGER.exception("%s - Irrigation session failed.", self.coordinator.controller_mac_address)
            async with self._lock:
                if self._session is session:
                    await self._stop_locked([station], "session worker failed")
            return False

    async def stop_irrigation(self) -> bool:
        """Explicit stop cancels the entire scheduled sequence too."""
        async with self._lock:
            self._generation += 1
            return await self._stop_locked(self._all_stations(), "stop requested")

    async def async_close(self) -> None:
        """Stop before unloading and dispose of all worker/retry tasks."""
        self._closing = True
        async with self._lock:
            self._generation += 1
            if self._session is not None or self.coordinator.active_irrigation is not None:
                await self._stop_locked(self._all_stations(), "integration unloading")
        tasks = list(self._workers)
        if self._retry_task is not None:
            self._retry_task.cancel()
            tasks.append(self._retry_task)
        # Invalidated workers are already awake and return without hardware I/O.
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

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
        self._controller_on_confirmed = True

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
        # Re-arm the preventive turn_on: after a deliberate Off, the next
        # sprinkle has to switch the controller back on or no water comes out.
        self._controller_on_confirmed = False

        data = await self.coordinator.async_update_all_sensors()
        self.coordinator.async_set_updated_data(data)

        _LOGGER.info(
            "%s - Irrigation controller turned off.",
            self.coordinator.controller_mac_address,
        )