"""Config flow helpers for opt-in mobile irrigation notifications."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.helpers.selector import selector

from ..const import (
    NOTIFICATIONS_ENABLED,
    NOTIFY_STATION_FINISHED,
    NOTIFY_CYCLE_FINISHED,
    NOTIFY_IRRIGATION_FAILED,
    NOTIFY_MANUAL_STOP,
    NOTIFICATION_TARGETS,
    NOTIFICATION_DEFAULTS,
)

NOTIFICATION_KEYS = tuple(NOTIFICATION_DEFAULTS)


def current_settings(entry: Any) -> dict[str, Any]:
    """Merge setup defaults, initial config and user-editable options."""
    return {
        **NOTIFICATION_DEFAULTS,
        **{key: entry.data[key] for key in NOTIFICATION_KEYS if key in entry.data},
        **{key: entry.options[key] for key in NOTIFICATION_KEYS if key in entry.options},
    }


def available_mobile_services(hass: Any, existing: list[str] | None = None) -> list[str]:
    """List individual mobile app notify services registered in this HA instance."""
    registered = getattr(hass.services, "async_services", lambda: {})()
    services = registered.get("notify", {})
    candidates = [f"notify.{service}" for service in services if service.startswith("mobile_app_")]
    candidates.extend(target for target in (existing or []) if isinstance(target, str) and target.startswith("notify.mobile_app_"))
    return sorted(set(candidates))


def notification_schema(settings: dict[str, Any], services: list[str]) -> vol.Schema:
    """Render a single final step containing all immutable-message subscriptions."""
    selected = [name for name in settings.get(NOTIFICATION_TARGETS, []) if name in services]
    return vol.Schema(
        {
            vol.Optional(NOTIFICATIONS_ENABLED, default=bool(settings.get(NOTIFICATIONS_ENABLED, False))): selector({"boolean": {}}),
            vol.Optional(NOTIFY_STATION_FINISHED, default=bool(settings.get(NOTIFY_STATION_FINISHED, True))): selector({"boolean": {}}),
            vol.Optional(NOTIFY_CYCLE_FINISHED, default=bool(settings.get(NOTIFY_CYCLE_FINISHED, True))): selector({"boolean": {}}),
            vol.Optional(NOTIFY_IRRIGATION_FAILED, default=bool(settings.get(NOTIFY_IRRIGATION_FAILED, True))): selector({"boolean": {}}),
            vol.Optional(NOTIFY_MANUAL_STOP, default=bool(settings.get(NOTIFY_MANUAL_STOP, True))): selector({"boolean": {}}),
            vol.Optional(NOTIFICATION_TARGETS, default=selected): selector(
                {"select": {"options": [{"value": name, "label": name} for name in services], "multiple": True, "mode": "dropdown"}}
            ),
        }
    )


def parse_notification_settings(user_input: dict[str, Any], available: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate recipients without allowing arbitrary service calls."""
    result = {key: bool(user_input.get(key, NOTIFICATION_DEFAULTS[key]))
              for key in (NOTIFICATIONS_ENABLED, NOTIFY_STATION_FINISHED, NOTIFY_CYCLE_FINISHED, NOTIFY_IRRIGATION_FAILED, NOTIFY_MANUAL_STOP)}
    targets = user_input.get(NOTIFICATION_TARGETS, [])
    if not isinstance(targets, list):
        return result, {NOTIFICATION_TARGETS: "invalid_targets"}
    targets = list(dict.fromkeys(targets))
    if any(not isinstance(target, str) or target not in available for target in targets):
        return result, {NOTIFICATION_TARGETS: "invalid_targets"}
    result[NOTIFICATION_TARGETS] = targets
    if result[NOTIFICATIONS_ENABLED] and not targets:
        return result, {NOTIFICATION_TARGETS: "target_required"}
    return result, {}
