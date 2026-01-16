"""Button setup for our Integration.

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
import asyncio
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from . import MyConfigEntry
from .base import SmartWaterControllerBaseEntity
from .coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class ButtonTypeClass:
    """Class for holding sensor type to sensor class."""

    device_type: str
    button_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Buttons."""
    # This gets the data update coordinator from the config entry runtime data as specified in your __init__.py
    coordinator: SmartWaterControllerCoordinator = config_entry.runtime_data.coordinator

    # ----------------------------------------------------------------------------
    # Here we enumerate the sensors in your data value from your
    # DataUpdateCoordinator and add an instance of your sensor class to a list
    # for each one.
    # This maybe different in your specific case, depending on how your data is
    # structured
    # ----------------------------------------------------------------------------

    button_types = [
        ButtonTypeClass("SPRINKLE_BUTTON", IrrigationStartButton),
        ButtonTypeClass("STOP_BUTTON", IrrigationStopButton),
        ButtonTypeClass("ON_BUTTON", ControllerOnButton),
        ButtonTypeClass("OFF_BUTTON", ControllerOffButton),
    ]

    buttons = []

    for button_type in button_types:
        buttons.extend(
            [
                button_type.button_class(coordinator, device)
                for device in coordinator.data
                if device.get("device_type") == button_type.device_type
            ]
        )

    # Now create the buttons.
    async_add_entities(buttons)


class SmartWaterControllerButtonEntity(SmartWaterControllerBaseEntity, ButtonEntity):
    def __init__(
        self, coordinator: SmartWaterControllerCoordinator, device: dict[str, Any]
    ) -> None:
        """Initialise entity."""
        super().__init__(coordinator, device, None)

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

class IrrigationStartButton(SmartWaterControllerButtonEntity):
    """Button entity to manually start irrigation."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Start Irrigation"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.start_irrigation(int(self.device_id.rsplit("_", 1)[-1])))

class IrrigationStopButton(SmartWaterControllerButtonEntity):
    """Button entity to manually stop irrigation."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Stop Irrigation"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.stop_irrigation())

class ControllerOnButton(SmartWaterControllerButtonEntity):
    """Button entity to manually stop irrigation."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Controller ON"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.turn_controller_on())

class ControllerOffButton(SmartWaterControllerButtonEntity):
    """Button entity to manually stop irrigation."""

    def __init__(self, coordinator: SmartWaterControllerCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Controller OFF"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.turn_controller_off())
