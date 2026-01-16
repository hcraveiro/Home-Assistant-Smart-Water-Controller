"""Base entity which all other entity platform classes can inherit.

As all entity types have a common set of properties, you can
create a base entity like this and inherit it in all your entity platforms.

This just makes your code more efficient and is totally optional.

See each entity platform (ie sensor.py, switch.py) for how this is inheritted
and what additional properties and methods you need to add for each entity type.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, CONTROLLER_MAC_ADDRESS
from .coordinator import SmartWaterControllerCoordinator
from .util import normalize_mac_address

_LOGGER = logging.getLogger(__name__)


class SmartWaterControllerBaseEntity(CoordinatorEntity):
    """Base Entity Class.

    This inherits a CoordinatorEntity class to register your entites to be updated
    by your DataUpdateCoordinator when async_update_data is called, either on the scheduled
    interval or by forcing an update.
    """

    coordinator: SmartWaterControllerCoordinator

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SmartWaterControllerCoordinator, device: dict[str, Any], parameter: str
    ) -> None:
        """Initialise entity."""
        super().__init__(coordinator)
        self.device = device
        self.device_id = device["device_id"]
        self.parameter = parameter

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update sensor with latest data from coordinator."""
        self.device = self.coordinator.get_device(self.device_id)
        _LOGGER.debug(
            "Updating device: %s, %s",
            self.device_id,
            self.coordinator.get_device_parameter(self.device_id, "device_name"),
        )
        self.async_write_ha_state()

    def _get_controller_unique_id(self) -> str:
        """Return the stable controller identifier used for entity unique ids.

        Prefer config_entry.unique_id (stable).
        Fallback to stored controller MAC in config_entry.data for backwards compatibility.
        """
        controller_uid = normalize_mac_address((self.coordinator.config_entry.unique_id or "").strip())
        if controller_uid:
            return controller_uid

        controller_uid = str(
            self.coordinator.config_entry.data.get(CONTROLLER_MAC_ADDRESS, "") or ""
        ).strip()
        if controller_uid:
            return controller_uid

        controller_uid = str(
            getattr(self.coordinator, "controller_mac_address", "") or ""
        ).strip()
        return controller_uid

    def _get_controller_display_name(self) -> str:
        """Return the name to show in the device registry.

        If a MAC exists, use it. Otherwise, use 'DOMAIN_<slugified_conf_name>'.
        """
        mac = str(
            self.coordinator.config_entry.data.get(CONTROLLER_MAC_ADDRESS, "") or ""
        ).strip()
        if mac:
            return mac

        unique_id_mac = (self.coordinator.config_entry.unique_id or "").strip()
        if unique_id_mac:
            return unique_id_mac

        conf_name = str(self.coordinator.config_entry.data.get(CONF_NAME, "") or "").strip()
        if not conf_name:
            conf_name = str(self.coordinator.config_entry.title or "").strip()

        if conf_name:
            return f"{DOMAIN}_{slugify(conf_name)}"

        # Last resort fallback (should rarely happen)
        return f"{DOMAIN}_controller"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information.
    
        Use a single device per config entry. If entities expose different identifiers,
        Home Assistant will create duplicate devices (one empty, another with entities).
        """
        entry = self.coordinator.config_entry
    
        controller_mac = normalize_mac_address(
            str(entry.data.get(CONTROLLER_MAC_ADDRESS, "") or entry.unique_id or "").strip()
        )
        connections = {(dr.CONNECTION_NETWORK_MAC, controller_mac)} if controller_mac else set()
    
        controller_name = str(entry.data.get(CONF_NAME, "") or entry.title or "Smart Water Controller").strip()
        model = (
            controller_mac.split("-")[0]
            if controller_mac and "-" in controller_mac
            else (controller_mac or "Controller")
        )
    
        return DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=controller_name,
            manufacturer="Smart Water Controller",
            model=model,
            sw_version="1.0",
            connections=connections,
        )


    @property
    def icon(self) -> str:
        """Return the name of the sensor."""
        return self.device["icon"] if self.device else "mdi:help-circle"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self.device["device_name"]

    @property
    def unique_id(self) -> str:
        """Return unique id."""
        controller_uid = self._get_controller_unique_id()
        device_uid = self.coordinator.get_device_parameter(self.device_id, "device_uid")

        # Namespace with DOMAIN to avoid collisions with copied integrations/domains
        # and handle missing controller id gracefully.
        if controller_uid:
            return f"{DOMAIN}-{controller_uid}-{device_uid}-{self.parameter}"

        return f"{DOMAIN}-unknown_controller-{device_uid}-{self.parameter}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the extra state attributes."""
        return {}
