"""Focused regression tests for persistence and schedule calculations."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import unittest

import ha_stub  # noqa: F401
from custom_components.smart_water_controller.helpers import planning
from custom_components.smart_water_controller.runtime.storage import IrrigationStorage


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = IrrigationStorage(
            None, "coverage-test", num_stations=2,
            station_name_resolver=lambda station: f"Zone {station}",
        )

    async def test_unchanged_state_is_not_rewritten(self):
        state = self.storage._default_state()
        self.storage._store.async_save = AsyncMock()
        await self.storage.async_save(state)
        await self.storage.async_save(state)
        self.assertEqual(self.storage._store.async_save.await_count, 1)

    async def test_mutated_live_list_is_persisted(self):
        state = self.storage._default_state()
        self.storage._store.async_save = AsyncMock()
        await self.storage.async_save(state)
        state.pending_stop_stations.append(2)
        await self.storage.async_save(state)
        self.assertEqual(self.storage._store.async_save.await_count, 2)
        self.assertEqual(
            self.storage._store.async_save.await_args.args[0]["pending_stop_stations"], [2]
        )

    async def test_failed_write_is_retried(self):
        state = self.storage._default_state()
        self.storage._store.async_save = AsyncMock(
            side_effect=[OSError("disk temporarily unavailable"), None]
        )
        with self.assertRaises(OSError):
            await self.storage.async_save(state)
        await self.storage.async_save(state)
        self.assertEqual(self.storage._store.async_save.await_count, 2)

    def test_missing_event_timestamps_round_trip_as_none(self):
        state = self.storage._default_state()
        saved = self.storage._state_to_storage(state)
        self.assertEqual(saved["last_rain"], "")
        self.assertEqual(saved["last_sprinkle"], "")
        restored = self.storage._state_from_storage(saved)
        self.assertIsNone(restored.last_rain)
        self.assertIsNone(restored.last_sprinkle)

    def test_invalid_stop_stations_are_discarded_on_restore(self):
        state = self.storage._default_state()
        saved = self.storage._state_to_storage(state)
        saved["pending_stop_stations"] = [0, 2, 3, 2, -1]
        restored = self.storage._state_from_storage(saved)
        self.assertEqual(restored.pending_stop_stations, [2])

    def test_daily_duration_counters_survive_persistence_round_trip(self):
        state = self.storage._default_state()
        state.sprinkle_duration_today_seconds = [203.25, 600.0]
        saved = self.storage._state_to_storage(state)
        restored = self.storage._state_from_storage(saved)
        self.assertEqual(restored.sprinkle_duration_today_seconds, [203.25, 600.0])

    def test_legacy_storage_without_daily_duration_defaults_to_zero(self):
        saved = self.storage._state_to_storage(self.storage._default_state())
        saved.pop("sprinkle_duration_today_seconds")
        restored = self.storage._state_from_storage(saved)
        self.assertEqual(restored.sprinkle_duration_today_seconds, [0.0, 0.0])

    async def test_daily_duration_changes_trigger_persistence(self):
        state = self.storage._default_state()
        self.storage._store.async_save = AsyncMock()
        await self.storage.async_save(state)
        state.sprinkle_duration_today_seconds[0] += 12.5
        await self.storage.async_save(state)
        self.assertEqual(self.storage._store.async_save.await_count, 2)

    def test_short_legacy_arrays_default_to_station_count(self):
        saved = self.storage._state_to_storage(self.storage._default_state())
        saved["water_flow_rate"] = [7]
        saved["remaining_sprinkle_today"] = [1]
        saved["forecasted_sprinkle_today"] = [2]
        restored = self.storage._state_from_storage(saved)
        self.assertEqual(restored.water_flow_rate, [12, 12])
        self.assertEqual(restored.remaining_sprinkle_today, [0.0, 0.0])


class PlanningRegressionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
        self.schedule = [{} for _ in range(12)]
        self.schedule[9] = {
            "hours": ["08:00:00", "18:00:00"],
            "stations": {"station_1_minutes": 5, "station_2_minutes": 0},
            "interval_days": 2,
        }

    def targets(self, **options):
        return planning.calculate_sprinkle_target_amounts(
            schedule=self.schedule,
            num_stations=2,
            water_flow_rate=[20, 10],
            station_areas=[50, 50],
            sprinkle_total_amount_today=[1.0, 0.0],
            now=self.now,
            **options,
        )

    def test_daily_targets_cover_both_slots(self):
        self.assertEqual(self.targets(), [4.0, 0.0])

    def test_only_future_slot_excludes_past_watering(self):
        self.assertEqual(self.targets(only_future_slots=True), [2.0, 0.0])

    def test_no_future_slots_preserves_applied_water_when_requested(self):
        with patch.object(planning, "get_future_watering_slots_for_day", return_value=[]):
            self.assertEqual(
                self.targets(only_future_slots=True, include_already_applied=True),
                [1.0, 0.0],
            )

    def test_rain_reduces_outstanding_irrigation_without_negative_amount(self):
        self.assertEqual(
            planning.calculate_remaining_sprinkle_today(
                target_mm=4, applied_mm=1, expected_rain_today_mm=2
            ), 1.0
        )
        self.assertEqual(
            planning.calculate_remaining_sprinkle_today(
                target_mm=4, applied_mm=1, expected_rain_today_mm=6
            ), 0.0
        )

    def test_invalid_station_duration_is_treated_as_zero(self):
        self.schedule[9]["stations"]["station_1_minutes"] = "invalid"
        with self.assertLogs(planning._LOGGER, "WARNING"):
            self.assertEqual(self.targets(), [0.0, 0.0])

    def test_description_includes_only_configured_months(self):
        result = planning.describe_schedule(self.schedule, ["Lawn", "Flowers"])
        self.assertEqual(len(result), 1)
        self.assertIn("October:", result[0])
        self.assertIn("5 min on Lawn", result[0])
        self.assertIn("every 2 days", result[0])

    def test_invalid_flow_rate_does_not_schedule_irrigation(self):
        self.assertEqual(planning.calculate_mm_per_minute("invalid", 50), 0.0)
        self.assertEqual(
            planning.calculate_daily_station_target_mm(
                scheduled_minutes=10, occurrences=2,
                flow_rate=0, area=50, already_applied_mm=1.2
            ), 1.2
        )


if __name__ == "__main__":
    unittest.main()
