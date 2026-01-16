"""Sensor setup for our Integration.

Here we use a different method to define some of our entity classes.
As, in our example, so much is common, we use our base entity class to define
many properties, then our base sensor class to define the property to get the
value of the sensor.

As such, for all our other sensor types, we can just set the _attr_ value to
keep our code small and easily readable.  You can do this for all entity properties(attributes)
if you so wish, or mix and match to suit.
"""

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
    """Set up the Sensors."""
    coordinator: SmartWaterControllerCoordinator = config_entry.runtime_data.coordinator

    use_weather = coordinator.weather_api is not None

    sensor_types = [
        SensorTypeClass("STATE_SENSOR", "state", StateSensor),
        SensorTypeClass("NEXT_SCHEDULE_SENSOR", "state", NextScheduleSensor),
        SensorTypeClass("LAST_SPRINKLE_SENSOR", "state", LastSprinkleSensor),
        SensorTypeClass("TOTAL_WATER_CONSUMPTION_SENSOR", "state", TotalWaterConsumptionSensor),
        SensorTypeClass("SPRINKLE_TOTAL_AMOUNT_SENSOR", "state", SprinkleTotalAmountSensor),
        SensorTypeClass("FORECASTED_SPRINKLE_TODAY_SENSOR", "state", ForecastedSprinkleTodaySensor),
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
    @property
    def native_value(self) -> int | float | str:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)

    @property
    def extra_state_attributes(self):
        attrs = {}
        if self.coordinator.controller.device_name == "Controller Status":
            attrs["schedule"] = self.coordinator.schedule
            attrs["num_stations"] = self.coordinator.num_stations
        return attrs


class NextScheduleSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        next_schedule = self.coordinator.next_schedule

        if next_schedule:
            try:
                if isinstance(next_schedule, str):
                    next_schedule = datetime.fromisoformat(next_schedule)

                if next_schedule.tzinfo is None:
                    next_schedule = dt_util.as_local(next_schedule)

                return next_schedule
            except Exception as e:
                _LOGGER.warning(f"Invalid format for schedule: {next_schedule} - {e}")

        return None


class LastSprinkleSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        last_sprinkle = self.coordinator.last_sprinkle

        if last_sprinkle:
            try:
                if isinstance(last_sprinkle, str):
                    last_sprinkle = datetime.fromisoformat(last_sprinkle)

                if last_sprinkle.tzinfo is None:
                    last_sprinkle = dt_util.as_local(last_sprinkle)

                return last_sprinkle
            except Exception as e:
                _LOGGER.warning(f"Invalid format for last_sprinkle: {last_sprinkle} - {e}")

        return None


class LastRainSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        last_rain = self.coordinator.last_rain

        if last_rain:
            try:
                if isinstance(last_rain, str):
                    last_rain = datetime.fromisoformat(last_rain)

                if last_rain.tzinfo is None:
                    last_rain = dt_util.as_local(last_rain)

                return last_rain
            except Exception as e:
                _LOGGER.warning(f"Invalid format for last_rain: {last_rain} - {e}")

        return None


class TotalRainTimeSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.DURATION

    @property
    def native_unit_of_measurement(self) -> str:
        return "min"

    @property
    def native_value(self) -> int:
        return self.coordinator.rain_time_today


class TotalAmountRainSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.PRECIPITATION

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> int:
        return round(self.coordinator.rain_total_amount_today, 2)


class TotalForecastedRainSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.PRECIPITATION

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> int:
        return round(self.coordinator.rain_total_amount_forecasted_today, 2)


class TotalWaterConsumptionSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = "L"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.total_water_consumption, 2)


class SprinkleTotalAmountSensor(SmartWaterControllerBaseEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)

class ForecastedSprinkleTodaySensor(SmartWaterControllerBaseEntity, SensorEntity):
    """Sensor que mostra os mm totais previstos de rega para hoje por estação."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def native_value(self) -> float:
        """Retorna o valor previsto de rega para hoje para esta estação (mm)."""
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)
