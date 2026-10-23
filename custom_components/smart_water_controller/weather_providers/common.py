"""Shared helpers for the weather provider implementations."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import WEATHER_REQUEST_TIMEOUT
from ..errors import APIConnectionError

_LOGGER = logging.getLogger(__name__)

# Start hour of every 3-hour forecast block of a day. It must include 21, so
# the last block of the day is part of the daily forecast.
FORECAST_BLOCK_HOURS = list(range(0, 24, 3))


async def fetch_json(
    url: str,
    description: str,
    *,
    hass: HomeAssistant | None = None,
) -> Any:
    """Fetch and decode JSON from a weather provider.

    Always bounded by a request timeout: without one a hanging provider would
    stall the coordinator poll indefinitely. Uses the shared Home Assistant
    client session when available instead of building a new connector per call.
    """
    timeout = aiohttp.ClientTimeout(total=WEATHER_REQUEST_TIMEOUT)

    try:
        if hass is not None:
            session = async_get_clientsession(hass)
            async with session.get(url, timeout=timeout) as response:
                return await _decode_json(response, description)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                return await _decode_json(response, description)
    except APIConnectionError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.error("Error requesting %s: %s", description, exc)
        raise APIConnectionError(f"Error requesting {description}: {exc}") from exc


async def _decode_json(response: aiohttp.ClientResponse, description: str) -> Any:
    """Decode a provider response, surfacing HTTP errors with their body."""
    if response.status >= 400:
        body = (await response.text())[:200]
        raise APIConnectionError(
            f"{description} request failed with HTTP {response.status}: {body}"
        )

    try:
        return await response.json()
    except Exception as exc:  # pylint: disable=broad-except
        raise APIConnectionError(f"{description}: JSON format invalid!") from exc


def rain_rate_from_owm_payload(current: dict[str, Any] | None) -> float:
    """Return the current rain rate (mm/h) from an OpenWeatherMap payload.

    OpenWeatherMap reports rain as accumulated volume over a window, e.g.
    ``{"rain": {"1h": 0.5}}``. The shortest window available is the most
    representative of the rate right now.
    """
    rain_data = (current or {}).get("rain") or {}
    best_hours: int | None = None
    best_amount = 0.0

    for key, amount in rain_data.items():
        if not (isinstance(key, str) and key.endswith("h") and key[:-1].isdigit()):
            continue

        hours = int(key[:-1])

        if hours <= 0:
            continue

        if best_hours is not None and hours >= best_hours:
            continue

        try:
            best_amount = float(amount)
        except (TypeError, ValueError):
            continue

        best_hours = hours

    if not best_hours:
        return 0.0

    return best_amount / best_hours
