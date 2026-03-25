from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.models import MetricSnapshot, Node, NodeStatus
from proxypulse.core.schemas import AgentRegisterRequest, HeartbeatRequest, MetricSnapshotIn
from proxypulse.services.alerts import resolve_offline_alert, sync_threshold_alerts


class NodeServiceError(RuntimeError):
    """Business-layer exception for node operations."""


def generate_token(length: int = 24) -> str:
    return secrets.token_urlsafe(length)


async def create_or_refresh_enrollment(session: AsyncSession, name: str) -> Node:
    name = name.strip()
    if not name:
        raise NodeServiceError("节点名不能为空。")

    result = await session.execute(select(Node).where(Node.name == name))
    node = result.scalar_one_or_none()
    token = generate_token(18)

    if node is None:
        node = Node(name=name, enrollment_token=token)
        session.add(node)
    else:
        node.enrollment_token = token
        if node.agent_token is None:
            node.status = NodeStatus.pending

    await session.commit()
    await session.refresh(node)
    return node


async def register_agent(session: AsyncSession, payload: AgentRegisterRequest) -> Node:
    result = await session.execute(
        select(Node).where(
            Node.name == payload.name,
            Node.enrollment_token == payload.enrollment_token,
        )
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise NodeServiceError("接入令牌无效，或节点不存在。")

    node.hostname = payload.hostname
    node.platform = payload.platform
    node.ips = payload.ips
    node.agent_token = generate_token(24)
    node.enrollment_token = None
    node.last_seen_at = datetime.now(UTC)
    node.is_online = True
    node.status = NodeStatus.online
    await session.commit()
    await session.refresh(node)
    return node


async def get_node_by_agent_token(session: AsyncSession, agent_token: str) -> Node:
    result = await session.execute(select(Node).where(Node.agent_token == agent_token))
    node = result.scalar_one_or_none()
    if node is None:
        raise NodeServiceError("Agent 令牌无效。")
    return node


async def record_heartbeat(session: AsyncSession, node: Node, payload: HeartbeatRequest) -> Node:
    node.hostname = payload.hostname or node.hostname
    node.platform = payload.platform or node.platform
    if payload.ips:
        node.ips = payload.ips
    node.last_seen_at = datetime.now(UTC)
    node.is_online = True
    node.status = NodeStatus.online
    await resolve_offline_alert(session, node)
    await session.commit()
    await session.refresh(node)
    return node


async def record_metrics(session: AsyncSession, node: Node, payload: MetricSnapshotIn) -> MetricSnapshot:
    snapshot = MetricSnapshot(
        node_id=node.id,
        cpu_percent=payload.cpu_percent,
        memory_percent=payload.memory_percent,
        disk_percent=payload.disk_percent,
        load_avg_1m=payload.load_avg_1m,
        rx_bytes=payload.rx_bytes,
        tx_bytes=payload.tx_bytes,
        uptime_seconds=payload.uptime_seconds,
        raw_payload=json.dumps(payload.model_dump()),
    )
    session.add(snapshot)

    node.last_seen_at = datetime.now(UTC)
    node.is_online = True
    node.status = NodeStatus.online
    node.latest_cpu_percent = payload.cpu_percent
    node.latest_memory_percent = payload.memory_percent
    node.latest_disk_percent = payload.disk_percent
    node.latest_load_avg_1m = payload.load_avg_1m
    node.latest_rx_bytes = payload.rx_bytes
    node.latest_tx_bytes = payload.tx_bytes
    await resolve_offline_alert(session, node)
    await sync_threshold_alerts(
        session,
        node,
        cpu_percent=payload.cpu_percent,
        memory_percent=payload.memory_percent,
        disk_percent=payload.disk_percent,
    )

    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def list_nodes(session: AsyncSession) -> list[Node]:
    result = await session.execute(select(Node).order_by(Node.name.asc()))
    return list(result.scalars().all())


async def get_node_by_name(session: AsyncSession, name: str) -> Node | None:
    result = await session.execute(select(Node).where(Node.name == name))
    return result.scalar_one_or_none()


async def delete_node_by_name(session: AsyncSession, name: str) -> Node:
    node = await get_node_by_name(session, name)
    if node is None:
        raise NodeServiceError("未找到对应节点。")

    await session.delete(node)
    await session.commit()
    return node
