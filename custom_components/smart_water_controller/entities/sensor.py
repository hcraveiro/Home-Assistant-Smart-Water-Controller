"""Sensor setup for our integration."""

from dataclasses import dataclass
import logging
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPrecipitationDepth,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .. import MyConfigEntry
from .base import SmartWaterControllerBaseEntity
from ..coordinator import SmartWaterControllerCoordinator
from ..helpers import planning

_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorTypeClass:
    """Class for holding sensor type to sensor class."""

    device_type: str
    state_field: str
    sensor_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the sensors."""
    coordinator: SmartWaterControllerCoordinator = config_entry.runtime_data.coordinator

    use_weather = coordinator.weather_api is not None

    sensor_types = [
        SensorTypeClass("STATE_SENSOR", "state", StateSensor),
        SensorTypeClass("NEXT_SCHEDULE_SENSOR", "state", NextScheduleSensor),
        SensorTypeClass("LAST_SPRINKLE_SENSOR", "state", LastSprinkleSensor),
        SensorTypeClass("CURRENT_IRRIGATION_STATION_SENSOR", "state", CurrentIrrigationStationSensor),
        SensorTypeClass("IRRIGATION_END_TIME_SENSOR", "state", IrrigationEndTimeSensor),
        SensorTypeClass("TOTAL_WATER_CONSUMPTION_SENSOR", "state", TotalWaterConsumptionSensor),
        SensorTypeClass("SPRINKLE_TOTAL_AMOUNT_SENSOR", "state", SprinkleTotalAmountSensor),
        SensorTypeClass("FORECASTED_SPRINKLE_TODAY_SENSOR", "state", ForecastedSprinkleTodaySensor),
        SensorTypeClass("REMAINING_SPRINKLE_TODAY_SENSOR", "state", RemainingSprinkleTodaySensor),
    ]

    if use_weather:
        sensor_types.extend(
            [
                SensorTypeClass("LAST_RAIN_SENSOR", "state", LastRainSensor),
                SensorTypeClass("RAIN_TIME_TODAY_SENSOR", "state", TotalRainTimeSensor),
                SensorTypeClass("TOTAL_AMOUNT_RAIN_TODAY", "state", TotalAmountRainSensor),
                SensorTypeClass("TOTAL_FORECASTED_RAIN_TODAY", "state", TotalForecastedRainSensor),
            ]
        )

    sensors = []

    for sensor_type in sensor_types:
        sensors.extend(
            [
                sensor_type.sensor_class(coordinator, device, sensor_type.state_field)
                for device in coordinator.data
                if device.get("device_type") == sensor_type.device_type
            ]
        )

    async_add_entities(sensors)


class StateSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Generic state sensor."""

    @property
    def native_value(self) -> int | float | str | None:
        """Return the sensor value."""
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        attrs = {}

        if self.device_id == self.coordinator.controller.device_id:
            attrs["pending_stop_stations"] = list(self.coordinator.pending_stop_stations)
            attrs["last_irrigation_result"] = self.coordinator.last_irrigation_result
            attrs["last_irrigation_failure"] = self.coordinator.last_irrigation_failure
            attrs["schedule"] = self.coordinator.schedule or []
            attrs["num_stations"] = self.coordinator.num_stations
            # The schedule card looks up station_names to label its rows. It was
            # never published, so the card always fell back to "Station 1",
            # "Station 2", ... no matter how the stations had been named.
            attrs["station_names"] = list(
                getattr(self.coordinator, "station_names", None) or []
            )
            attrs["service_prefix"] = self.coordinator.controller_service_prefix
            attrs["controller_service_prefix"] = self.coordinator.controller_service_prefix
            attrs.update(self.coordinator.get_controller_irrigation_attributes())
            return attrs

        station_number = self.coordinator.get_device_parameter(
            self.device_id,
            "station_number",
        )

        if station_number is not None:
            attrs["stop_pending"] = int(station_number) in self.coordinator.pending_stop_stations
            attrs["last_irrigation_result"] = self.coordinator.station_irrigation_results.get(str(station_number))
            try:
                attrs.update(
                    self.coordinator.get_station_irrigation_attributes(
                        int(station_number)
                    )
                )
            except (TypeError, ValueError):
                pass

        return attrs


class NextScheduleSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the next scheduled watering time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the next scheduled watering time."""
        next_schedule = self.coordinator.next_schedule

        if next_schedule:
            try:
                if isinstance(next_schedule, str):
                    next_schedule = datetime.fromisoformat(next_schedule)

                if next_schedule.tzinfo is None:
                    next_schedule = dt_util.as_local(next_schedule)

                return next_schedule
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.warning("Invalid format for schedule: %s - %s", next_schedule, ex)

        return None

    @property
    def extra_state_attributes(self):
        """Describe the stored plan next to the next run time.

        The raw schedule is already published on the controller sensor, but as
        twelve nested dicts it is unreadable without a template. A plan that
        outlives the card that created it has to be visible somewhere a user
        actually looks, and this sensor is where they look to ask "when does it
        water next?".
        """
        configured_months = planning.describe_schedule(
            self.coordinator.schedule,
            list(getattr(self.coordinator, "station_names", None) or []),
        )

        attrs = {
            "configured_months": configured_months,
            "has_watering_slots": bool(configured_months),
        }
        attrs.update(self._interval_attributes())

        return attrs

    def _interval_attributes(self) -> dict:
        """Publish what actually decides whether today waters.

        `interval_days` is not a calendar grid. `runtime/schedule_state.py`
        anchors it on `max(last_rain, last_sprinkle)`, so rain resets the
        countdown: "every 2 days" in June means "two days after the ground last
        got wet", not "even days". Anything that draws a plan - a calendar in
        the card, an automation, a person reading a dashboard - needs the
        anchor, not just the interval, or it draws a grid that the first
        thunderstorm invalidates.

        The two timestamps are already published as sensors, but on two
        *different* devices with different entity prefixes (`last_rain` belongs
        to the weather side), so a card cannot derive their entity ids from the
        controller it was pointed at. Here they sit next to the interval they
        act on, which costs one lookup instead of three and a config option.

        Deliberately tolerant: an attribute for a dashboard must never be the
        thing that takes the sensor offline.
        """
        attrs: dict = {}

        try:
            schedule = self.coordinator.schedule or []
            month_config = schedule[dt_util.now().month - 1] if schedule else None
            interval_days = int((month_config or {}).get("interval_days") or 0)

            last_rain = getattr(self.coordinator, "last_rain", None)
            last_sprinkle = getattr(self.coordinator, "last_sprinkle", None)
            events = [event for event in (last_rain, last_sprinkle) if event]

            attrs["interval_days"] = interval_days
            attrs["last_rain"] = last_rain.isoformat() if last_rain else None
            attrs["last_sprinkle"] = last_sprinkle.isoformat() if last_sprinkle else None

            anchor = max(events) if events else None
            attrs["interval_anchor"] = anchor.isoformat() if anchor else None

            # Both read off the same helper the scheduler uses, rather than
            # recomputed here: two implementations of "is today blocked?" would
            # drift, and the dashboard would be the last place to find out.
            state = getattr(self.coordinator, "schedule_state", None)
            blocked = bool(
                state and interval_days > 0
                and state.recent_event_blocks_today(interval_days)
            )
            attrs["blocked_today"] = blocked
            attrs["blocked_until"] = (
                (anchor.date() + timedelta(days=interval_days)).isoformat()
                if blocked and anchor
                else None
            )
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.debug("Could not describe the watering interval: %s", ex)

        return attrs


class LastSprinkleSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the last sprinkle time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the last sprinkle time."""
        last_sprinkle = self.coordinator.last_sprinkle

        if last_sprinkle:
            try:
                if isinstance(last_sprinkle, str):
                    last_sprinkle = datetime.fromisoformat(last_sprinkle)

                if last_sprinkle.tzinfo is None:
                    last_sprinkle = dt_util.as_local(last_sprinkle)

                return last_sprinkle
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.warning("Invalid format for last_sprinkle: %s - %s", last_sprinkle, ex)

        return None

class CurrentIrrigationStationSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the current irrigation station."""

    @property
    def native_value(self) -> str:
        """Return the current irrigation station name."""
        return self.coordinator.get_current_irrigation_station_name()


class IrrigationEndTimeSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the current irrigation end time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the current irrigation end time."""
        end_time = self.coordinator.get_current_irrigation_end_time()

        if end_time and end_time.tzinfo is None:
            return dt_util.as_local(end_time)

        return end_time

class LastRainSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the last rain time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the last rain time."""
        last_rain = self.coordinator.last_rain

        if last_rain:
            try:
                if isinstance(last_rain, str):
                    last_rain = datetime.fromisoformat(last_rain)

                if last_rain.tzinfo is None:
                    last_rain = dt_util.as_local(last_rain)

                return last_rain
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.warning("Invalid format for last_rain: %s - %s", last_rain, ex)

        return None


class TotalRainTimeSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for total rain time today."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.DURATION

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "min"

    @property
    def native_value(self) -> float:
        """Return total rain time today."""
        return round(self.coordinator.rain_time_today, 2)


class TotalAmountRainSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for total rain amount today."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.PRECIPITATION

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        """Return total rain amount today."""
        return round(self.coordinator.rain_total_amount_today, 2)


class TotalForecastedRainSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for total expected rain today."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.PRECIPITATION

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        """Return total expected rain today."""
        return round(self.coordinator.rain_total_amount_forecasted_today, 2)


class TotalWaterConsumptionSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for total water consumption."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = "L"

    @property
    def native_value(self) -> float:
        """Return total water consumption."""
        return round(self.coordinator.total_water_consumption, 2)


class SprinkleTotalAmountSensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the amount already sprinkled today."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        """Return the amount already sprinkled today."""
        value = self.coordinator.get_device_parameter(self.device_id, self.parameter)
        return round(float(value or 0), 2)


class ForecastedSprinkleTodaySensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the total planned sprinkle amount today."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        """Return the total planned sprinkle amount today."""
        value = self.coordinator.get_device_parameter(self.device_id, self.parameter)
        return round(float(value or 0), 2)


class RemainingSprinkleTodaySensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor for the remaining sprinkle amount needed today."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        """Return the remaining sprinkle amount needed today."""
        value = self.coordinator.get_device_parameter(self.device_id, self.parameter)
        return round(float(value or 0), 2)
