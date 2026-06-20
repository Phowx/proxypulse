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

    async def test_daily_report_includes_month_usage_and_quota_available(self) -> None:
        local_tz = ZoneInfo("Asia/Shanghai")
        async with self.session_factory() as session:
            node = Node(
                name="tokyo",
                status=NodeStatus.online,
                is_online=True,
                agent_token="token",
                traffic_quota_limit_bytes=10_000,
                traffic_quota_cycle_type=TrafficQuotaCycle.monthly,
                traffic_quota_reset_day=1,
                traffic_quota_reset_hour=0,
                traffic_quota_reset_minute=0,
            )
            session.add(node)
            await session.flush()

            samples = [
                (datetime(2026, 4, 1, 0, 0, tzinfo=local_tz), 1_000, 500),
                (datetime(2026, 4, 10, 0, 0, tzinfo=local_tz), 1_700, 800),
                (datetime(2026, 4, 10, 12, 0, tzinfo=local_tz), 2_700, 1_300),
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

            _, summary = await summarize_previous_local_day(session, today_local=datetime(2026, 4, 11).date())

        item = summary.node_summaries[0]
        self.assertEqual(item.total_bytes, 1_500)
        self.assertEqual(item.month_used_bytes, 2_500)
        self.assertEqual(item.available_bytes, 7_500)

        rendered = format_traffic_summary(summary)
        self.assertIn("本月累计  2.4 KB", rendered)
        self.assertIn("套餐可用  7.3 KB", rendered)
