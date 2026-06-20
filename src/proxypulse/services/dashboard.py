from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node
from proxypulse.services.quota import QuotaStatus, get_quota_status
from proxypulse.services.reports import counter_delta, sum_snapshot_traffic_by_node

settings = get_settings()


@dataclass(slots=True)
class NodeRateSummary:
    rx_bps: float | None
    tx_bps: float | None
    sample_seconds: float | None


@dataclass(slots=True)
class NodeTrafficWindowSummary:
    rx_bytes: int
    tx_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes


@dataclass(slots=True)
class NodeTrendSummary:
    sample_count: int
    avg_cpu_percent: float | None
    peak_cpu_percent: float | None
    avg_memory_percent: float | None
    peak_memory_percent: float | None
    avg_disk_percent: float | None
    peak_disk_percent: float | None
    rx_bytes: int
    tx_bytes: int


@dataclass(slots=True)
class NodesOverviewSummary:
    online_count: int
    offline_count: int
    pending_count: int


@dataclass(slots=True)
class NodeCardSummary:
    node: Node
    trend_1h: NodeTrendSummary
    quota_status: QuotaStatus


@dataclass(slots=True)
class NodeDetailSummary:
    current_rate: NodeRateSummary
    trend_1h: NodeTrendSummary


def _db_datetime(value: datetime) -> datetime:
    if settings.database_url.startswith("sqlite"):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _calculate_rate(latest_row, previous_row) -> NodeRateSummary:
    latest_at = _ensure_aware(latest_row.created_at)
    previous_at = _ensure_aware(previous_row.created_at)
    elapsed_seconds = (latest_at - previous_at).total_seconds()
    if elapsed_seconds <= 0:
        return NodeRateSummary(rx_bps=None, tx_bps=None, sample_seconds=None)

    rx_delta = counter_delta(latest_row.rx_bytes, previous_row.rx_bytes)
    tx_delta = counter_delta(latest_row.tx_bytes, previous_row.tx_bytes)
    return NodeRateSummary(
        rx_bps=rx_delta / elapsed_seconds,
        tx_bps=tx_delta / elapsed_seconds,
        sample_seconds=elapsed_seconds,
    )


async def get_current_rate_map(session: AsyncSession, node_ids: list[str]) -> dict[str, NodeRateSummary]:
    if not node_ids:
        return {}

    rate_map: dict[str, NodeRateSummary] = {}
    for node_id in node_ids:
        result = await session.execute(
            select(
                MetricSnapshot.created_at,
                MetricSnapshot.rx_bytes,
                MetricSnapshot.tx_bytes,
            )
            .where(MetricSnapshot.node_id == node_id)
            .order_by(MetricSnapshot.created_at.desc())
            .limit(2)
        )
        rows = result.all()
        if len(rows) < 2:
            rate_map[node_id] = NodeRateSummary(rx_bps=None, tx_bps=None, sample_seconds=None)
            continue
        latest_row, previous_row = rows[0], rows[1]
        rate_map[node_id] = _calculate_rate(latest_row, previous_row)

    return rate_map


async def get_traffic_window_map(
    session: AsyncSession,
    node_ids: list[str],
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, NodeTrafficWindowSummary]:
    if not node_ids:
        return {}

    totals = await sum_snapshot_traffic_by_node(
        session,
        start_at=start_at,
        end_at=end_at,
        node_ids=node_ids,
    )
    return {
        node_id: NodeTrafficWindowSummary(rx_bytes=rx_bytes, tx_bytes=tx_bytes)
        for node_id, (rx_bytes, tx_bytes) in totals.items()
    }


async def get_trend_summary(
    session: AsyncSession,
    node_id: str,
    *,
    end_at: datetime | None = None,
    window: timedelta = timedelta(hours=1),
    include_traffic: bool = True,
) -> NodeTrendSummary:
    window_end = end_at or datetime.now(UTC)
    window_start = window_end - window
    aggregate_result = await session.execute(
        select(
            func.count(MetricSnapshot.id),
            func.avg(MetricSnapshot.cpu_percent),
            func.max(MetricSnapshot.cpu_percent),
            func.avg(MetricSnapshot.memory_percent),
            func.max(MetricSnapshot.memory_percent),
            func.avg(MetricSnapshot.disk_percent),
            func.max(MetricSnapshot.disk_percent),
        ).where(
            MetricSnapshot.node_id == node_id,
            MetricSnapshot.created_at >= _db_datetime(window_start),
            MetricSnapshot.created_at <= _db_datetime(window_end),
        )
    )
    (
        sample_count,
        avg_cpu_percent,
        peak_cpu_percent,
        avg_memory_percent,
        peak_memory_percent,
        avg_disk_percent,
        peak_disk_percent,
    ) = aggregate_result.one()

    if include_traffic:
        traffic_map = await get_traffic_window_map(session, [node_id], start_at=window_start, end_at=window_end)
        traffic_summary = traffic_map.get(node_id, NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0))
    else:
        traffic_summary = NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0)
    return NodeTrendSummary(
        sample_count=sample_count or 0,
        avg_cpu_percent=avg_cpu_percent,
        peak_cpu_percent=peak_cpu_percent,
        avg_memory_percent=avg_memory_percent,
        peak_memory_percent=peak_memory_percent,
        avg_disk_percent=avg_disk_percent,
        peak_disk_percent=peak_disk_percent,
        rx_bytes=traffic_summary.rx_bytes,
        tx_bytes=traffic_summary.tx_bytes,
    )


async def build_nodes_dashboard(session: AsyncSession, nodes: list[Node]) -> tuple[NodesOverviewSummary, list[NodeCardSummary]]:
    now = datetime.now(UTC)

    cards: list[NodeCardSummary] = []
    for node in nodes:
        cards.append(
            NodeCardSummary(
                node=node,
                trend_1h=await get_trend_summary(session, node.id, end_at=now, include_traffic=False),
                quota_status=await get_quota_status(session, node, now=now),
            )
        )

    overview = NodesOverviewSummary(
        online_count=sum(1 for node in nodes if node.is_online),
        offline_count=sum(1 for node in nodes if node.status.value == "offline"),
        pending_count=sum(1 for node in nodes if node.status.value == "pending"),
    )
    return overview, cards


async def build_node_detail_summary(session: AsyncSession, node: Node) -> NodeDetailSummary:
    now = datetime.now(UTC)
    rate_map = await get_current_rate_map(session, [node.id])
    return NodeDetailSummary(
        current_rate=rate_map.get(node.id, NodeRateSummary(None, None, None)),
        trend_1h=await get_trend_summary(session, node.id, end_at=now),
    )
