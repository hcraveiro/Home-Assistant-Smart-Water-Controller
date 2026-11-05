"""Immutable execution facts captured before an irrigation start.

This adapts existing API capabilities, not physical flow telemetry. Recovery of
persisted intents still uses the configured API because contracts are in-memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..const import SOLEM_CONTROL_METHODS


@dataclass(frozen=True)
class ExecutionContract:
    """One start's duration, shutdown obligation and acknowledgement semantics."""

    backend_kind: Literal["switch", "service", "native_timed_service"]
    effective_seconds: float
    expiry_policy: Literal["explicit_stop", "controller_timer"]
    confirmation_basis: Literal["switch_state", "service_ack_and_elapsed"]
    command_budget: float
    stop_scope: Literal["station_may_stop_all_zones", "controller"]

    @property
    def is_switch(self) -> bool:
        return self.backend_kind == "switch"

    @property
    def controller_managed_duration(self) -> bool:
        return self.expiry_policy == "controller_timer"

    @property
    def completion_reason(self) -> str:
        if self.controller_managed_duration:
            return "Controller-managed duration elapsed; water flow not verified"
        if self.is_switch:
            return "Station switch confirmed OFF; water flow not verified"
        return "Local duration elapsed and stop acknowledged; water flow not verified"


def resolve_execution_contract(
    *, control_method: str, is_switch: bool, api,
    duration_minutes: int, requested_seconds: float | None,
    switch_budget: float, service_budget: float,
) -> ExecutionContract:
    """Resolve editable preset mappings once, rather than trusting preset names."""
    native = (
        not is_switch and control_method in SOLEM_CONTROL_METHODS
        and api.uses_native_watering_duration()
    )
    full_seconds = duration_minutes * 60
    seconds = full_seconds if requested_seconds is None else min(full_seconds, requested_seconds)
    return ExecutionContract(
        backend_kind="switch" if is_switch else "native_timed_service" if native else "service",
        effective_seconds=full_seconds if native else seconds,
        expiry_policy="controller_timer" if native else "explicit_stop",
        confirmation_basis="switch_state" if is_switch else "service_ack_and_elapsed",
        command_budget=switch_budget if is_switch else service_budget,
        stop_scope="station_may_stop_all_zones" if is_switch else "controller",
    )
