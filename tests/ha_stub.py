"""Minimal Home Assistant boundary for dependency-free runtime regression tests.

The production runtime, planning, API and storage modules are imported unchanged.
Only the HA interfaces those modules consume are faked; no valves are operated.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def module(name, **attrs):
    value = types.ModuleType(name)
    value.__dict__.update(attrs)
    sys.modules[name] = value
    return value


for name, path in [
    ('custom_components', ROOT / 'custom_components'),
    ('custom_components.smart_water_controller', ROOT / 'custom_components/smart_water_controller'),
    ('custom_components.smart_water_controller.runtime', ROOT / 'custom_components/smart_water_controller/runtime'),
    ('custom_components.smart_water_controller.helpers', ROOT / 'custom_components/smart_water_controller/helpers'),
    ('custom_components.smart_water_controller.api', ROOT / 'custom_components/smart_water_controller/api'),
]:
    module(name, __path__=[str(path)])

module('homeassistant', __path__=[])
module('homeassistant.core', HomeAssistant=object, callback=lambda fn: fn)
module('homeassistant.const', STATE_ON='on', STATE_OFF='off')
module('homeassistant.exceptions', HomeAssistantError=RuntimeError)
module('homeassistant.helpers', __path__=[])
module('homeassistant.helpers.event', async_track_state_change_event=lambda *args: lambda: None)
module('homeassistant.helpers.entity_registry', async_get=lambda hass: hass.registry)


class Store:
    def __init__(self, *args):
        self.data = None

    async def async_save(self, value):
        self.data = value

    async def async_load(self):
        return self.data


module('homeassistant.helpers.storage', Store=Store)
dt = module('homeassistant.util.dt', now=lambda: datetime.now(timezone.utc),
            as_local=lambda value: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value)
module('homeassistant.util', dt=dt, slugify=lambda value: value.lower().replace(' ', '_'))
