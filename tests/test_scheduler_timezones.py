"""Absolute scheduling, DST and midnight ordering; no real HA callbacks."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import ha_stub
from custom_components.smart_water_controller.helpers import planning
from custom_components.smart_water_controller.runtime import scheduler

LISBON = ZoneInfo('Europe/Lisbon')


class PlanningDSTTests(unittest.TestCase):
    def assert_daily_plan(self, now, hours, expected, index):
        schedule = [{} for _ in range(12)]
        schedule[now.month - 1] = {
            'hours': hours, 'stations': {'station_1_minutes': 10},
        }
        target = planning.calculate_sprinkle_target_amounts(
            schedule=schedule, num_stations=1, water_flow_rate=[1],
            station_areas=[1], sprinkle_total_amount_today=[0], now=now,
        )
        self.assertEqual(target, [expected])
        slot_index = planning.get_current_slot_index(
            hours, scheduled_hour='06:00:00', now=now,
        )
        self.assertEqual(slot_index, index)
        plan = planning.calculate_station_slot_irrigation_plan(
            station_id=1, scheduled_minutes=10, current_slot_index=slot_index,
            flow_rate=1, area=1, already_applied_mm=0,
            expected_rain_today_mm=0, daily_target_mm=target[0],
        )
        self.assertEqual(plan.amount_to_apply_mm, expected)
        self.assertEqual(plan.minutes_needed, expected)

    def test_spring_gap_is_not_daily_target_or_catchup_debt(self):
        self.assert_daily_plan(
            datetime(2026, 3, 29, 6, tzinfo=LISBON),
            ['01:30:00', '06:00:00'], 10, 1,
        )

    def test_all_nonexistent_slots_have_zero_daily_target(self):
        self.assert_daily_plan(
            datetime(2026, 3, 29, 6, tzinfo=LISBON), ['01:30:00'], 0, 1,
        )

    def test_autumn_repeated_slot_counts_once(self):
        self.assert_daily_plan(
            datetime(2026, 10, 25, 6, tzinfo=LISBON),
            ['01:30:00', '06:00:00'], 20, 2,
        )

    def test_missed_valid_slot_still_counts_as_catchup_debt(self):
        self.assert_daily_plan(
            datetime(2026, 3, 30, 6, tzinfo=LISBON),
            ['01:30:00', '06:00:00'], 20, 2,
        )


class SchedulerTimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.points = []
        self.tasks = []
        self.events = []

        def create_task(coro):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

        async def reset():
            self.events.append('reset')

        async def run(**kwargs):
            self.events.append(('run', kwargs['scheduled_hour']))

        self.c = SimpleNamespace(
            hass=SimpleNamespace(), controller_mac_address='test',
            schedule=[{} for _ in range(12)],
            async_create_task=create_task,
            async_ensure_current_day=AsyncMock(side_effect=reset),
            run_watering_cycle=AsyncMock(side_effect=run),
            _get_future_watering_slots_for_day=planning.get_future_watering_slots_for_day,
            _get_valid_watering_hours=planning.get_valid_watering_hours,
            _recent_event_blocks_today=lambda interval: False,
        )
        self.scheduler = scheduler.IrrigationScheduler(self.c)
        self.patcher = patch.object(scheduler, 'async_track_point_in_utc_time', side_effect=self.register)
        self.patcher.start()

    async def asyncTearDown(self):
        self.scheduler.clear()
        self.patcher.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def register(self, hass, callback, instant):
        record = SimpleNamespace(callback=callback, instant=instant, cancelled=False)
        self.points.append(record)
        def cancel():
            record.cancelled = True
        return cancel

    async def test_dst_days_use_absolute_utc_instants(self):
        for day, utc_hour in ((datetime(2026, 3, 29, 0, 1, tzinfo=LISBON), 5),
                              (datetime(2026, 10, 25, 0, 1, tzinfo=LISBON), 6)):
            with self.subTest(day=day):
                self.c.schedule[day.month - 1] = {'hours': ['06:00:00']}
                with patch.object(scheduler.dt_util, 'now', return_value=day):
                    await self.scheduler.check_and_schedule_watering()
                today = next(p for p in self.points if not p.cancelled and p.instant.date() == day.date())
                self.assertEqual(today.instant, datetime(day.year, day.month, day.day, utc_hour, tzinfo=timezone.utc))
                self.assertEqual(today.instant.timestamp() - day.timestamp(), 17940 if day.month == 3 else 25140)

    async def test_lookahead_includes_next_month_midnight(self):
        now = datetime(2026, 11, 30, 23, 50, tzinfo=LISBON)
        self.c.schedule[11] = {'hours': ['00:00:00']}
        with patch.object(scheduler.dt_util, 'now', return_value=now):
            await self.scheduler.check_and_schedule_watering()
        self.assertEqual([p.instant for p in self.points], [datetime(2026, 12, 1, tzinfo=timezone.utc)])
        midnight = datetime(2026, 12, 1, tzinfo=LISBON)
        with patch.object(scheduler.dt_util, 'now', return_value=midnight):
            self.points[0].callback(midnight)
            await asyncio.gather(*self.tasks)
        self.assertEqual(self.events, ['reset', ('run', '00:00:00')])

    async def test_dispatch_rechecks_interval_not_yesterdays_eligibility(self):
        now = datetime(2026, 11, 4, 23, 50, tzinfo=LISBON)
        self.c.schedule[10] = {'hours': ['00:00:00'], 'interval_days': 2}
        self.c._recent_event_blocks_today = lambda interval: True
        with patch.object(scheduler.dt_util, 'now', return_value=now):
            await self.scheduler.check_and_schedule_watering()
        self.assertEqual(len(self.points), 1)
        midnight = datetime(2026, 11, 5, tzinfo=LISBON)
        with patch.object(scheduler.dt_util, 'now', return_value=midnight):
            self.points[0].callback(midnight)
            await asyncio.gather(*self.tasks)
        self.c.run_watering_cycle.assert_not_awaited()
        self.c.async_ensure_current_day.assert_awaited_once()

    async def test_cleared_schedule_and_closed_runtime_do_not_dispatch(self):
        now = datetime(2026, 11, 5, tzinfo=LISBON)
        self.c.schedule[10] = {'hours': ['06:00:00']}
        with patch.object(scheduler.dt_util, 'now', return_value=now):
            await self.scheduler.check_and_schedule_watering()
            self.c.schedule[10] = {}
            self.points[0].callback(now)
            await asyncio.gather(*self.tasks)
            self.scheduler.clear()
            self.points[-1].callback(now)
            await self.scheduler.check_and_schedule_watering()
        self.c.run_watering_cycle.assert_not_awaited()
        self.assertTrue(all(point.cancelled for point in self.points))

    async def test_nonexistent_spring_time_is_skipped_and_autumn_fold_runs_once(self):
        spring = datetime(2026, 3, 29, 0, 1, tzinfo=LISBON)
        self.assertEqual(planning.get_future_watering_slots_for_day(
            spring.date(), {'hours': ['01:30:00']}, now=spring), [])
        autumn = datetime(2026, 10, 25, 0, 1, tzinfo=LISBON)
        slots = planning.get_future_watering_slots_for_day(
            autumn.date(), {'hours': ['01:30:00']}, now=autumn)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0][1].fold, 0)
        after_first = datetime(2026, 10, 25, 1, 15, tzinfo=LISBON, fold=1)
        self.assertEqual(planning.get_future_watering_slots_for_day(
            autumn.date(), {'hours': ['01:30:00']}, now=after_first), [])

    async def test_restart_does_not_replay_past_slots(self):
        now = datetime(2026, 11, 5, 9, 1, tzinfo=LISBON)
        self.c.schedule[10] = {'hours': ['09:00:00', '18:00:00']}
        with patch.object(scheduler.dt_util, 'now', return_value=now):
            await self.scheduler.check_and_schedule_watering()
        self.assertTrue(all(point.instant.timestamp() > now.timestamp() for point in self.points))
        self.assertEqual(len(self.points), 3)
