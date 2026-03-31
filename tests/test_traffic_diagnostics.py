from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase, TestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import MetricSnapshot, Node, NodeStatus
from proxypulse.services.traffic_diagnostics import build_traffic_diagnosis, format_traffic_diagnosis


class TrafficDiagnosisTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_build_traffic_diagnosis_reports_aggregate_and_24h_delta(self) -> None:
        now = datetime(2026, 3, 31, 6, 0, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            node = Node(
                name="tokyo",
                status=NodeStatus.online,
                is_online=True,
                agent_token="token",
                latest_network_interface="aggregate",
                latest_rx_bytes=4_100,
                latest_tx_bytes=2_200,
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
                        network_interface="eth0",
                        rx_bytes=1_000,
                        tx_bytes=500,
                        uptime_seconds=100,
                        created_at=now - timedelta(hours=3),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=12.0,
                        memory_percent=22.0,
                        disk_percent=31.0,
                        load_avg_1m=0.1,
                        network_interface="aggregate",
                        rx_bytes=2_600,
                        tx_bytes=1_300,
                        uptime_seconds=200,
                        created_at=now - timedelta(hours=2),
                    ),
                    MetricSnapshot(
                        node_id=node.id,
                        cpu_percent=15.0,
                        memory_percent=24.0,
                        disk_percent=33.0,
                        load_avg_1m=0.2,
                        network_interface="aggregate",
                        rx_bytes=4_100,
                        tx_bytes=2_200,
                        uptime_seconds=300,
                        created_at=now - timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()

            diagnosis = await build_traffic_diagnosis(session, "tokyo", now=now, recent_limit=3)

        self.assertEqual(diagnosis.snapshot_count_24h, 3)
        self.assertEqual(diagnosis.traffic_24h_rx_bytes, 3_100)
        self.assertEqual(diagnosis.traffic_24h_tx_bytes, 1_700)
        self.assertTrue(diagnosis.uses_aggregate)
        self.assertTrue(diagnosis.has_multiple_interfaces)
        self.assertEqual(diagnosis.recent_samples[0].rx_bytes, 4_100)
        self.assertEqual(diagnosis.recent_samples[1].rx_delta, 1_600)
        self.assertEqual(diagnosis.recent_samples[0].rx_delta, 1_500)


class TrafficDiagnosisFormattingTests(TestCase):
    def test_format_traffic_diagnosis_mentions_aggregate_warning(self) -> None:
        node = Node(
            name="tokyo",
            status=NodeStatus.online,
            is_online=True,
            latest_network_interface="aggregate",
            latest_rx_bytes=4_100,
            latest_tx_bytes=2_200,
        )
        diagnosis_text = format_traffic_diagnosis(
            type(
                "DiagnosisStub",
                (),
                {
                    "node": node,
                    "current_interface": "aggregate",
                    "interfaces_seen": ["aggregate", "eth0"],
                    "current_total_bytes": 6_300,
                    "snapshot_count_24h": 3,
                    "traffic_24h_rx_bytes": 3_100,
                    "traffic_24h_tx_bytes": 1_700,
                    "traffic_24h_total_bytes": 4_800,
                    "uses_aggregate": True,
                    "has_multiple_interfaces": True,
                    "recent_samples": [],
                },
            )()
        )

        self.assertIn("aggregate（汇总）", diagnosis_text)
        self.assertIn("aggregate 汇总口径", diagnosis_text)
        self.assertIn("RX+TX 合计", diagnosis_text)
