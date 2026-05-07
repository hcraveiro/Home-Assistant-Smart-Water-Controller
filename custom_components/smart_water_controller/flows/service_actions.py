"""Service action flow helpers for Smart Water Controller."""

from __future__ import annotations

from typing import Any

from .common import _is_mac_address
from ..const import (
    ACTION_SPRINKLE_STATION,
    ACTION_STOP_SPRINKLE,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    SERVICE_ACTION_ENABLED,
    SERVICE_ACTION_PARAMS,
    SERVICE_ACTION_SERVICE,
    SERVICE_PARAM_LABEL,
    SERVICE_PARAM_NAME,
    SERVICE_PARAM_TYPE,
    SERVICE_PARAM_TYPE_MAC,
    SERVICE_PARAM_TYPE_OTHER,
    SERVICE_PARAM_TYPE_STATION,
    SERVICE_PARAM_TYPE_TIME,
    SERVICE_PARAM_VALUE,
    SOLEM_TOOLKIT_SERVICE_SPRINKLE,
    SOLEM_TOOLKIT_SERVICE_STOP,
    SOLEM_TOOLKIT_SERVICE_TURN_OFF,
    SOLEM_TOOLKIT_SERVICE_TURN_ON,
    SUPPORTED_ACTIONS_IN_ORDER,
)


def build_solem_toolkit_defaults() -> dict[str, Any]:
    """Return default SERVICE_ACTIONS configuration for Solem Toolkit."""
    return {
        ACTION_SPRINKLE_STATION: {
            SERVICE_ACTION_ENABLED: True,
            SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_SPRINKLE,
            SERVICE_ACTION_PARAMS: [
                {
                    SERVICE_PARAM_NAME: "device_mac",
                    SERVICE_PARAM_LABEL: "MAC Address",
                    SERVICE_PARAM_VALUE: "",
                    SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                },
                {
                    SERVICE_PARAM_NAME: "station",
                    SERVICE_PARAM_LABEL: "Station",
                    SERVICE_PARAM_VALUE: "",
                    SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_STATION,
                },
                {
                    SERVICE_PARAM_NAME: "minutes",
                    SERVICE_PARAM_LABEL: "Minutes to sprinkle",
                    SERVICE_PARAM_VALUE: "",
                    SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_TIME,
                },
            ],
        },
        ACTION_STOP_SPRINKLE: {
            SERVICE_ACTION_ENABLED: True,
            SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_STOP,
            SERVICE_ACTION_PARAMS: [
                {
                    SERVICE_PARAM_NAME: "device_mac",
                    SERVICE_PARAM_LABEL: "MAC Address",
                    SERVICE_PARAM_VALUE: "",
                    SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                }
            ],
        },
        ACTION_TURN_ON: {
            SERVICE_ACTION_ENABLED: True,
            SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_TURN_ON,
            SERVICE_ACTION_PARAMS: [
                {
                    SERVICE_PARAM_NAME: "device_mac",
                    SERVICE_PARAM_LABEL: "MAC Address",
                    SERVICE_PARAM_VALUE: "",
                    SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                }
            ],
        },
        ACTION_TURN_OFF: {
            SERVICE_ACTION_ENABLED: True,
            SERVICE_ACTION_SERVICE: SOLEM_TOOLKIT_SERVICE_TURN_OFF,
            SERVICE_ACTION_PARAMS: [
                {
                    SERVICE_PARAM_NAME: "device_mac",
                    SERVICE_PARAM_LABEL: "MAC Address",
                    SERVICE_PARAM_VALUE: "",
                    SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_MAC,
                }
            ],
        },
    }


def parse_service_config_form(
    existing_actions: dict[str, Any],
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """Parse the service config form into SERVICE_ACTIONS format."""
    actions_config: dict[str, Any] = {}
    selected_actions: list[str] = []
    errors: dict[str, str] = {}

    for action in SUPPORTED_ACTIONS_IN_ORDER:
        enabled = bool(user_input.get(f"enable_{action}", False))
        service_call = (user_input.get(f"service_{action}") or "").strip()

        if enabled and (not service_call or "." not in service_call):
            errors[f"service_{action}"] = "invalid_service"

        existing_action = existing_actions.get(action, {})
        existing_params = existing_action.get(SERVICE_ACTION_PARAMS, []) or []

        actions_config[action] = {
            SERVICE_ACTION_ENABLED: enabled,
            SERVICE_ACTION_SERVICE: service_call if enabled else "",
            SERVICE_ACTION_PARAMS: existing_params,
        }

        if enabled:
            selected_actions.append(action)

    return actions_config, selected_actions, errors


def parse_action_params_form(
    user_input: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Parse parameter fields for a single action configuration step."""
    params: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    for idx in range(1, 6):
        name = (user_input.get(f"param_{idx}_name") or "").strip()
        label = (user_input.get(f"param_{idx}_label") or "").strip()
        value = (user_input.get(f"param_{idx}_value") or "").strip()
        ptype = (user_input.get(f"param_{idx}_type") or "").strip()

        if not name and not label and not value and not ptype:
            continue

        if not ptype:
            ptype = SERVICE_PARAM_TYPE_OTHER

        if ptype == SERVICE_PARAM_TYPE_MAC and value and not _is_mac_address(value):
            errors["base"] = "invalid_mac"

        params.append(
            {
                SERVICE_PARAM_NAME: name,
                SERVICE_PARAM_LABEL: label,
                SERVICE_PARAM_VALUE: value if value else "",
                SERVICE_PARAM_TYPE: ptype,
            }
        )

    return params, errors


def find_first_mac_in_enabled_actions(actions_config: dict[str, Any]) -> str | None:
    """Return the first MAC parameter value found in enabled actions."""
    for action in SUPPORTED_ACTIONS_IN_ORDER:
        cfg = actions_config.get(action, {})
        if not cfg.get(SERVICE_ACTION_ENABLED):
            continue

        for param in (cfg.get(SERVICE_ACTION_PARAMS, []) or []):
            if param.get(SERVICE_PARAM_TYPE) == SERVICE_PARAM_TYPE_MAC:
                candidate = str(param.get(SERVICE_PARAM_VALUE, "") or "").strip()
                if candidate:
                    return candidate

    return None


def build_action_description(action: str, service_call: str) -> str:
    """Build action form description."""
    return (
        f"Action: {action}\n"
        f"Service: {service_call}\n\n"
        "Fill only the parameters you want to hardcode.\n"
        "Empty values will be provided by the coordinator at runtime."
    )