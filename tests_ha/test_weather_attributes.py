"""Exercise the real HA entity presentation boundary without devices."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.smart_water_controller.entities.binary_sensor import WillRainToday


def test_forecast_entity_keeps_raw_cache_but_publishes_small_attributes():
    now = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
    raw = [{'dt': int(now.replace(hour=18).timestamp()), 'pop': .7,
            'rain': {'3h': 2}, 'main': {'large_payload': 'x' * 20000}}]
    original = deepcopy(raw)
    entity = object.__new__(WillRainToday)
    entity.coordinator = SimpleNamespace(will_it_rain_today=True, will_it_rain_today_forecast=raw)
    with patch('custom_components.smart_water_controller.entities.binary_sensor.dt_util.now', return_value=now):
        attrs = entity.extra_state_attributes
        assert entity.is_on is True
    assert len(json.dumps(attrs).encode()) < 8192
    assert attrs['forecast'][0]['rain']['3h'] == 2
    assert 'main' not in attrs['forecast'][0]
    assert raw == original
