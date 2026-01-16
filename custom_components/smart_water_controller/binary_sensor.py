"""Binary sensor setup for our Integration."""

from dataclasses import dataclass
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .base import SmartWaterControllerBaseEntity
from .coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)

@dataclass
class BinaryTypeClass:
    """Class for holding sensor type to sensor class."""

    device_type: str
    state_field: str
    binary_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Binary Sensors."""
    # This gets the data update coordinator from hass.data as specified in your __init__.py
    # This gets the data update coordinator from the config entry runtime data as specified in your __init__.py
    coordinator: SmartWaterControllerCoordinator = config_entry.runtime_data.coordinator

    use_weather = coordinator.weather_api is not None

    binary_sensor_types = [
        BinaryTypeClass("WILL_RAIN_SENSOR", "state", WillRainToday),
        BinaryTypeClass("HAS_RAINED_SENSOR", "state", HasRainedToday),
        BinaryTypeClass("IS_RAINING_SENSOR", "state", IsRainingNow),
    ]
    
    if not use_weather:
        binary_sensor_types = []

    # ----------------------------------------------------------------------------
    # Here we are going to add some binary sensors for the contact sensors in our
    # mock data. So we add an instance of our BooleanBinarySensor class for each
    # contact sensor we have in our data.
    # ----------------------------------------------------------------------------
    binary_sensors = []

    for sensor_type in binary_sensor_types:
        binary_sensors.extend(
            [
                sensor_type.binary_class(coordinator, device, sensor_type.state_field)
                for device in coordinator.data
                if device.get("device_type") == sensor_type.device_type
            ]
        )

    # Create the binary sensors.
    async_add_entities(binary_sensors)


class BooleanBinarySensor(SmartWaterControllerBaseEntity, BinarySensorEntity):
    """Implementation of a sensor.

    This inherits our SmartWaterControllerBaseEntity to set common properties.
    See base.py for this class.

    https://developers.home-assistant.io/docs/core/entity/binary-sensor
    """

class WillRainToday(BooleanBinarySensor):

    @property
    def is_on(self) -> bool | None:
        """Return if the binary sensor is True."""
        # This needs to enumerate to true or false
        return self.coordinator.will_it_rain_today
    
    @property
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        # Add any additional attributes you want on your sensor.
        attrs = {}
        attrs["forecast"] = self.coordinator.will_it_rain_today_forecast
        return attrs

class HasRainedToday(BooleanBinarySensor):

    @property
    def is_on(self) -> bool | None:
        """Return if the binary sensor is True."""
        # This needs to enumerate to true or false
        return self.coordinator.has_rained_today

class IsRainingNow(BooleanBinarySensor):

    @property
    def is_on(self) -> bool | None:
        """Return if the binary sensor is True."""
        # This needs to enumerate to true or false
        return self.coordinator.is_raining_now
    
    @property
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        # Add any additional attributes you want on your sensor.
        attrs = {}
        attrs["current"] = self.coordinator.is_raining_now_json
        return attrs
