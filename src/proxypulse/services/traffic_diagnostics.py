from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node
from proxypulse.services.reports import contextual_counter_delta, format_bytes, sum_snapshot_traffic_by_node

AGGREGATE_INTERFACE_NAME = "aggregate"
settings = get_settings()


class TrafficDiagnosisError(RuntimeError):
    """Business-layer exception for traffic diagnosis."""


@dataclass(slots=True)
class RecentTrafficSample:
    created_at: datetime
    network_interface: str | None
    rx_bytes: int
    tx_bytes: int
    rx_delta: int | None
    tx_delta: int | None
    elapsed_seconds: float | None


@dataclass(slots=True)
class TrafficDiagnosis:
    node: Node
    snapshot_count_24h: int
    traffic_24h_rx_bytes: int
    traffic_24h_tx_bytes: int
    interfaces_seen: list[str]
    recent_samples: list[RecentTrafficSample]

    @property
    def current_interface(self) -> str | None:
        return self.node.latest_network_interface

    @property
    def current_total_bytes(self) -> int:
        return int((self.node.latest_rx_bytes or 0) + (self.node.latest_tx_bytes or 0))

    @property
    def traffic_24h_total_bytes(self) -> int:
        return self.traffic_24h_rx_bytes + self.traffic_24h_tx_bytes

    @property
    def uses_aggregate(self) -> bool:
        return AGGREGATE_INTERFACE_NAME in self.interfaces_seen

    @property
    def has_multiple_interfaces(self) -> bool:
        return len(self.interfaces_seen) > 1


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_datetime(value: datetime) -> datetime:
    if settings.database_url.startswith("sqlite"):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _format_interface(value: str | None) -> str:
    if not value:
        return "暂无"
    if value == AGGREGATE_INTERFACE_NAME:
        return "aggregate（汇总）"
    return value


async def build_traffic_diagnosis(
    session: AsyncSession,
    node_name: str,
    *,
    now: datetime | None = None,
    recent_limit: int = 5,
) -> TrafficDiagnosis:
    node_result = await session.execute(select(Node).where(Node.name == node_name))
    node = node_result.scalar_one_or_none()
    if node is None:
        raise TrafficDiagnosisError("未找到对应节点。")

    now = _ensure_aware(now or datetime.now(UTC))
    window_start = now - timedelta(hours=24)

    window_filter = (
        MetricSnapshot.node_id == node.id,
        MetricSnapshot.rx_bytes.is_not(None),
        MetricSnapshot.tx_bytes.is_not(None),
        MetricSnapshot.uptime_seconds.is_not(None),
        MetricSnapshot.created_at >= _db_datetime(window_start),
        MetricSnapshot.created_at <= _db_datetime(now),
    )
    count_result = await session.execute(
        select(func.count(MetricSnapshot.id)).where(*window_filter)
    )
    snapshot_count_24h = int(count_result.scalar_one())
    traffic_map = await sum_snapshot_traffic_by_node(
        session,
        start_at=window_start,
        end_at=now,
        node_ids=[node.id],
    )
    traffic_24h_rx_bytes, traffic_24h_tx_bytes = traffic_map.get(node.id, (0, 0))

    interface_result = await session.execute(
        select(MetricSnapshot.network_interface)
        .where(
            *window_filter,
            MetricSnapshot.network_interface.is_not(None),
        )
        .distinct()
    )
    interfaces_seen = {value for value in interface_result.scalars().all() if value}

    recent_result = await session.execute(
        select(
            MetricSnapshot.created_at,
            MetricSnapshot.network_interface,
            MetricSnapshot.rx_bytes,
            MetricSnapshot.tx_bytes,
            MetricSnapshot.uptime_seconds,
        )
        .where(
            MetricSnapshot.node_id == node.id,
            MetricSnapshot.rx_bytes.is_not(None),
            MetricSnapshot.tx_bytes.is_not(None),
            MetricSnapshot.uptime_seconds.is_not(None),
        )
        .order_by(MetricSnapshot.created_at.desc())
        .limit(recent_limit)
    )
    recent_snapshots = list(recent_result.all())
    recent_snapshots.reverse()

    recent_samples: list[RecentTrafficSample] = []
    previous_snapshot = None
    for snapshot in recent_snapshots:
        if snapshot.network_interface:
            interfaces_seen.add(snapshot.network_interface)
        rx_delta = None
        tx_delta = None
        elapsed_seconds = None
        if previous_snapshot is not None:
            elapsed_seconds = max(
                (_ensure_aware(snapshot.created_at) - _ensure_aware(previous_snapshot.created_at)).total_seconds(),
                0.0,
            )
            context = {
                "current_interface": snapshot.network_interface,
                "previous_interface": previous_snapshot.network_interface,
                "current_uptime": snapshot.uptime_seconds,
                "previous_uptime": previous_snapshot.uptime_seconds,
            }
            rx_delta = contextual_counter_delta(snapshot.rx_bytes, previous_snapshot.rx_bytes, **context)
            tx_delta = contextual_counter_delta(snapshot.tx_bytes, previous_snapshot.tx_bytes, **context)
        recent_samples.append(
            RecentTrafficSample(
                created_at=_ensure_aware(snapshot.created_at),
                network_interface=snapshot.network_interface,
                rx_bytes=snapshot.rx_bytes,
                tx_bytes=snapshot.tx_bytes,
                rx_delta=rx_delta,
                tx_delta=tx_delta,
                elapsed_seconds=elapsed_seconds,
            )
        )
        previous_snapshot = snapshot

    return TrafficDiagnosis(
        node=node,
        snapshot_count_24h=snapshot_count_24h,
        traffic_24h_rx_bytes=traffic_24h_rx_bytes,
        traffic_24h_tx_bytes=traffic_24h_tx_bytes,
        interfaces_seen=sorted(interfaces_seen),
        recent_samples=list(reversed(recent_samples)),
    )


def format_traffic_diagnosis(diagnosis: TrafficDiagnosis) -> str:
    node = diagnosis.node
    lines = [
        f"<b>🧪 流量诊断 · {html.escape(node.name)}</b>",
        "<blockquote><b>当前口径</b>",
        f"状态 <code>{html.escape(node.status.value)}</code>",
        f"当前网卡 <code>{html.escape(_format_interface(diagnosis.current_interface))}</code>",
        "最近出现 <code>"
        + html.escape(", ".join(_format_interface(item) for item in diagnosis.interfaces_seen) if diagnosis.interfaces_seen else "暂无")
        + "</code>",
        f"累计 ↓ <code>{format_bytes(node.latest_rx_bytes or 0)}</code> · ↑ <code>{format_bytes(node.latest_tx_bytes or 0)}</code>",
        f"合计 <code>{format_bytes(diagnosis.current_total_bytes)}</code></blockquote>",
        "",
        "<blockquote><b>24h 统计依据</b>",
        f"样本 <code>{diagnosis.snapshot_count_24h}</code>",
        f"下行 <code>{format_bytes(diagnosis.traffic_24h_rx_bytes)}</code> · 上行 <code>{format_bytes(diagnosis.traffic_24h_tx_bytes)}</code>",
        f"合计 <code>{format_bytes(diagnosis.traffic_24h_total_bytes)}</code></blockquote>",
    ]

    hints: list[str] = []
    if diagnosis.uses_aggregate:
        hints.append("当前或最近快照使用了 aggregate 汇总口径，私网/隧道/额外网卡流量可能被一起统计。")
    if diagnosis.has_multiple_interfaces:
        hints.append("最近快照出现了多张网卡，说明统计口径可能切换过，和服务商面板更容易不一致。")
    hints.append("ProxyPulse 统计口径是 RX+TX 合计；部分服务商面板只统计公网主网卡或计费流量。")

    lines.extend(["", "<blockquote><b>诊断提示</b>"])
    for hint in hints:
        lines.append(f"• {html.escape(hint)}")
    lines[-1] += "</blockquote>"

    lines.extend(["", "<b>最近快照</b>"])
    if not diagnosis.recent_samples:
        lines.append("<blockquote>暂无历史快照。</blockquote>")
        return "\n".join(lines)

    sample_lines: list[str] = []
    for index, sample in enumerate(diagnosis.recent_samples):
        sample_lines.append(
            f"<b>{sample.created_at.astimezone(UTC).strftime('%m-%d %H:%M:%S')}</b>"
            f" · <code>{html.escape(_format_interface(sample.network_interface))}</code>"
        )
        sample_lines.append(f"累计 ↓ <code>{format_bytes(sample.rx_bytes)}</code> · ↑ <code>{format_bytes(sample.tx_bytes)}</code>")
        if index == 0 or sample.rx_delta is None or sample.tx_delta is None or sample.elapsed_seconds is None:
            sample_lines.append("变化 <code>--</code>")
        else:
            sample_lines.append(
                f"变化 <code>{int(sample.elapsed_seconds)}s</code> · ↓ <code>{format_bytes(sample.rx_delta)}</code> · ↑ <code>{format_bytes(sample.tx_delta)}</code>"
            )
        if index != len(diagnosis.recent_samples) - 1:
            sample_lines.append("")
    lines.append("<blockquote expandable>" + "\n".join(sample_lines) + "</blockquote>")
    return "\n".join(lines)
