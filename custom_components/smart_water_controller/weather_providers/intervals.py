"""UTC precipitation intervals, independent of provider display buckets."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone


@dataclass(frozen=True)
class RainInterval:
    """An amount uniformly distributed over an explicit UTC interval."""

    start: datetime
    end: datetime
    millimetres: float
    probability: float = 0.0

    def overlap_fraction(self, start: datetime, end: datetime) -> float:
        """Return elapsed-time overlap (never wall-clock arithmetic across DST)."""
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        seconds = (min(self.end, end) - max(self.start, start)).total_seconds()
        return max(0.0, seconds) / (self.end - self.start).total_seconds()


def remaining_day(now: datetime) -> tuple[datetime, datetime]:
    """Return now through next *local* midnight as UTC instants (23/25h days)."""
    midnight = datetime.combine(now.date() + timedelta(days=1), time.min, now.tzinfo)
    return now.astimezone(timezone.utc), midnight.astimezone(timezone.utc)


def remaining_rain(intervals: list[RainInterval], now: datetime) -> float:
    """Integrate interval amounts over the remaining local calendar day."""
    start, end = remaining_day(now)
    return sum(item.millimetres * item.overlap_fraction(start, end) for item in intervals)


def remaining_probability(intervals: list[RainInterval], now: datetime) -> bool:
    """Whether an overlapping forecast exceeds the existing 50% threshold."""
    start, end = remaining_day(now)
    return any(item.probability > 0.5 and item.overlap_fraction(start, end) > 0 for item in intervals)
