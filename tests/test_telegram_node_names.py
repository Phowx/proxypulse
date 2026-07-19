from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, TestCase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxypulse.core.db import Base
from proxypulse.core.models import Node
from proxypulse.services.telegram_node_names import (
    TelegramNodeDisplayNameError,
    clear_telegram_node_display_name,
    get_telegram_node_display_name,
    get_telegram_node_display_names,
    normalize_telegram_node_display_name,
    set_telegram_node_display_name,
)


class TelegramNodeDisplayNameValidationTests(TestCase):
    def test_rejects_empty_long_and_multiline_names(self) -> None:
        for value in ("   ", "x" * 41, "东京\n备用"):
            with self.subTest(value=value):
                with self.assertRaises(TelegramNodeDisplayNameError):
                    normalize_telegram_node_display_name(value)


class TelegramNodeDisplayNameTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_alias_is_persisted_without_changing_node_identity(self) -> None:
        async with self.session_factory() as session:
            node = Node(name="tokyo")
            session.add(node)
            await session.commit()

            display_name = await set_telegram_node_display_name(session, node, " 东京主节点 ")
            single_name = await get_telegram_node_display_name(session, node)
            name_map = await get_telegram_node_display_names(session, [node])

            self.assertEqual(display_name, "东京主节点")
            self.assertEqual(single_name, "东京主节点")
            self.assertEqual(name_map, {"tokyo": "东京主节点"})
            self.assertEqual(node.name, "tokyo")

    async def test_alias_can_be_restored_to_original_name(self) -> None:
        async with self.session_factory() as session:
            node = Node(name="tokyo")
            session.add(node)
            await session.commit()

            await set_telegram_node_display_name(session, node, "东京主节点")
            await clear_telegram_node_display_name(session, node)

            self.assertEqual(await get_telegram_node_display_name(session, node), "tokyo")

    async def test_setting_original_name_removes_existing_alias(self) -> None:
        async with self.session_factory() as session:
            node = Node(name="tokyo")
            session.add(node)
            await session.commit()

            await set_telegram_node_display_name(session, node, "东京主节点")
            display_name = await set_telegram_node_display_name(session, node, "tokyo")

            self.assertEqual(display_name, "tokyo")
            self.assertEqual(await get_telegram_node_display_name(session, node), "tokyo")
