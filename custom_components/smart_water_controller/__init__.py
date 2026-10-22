from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.yaml import load_yaml

from .const import DOMAIN, CONTROLLER_MAC_ADDRESS
from .coordinator import SmartWaterControllerCoordinator

_LOGGER = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# A list of the different platforms we wish to setup.
# Add or remove from this list based on your specific need
# of entity platform types.
# ----------------------------------------------------------------------------
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
]

MyConfigEntry = ConfigEntry

# Omitting `month` clears all twelve. Validated here rather than in the handler
# because this service destroys a whole season's plan: a typo like month 0 must
# be refused, not quietly treated as "clear everything".
CLEAR_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("month"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
    }
)


async def _async_describe_services(
    hass: HomeAssistant, names: dict[str, str]
) -> None:
    """Attach the services.yaml documentation to per-controller service names.

    Home Assistant looks service descriptions up by *exact* name, and ours carry
    the controller prefix — `set_irrigation_schedule_c8_b9_61_d3_fa_0d` — so the
    entry keyed `set_irrigation_schedule` in services.yaml never matches. The
    symptom is a service that works perfectly while the API reports
    `{"fields": {}}`: in Developer tools -> Actions there is no description, no
    `month` field and no selector, so the one service that can erase a season's
    plan has to be driven by guessing its YAML by hand.

    `names` maps the services.yaml key to the registered name.
    """
    path = str(Path(__file__).parent / "services.yaml")

    try:
        descriptions: Any = await hass.async_add_executor_job(load_yaml, path)
    except (OSError, HomeAssistantError) as err:
        # A missing or broken services.yaml must not take the integration down:
        # the services themselves are already registered and working.
        _LOGGER.warning("Could not read %s, services stay undocumented: %s", path, err)
        return

    if not isinstance(descriptions, dict):
        _LOGGER.warning("%s is not a mapping, services stay undocumented", path)
        return

    for base_name, service_name in names.items():
        description = descriptions.get(base_name)

        if not isinstance(description, dict):
            _LOGGER.warning(
                "services.yaml has no entry for '%s', so '%s' stays undocumented",
                base_name,
                service_name,
            )
            continue

        async_set_service_schema(hass, DOMAIN, service_name, description)


@dataclass
class RuntimeData:
    """Class to hold your data."""

    coordinator: SmartWaterControllerCoordinator
    cancel_update_listener: Callable


async def async_setup_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Set up Smart Water Controller Integration from a config entry."""

    # ----------------------------------------------------------------------------
    # Initialise the coordinator that manages data updates from your api.
    # This is defined in coordinator.py
    # ----------------------------------------------------------------------------
    coordinator = SmartWaterControllerCoordinator(hass, config_entry)
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    # ----------------------------------------------------------------------------
    # Perform an initial data load from api.
    # async_config_entry_first_refresh() is special in that it does not log errors
    # if it fails.
    # ----------------------------------------------------------------------------
    # Restore durable shutdown intent before any refresh can save defaults.
    await coordinator.init_task
    await coordinator.async_config_entry_first_refresh()

    # ----------------------------------------------------------------------------
    # Test to see if api initialised correctly, else raise ConfigNotReady to make
    # HA retry setup.
    # Change this to match how your api will know if connected or successful
    # update.
    # ----------------------------------------------------------------------------
    if not coordinator.data:
        raise ConfigEntryNotReady

    # ----------------------------------------------------------------------------
    # Initialise a listener for config flow options changes.
    # This will be removed automatically if the integraiton is unloaded.
    # See config_flow for defining an options setting that shows up as configure
    # on the integration.
    # If you do not want any config flow options, no need to have listener.
    # ----------------------------------------------------------------------------
    cancel_update_listener = config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )

    # ----------------------------------------------------------------------------
    # Add the coordinator and update listener to your config entry to make
    # accessible throughout your integration
    # ----------------------------------------------------------------------------
    config_entry.runtime_data = RuntimeData(coordinator, cancel_update_listener)

    # ----------------------------------------------------------------------------
    # Ensure the Device Registry entry is created and linked to this config entry.
    # This guarantees the integration UI shows the device under the config entry.
    # ----------------------------------------------------------------------------
    device_registry = dr.async_get(hass)

    controller_mac = (
        (config_entry.unique_id or "").strip() or (coordinator.controller_mac_address or "").strip() or (config_entry.data.get(CONTROLLER_MAC_ADDRESS, "") or "").strip()
    )
    controller_name = (config_entry.data.get(CONF_NAME) or config_entry.title or "").strip()

    # Use a single stable device identifier for the whole config entry.
    # Entities must use the same identifiers in device_info, otherwise HA will create duplicate devices.
    identifiers_value = config_entry.entry_id
    connections = {(dr.CONNECTION_NETWORK_MAC, controller_mac)} if controller_mac else set()

    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, identifiers_value)},
        connections=connections,
        name=controller_name or config_entry.title or "Smart Water Controller",
        manufacturer="Smart Water Controller",
        model=controller_mac.split("-")[0] if controller_mac else "Controller",
        sw_version="1.0",
    )
    # ----------------------------------------------------------------------------
    # Registers the new service to update schedule
    # ----------------------------------------------------------------------------
    async def handle_set_schedule(call: ServiceCall) -> None:
        """Updates irrigation schedule from frontend."""
        new_schedule = call.data["schedule"]
        
        await coordinator.async_set_schedule(new_schedule)

    service_name = f"set_irrigation_schedule_{coordinator.controller_service_prefix}"

    if not hass.services.has_service(DOMAIN, service_name):
        _LOGGER.info(
            f"{coordinator.controller_service_prefix} - Registering set_irrigation_schedule_{coordinator.controller_service_prefix.lower().replace(':', '_')} service..."
        )
        hass.services.async_register(DOMAIN, service_name, handle_set_schedule)
        _LOGGER.info(f"{coordinator.controller_service_prefix} - Registered.")

    # ----------------------------------------------------------------------------
    # Registers the service that stops a schedule.
    #
    # The schedule is stored by the integration, not by the dashboard card that
    # wrote it, so deleting the card leaves its plan watering with nothing left
    # in the UI to stop it. Before this service the only way out was to call
    # set_irrigation_schedule with twelve hand-written empty months.
    # ----------------------------------------------------------------------------
    async def handle_clear_schedule(call: ServiceCall) -> None:
        """Clear the whole irrigation schedule, or a single month."""
        await coordinator.async_clear_schedule(call.data.get("month"))

    clear_service_name = (
        f"clear_irrigation_schedule_{coordinator.controller_service_prefix}"
    )

    if not hass.services.has_service(DOMAIN, clear_service_name):
        _LOGGER.info(
            f"{coordinator.controller_service_prefix} - Registering {clear_service_name} service..."
        )
        hass.services.async_register(
            DOMAIN,
            clear_service_name,
            handle_clear_schedule,
            schema=CLEAR_SCHEDULE_SCHEMA,
        )
        _LOGGER.info(f"{coordinator.controller_service_prefix} - Registered.")

    # Both services are registered under a per-controller name, which is why
    # they need their descriptions attached explicitly. Done once here rather
    # than next to each registration so the two names stay side by side.
    await _async_describe_services(
        hass,
        {
            "set_irrigation_schedule": service_name,
            "clear_irrigation_schedule": clear_service_name,
        },
    )

    # ----------------------------------------------------------------------------
    # Setup platforms (based on the list of entity types in PLATFORMS defined above)
    # This calls the async_setup method in each of your entity type files.
    # ----------------------------------------------------------------------------
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)


    # Return true to denote a successful setup.
    return True


async def _async_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle config options update.

    Reload the integration when the options change.
    Called from our listener created above.
    """
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Delete device if selected from UI.

    Adding this function shows the delete device option in the UI.
    Remove this function if you do not want that option.
    You may need to do some checks here before allowing devices to be removed.
    """
    return True

async def async_reconfigure_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle reconfiguration of an entry."""
    _LOGGER.debug("Reconfiguring integration: %s", config_entry.entry_id)

    # Get runtime data from the existing entry
    runtime_data: RuntimeData = hass.data[DOMAIN][config_entry.entry_id]

    # Atualizar a configuração do Coordinator
    await runtime_data.coordinator.update_config(config_entry)

    # Force coordinator refresh
    await runtime_data.coordinator.async_refresh()

async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload a config entry.

    This is called when you remove your integration or shutdown HA.
    If you have created any custom services, they need to be removed here too.
    """
    runtime_data = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    coordinator.irrigation_scheduler.clear()
    coordinator.station_switch_tracker.clear()
    if not coordinator.init_task.done():
        coordinator.init_task.cancel()
        with suppress(asyncio.CancelledError):
            await coordinator.init_task
    await coordinator.irrigation_runner.async_close()

    # Unload services
    #for service in hass.services.async_services_for_domain(DOMAIN):
        #hass.services.async_remove(DOMAIN, service)

    service_prefix = runtime_data.coordinator.controller_service_prefix

    service_name = f"set_irrigation_schedule_{service_prefix}"
    hass.services.async_remove(DOMAIN, service_name)

    hass.services.async_remove(DOMAIN, f"clear_irrigation_schedule_{service_prefix}")

    # Unload platforms and return result
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
