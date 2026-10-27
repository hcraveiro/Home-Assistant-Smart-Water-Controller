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
    SUPPORTED_PARAM_TYPES,
)


def build_solem_toolkit_defaults(model: str = "bl_ip") -> dict[str, Any]:
    """Return independent preset actions with explicit Toolkit model routing."""
    if model not in ("bl_ip", "bl_nr"):
        raise ValueError("Unsupported Solem Toolkit model")
    actions = {
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

    for config in actions.values():
        config[SERVICE_ACTION_PARAMS].append({
            SERVICE_PARAM_NAME: "model",
            SERVICE_PARAM_LABEL: "Solem model",
            SERVICE_PARAM_VALUE: model,
            SERVICE_PARAM_TYPE: SERVICE_PARAM_TYPE_OTHER,
        })
    return actions


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
    """Validate the editable parameter list without changing its stored format."""
    raw_params = user_input.get(SERVICE_ACTION_PARAMS, [])
    if not isinstance(raw_params, list):
        return [], {"base": "invalid_parameter"}

    params: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_params:
        if not isinstance(item, dict):
            return [], {"base": "invalid_parameter"}
        name = item.get(SERVICE_PARAM_NAME, "")
        label = item.get(SERVICE_PARAM_LABEL, "")
        value = item.get(SERVICE_PARAM_VALUE, "")
        ptype = item.get(SERVICE_PARAM_TYPE) or SERVICE_PARAM_TYPE_OTHER
        if not isinstance(name, str) or not name.strip() or not isinstance(label, str):
            return [], {"base": "invalid_parameter"}
        if not isinstance(ptype, str) or ptype not in SUPPORTED_PARAM_TYPES:
            return [], {"base": "invalid_parameter"}
        if value is not None and not isinstance(value, (str, int, float, bool)):
            return [], {"base": "invalid_parameter"}
        name = name.strip()
        if name in seen:
            return [], {"base": "duplicate_parameter"}
        seen.add(name)
        value = "" if value is None else str(value).strip()
        if ptype == SERVICE_PARAM_TYPE_MAC and value and not _is_mac_address(value):
            return [], {"base": "invalid_mac"}
        params.append({
            SERVICE_PARAM_NAME: name,
            SERVICE_PARAM_LABEL: label.strip(),
            SERVICE_PARAM_VALUE: value,
            SERVICE_PARAM_TYPE: ptype,
        })
    return params, {}


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
        "Use + to add parameters, or remove entries you do not need.\n"
        "Fill only the values you want to fix.\n"
        "Empty values will be provided by the coordinator at runtime."
    )
