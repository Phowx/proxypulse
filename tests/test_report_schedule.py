from __future__ import annotations

from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase, TestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.services.report_schedule import get_daily_report_schedule, parse_daily_report_clock, set_daily_report_schedule
from proxypulse.services.reports import should_send_daily_report


class ReportScheduleTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_daily_report_schedule_defaults_then_persists_update(self) -> None:
        async with self.session_factory() as session:
            default_schedule = await get_daily_report_schedule(session)
            self.assertEqual(default_schedule.clock_text, "09:00")

            updated_schedule = await set_daily_report_schedule(session, hour=8, minute=30)
            self.assertEqual(updated_schedule.clock_text, "08:30")

            persisted_schedule = await get_daily_report_schedule(session)
            self.assertEqual(persisted_schedule.clock_text, "08:30")


class ReportScheduleParsingTests(TestCase):
    def test_parse_daily_report_clock_and_should_send(self) -> None:
        hour, minute = parse_daily_report_clock("08:30")
        self.assertEqual((hour, minute), (8, 30))

        self.assertFalse(should_send_daily_report(datetime(2026, 4, 5, 8, 29, tzinfo=timezone.utc), hour=8, minute=30))
        self.assertTrue(should_send_daily_report(datetime(2026, 4, 5, 8, 30, tzinfo=timezone.utc), hour=8, minute=30))
