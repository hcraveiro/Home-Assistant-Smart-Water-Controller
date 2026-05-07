"""Active irrigation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.util import dt as dt_util

from ..helpers.util import ensure_aware


@dataclass
class ActiveIrrigationState:
    """Represents the currently active irrigation."""

    station: int
    source: str
    duration_minutes: int | None
    start_at: datetime | None
    end_at: datetime | None
    station_name: str | None

    @classmethod
    def create(
        cls,
        *,
        station: int,
        source: str,
        duration_minutes: int | None,
        station_name: str | None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> "ActiveIrrigationState":
        """Create a new active irrigation state."""
        aware_start_at = ensure_aware(start_at or dt_util.now())

        aware_end_at: datetime | None = None
        if end_at is not None:
            aware_end_at = ensure_aware(end_at)
        elif duration_minutes is not None:
            aware_end_at = aware_start_at + timedelta(minutes=duration_minutes)

        return cls(
            station=int(station),
            source=source,
            duration_minutes=int(duration_minutes) if duration_minutes is not None else None,
            start_at=aware_start_at,
            end_at=aware_end_at,
            station_name=station_name,
        )

    @classmethod
    def from_storage(
        cls,
        data: Any,
        *,
        station_name_resolver: Callable[[int | None], str | None],
    ) -> "ActiveIrrigationState | None":
        """Restore an active irrigation state from storage."""
        if not isinstance(data, dict):
            return None

        station = data.get("station")
        source = data.get("source", "automatic")
        duration_minutes = data.get("duration_minutes")
        start_at_value = data.get("start_at")
        end_at_value = data.get("end_at")

        try:
            station_id = int(station)
        except (TypeError, ValueError):
            return None

        start_at = _parse_datetime(start_at_value)
        end_at = _parse_datetime(end_at_value)

        return cls(
            station=station_id,
            source=str(source),
            duration_minutes=int(duration_minutes) if duration_minutes is not None else None,
            start_at=start_at,
            end_at=end_at,
            station_name=station_name_resolver(station_id),
        )

    def to_storage(self) -> dict[str, Any]:
        """Serialize active irrigation to storage."""
        return {
            "station": self.station,
            "source": self.source,
            "duration_minutes": self.duration_minutes,
            "start_at": self.start_at.isoformat() if self.start_at else None,
            "end_at": self.end_at.isoformat() if self.end_at else None,
        }

    def controller_attributes(self) -> dict[str, Any]:
        """Return controller status attributes."""
        return {
            "irrigation_source": self.source,
            "current_station": self.station,
            "current_station_name": self.station_name,
            "current_duration_minutes": self.duration_minutes,
            "current_started_at": self.start_at.isoformat() if self.start_at else None,
            "current_end_at": self.end_at.isoformat() if self.end_at else None,
        }

    def station_attributes(self, station_id: int) -> dict[str, Any]:
        """Return station status attributes."""
        if int(station_id) != self.station:
            return empty_station_attributes()

        return {
            "irrigation_source": self.source,
            "duration_minutes": self.duration_minutes,
            "started_at": self.start_at.isoformat() if self.start_at else None,
            "end_at": self.end_at.isoformat() if self.end_at else None,
        }

    def has_ended(self, now: datetime | None = None) -> bool:
        """Return True if the irrigation end time has already passed."""
        if self.end_at is None:
            return False

        return ensure_aware(now or dt_util.now()) >= self.end_at


def empty_controller_attributes() -> dict[str, Any]:
    """Return empty controller irrigation attributes."""
    return {
        "irrigation_source": None,
        "current_station": None,
        "current_station_name": None,
        "current_duration_minutes": None,
        "current_started_at": None,
        "current_end_at": None,
    }


def empty_station_attributes() -> dict[str, Any]:
    """Return empty station irrigation attributes."""
    return {
        "irrigation_source": None,
        "duration_minutes": None,
        "started_at": None,
        "end_at": None,
    }


def _parse_datetime(value: Any) -> datetime | None:
    """Parse a datetime from storage."""
    if not value:
        return None

    if isinstance(value, datetime):
        return ensure_aware(value)

    if isinstance(value, str):
        try:
            return ensure_aware(datetime.fromisoformat(value))
        except ValueError:
            return None

    return None