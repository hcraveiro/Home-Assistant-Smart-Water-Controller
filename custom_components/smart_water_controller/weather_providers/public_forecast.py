"""Small presentation-only forecasts; never used for irrigation accounting."""
from datetime import datetime, timedelta, timezone
import math

from .intervals import remaining_day


def compact_today_forecast(forecast: list, now: datetime) -> list[dict]:
    """Expose remaining-day blocks without copying unbounded provider payloads.

    Keep legacy dt/dt_txt/pop/rain keys for custom consumers. Their timestamps
    and rain[3h] retain provider block semantics (OWM ends, PirateWeather starts).
    Explicit start/end identify that original block, including a partial current
    block. Display amounts are not re-prorated or used for accounting.
    Internal display caches and normalized accounting intervals are untouched.
    """
    day_start, day_end = remaining_day(now)
    result = []
    for item in forecast or []:
        if not isinstance(item, dict):
            continue
        try:
            if "dt" in item:
                stamp = datetime.fromtimestamp(float(item["dt"]), timezone.utc)
                start, end = stamp - timedelta(hours=3), stamp
            else:
                stamp = datetime.strptime(item["dt_txt"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo)
                start = stamp.astimezone(timezone.utc)
                end = (stamp + timedelta(hours=3)).astimezone(timezone.utc)
            start_clip, end_clip = max(start, day_start), min(end, day_end)
            if start_clip >= end_clip:
                continue
            rain_data = item.get("rain") or {}
            if not isinstance(rain_data, dict):
                continue
            rain = float(rain_data.get("3h") or 0)
            probability = float(item.get("pop") or 0)
            if not all(math.isfinite(value) for value in (rain, probability)):
                continue
            row = {
                "dt_txt": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "pop": probability,
                "rain": {"3h": rain},
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
            if "dt" in item:
                row["dt"] = int(stamp.timestamp())
            result.append(row)
        except (ValueError, TypeError, KeyError, OverflowError, OSError, ZeroDivisionError):
            continue
    # A normal day has at most nine three-hour provider blocks. Keep an explicit
    # bound as a defensive Recorder-size guarantee even for malformed payloads.
    return sorted(result, key=lambda row: row["start"])[:32]
