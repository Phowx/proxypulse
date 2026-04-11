from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import MetricSnapshot, Node, NodeStatus
from proxypulse.services.reports import summarize_traffic_window
from proxypulse.services.dashboard import get_current_rate_map, get_traffic_window_map, get_trend_summary


class DashboardTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_rate_and_trend_use_snapshot_deltas(self) -> None:
        now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            node = Node(name="tokyo", status=NodeStatus.online, is_online=True, agent_token="token")
            session.add(node)
            await session.flush()
            session.add_all(
                [
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=10.0,
                        memory_percent=20.0,
                        disk_percent=30.0,
                        load_avg_1m=0.1,
                        rx_bytes=1_000,
                        tx_bytes=500,
                        uptime_seconds=100,
                        created_at=now - timedelta(minutes=2),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=20.0,
                        memory_percent=40.0,
                        disk_percent=35.0,
                        load_avg_1m=0.2,
                        rx_bytes=2_800,
                        tx_bytes=1_400,
                        uptime_seconds=160,
                        created_at=now - timedelta(seconds=50),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=40.0,
                        memory_percent=50.0,
                        disk_percent=45.0,
                        load_avg_1m=0.3,
                        rx_bytes=4_000,
                        tx_bytes=2_600,
                        uptime_seconds=210,
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

            rate_map = await get_current_rate_map(session, [node.id])
            trend_summary = await get_trend_summary(session, node.id, end_at=now, window=timedelta(hours=1))
            traffic_map = await get_traffic_window_map(
                session,
                [node.id],
                start_at=now - timedelta(hours=24),
                end_at=now,
            )

        self.assertAlmostEqual(rate_map[node.id].rx_bps or 0.0, 24.0)
        self.assertAlmostEqual(rate_map[node.id].tx_bps or 0.0, 24.0)
        self.assertEqual(trend_summary.sample_count, 3)
        self.assertAlmostEqual(trend_summary.avg_cpu_percent or 0.0, 23.3333333333, places=3)
        self.assertEqual(trend_summary.peak_memory_percent, 50.0)
        self.assertEqual(traffic_map[node.id].rx_bytes, 3_000)
        self.assertEqual(traffic_map[node.id].tx_bytes, 2_100)

    async def test_traffic_window_handles_counter_reset(self) -> None:
        now = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            node = Node(name="lagia", status=NodeStatus.online, is_online=True, agent_token="token")
            session.add(node)
            await session.flush()
            session.add_all(
                [
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=10.0,
                        memory_percent=20.0,
                        disk_percent=30.0,
                        load_avg_1m=0.1,
                        rx_bytes=10_000,
                        tx_bytes=5_000,
                        uptime_seconds=100,
                        created_at=now - timedelta(hours=3),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=12.0,
                        memory_percent=22.0,
                        disk_percent=31.0,
                        load_avg_1m=0.1,
                        rx_bytes=14_000,
                        tx_bytes=7_000,
                        uptime_seconds=200,
                        created_at=now - timedelta(hours=2),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=15.0,
                        memory_percent=24.0,
                        disk_percent=32.0,
                        load_avg_1m=0.1,
                        rx_bytes=800,
                        tx_bytes=500,
                        uptime_seconds=30,
                        created_at=now - timedelta(hours=1),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=16.0,
                        memory_percent=25.0,
                        disk_percent=33.0,
                        load_avg_1m=0.2,
                        rx_bytes=1_300,
                        tx_bytes=900,
                        uptime_seconds=90,
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

            traffic_map = await get_traffic_window_map(
                session,
                [node.id],
                start_at=now - timedelta(hours=24),
                end_at=now,
            )
            summary = await summarize_traffic_window(
                session,
                title="test",
                start_at=now - timedelta(hours=24),
                end_at=now,
            )

        self.assertEqual(traffic_map[node.id].rx_bytes, 5_300)
        self.assertEqual(traffic_map[node.id].tx_bytes, 2_900)
        self.assertEqual(summary.node_summaries[0].rx_bytes, 5_300)
        self.assertEqual(summary.node_summaries[0].tx_bytes, 2_900)
