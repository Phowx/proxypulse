from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node
from proxypulse.services.reports import format_bytes

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

    window_result = await session.execute(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.node_id == node.id,
            MetricSnapshot.created_at >= _db_datetime(window_start),
            MetricSnapshot.created_at <= _db_datetime(now),
        )
        .order_by(MetricSnapshot.created_at.asc())
    )
    window_snapshots = list(window_result.scalars().all())

    traffic_24h_rx_bytes = 0
    traffic_24h_tx_bytes = 0
    if window_snapshots:
        traffic_24h_rx_bytes = max(window_snapshots[-1].rx_bytes - window_snapshots[0].rx_bytes, 0)
        traffic_24h_tx_bytes = max(window_snapshots[-1].tx_bytes - window_snapshots[0].tx_bytes, 0)

    recent_result = await session.execute(
        select(MetricSnapshot)
        .where(MetricSnapshot.node_id == node.id)
        .order_by(MetricSnapshot.created_at.desc())
        .limit(recent_limit)
    )
    recent_snapshots = list(recent_result.scalars().all())
    recent_snapshots.reverse()

    recent_samples: list[RecentTrafficSample] = []
    previous_snapshot: MetricSnapshot | None = None
    interfaces_seen = {
        snapshot.network_interface
        for snapshot in [*window_snapshots[-recent_limit:], *recent_snapshots]
        if snapshot.network_interface
    }
    for snapshot in recent_snapshots:
        rx_delta = None
        tx_delta = None
        elapsed_seconds = None
        if previous_snapshot is not None:
            elapsed_seconds = max(
                (_ensure_aware(snapshot.created_at) - _ensure_aware(previous_snapshot.created_at)).total_seconds(),
                0.0,
            )
            rx_delta = max(snapshot.rx_bytes - previous_snapshot.rx_bytes, 0)
            tx_delta = max(snapshot.tx_bytes - previous_snapshot.tx_bytes, 0)
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
        snapshot_count_24h=len(window_snapshots),
        traffic_24h_rx_bytes=traffic_24h_rx_bytes,
        traffic_24h_tx_bytes=traffic_24h_tx_bytes,
        interfaces_seen=sorted(interfaces_seen),
        recent_samples=list(reversed(recent_samples)),
    )


def format_traffic_diagnosis(diagnosis: TrafficDiagnosis) -> str:
    node = diagnosis.node
    lines = [
        f"🧪 流量诊断 | {node.name}",
        "",
        "── 当前口径",
        f"状态 {node.status.value}",
        f"当前网卡 {_format_interface(diagnosis.current_interface)}",
        f"最近出现 {', '.join(_format_interface(item) for item in diagnosis.interfaces_seen) if diagnosis.interfaces_seen else '暂无'}",
        f"最新累计↓ {format_bytes(node.latest_rx_bytes or 0)}",
        f"最新累计↑ {format_bytes(node.latest_tx_bytes or 0)}",
        f"最新合计 {format_bytes(diagnosis.current_total_bytes)}",
        "",
        "── 24h 统计依据",
        f"样本数 {diagnosis.snapshot_count_24h}",
        f"24h↓ {format_bytes(diagnosis.traffic_24h_rx_bytes)}",
        f"24h↑ {format_bytes(diagnosis.traffic_24h_tx_bytes)}",
        f"24h合计 {format_bytes(diagnosis.traffic_24h_total_bytes)}",
        "",
        "── 诊断提示",
    ]

    hints: list[str] = []
    if diagnosis.uses_aggregate:
        hints.append("当前或最近快照使用了 aggregate 汇总口径，私网/隧道/额外网卡流量可能被一起统计。")
    if diagnosis.has_multiple_interfaces:
        hints.append("最近快照出现了多张网卡，说明统计口径可能切换过，和服务商面板更容易不一致。")
    hints.append("ProxyPulse 统计口径是 RX+TX 合计；部分服务商面板只统计公网主网卡或计费流量。")

    for hint in hints:
        lines.append(f"• {hint}")

    lines.extend(["", "── 最近快照"])
    if not diagnosis.recent_samples:
        lines.append("暂无历史快照。")
        return "\n".join(lines)

    for index, sample in enumerate(diagnosis.recent_samples):
        lines.append(
            f"{sample.created_at.astimezone(UTC).strftime('%m-%d %H:%M:%S')}  {_format_interface(sample.network_interface)}"
        )
        lines.append(f"累计↓ {format_bytes(sample.rx_bytes)}  累计↑ {format_bytes(sample.tx_bytes)}")
        if index == 0 or sample.rx_delta is None or sample.tx_delta is None or sample.elapsed_seconds is None:
            lines.append("Δ --")
        else:
            lines.append(
                f"Δ {int(sample.elapsed_seconds)}s  ↓ {format_bytes(sample.rx_delta)}  ↑ {format_bytes(sample.tx_delta)}"
            )
        if index != len(diagnosis.recent_samples) - 1:
            lines.append("")
    return "\n".join(lines)
