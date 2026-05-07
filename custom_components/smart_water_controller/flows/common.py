"""Common flow helpers for Smart Water Controller."""

from __future__ import annotations

import re

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


def _is_mac_address(value: str) -> bool:
    """Return True if the provided value looks like a MAC address."""
    if not value:
        return False

    return bool(
        re.fullmatch(
            r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$",
            value.strip(),
        )
    )


def _bool_select_schema():
    """Return a selector for boolean values stored as 'true'/'false' strings."""
    return selector(
        {
            "select": {
                "options": ["true", "false"],
                "mode": "dropdown",
            }
        }
    )