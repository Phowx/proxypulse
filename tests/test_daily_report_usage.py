from __future__ import annotations

from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import MetricSnapshot, Node, NodeStatus, TrafficQuotaCycle
from proxypulse.services.reports import format_traffic_summary, summarize_previous_local_day


class DailyReportUsageTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_daily_report_uses_quota_period_and_stops_at_report_end(self) -> None:
        local_tz = ZoneInfo("Asia/Shanghai")
        async with self.session_factory() as session:
            node = Node(
                name="tokyo",
                status=NodeStatus.online,
                is_online=True,
                agent_token="token",
                traffic_quota_limit_bytes=10_000,
                traffic_quota_cycle_type=TrafficQuotaCycle.monthly,
                traffic_quota_reset_day=10,
                traffic_quota_reset_hour=0,
                traffic_quota_reset_minute=0,
            )
            session.add(node)
            await session.flush()

            samples = [
                (datetime(2026, 6, 9, 23, 59, tzinfo=local_tz), 1_000, 500),
                (datetime(2026, 6, 10, 0, 1, tzinfo=local_tz), 1_200, 600),
                (datetime(2026, 6, 10, 12, 0, tzinfo=local_tz), 2_200, 1_100),
                # The report is generated later, but this sample belongs to the
                # next day and must not leak into the previous day's totals.
                (datetime(2026, 6, 11, 8, 0, tzinfo=local_tz), 4_200, 2_100),
            ]
            for created_at, rx_bytes, tx_bytes in samples:
                session.add(
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=10.0,
                        memory_percent=20.0,
                        disk_percent=30.0,
                        load_avg_1m=0.1,
                        rx_bytes=rx_bytes,
                        tx_bytes=tx_bytes,
                        uptime_seconds=100,
                        created_at=created_at.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                    )
                )
            await session.commit()

            _, summary = await summarize_previous_local_day(session, today_local=datetime(2026, 6, 11).date())

        item = summary.node_summaries[0]
        self.assertEqual(item.total_bytes, 1_800)
        self.assertEqual(item.period_used_bytes, 1_800)
        self.assertEqual(item.available_bytes, 8_200)
        self.assertEqual(item.days_until_reset, 30)
        self.assertEqual(
            item.period_start_at.astimezone(local_tz),
            datetime(2026, 6, 10, 0, 0, tzinfo=local_tz),
        )

        rendered = format_traffic_summary(summary)
        self.assertIn("<blockquote><b>总览</b>", rendered)
        self.assertIn("本期累计 <code>1.8 KB</code> · <code>06-10 00:00</code> 起", rendered)
        self.assertIn("套餐可用 <code>8.0 KB</code>", rendered)
        self.assertIn("重置 <code>30 天后</code>", rendered)
        self.assertNotIn("本月累计", rendered)
        self.assertNotIn("<pre>", rendered)
