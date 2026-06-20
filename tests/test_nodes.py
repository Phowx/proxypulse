from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import Node, NodeStatus
from proxypulse.services.nodes import mark_stale_nodes_offline


class NodeStatusTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_stale_node_is_marked_offline_without_alert_storage(self) -> None:
        async with self.session_factory() as session:
            node = Node(
                name="tokyo",
                status=NodeStatus.online,
                is_online=True,
                agent_token="token",
                last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
            )
            session.add(node)
            await session.commit()

            changed = await mark_stale_nodes_offline(session)
            await session.commit()
            await session.refresh(node)

        self.assertEqual(changed, 1)
        self.assertEqual(node.status, NodeStatus.offline)
        self.assertFalse(node.is_online)
        self.assertNotIn("alert_events", Base.metadata.tables)
