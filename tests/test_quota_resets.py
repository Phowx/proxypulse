from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import MetricSnapshot, Node, NodeStatus, TrafficQuotaCycle
from proxypulse.services.quota import get_quota_status


class QuotaResetTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_quota_status_handles_counter_reset_after_calibration(self) -> None:
        now = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            node = Node(
                name="lagia",
                status=NodeStatus.online,
                is_online=True,
                agent_token="token",
                latest_rx_bytes=1_300,
                latest_tx_bytes=900,
                traffic_quota_limit_bytes=1_000_000,
                traffic_quota_cycle_type=TrafficQuotaCycle.interval_days,
                traffic_quota_interval_days=30,
                traffic_quota_anchor_at=now - timedelta(days=2),
                traffic_quota_calibrated_usage_bytes=200_000,
                traffic_quota_calibrated_total_bytes=21_000,
                traffic_quota_calibrated_at=now - timedelta(hours=2, minutes=30),
            )
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
                        rx_bytes=22_000,
                        tx_bytes=11_000,
                        uptime_seconds=100,
                        created_at=now - timedelta(hours=2),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=11.0,
                        memory_percent=21.0,
                        disk_percent=31.0,
                        load_avg_1m=0.1,
                        rx_bytes=800,
                        tx_bytes=500,
                        uptime_seconds=30,
                        created_at=now - timedelta(hours=1),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=12.0,
                        memory_percent=22.0,
                        disk_percent=32.0,
                        load_avg_1m=0.2,
                        rx_bytes=1_300,
                        tx_bytes=900,
                        uptime_seconds=90,
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

            status = await get_quota_status(session, node, now=now)

        self.assertEqual(status.used_bytes, 214_200)
