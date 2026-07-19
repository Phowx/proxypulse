from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.models import AppSetting, Node

TELEGRAM_NODE_DISPLAY_NAME_PREFIX = "telegram.node_display_name:"
TELEGRAM_NODE_DISPLAY_NAME_MAX_LENGTH = 40


class TelegramNodeDisplayNameError(RuntimeError):
    """Raised when a Telegram-only node display name is invalid."""


def _setting_key(node_id: str) -> str:
    return f"{TELEGRAM_NODE_DISPLAY_NAME_PREFIX}{node_id}"


def normalize_telegram_node_display_name(value: str) -> str:
    display_name = value.strip()
    if not display_name:
        raise TelegramNodeDisplayNameError("显示名称不能为空。")
    if len(display_name) > TELEGRAM_NODE_DISPLAY_NAME_MAX_LENGTH:
        raise TelegramNodeDisplayNameError(
            f"显示名称不能超过 {TELEGRAM_NODE_DISPLAY_NAME_MAX_LENGTH} 个字符。"
        )
    if any(not character.isprintable() for character in display_name):
        raise TelegramNodeDisplayNameError("显示名称不能包含换行或控制字符。")
    return display_name


async def get_telegram_node_display_names(
    session: AsyncSession,
    nodes: Sequence[Node],
) -> dict[str, str]:
    if not nodes:
        return {}

    keys = {_setting_key(node.id) for node in nodes}
    result = await session.execute(select(AppSetting).where(AppSetting.key.in_(keys)))
    values = {setting.key: setting.value for setting in result.scalars().all()}
    return {
        node.name: values.get(_setting_key(node.id), node.name)
        for node in nodes
    }


async def get_telegram_node_display_name(session: AsyncSession, node: Node) -> str:
    names = await get_telegram_node_display_names(session, [node])
    return names[node.name]


async def set_telegram_node_display_name(
    session: AsyncSession,
    node: Node,
    value: str,
) -> str:
    display_name = normalize_telegram_node_display_name(value)
    key = _setting_key(node.id)
    setting = await session.get(AppSetting, key)

    if display_name == node.name:
        if setting is not None:
            await session.delete(setting)
        await session.commit()
        return node.name

    if setting is None:
        session.add(AppSetting(key=key, value=display_name))
    else:
        setting.value = display_name
    await session.commit()
    return display_name


async def clear_telegram_node_display_name(
    session: AsyncSession,
    node: Node,
    *,
    commit: bool = True,
) -> None:
    setting = await session.get(AppSetting, _setting_key(node.id))
    if setting is not None:
        await session.delete(setting)
    if commit:
        await session.commit()
