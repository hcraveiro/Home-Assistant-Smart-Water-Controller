"""Entity data builders for Smart Water Controller."""

from __future__ import annotations

from typing import Any


def _stable_uid(coordinator: Any, device_id: str) -> str:
    """Return a stable unique id for an entity regardless of sensor ordering."""
    return f"{coordinator.controller_unique_prefix}_{device_id}"


def _station_label(coordinator: Any, station_id: int) -> str:
    """Return a station label."""
    if (
        isinstance(getattr(coordinator, "station_names", None), list)
        and len(coordinator.station_names) >= station_id
    ):
        return coordinator.station_names[station_id - 1]

    return f"Station {station_id}"


def build_controller_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build controller state data."""
    controller_device_id = coordinator.controller.device_id

    return [
        {
            "device_id": controller_device_id,
            "device_type": "STATE_SENSOR",
            "device_name": coordinator.controller.device_name,
            "device_uid": _stable_uid(coordinator, controller_device_id),
            "software_version": coordinator.controller.software_version,
            "state": coordinator.controller.state,
            "icon": coordinator.controller.icon,
            "last_reboot": coordinator.controller.last_reboot,
        }
    ]


def build_station_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build station state data."""
    data: list[dict[str, Any]] = []

    for station_id in range(1, coordinator.num_stations + 1):
        station = coordinator.stations[station_id - 1]
        station_device_id = station.device_id

        data.append(
            {
                "device_id": station_device_id,
                "device_type": "STATE_SENSOR",
                "device_name": station.device_name,
                "device_uid": _stable_uid(coordinator, station_device_id),
                "software_version": station.software_version,
                "state": station.state,
                "icon": station.icon,
                "last_reboot": station.last_reboot,
                "station_number": station_id,
            }
        )

    return data


def build_configuration_number_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build number entity data."""
    data: list[dict[str, Any]] = []

    manual_duration_device_id = f"{coordinator.controller_mac_address}_irrigation_manual_duration"
    data.append(
        {
            "device_id": manual_duration_device_id,
            "device_type": "IRRIGATION_DURATION_NUMBER",
            "device_name": "Irrigation Manual Duration",
            "device_uid": _stable_uid(coordinator, manual_duration_device_id),
            "software_version": "1.0",
            "value": coordinator.irrigation_manual_duration,
            "icon": "mdi:clock-time-five-outline",
            "last_reboot": None,
        }
    )

    for station_id in range(1, coordinator.num_stations + 1):
        station_label = _station_label(coordinator, station_id)
        water_flow_device_id = f"{coordinator.controller_mac_address}_water_flow_rate_{station_id}"

        data.append(
            {
                "device_id": water_flow_device_id,
                "device_type": "WATER_FLOW_NUMBER",
                "device_name": f"Water Flow Rate {station_label}",
                "device_uid": _stable_uid(coordinator, water_flow_device_id),
                "software_version": "1.0",
                "value": coordinator.water_flow_rate[station_id - 1],
                "icon": "mdi:water-pump",
                "last_reboot": None,
            }
        )

    return data


def build_button_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build button entity data."""
    data: list[dict[str, Any]] = []

    for station_id in range(1, coordinator.num_stations + 1):
        station_label = _station_label(coordinator, station_id)
        sprinkle_button_device_id = (
            f"{coordinator.controller_mac_address}_irrigation_manual_start_station_{station_id}"
        )

        data.append(
            {
                "device_id": sprinkle_button_device_id,
                "device_type": "SPRINKLE_BUTTON",
                "device_name": f"Sprinkle {station_label}",
                "device_uid": _stable_uid(coordinator, sprinkle_button_device_id),
                "software_version": "1.0",
                "icon": "mdi:sprinkler",
                "last_reboot": None,
            }
        )

    stop_device_id = f"{coordinator.controller_mac_address}_irrigation_stop"
    data.append(
        {
            "device_id": stop_device_id,
            "device_type": "STOP_BUTTON",
            "device_name": "Stop sprinkle",
            "device_uid": _stable_uid(coordinator, stop_device_id),
            "software_version": "1.0",
            "icon": "mdi:water-off",
            "last_reboot": None,
        }
    )

    on_device_id = f"{coordinator.controller_mac_address}_irrigation_controller_on"
    data.append(
        {
            "device_id": on_device_id,
            "device_type": "ON_BUTTON",
            "device_name": "Turn on controller",
            "device_uid": _stable_uid(coordinator, on_device_id),
            "software_version": "1.0",
            "icon": "mdi:power-on",
            "last_reboot": None,
        }
    )

    off_device_id = f"{coordinator.controller_mac_address}_irrigation_controller_off"
    data.append(
        {
            "device_id": off_device_id,
            "device_type": "OFF_BUTTON",
            "device_name": "Turn off controller",
            "device_uid": _stable_uid(coordinator, off_device_id),
            "software_version": "1.0",
            "icon": "mdi:power-off",
            "last_reboot": None,
        }
    )

    return data


def build_irrigation_planning_sensor_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build irrigation planning sensor data."""
    data: list[dict[str, Any]] = []

    for station_id in range(1, coordinator.num_stations + 1):
        station_label = _station_label(coordinator, station_id)
        sprinkle_total_device_id = (
            f"{coordinator.controller_mac_address}_sprinkle_total_amount_today_station_{station_id}"
        )

        data.append(
            {
                "device_id": sprinkle_total_device_id,
                "device_type": "SPRINKLE_TOTAL_AMOUNT_SENSOR",
                "device_name": f"Sprinkle Total Amount Today {station_label}",
                "device_uid": _stable_uid(coordinator, sprinkle_total_device_id),
                "software_version": "1.0",
                "state": round(coordinator.sprinkle_total_amount_today[station_id - 1], 2),
                "icon": "mdi:water",
                "last_reboot": None,
            }
        )

    for station_id in range(1, coordinator.num_stations + 1):
        station_label = _station_label(coordinator, station_id)
        forecasted_sprinkle_device_id = (
            f"{coordinator.controller_mac_address}_forecasted_sprinkle_today_station_{station_id}"
        )

        data.append(
            {
                "device_id": forecasted_sprinkle_device_id,
                "device_type": "FORECASTED_SPRINKLE_TODAY_SENSOR",
                "device_name": f"Forecasted Sprinkle Today {station_label}",
                "device_uid": _stable_uid(coordinator, forecasted_sprinkle_device_id),
                "software_version": "1.0",
                "state": round(coordinator.forecasted_sprinkle_today[station_id - 1], 2),
                "icon": "mdi:water-check",
                "last_reboot": None,
            }
        )

    for station_id in range(1, coordinator.num_stations + 1):
        station_label = _station_label(coordinator, station_id)
        remaining_sprinkle_device_id = (
            f"{coordinator.controller_mac_address}_remaining_sprinkle_today_station_{station_id}"
        )

        data.append(
            {
                "device_id": remaining_sprinkle_device_id,
                "device_type": "REMAINING_SPRINKLE_TODAY_SENSOR",
                "device_name": f"Remaining Sprinkle Today {station_label}",
                "device_uid": _stable_uid(coordinator, remaining_sprinkle_device_id),
                "software_version": "1.0",
                "state": round(coordinator.remaining_sprinkle_today[station_id - 1], 2),
                "icon": "mdi:water-sync",
                "last_reboot": None,
            }
        )

    return data


def build_current_irrigation_sensor_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build current irrigation sensor data when supported by the coordinator."""
    if not (
        hasattr(coordinator, "get_current_irrigation_station_name")
        and hasattr(coordinator, "get_current_irrigation_end_time")
    ):
        return []

    current_station_device_id = f"{coordinator.controller_mac_address}_current_irrigation_station"
    irrigation_end_time_device_id = f"{coordinator.controller_mac_address}_irrigation_end_time"

    return [
        {
            "device_id": current_station_device_id,
            "device_type": "CURRENT_IRRIGATION_STATION_SENSOR",
            "device_name": "Current Irrigation Station",
            "device_uid": _stable_uid(coordinator, current_station_device_id),
            "software_version": "1.0",
            "state": coordinator.get_current_irrigation_station_name(),
            "icon": "mdi:sprinkler-variant",
            "last_reboot": None,
        },
        {
            "device_id": irrigation_end_time_device_id,
            "device_type": "IRRIGATION_END_TIME_SENSOR",
            "device_name": "Irrigation End Time",
            "device_uid": _stable_uid(coordinator, irrigation_end_time_device_id),
            "software_version": "1.0",
            "state": coordinator.get_current_irrigation_end_time(),
            "icon": "mdi:clock-end",
            "last_reboot": None,
        },
    ]


def build_schedule_sensor_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build schedule-related sensor data."""
    next_schedule_device_id = f"{coordinator.controller_mac_address}_next_schedule"
    last_sprinkle_device_id = f"{coordinator.controller_mac_address}_last_sprinkle"

    return [
        {
            "device_id": next_schedule_device_id,
            "device_type": "NEXT_SCHEDULE_SENSOR",
            "device_name": "Next schedule",
            "device_uid": _stable_uid(coordinator, next_schedule_device_id),
            "software_version": "1.0",
            "state": coordinator.next_schedule,
            "icon": "mdi:home-clock",
            "last_reboot": None,
        },
        {
            "device_id": last_sprinkle_device_id,
            "device_type": "LAST_SPRINKLE_SENSOR",
            "device_name": "Last sprinkle",
            "device_uid": _stable_uid(coordinator, last_sprinkle_device_id),
            "software_version": "1.0",
            "state": coordinator.last_sprinkle,
            "icon": "mdi:sprinkler",
            "last_reboot": None,
        },
    ]


def build_weather_sensor_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build weather-related entity data."""
    if not coordinator.weather_api:
        return []

    data: list[dict[str, Any]] = []

    will_rain_device_id = f"{coordinator.controller_mac_address}_will_rain_today"
    data.append(
        {
            "device_id": will_rain_device_id,
            "device_type": "WILL_RAIN_SENSOR",
            "device_name": "Will it rain today",
            "device_uid": _stable_uid(coordinator, will_rain_device_id),
            "software_version": "1.0",
            "state": coordinator.will_it_rain_today,
            "icon": "mdi:weather-rainy",
            "last_reboot": None,
        }
    )

    has_rained_device_id = f"{coordinator.controller_mac_address}_has_rained_today"
    data.append(
        {
            "device_id": has_rained_device_id,
            "device_type": "HAS_RAINED_SENSOR",
            "device_name": "Has rained today",
            "device_uid": _stable_uid(coordinator, has_rained_device_id),
            "software_version": "1.0",
            "state": coordinator.has_rained_today,
            "icon": "mdi:weather-rainy",
            "last_reboot": None,
        }
    )

    is_raining_device_id = f"{coordinator.controller_mac_address}_is_raining_now"
    data.append(
        {
            "device_id": is_raining_device_id,
            "device_type": "IS_RAINING_SENSOR",
            "device_name": "Is it raining now",
            "device_uid": _stable_uid(coordinator, is_raining_device_id),
            "software_version": "1.0",
            "state": coordinator.is_raining_now,
            "icon": "mdi:weather-pouring",
            "last_reboot": None,
        }
    )

    last_rain_device_id = f"{coordinator.controller_mac_address}_last_rain"
    data.append(
        {
            "device_id": last_rain_device_id,
            "device_type": "LAST_RAIN_SENSOR",
            "device_name": "Last rain",
            "device_uid": _stable_uid(coordinator, last_rain_device_id),
            "software_version": "1.0",
            "state": coordinator.last_rain,
            "icon": "mdi:weather-pouring",
            "last_reboot": None,
        }
    )

    rain_time_device_id = f"{coordinator.controller_mac_address}_rain_time_today"
    data.append(
        {
            "device_id": rain_time_device_id,
            "device_type": "RAIN_TIME_TODAY_SENSOR",
            "device_name": "Rain time today",
            "device_uid": _stable_uid(coordinator, rain_time_device_id),
            "software_version": "1.0",
            "state": coordinator.rain_time_today,
            "icon": "mdi:weather-rainy",
            "last_reboot": None,
        }
    )

    total_rain_device_id = f"{coordinator.controller_mac_address}_total_amount_rain_today"
    data.append(
        {
            "device_id": total_rain_device_id,
            "device_type": "TOTAL_AMOUNT_RAIN_TODAY",
            "device_name": "Total amount of rain today",
            "device_uid": _stable_uid(coordinator, total_rain_device_id),
            "software_version": "1.0",
            "state": coordinator.rain_total_amount_today,
            "icon": "mdi:weather-rainy",
            "last_reboot": None,
        }
    )

    total_forecasted_rain_device_id = (
        f"{coordinator.controller_mac_address}_total_forecasted_rain_today"
    )
    data.append(
        {
            "device_id": total_forecasted_rain_device_id,
            "device_type": "TOTAL_FORECASTED_RAIN_TODAY",
            "device_name": "Total forecasted rain today",
            "device_uid": _stable_uid(coordinator, total_forecasted_rain_device_id),
            "software_version": "1.0",
            "state": coordinator.rain_total_amount_forecasted_today,
            "icon": "mdi:weather-rainy",
            "last_reboot": None,
        }
    )

    return data


def build_water_usage_sensor_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build water usage sensor data."""
    total_water_device_id = f"{coordinator.controller_mac_address}_total_water_consumption"

    return [
        {
            "device_id": total_water_device_id,
            "device_type": "TOTAL_WATER_CONSUMPTION_SENSOR",
            "device_name": "Total water consumption",
            "device_uid": _stable_uid(coordinator, total_water_device_id),
            "software_version": "1.0",
            "state": coordinator.total_water_consumption,
            "icon": "mdi:water-pump",
            "last_reboot": None,
        }
    ]


def build_all_entity_data(coordinator: Any) -> list[dict[str, Any]]:
    """Build all coordinator entity data."""
    data: list[dict[str, Any]] = []

    data.extend(build_controller_data(coordinator))
    data.extend(build_station_data(coordinator))
    data.extend(build_configuration_number_data(coordinator))
    data.extend(build_button_data(coordinator))
    data.extend(build_irrigation_planning_sensor_data(coordinator))
    data.extend(build_current_irrigation_sensor_data(coordinator))
    data.extend(build_schedule_sensor_data(coordinator))
    data.extend(build_water_usage_sensor_data(coordinator))
    data.extend(build_weather_sensor_data(coordinator))

    return data