"""Persistent storage helpers for Smart Water Controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .active_irrigation import ActiveIrrigationState
from ..helpers.util import ensure_aware

_LOGGER = logging.getLogger(__name__)


@dataclass
class PersistentIrrigationState:
    """Persistent state stored by the integration."""

    will_it_rain_today: bool = False
    will_it_rain_today_forecast: list[Any] = field(default_factory=list)
    has_rained_today: bool = False
    is_raining_now: bool = False
    is_raining_now_json: dict[str, Any] = field(default_factory=dict)

    last_reset: datetime = field(default_factory=dt_util.now)
    last_sprinkle: datetime = field(default_factory=dt_util.now)
    last_rain: datetime = field(default_factory=dt_util.now)

    irrigation_manual_duration: int = 10
    water_flow_rate: list[float] = field(default_factory=list)

    rain_time_today: float = 0
    rain_total_amount_today: float = 0
    rain_total_amount_forecasted_today: float = 0
    total_water_consumption: float = 0

    sprinkle_total_amount_today: list[float] = field(default_factory=list)
    sprinkle_target_amount_today: list[float] = field(default_factory=list)
    forecasted_sprinkle_today: list[float] = field(default_factory=list)
    remaining_sprinkle_today: list[float] = field(default_factory=list)

    schedule: list[dict[str, Any]] | None = None
    active_irrigation: ActiveIrrigationState | None = None


class IrrigationStorage:
    """Persistent storage wrapper for Smart Water Controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        storage_key: str,
        *,
        num_stations: int,
        station_name_resolver: Callable[[int | None], str | None],
        log_prefix: str = "",
    ) -> None:
        """Initialize irrigation storage."""
        self._store = Store(hass, 1, storage_key)
        self._num_stations = num_stations
        self._station_name_resolver = station_name_resolver
        self._log_prefix = log_prefix

    def update_num_stations(self, num_stations: int) -> None:
        """Update the expected number of stations."""
        self._num_stations = num_stations

    async def async_load(self) -> PersistentIrrigationState:
        """Load persistent state."""
        storage_data = await self._store.async_load()

        if not storage_data:
            _LOGGER.debug("%sNo persistent data found, using defaults.", self._log_prefix)
            return self._default_state()

        state = self._state_from_storage(storage_data)
        _LOGGER.debug("%sPersistent data loaded.", self._log_prefix)
        return state

    async def async_save(self, state: PersistentIrrigationState) -> None:
        """Save persistent state."""
        storage_data = self._state_to_storage(state)
        await self._store.async_save(storage_data)
        _LOGGER.debug("%sPersistent data saved.", self._log_prefix)

    def _default_state(self) -> PersistentIrrigationState:
        """Return default persistent state."""
        return PersistentIrrigationState(
            will_it_rain_today=False,
            will_it_rain_today_forecast=[],
            has_rained_today=False,
            is_raining_now=False,
            is_raining_now_json={},
            last_reset=dt_util.now(),
            last_sprinkle=dt_util.now(),
            last_rain=dt_util.now(),
            irrigation_manual_duration=10,
            water_flow_rate=[12] * self._num_stations,
            rain_time_today=0,
            rain_total_amount_today=0,
            rain_total_amount_forecasted_today=0,
            total_water_consumption=0,
            sprinkle_total_amount_today=[0.0] * self._num_stations,
            sprinkle_target_amount_today=[0.0] * self._num_stations,
            forecasted_sprinkle_today=[0.0] * self._num_stations,
            remaining_sprinkle_today=[0.0] * self._num_stations,
            schedule=None,
            active_irrigation=None,
        )

    def _state_from_storage(self, storage_data: dict[str, Any]) -> PersistentIrrigationState:
        """Build persistent state from raw storage data."""
        sprinkle_total_amount_today = _get_list(
            storage_data.get("sprinkle_total_amount_today"),
            expected_length=self._num_stations,
            default_value=0.0,
            log_prefix=self._log_prefix,
            field_name="sprinkle_total_amount_today",
        )

        sprinkle_target_amount_today = _get_list(
            storage_data.get("sprinkle_target_amount_today"),
            expected_length=self._num_stations,
            default_value=0.0,
            log_prefix=self._log_prefix,
            field_name="sprinkle_target_amount_today",
        )

        stored_forecasted_sprinkle_today = storage_data.get("forecasted_sprinkle_today")
        stored_remaining_sprinkle_today = storage_data.get("remaining_sprinkle_today")

        remaining_sprinkle_today = self._restore_remaining_sprinkle_today(
            stored_remaining_sprinkle_today,
            stored_forecasted_sprinkle_today,
        )

        # New meaning: forecasted_sprinkle_today is the planned amount for the day.
        # It is restored from sprinkle_target_amount_today to avoid preserving the old
        # pre-migration meaning where forecasted_sprinkle_today represented remaining water.
        forecasted_sprinkle_today = list(sprinkle_target_amount_today)

        water_flow_rate = _get_list(
            storage_data.get("water_flow_rate"),
            expected_length=self._num_stations,
            default_value=12,
            log_prefix=self._log_prefix,
            field_name="water_flow_rate",
        )

        active_irrigation = ActiveIrrigationState.from_storage(
            storage_data.get("active_irrigation"),
            station_name_resolver=self._station_name_resolver,
        )

        return PersistentIrrigationState(
            will_it_rain_today=bool(storage_data.get("will_it_rain_today", False)),
            will_it_rain_today_forecast=storage_data.get("will_it_rain_today_forecast") or [],
            has_rained_today=bool(storage_data.get("has_rained_today", False)),
            is_raining_now=bool(storage_data.get("is_raining_now", False)),
            is_raining_now_json=storage_data.get("is_raining_now_json") or {},
            last_reset=_parse_stored_datetime(storage_data.get("last_reset"), dt_util.now()),
            last_sprinkle=_parse_stored_datetime(storage_data.get("last_sprinkle"), dt_util.now()),
            last_rain=_parse_stored_datetime(storage_data.get("last_rain"), dt_util.now()),
            irrigation_manual_duration=int(storage_data.get("irrigation_manual_duration") or 10),
            water_flow_rate=water_flow_rate,
            rain_time_today=float(storage_data.get("rain_time_today", 0) or 0),
            rain_total_amount_today=float(storage_data.get("rain_total_amount_today", 0) or 0),
            rain_total_amount_forecasted_today=float(
                storage_data.get("rain_total_amount_forecasted_today", 0) or 0
            ),
            total_water_consumption=float(storage_data.get("total_water_consumption", 0) or 0),
            sprinkle_total_amount_today=sprinkle_total_amount_today,
            sprinkle_target_amount_today=sprinkle_target_amount_today,
            forecasted_sprinkle_today=forecasted_sprinkle_today,
            remaining_sprinkle_today=remaining_sprinkle_today,
            schedule=storage_data.get("schedule"),
            active_irrigation=active_irrigation,
        )

    def _restore_remaining_sprinkle_today(
        self,
        stored_remaining_sprinkle_today: Any,
        stored_forecasted_sprinkle_today: Any,
    ) -> list[float]:
        """Restore remaining sprinkle values with backward compatibility."""
        if _is_valid_list(stored_remaining_sprinkle_today, self._num_stations):
            return list(stored_remaining_sprinkle_today)

        if _is_valid_list(stored_forecasted_sprinkle_today, self._num_stations):
            _LOGGER.debug(
                "%sMigrating old forecasted_sprinkle_today values to remaining_sprinkle_today.",
                self._log_prefix,
            )
            return list(stored_forecasted_sprinkle_today)

        _LOGGER.debug(
            "%sInitializing remaining_sprinkle_today with default values.",
            self._log_prefix,
        )
        return [0.0] * self._num_stations

    def _state_to_storage(self, state: PersistentIrrigationState) -> dict[str, Any]:
        """Convert persistent state into raw storage data."""
        return {
            "will_it_rain_today": state.will_it_rain_today,
            "will_it_rain_today_forecast": state.will_it_rain_today_forecast,
            "has_rained_today": state.has_rained_today,
            "is_raining_now": state.is_raining_now,
            "is_raining_now_json": state.is_raining_now_json,
            "last_reset": _format_datetime(state.last_reset),
            "last_sprinkle": _format_datetime(state.last_sprinkle),
            "last_rain": _format_datetime(state.last_rain),
            "irrigation_manual_duration": state.irrigation_manual_duration,
            "water_flow_rate": state.water_flow_rate,
            "rain_time_today": state.rain_time_today,
            "rain_total_amount_today": state.rain_total_amount_today,
            "rain_total_amount_forecasted_today": state.rain_total_amount_forecasted_today,
            "total_water_consumption": state.total_water_consumption,
            "sprinkle_total_amount_today": state.sprinkle_total_amount_today,
            "sprinkle_target_amount_today": state.sprinkle_target_amount_today,
            "forecasted_sprinkle_today": state.forecasted_sprinkle_today,
            "remaining_sprinkle_today": state.remaining_sprinkle_today,
            "schedule": state.schedule,
            "active_irrigation": state.active_irrigation.to_storage()
            if state.active_irrigation
            else None,
        }


def _is_valid_list(value: Any, expected_length: int) -> bool:
    """Return True if value is a list with the expected length."""
    return isinstance(value, list) and len(value) == expected_length


def _get_list(
    value: Any,
    *,
    expected_length: int,
    default_value: Any,
    log_prefix: str,
    field_name: str,
) -> list[Any]:
    """Return a stored list or a default list."""
    if _is_valid_list(value, expected_length):
        return list(value)

    _LOGGER.debug("%sInitializing %s with default values.", log_prefix, field_name)
    return [default_value] * expected_length


def _parse_stored_datetime(value: Any, default: datetime) -> datetime:
    """Parse a stored datetime."""
    if isinstance(value, datetime):
        return ensure_aware(value)

    if isinstance(value, str):
        for parser in (
            lambda text: datetime.strptime(text, "%Y-%m-%d %H:%M:%S"),
            datetime.fromisoformat,
        ):
            try:
                return ensure_aware(parser(value))
            except ValueError:
                continue

        _LOGGER.warning("Invalid stored datetime value: %s", value)

    return ensure_aware(default)


def _format_datetime(value: datetime | None) -> str:
    """Format datetime for storage."""
    return ensure_aware(value or datetime.min).strftime("%Y-%m-%d %H:%M:%S")