"""Sensor setup for our integration."""

from dataclasses import dataclass
import logging
from datetime import datetime

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

from . import MyConfigEntry
from .base import SmartWaterControllerBaseEntity
from .coordinator import SmartWaterControllerCoordinator

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
            attrs["schedule"] = self.coordinator.schedule or []
            attrs["num_stations"] = self.coordinator.num_stations
            attrs["service_prefix"] = self.coordinator.controller_service_prefix
            attrs["controller_service_prefix"] = self.coordinator.controller_service_prefix
            attrs.update(self.coordinator.get_controller_irrigation_attributes())
            return attrs

        station_number = self.coordinator.get_device_parameter(
            self.device_id,
            "station_number",
        )

        if station_number is not None:
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