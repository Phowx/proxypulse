from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import AlertEvent, AlertStatus, Node, NodeStatus

logger = logging.getLogger(__name__)
settings = get_settings()


def _metric_label(metric_name: str) -> str:
    mapping = {
        "cpu": "CPU",
        "memory": "内存",
        "disk": "磁盘",
        "offline": "在线状态",
    }
    return mapping.get(metric_name, metric_name)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_for_compare(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


async def _get_active_alert(session: AsyncSession, node_id: str, alert_key: str) -> AlertEvent | None:
    result = await session.execute(
        select(AlertEvent).where(
            AlertEvent.node_id == node_id,
            AlertEvent.alert_key == alert_key,
            AlertEvent.status == AlertStatus.active,
        )
    )
    return result.scalar_one_or_none()


async def _activate_alert(
    session: AsyncSession,
    node: Node,
    *,
    alert_key: str,
    metric_name: str,
    summary: str,
    current_value: float | None,
    threshold_value: float | None,
    severity: str = "warning",
    now: datetime | None = None,
) -> None:
    now = now or utc_now()
    alert = await _get_active_alert(session, node.id, alert_key)
    if alert is None:
        session.add(
            AlertEvent(
                node_id=node.id,
                alert_key=alert_key,
                metric_name=metric_name,
                status=AlertStatus.active,
                severity=severity,
                summary=summary,
                current_value=current_value,
                threshold_value=threshold_value,
                triggered_at=now,
            )
        )
        return

    alert.summary = summary
    alert.current_value = current_value
    alert.threshold_value = threshold_value
    alert.severity = severity


async def _resolve_alert(
    session: AsyncSession,
    node: Node,
    *,
    alert_key: str,
    summary: str,
    current_value: float | None,
    threshold_value: float | None,
    now: datetime | None = None,
) -> None:
    now = now or utc_now()
    alert = await _get_active_alert(session, node.id, alert_key)
    if alert is None:
        return

    alert.status = AlertStatus.resolved
    alert.summary = summary
    alert.current_value = current_value
    alert.threshold_value = threshold_value
    alert.resolved_at = now


async def sync_threshold_alerts(
    session: AsyncSession,
    node: Node,
    *,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
) -> None:
    checks = [
        ("cpu_high", "cpu", cpu_percent, settings.cpu_alert_threshold),
        ("memory_high", "memory", memory_percent, settings.memory_alert_threshold),
        ("disk_high", "disk", disk_percent, settings.disk_alert_threshold),
    ]
    now = utc_now()
    if not settings.resource_alerts_enabled:
        for alert_key, metric_name, current_value, threshold_value in checks:
            await _resolve_alert(
                session,
                node,
                alert_key=alert_key,
                summary=f"{node.name} 的{_metric_label(metric_name)}资源告警已关闭。",
                current_value=current_value,
                threshold_value=threshold_value,
                now=now,
            )
        return

    for alert_key, metric_name, current_value, threshold_value in checks:
        above_threshold = current_value >= threshold_value
        if above_threshold:
            await _activate_alert(
                session,
                node,
                alert_key=alert_key,
                metric_name=metric_name,
                summary=f"{node.name} 的{_metric_label(metric_name)}使用率为 {current_value:.1f}% ，已超过阈值 {threshold_value:.1f}%。",
                current_value=current_value,
                threshold_value=threshold_value,
                now=now,
            )
        else:
            await _resolve_alert(
                session,
                node,
                alert_key=alert_key,
                summary=f"{node.name} 的{_metric_label(metric_name)}已恢复到 {current_value:.1f}% ，阈值为 {threshold_value:.1f}%。",
                current_value=current_value,
                threshold_value=threshold_value,
                now=now,
            )


async def resolve_offline_alert(session: AsyncSession, node: Node) -> None:
    await _resolve_alert(
        session,
        node,
        alert_key="node_offline",
        summary=f"{node.name} 已恢复在线。",
        current_value=None,
        threshold_value=float(settings.offline_after_seconds),
    )


async def mark_stale_nodes_offline(session: AsyncSession) -> int:
    result = await session.execute(select(Node).where(Node.agent_token.is_not(None)))
    nodes = list(result.scalars().all())
    now = utc_now()
    changed = 0

    for node in nodes:
        if node.last_seen_at is None:
            continue
        last_seen_at = _normalize_for_compare(node.last_seen_at, now)
        if now - last_seen_at <= timedelta(seconds=settings.offline_after_seconds):
            continue

        if node.is_online or node.status != NodeStatus.offline:
            node.is_online = False
            node.status = NodeStatus.offline
            changed += 1

        await _activate_alert(
            session,
            node,
            alert_key="node_offline",
            metric_name="offline",
            summary=f"{node.name} 已离线超过 {settings.offline_after_seconds} 秒。",
            current_value=None,
            threshold_value=float(settings.offline_after_seconds),
            severity="critical",
            now=now,
        )

    return changed


async def list_active_alerts(session: AsyncSession, limit: int = 20) -> list[tuple[AlertEvent, Node]]:
    result = await session.execute(
        select(AlertEvent, Node)
        .join(Node, AlertEvent.node_id == Node.id)
        .where(AlertEvent.status == AlertStatus.active)
        .order_by(AlertEvent.triggered_at.desc())
        .limit(limit)
    )
    return list(result.all())


async def list_pending_notifications(session: AsyncSession, limit: int = 20) -> list[tuple[AlertEvent, Node]]:
    result = await session.execute(
        select(AlertEvent, Node)
        .join(Node, AlertEvent.node_id == Node.id)
        .where(
            (AlertEvent.last_notified_at.is_(None))
            | (AlertEvent.last_notification_status != AlertEvent.status)
        )
        .order_by(AlertEvent.updated_at.asc())
        .limit(limit)
    )
    return list(result.all())


def mark_notified(alert: AlertEvent) -> None:
    alert.last_notified_at = utc_now()
    alert.last_notification_status = alert.status


def format_alert_message(alert: AlertEvent, node: Node) -> str:
    prefix = "告警触发" if alert.status == AlertStatus.active else "告警恢复"
    severity = "严重" if alert.severity == "critical" else "警告"
    prefix_icon = "🚨" if alert.status == AlertStatus.active else "✅"
    severity_icon = "⛔" if alert.severity == "critical" else "⚠️"
    return (
        f"{prefix_icon} {prefix}\n"
        f"节点：{node.name}\n"
        f"内容：{alert.summary}\n"
        f"级别：{severity_icon} {severity}"
    )
