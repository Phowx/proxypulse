from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.agent import main as agent_main
from proxypulse.agent.collectors import collect_metrics
from proxypulse.bot.collection_formatting import format_scoped_value
from proxypulse.core.collections import STANDARD_COLLECTIONS, normalize_collections
from proxypulse.core.config import Settings
from proxypulse.core.db import Base
from proxypulse.core.migrations import migrate_collection_schema
from proxypulse.core.models import Node
from proxypulse.core.schemas import HeartbeatRequest, MetricSnapshotIn
from proxypulse.services.nodes import record_heartbeat


class CollectionScopeTests(TestCase):
    def test_default_and_network_dependency_are_normalized(self) -> None:
        self.assertEqual(normalize_collections(None), STANDARD_COLLECTIONS)
        self.assertEqual(
            normalize_collections("network,cpu,network"),
            ("cpu", "network", "uptime"),
        )

    def test_unknown_and_empty_scopes_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            normalize_collections("cpu,gpu")
        with self.assertRaisesRegex(ValueError, "at least one"):
            normalize_collections("")
        with mock.patch.dict(os.environ, {"PROXYPULSE_COLLECTIONS": "network"}):
            self.assertEqual(Settings(_env_file=None).collections, ("network", "uptime"))

    def test_settings_and_heartbeat_use_normalized_scope(self) -> None:
        settings = Settings(collections="memory,cpu", _env_file=None)
        self.assertEqual(settings.collections, ("cpu", "memory"))
        heartbeat = HeartbeatRequest(collections=["network"])
        self.assertEqual(heartbeat.collections, ["network", "uptime"])

    def test_partial_metrics_are_valid_but_empty_snapshot_is_not(self) -> None:
        payload = MetricSnapshotIn(rx_bytes=100, tx_bytes=200, uptime_seconds=30)
        self.assertIsNone(payload.cpu_percent)
        with self.assertRaisesRegex(ValueError, "at least one metric"):
            MetricSnapshotIn()

    @mock.patch("proxypulse.agent.collectors.psutil.boot_time")
    @mock.patch("proxypulse.agent.collectors.psutil.net_io_counters")
    @mock.patch("proxypulse.agent.collectors.psutil.disk_usage")
    @mock.patch("proxypulse.agent.collectors.psutil.virtual_memory")
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_count", return_value=4)
    @mock.patch("proxypulse.agent.collectors.os.getloadavg", return_value=(0.5, 0.0, 0.0))
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_percent", return_value=12.5)
    def test_cpu_only_scope_does_not_touch_other_collectors(
        self,
        _cpu_percent,
        _getloadavg,
        _cpu_count,
        virtual_memory,
        disk_usage,
        net_io_counters,
        boot_time,
    ) -> None:
        metrics = collect_metrics("", "auto", ("cpu",))

        self.assertEqual(metrics.cpu_percent, 12.5)
        self.assertIsNone(metrics.memory_percent)
        self.assertIsNone(metrics.rx_bytes)
        virtual_memory.assert_not_called()
        disk_usage.assert_not_called()
        net_io_counters.assert_not_called()
        boot_time.assert_not_called()

    def test_bot_distinguishes_disabled_and_pending(self) -> None:
        traffic_node = SimpleNamespace(collection_scope=["network", "uptime"])
        standard_node = SimpleNamespace(collection_scope=list(STANDARD_COLLECTIONS))
        self.assertEqual(format_scoped_value(traffic_node, "cpu", None, str), "未启用")
        self.assertEqual(format_scoped_value(standard_node, "cpu", None, str), "暂未上报")


class CollectionScopeAsyncTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_privacy_heartbeat_clears_identity_and_disabled_metrics(self) -> None:
        async with self.session_factory() as session:
            node = Node(
                name="tokyo",
                hostname="secret-host",
                platform="secret-platform",
                ips=["10.0.0.1"],
                latest_cpu_percent=50.0,
                latest_rx_bytes=100,
                latest_tx_bytes=200,
            )
            session.add(node)
            await session.commit()

            await record_heartbeat(
                session,
                node,
                HeartbeatRequest(collections=["network", "uptime"]),
            )

            self.assertEqual(node.collection_scope, ["network", "uptime"])
            self.assertIsNone(node.hostname)
            self.assertEqual(node.ips, [])
            self.assertIsNone(node.latest_cpu_percent)
            self.assertEqual(node.latest_rx_bytes, 100)

    async def test_identity_only_cycle_skips_metric_upload(self) -> None:
        fake_settings = SimpleNamespace(collections=("identity",))
        with (
            mock.patch.object(agent_main, "settings", fake_settings),
            mock.patch.object(agent_main, "post_heartbeat", new=mock.AsyncMock()) as heartbeat,
            mock.patch.object(agent_main, "post_metrics", new=mock.AsyncMock()) as metrics,
        ):
            await agent_main.run_cycle(mock.AsyncMock(), "token")

        heartbeat.assert_awaited_once()
        metrics.assert_not_awaited()


class CollectionMigrationTests(IsolatedAsyncioTestCase):
    async def test_old_sqlite_metric_rows_survive_nullable_migration(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE nodes (id VARCHAR(36) PRIMARY KEY, name VARCHAR(120))"))
                await conn.execute(
                    text(
                        """
                        CREATE TABLE metric_snapshots (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            node_id VARCHAR(36) NOT NULL,
                            cpu_percent FLOAT NOT NULL,
                            memory_percent FLOAT NOT NULL,
                            disk_percent FLOAT NOT NULL,
                            load_avg_1m FLOAT NOT NULL,
                            cpu_count INTEGER,
                            memory_total_bytes INTEGER,
                            memory_used_bytes INTEGER,
                            disk_total_bytes INTEGER,
                            disk_used_bytes INTEGER,
                            network_interface VARCHAR(64),
                            rx_bytes INTEGER NOT NULL,
                            tx_bytes INTEGER NOT NULL,
                            uptime_seconds INTEGER NOT NULL,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                        )
                        """
                    )
                )
                await conn.execute(text("INSERT INTO nodes (id, name) VALUES ('n1', 'tokyo')"))
                await conn.execute(
                    text(
                        "INSERT INTO metric_snapshots "
                        "(node_id, cpu_percent, memory_percent, disk_percent, load_avg_1m, rx_bytes, tx_bytes, uptime_seconds) "
                        "VALUES ('n1', 1, 2, 3, 4, 5, 6, 7)"
                    )
                )

                await migrate_collection_schema(conn)

                count = await conn.scalar(text("SELECT COUNT(*) FROM metric_snapshots"))
                columns = (await conn.execute(text("PRAGMA table_info(metric_snapshots)"))).fetchall()
                not_null = {row[1]: row[3] for row in columns}
                scope = await conn.scalar(text("SELECT collection_scope FROM nodes WHERE id = 'n1'"))

            self.assertEqual(count, 1)
            self.assertEqual(not_null["cpu_percent"], 0)
            self.assertEqual(not_null["rx_bytes"], 0)
            self.assertIn("identity", scope)
        finally:
            await engine.dispose()
