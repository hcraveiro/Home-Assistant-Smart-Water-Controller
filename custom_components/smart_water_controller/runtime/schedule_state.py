"""Schedule state helpers for Smart Water Controller."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from ..helpers import planning
from ..helpers.util import ensure_aware, parse_time_string

if TYPE_CHECKING:
    from ..coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


class ScheduleStateManager:
    """Manage schedule-related state and calculations."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator) -> None:
        """Initialize schedule state manager."""
        self.coordinator = coordinator

    def get_valid_watering_hours(self, month_config: dict[str, Any]) -> list[str]:
        """Return valid watering hours sorted by time."""
        return planning.get_valid_watering_hours(
            month_config,
            log_prefix=f"{self.coordinator.controller_mac_address} - ",
        )

    def get_future_watering_slots_for_day(
        self,
        day,
        month_config: dict[str, Any],
    ) -> list[tuple[str, datetime]]:
        """Return future watering slots for the provided day."""
        return planning.get_future_watering_slots_for_day(
            day,
            month_config,
            now=dt_util.now(),
            log_prefix=f"{self.coordinator.controller_mac_address} - ",
        )

    def latest_rain_or_sprinkle_event(self) -> datetime | None:
        """Return the latest rain or sprinkle event."""
        last_rain = ensure_aware(self.coordinator.last_rain)
        last_sprinkle = ensure_aware(self.coordinator.last_sprinkle)

        events = [event for event in (last_rain, last_sprinkle) if event is not None]

        if not events:
            return None

        return max(events)

    def recent_event_blocks_today(self, interval_days: int) -> bool:
        """Check if a previous event should block watering today.

        Events from today do not block today's remaining watering slots.
        Rain amount and expected rain amount are handled by the remaining water calculation.
        """
        if interval_days <= 0:
            return False

        today = dt_util.now().date()
        latest_event = self.latest_rain_or_sprinkle_event()

        if latest_event is None:
            return False

        latest_event = ensure_aware(latest_event)

        if latest_event.date() == today:
            return False

        days_since_last_event = (today - latest_event.date()).days

        if days_since_last_event >= interval_days:
            return False

        _LOGGER.info(
            "%s - Last event was %s days ago. Interval of %s days not yet passed.",
            self.coordinator.controller_mac_address,
            days_since_last_event,
            interval_days,
        )

        return True

    async def calculate_sprinkle_target_amounts(
        self,
        *,
        only_future_slots: bool = False,
        include_already_applied: bool = False,
    ) -> list[float]:
        """Calculate today's target sprinkle amount per station."""
        if not self.coordinator.schedule:
            target = [0.0] * self.coordinator.num_stations
            _LOGGER.debug(
                "%s - Schedule not initialized. Target amounts: %s",
                self.coordinator.controller_mac_address,
                target,
            )
            return target

        today = dt_util.now().date()
        current_month_index = today.month - 1
        month_config = self.coordinator.schedule[current_month_index]

        if not month_config:
            target = [0.0] * self.coordinator.num_stations
            _LOGGER.debug(
                "%s - No month configuration. Target amounts: %s",
                self.coordinator.controller_mac_address,
                target,
            )
            return target

        interval_days = month_config.get("interval_days", 2)

        if not only_future_slots and self.recent_event_blocks_today(interval_days):
            target = [0.0] * self.coordinator.num_stations
            _LOGGER.debug(
                "%s - Recent event blocks today. Target amounts: %s",
                self.coordinator.controller_mac_address,
                target,
            )
            return target

        return planning.calculate_sprinkle_target_amounts(
            schedule=self.coordinator.schedule,
            num_stations=self.coordinator.num_stations,
            water_flow_rate=self.coordinator.water_flow_rate,
            station_areas=self.coordinator.station_areas,
            sprinkle_total_amount_today=self.coordinator.sprinkle_total_amount_today,
            only_future_slots=only_future_slots,
            include_already_applied=include_already_applied,
            now=dt_util.now(),
            log_prefix=f"{self.coordinator.controller_mac_address} - ",
        )

    def needs_watering_today(self) -> bool:
        """Check if any station still needs watering today."""
        for station_id in range(1, self.coordinator.num_stations + 1):
            target_mm = self.coordinator.sprinkle_target_amount_today[station_id - 1]
            applied_mm = self.coordinator.sprinkle_total_amount_today[station_id - 1]
            expected_rain_today_mm = self.coordinator.rain_total_amount_forecasted_today

            if planning.station_needs_watering_today(
                target_mm=target_mm,
                applied_mm=applied_mm,
                expected_rain_today_mm=expected_rain_today_mm,
            ):
                remaining_mm = planning.calculate_remaining_sprinkle_today(
                    target_mm=target_mm,
                    applied_mm=applied_mm,
                    expected_rain_today_mm=expected_rain_today_mm,
                )

                _LOGGER.debug(
                    "%s - Station %s needs more water: Target=%smm, Applied=%smm, "
                    "ExpectedRain=%smm, Remaining=%smm",
                    self.coordinator.controller_mac_address,
                    station_id,
                    target_mm,
                    applied_mm,
                    expected_rain_today_mm,
                    remaining_mm,
                )
                return True

        _LOGGER.debug(
            "%s - All stations have enough water today.",
            self.coordinator.controller_mac_address,
        )
        return False

    async def get_next_watering_date(self) -> datetime | None:
        """Get the next watering time considering today's remaining slots."""
        _LOGGER.debug(
            "%s - Determining next watering schedule...",
            self.coordinator.controller_mac_address,
        )

        if not self.coordinator.schedule:
            _LOGGER.debug(
                "%s - Schedule not initialized yet.",
                self.coordinator.controller_mac_address,
            )
            return None

        today = dt_util.now().date()
        current_month_index = today.month - 1

        month_config = self.coordinator.schedule[current_month_index]

        if month_config:
            interval_days = month_config.get("interval_days", 2)
            future_slots_today = self.get_future_watering_slots_for_day(today, month_config)

            if (
                future_slots_today
                and not self.recent_event_blocks_today(interval_days)
                and self.needs_watering_today()
            ):
                next_datetime = future_slots_today[0][1]
                _LOGGER.debug(
                    "%s - Next watering is today: %s",
                    self.coordinator.controller_mac_address,
                    next_datetime,
                )
                return next_datetime

        latest_event = self.latest_rain_or_sprinkle_event()

        if latest_event:
            latest_event = ensure_aware(latest_event)
            event_month_config = self.coordinator.schedule[latest_event.date().month - 1]
            interval_days = (
                event_month_config.get("interval_days", 2)
                if event_month_config
                else 1
            )
            next_watering_day = latest_event.date() + timedelta(days=max(1, interval_days))
        else:
            next_watering_day = today + timedelta(days=1)

        if next_watering_day <= today:
            next_watering_day = today + timedelta(days=1)

        for _ in range(370):
            day_config = self.coordinator.schedule[next_watering_day.month - 1]

            if day_config:
                watering_hours = self.get_valid_watering_hours(day_config)

                if watering_hours:
                    next_datetime = dt_util.as_local(
                        datetime.combine(next_watering_day, parse_time_string(watering_hours[0]))
                    )

                    _LOGGER.debug(
                        "%s - Next watering schedule determined: %s",
                        self.coordinator.controller_mac_address,
                        next_datetime,
                    )

                    return next_datetime

            next_watering_day += timedelta(days=1)

        _LOGGER.debug(
            "%s - No watering schedule found.",
            self.coordinator.controller_mac_address,
        )
        return None

    def calculate_forecasted_sprinkle_today(self, station_id: int) -> float:
        """Calculate the planned sprinkle amount for today for a station."""
        target_mm = self.coordinator.sprinkle_target_amount_today[station_id - 1]
        return round(max(0.0, target_mm), 2)

    def calculate_remaining_sprinkle_today(self, station_id: int) -> float:
        """Calculate the remaining sprinkle amount needed today for a station."""
        target_mm = self.coordinator.sprinkle_target_amount_today[station_id - 1]
        applied_mm = self.coordinator.sprinkle_total_amount_today[station_id - 1]
        expected_rain_mm = self.coordinator.rain_total_amount_forecasted_today

        return planning.calculate_remaining_sprinkle_today(
            target_mm=target_mm,
            applied_mm=applied_mm,
            expected_rain_today_mm=expected_rain_mm,
        )

    async def set_schedule(self, new_schedule) -> None:
        """Replace irrigation schedule from the frontend card."""
        _LOGGER.info(
            "%s - Updating schedule.",
            self.coordinator.controller_mac_address,
        )

        self.coordinator.schedule = new_schedule

        self.coordinator.sprinkle_target_amount_today = await self.calculate_sprinkle_target_amounts(
            only_future_slots=True,
            include_already_applied=True,
        )

        self.coordinator.forecasted_sprinkle_today = [
            self.calculate_forecasted_sprinkle_today(station_id)
            for station_id in range(1, self.coordinator.num_stations + 1)
        ]

        self.coordinator.remaining_sprinkle_today = [
            self.calculate_remaining_sprinkle_today(station_id)
            for station_id in range(1, self.coordinator.num_stations + 1)
        ]

        await self.coordinator.save_persistent_data()
        await self.coordinator.check_and_schedule_watering()

        data = await self.coordinator.async_update_all_sensors()

        if data is not None:
            self.coordinator.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                "%s - async_update_all_sensors() returned None after schedule update.",
                self.coordinator.controller_mac_address,
            )

        _LOGGER.info(
            "%s - Updated schedule.",
            self.coordinator.controller_mac_address,
        )

    async def initialize_schedule(self) -> None:
        """Initialize the schedule if not already set."""
        _LOGGER.info(
            "%s - Initializing schedule...",
            self.coordinator.controller_mac_address,
        )

        if not self.coordinator.schedule:
            _LOGGER.debug(
                "%s - No schedule found, creating a new one...",
                self.coordinator.controller_mac_address,
            )

            new_schedule = [
                {
                    "interval_days": 0,
                    "stations": {
                        f"station_{i+1}_minutes": 0
                        for i in range(self.coordinator.num_stations)
                    },
                    "hours": [],
                }
                for _ in range(12)
            ]

            self.coordinator.schedule = new_schedule
            await self.coordinator.save_persistent_data()
            return

        current_num_stations = len(next(iter(self.coordinator.schedule))["stations"])

        if current_num_stations != self.coordinator.num_stations:
            _LOGGER.debug(
                "%s - Updating schedule due to station count change.",
                self.coordinator.controller_mac_address,
            )

            for month_config in self.coordinator.schedule:
                current_stations = month_config.get("stations", {})
                new_station_keys = {
                    f"station_{i+1}_minutes"
                    for i in range(self.coordinator.num_stations)
                }

                for new_station in new_station_keys - set(current_stations.keys()):
                    month_config["stations"][new_station] = 0

                for old_station in set(current_stations.keys()) - new_station_keys:
                    del month_config["stations"][old_station]

            await self.coordinator.save_persistent_data()

        _LOGGER.info(
            "%s - Schedule initialized.",
            self.coordinator.controller_mac_address,
        )