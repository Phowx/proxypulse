from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import MetricSnapshot, Node
from proxypulse.services.dashboard import get_current_rate_map
from proxypulse.services.reports import sum_snapshot_traffic_by_node


class NullableMetricConsumerTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_traffic_consumers_skip_cpu_only_snapshots(self) -> None:
        start = datetime.now(UTC) - timedelta(minutes=5)
        async with self.session_factory() as session:
            node = Node(name="tokyo")
            session.add(node)
            await session.flush()
            session.add_all(
                [
                    MetricSnapshot(
                        node_id=node.id,
                        rx_bytes=100,
                        tx_bytes=200,
                        uptime_seconds=100,
                        network_interface="eth0",
                        created_at=start,
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=25.0,
                        created_at=start + timedelta(seconds=30),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        rx_bytes=150,
                        tx_bytes=270,
                        uptime_seconds=160,
                        network_interface="eth0",
                        created_at=start + timedelta(seconds=60),
                    ),
                ]
            )
            await session.commit()

            totals = await sum_snapshot_traffic_by_node(
                session,
                start_at=start,
                end_at=start + timedelta(minutes=2),
                node_ids=[node.id],
            )
            rates = await get_current_rate_map(session, [node.id])

        self.assertEqual(totals[node.id], (50, 70))
        self.assertAlmostEqual(rates[node.id].rx_bps or 0, 50 / 60)
        self.assertAlmostEqual(rates[node.id].tx_bps or 0, 70 / 60)
