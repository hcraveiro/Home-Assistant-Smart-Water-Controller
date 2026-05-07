"""Irrigation planning helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .util import parse_time_string

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StationSlotIrrigationPlan:
    """Calculated irrigation plan for a station in the current slot."""

    station_id: int
    minutes_needed: int
    amount_to_apply_mm: float
    mm_per_minute: float
    planned_per_slot_mm: float
    planned_until_slot_mm: float
    already_applied_mm: float
    expected_rain_today_mm: float


def get_valid_watering_hours(
    month_config: dict[str, Any],
    *,
    log_prefix: str = "",
) -> list[str]:
    """Return valid watering hours sorted by time."""
    valid_hours: list[str] = []

    for hour in month_config.get("hours", []) or []:
        if not hour:
            continue

        try:
            parse_time_string(hour)
            valid_hours.append(hour)
        except ValueError:
            _LOGGER.error("%sInvalid hour format: %s", log_prefix, hour)

    return sorted(valid_hours, key=lambda value: parse_time_string(value))


def get_future_watering_slots_for_day(
    day: date,
    month_config: dict[str, Any],
    *,
    now: datetime | None = None,
    log_prefix: str = "",
) -> list[tuple[str, datetime]]:
    """Return future watering slots for the provided day."""
    now = now or dt_util.now()
    future_slots: list[tuple[str, datetime]] = []

    for hour in get_valid_watering_hours(month_config, log_prefix=log_prefix):
        watering_datetime = dt_util.as_local(datetime.combine(day, parse_time_string(hour)))

        if watering_datetime > now:
            future_slots.append((hour, watering_datetime))

    return future_slots


def calculate_mm_per_minute(flow_rate: float, area: float | int | None) -> float:
    """Calculate how many millimeters are applied per minute."""
    safe_area = area or 1

    try:
        return float(flow_rate) / float(safe_area)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def calculate_daily_station_target_mm(
    *,
    scheduled_minutes: int,
    occurrences: int,
    flow_rate: float,
    area: float | int | None,
    already_applied_mm: float = 0.0,
) -> float:
    """Calculate the daily target for a station."""
    if scheduled_minutes <= 0 or occurrences <= 0:
        return round(float(already_applied_mm), 2)

    mm_per_minute = calculate_mm_per_minute(flow_rate, area)

    if mm_per_minute <= 0:
        return round(float(already_applied_mm), 2)

    scheduled_mm = mm_per_minute * scheduled_minutes * occurrences
    return round(float(already_applied_mm) + scheduled_mm, 2)


def calculate_sprinkle_target_amounts(
    *,
    schedule: list[dict[str, Any]] | None,
    num_stations: int,
    water_flow_rate: list[float],
    station_areas: list[float],
    sprinkle_total_amount_today: list[float],
    only_future_slots: bool = False,
    include_already_applied: bool = False,
    now: datetime | None = None,
    log_prefix: str = "",
) -> list[float]:
    """Calculate today's target sprinkle amount per station."""
    target = [0.0] * num_stations

    if not schedule:
        _LOGGER.debug("%sSchedule not initialized. Target amounts: %s", log_prefix, target)
        return target

    now = now or dt_util.now()
    today = now.date()
    current_month_index = today.month - 1
    month_config = schedule[current_month_index]

    if not month_config:
        _LOGGER.debug("%sNo month configuration. Target amounts: %s", log_prefix, target)
        return target

    watering_hours = get_valid_watering_hours(month_config, log_prefix=log_prefix)

    if only_future_slots:
        future_slots = get_future_watering_slots_for_day(
            today,
            month_config,
            now=now,
            log_prefix=log_prefix,
        )
        watering_hours = [hour for hour, _watering_datetime in future_slots]

    if not watering_hours:
        if include_already_applied:
            target = [
                round(float(sprinkle_total_amount_today[station_id - 1]), 2)
                for station_id in range(1, num_stations + 1)
            ]

        _LOGGER.debug("%sNo watering hours. Target amounts: %s", log_prefix, target)
        return target

    occurrences = len(watering_hours)
    stations = month_config.get("stations", {})

    for station_id in range(1, num_stations + 1):
        key = f"station_{station_id}_minutes"

        try:
            scheduled_minutes = int(stations.get(key, 0))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "%sInvalid scheduled minutes for station %s: %s",
                log_prefix,
                station_id,
                stations.get(key, 0),
            )
            scheduled_minutes = 0

        already_applied = 0.0

        if include_already_applied:
            already_applied = float(sprinkle_total_amount_today[station_id - 1])

        flow_rate = water_flow_rate[station_id - 1]
        area = station_areas[station_id - 1] if station_id - 1 < len(station_areas) else 1

        target[station_id - 1] = calculate_daily_station_target_mm(
            scheduled_minutes=scheduled_minutes,
            occurrences=occurrences,
            flow_rate=flow_rate,
            area=area,
            already_applied_mm=already_applied,
        )

    _LOGGER.debug(
        "%sSprinkle target amounts: %s only_future_slots=%s include_already_applied=%s",
        log_prefix,
        target,
        only_future_slots,
        include_already_applied,
    )

    return target


def calculate_remaining_sprinkle_today(
    *,
    target_mm: float,
    applied_mm: float,
    expected_rain_today_mm: float,
) -> float:
    """Calculate remaining sprinkle amount needed today."""
    remaining_mm = max(0.0, float(target_mm) - (float(applied_mm) + float(expected_rain_today_mm)))
    return round(remaining_mm, 2)


def station_needs_watering_today(
    *,
    target_mm: float,
    applied_mm: float,
    expected_rain_today_mm: float,
) -> bool:
    """Return True if a station still needs watering today."""
    return calculate_remaining_sprinkle_today(
        target_mm=target_mm,
        applied_mm=applied_mm,
        expected_rain_today_mm=expected_rain_today_mm,
    ) > 0


def get_current_slot_index(
    watering_hours: list[str],
    *,
    scheduled_hour: str | None,
    now: datetime | None = None,
    log_prefix: str = "",
) -> int:
    """Return the current watering slot index for the active watering run."""
    now = now or dt_util.now()
    reference_time = now.time()

    if scheduled_hour:
        try:
            reference_time = parse_time_string(scheduled_hour)
        except ValueError:
            _LOGGER.error("%sInvalid scheduled hour received: %s", log_prefix, scheduled_hour)

    reference_hm = (reference_time.hour, reference_time.minute)
    current_slot_index = 0

    for hour in watering_hours:
        try:
            watering_time = parse_time_string(hour)

            if (watering_time.hour, watering_time.minute) <= reference_hm:
                current_slot_index += 1
        except ValueError:
            _LOGGER.error("%sInvalid hour format: %s", log_prefix, hour)

    return max(1, current_slot_index)


def calculate_station_slot_irrigation_plan(
    *,
    station_id: int,
    scheduled_minutes: int,
    current_slot_index: int,
    flow_rate: float,
    area: float | int | None,
    already_applied_mm: float,
    expected_rain_today_mm: float,
) -> StationSlotIrrigationPlan:
    """Calculate how much a station should water in the current slot."""
    mm_per_minute = calculate_mm_per_minute(flow_rate, area)

    if mm_per_minute <= 0:
        return StationSlotIrrigationPlan(
            station_id=station_id,
            minutes_needed=0,
            amount_to_apply_mm=0.0,
            mm_per_minute=mm_per_minute,
            planned_per_slot_mm=0.0,
            planned_until_slot_mm=0.0,
            already_applied_mm=already_applied_mm,
            expected_rain_today_mm=expected_rain_today_mm,
        )

    planned_per_slot_mm = scheduled_minutes * mm_per_minute
    planned_until_slot_mm = planned_per_slot_mm * current_slot_index

    amount_to_apply_now_mm = max(
        0.0,
        planned_until_slot_mm - (already_applied_mm + expected_rain_today_mm),
    )

    minutes_needed = int((amount_to_apply_now_mm / mm_per_minute) + 0.999)

    return StationSlotIrrigationPlan(
        station_id=station_id,
        minutes_needed=minutes_needed,
        amount_to_apply_mm=amount_to_apply_now_mm,
        mm_per_minute=mm_per_minute,
        planned_per_slot_mm=planned_per_slot_mm,
        planned_until_slot_mm=planned_until_slot_mm,
        already_applied_mm=already_applied_mm,
        expected_rain_today_mm=expected_rain_today_mm,
    )