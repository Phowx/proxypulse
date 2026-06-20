from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.bot import main as bot_main
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

    async def test_sent_daily_report_skips_expensive_summary(self) -> None:
        class SessionContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        summarize = AsyncMock()
        with (
            patch.object(bot_main, "SessionLocal", return_value=SessionContext()),
            patch.object(
                bot_main,
                "get_daily_report_schedule",
                AsyncMock(return_value=SimpleNamespace(hour=0, minute=0)),
            ),
            patch.object(bot_main, "should_send_daily_report", return_value=True),
            patch.object(bot_main, "has_daily_report_run", AsyncMock(return_value=True)),
            patch.object(bot_main, "summarize_previous_local_day", summarize),
        ):
            await bot_main.maybe_send_daily_report(AsyncMock())

        summarize.assert_not_awaited()

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
