from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node
from proxypulse.services.alerts import count_active_alerts_by_node
from proxypulse.services.reports import accumulate_snapshot_traffic, counter_delta

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
    active_alert_count: int
    total_rx_bytes_24h: int
    total_tx_bytes_24h: int

    @property
    def total_bytes_24h(self) -> int:
        return self.total_rx_bytes_24h + self.total_tx_bytes_24h


@dataclass(slots=True)
class NodeCardSummary:
    node: Node
    active_alert_count: int
    current_rate: NodeRateSummary
    traffic_24h: NodeTrafficWindowSummary


@dataclass(slots=True)
class NodeDetailSummary:
    current_rate: NodeRateSummary
    trend_1h: NodeTrendSummary
    traffic_24h: NodeTrafficWindowSummary
    active_alert_count: int


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

    ranked_snapshots = (
        select(
            MetricSnapshot.node_id.label("node_id"),
            MetricSnapshot.created_at.label("created_at"),
            MetricSnapshot.rx_bytes.label("rx_bytes"),
            MetricSnapshot.tx_bytes.label("tx_bytes"),
            func.row_number()
            .over(partition_by=MetricSnapshot.node_id, order_by=MetricSnapshot.created_at.desc())
            .label("rank_desc"),
        )
        .where(MetricSnapshot.node_id.in_(node_ids))
        .subquery()
    )
    result = await session.execute(
        select(ranked_snapshots)
        .where(ranked_snapshots.c.rank_desc <= 2)
        .order_by(ranked_snapshots.c.node_id.asc(), ranked_snapshots.c.rank_desc.asc())
    )

    rows_by_node: dict[str, list] = {}
    for row in result.all():
        rows_by_node.setdefault(row.node_id, []).append(row)

    rate_map: dict[str, NodeRateSummary] = {}
    for node_id, rows in rows_by_node.items():
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

    result = await session.execute(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.node_id.in_(node_ids),
            MetricSnapshot.created_at >= _db_datetime(start_at),
            MetricSnapshot.created_at <= _db_datetime(end_at),
        )
        .order_by(MetricSnapshot.node_id.asc(), MetricSnapshot.created_at.asc())
    )

    snapshots_by_node: dict[str, list[MetricSnapshot]] = {}
    for snapshot in result.scalars().all():
        snapshots_by_node.setdefault(snapshot.node_id, []).append(snapshot)

    traffic_map: dict[str, NodeTrafficWindowSummary] = {}
    for node_id, node_snapshots in snapshots_by_node.items():
        rx_bytes, tx_bytes = accumulate_snapshot_traffic(node_snapshots)
        traffic_map[node_id] = NodeTrafficWindowSummary(
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
        )
    return traffic_map


async def get_trend_summary(
    session: AsyncSession,
    node_id: str,
    *,
    end_at: datetime | None = None,
    window: timedelta = timedelta(hours=1),
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

    traffic_map = await get_traffic_window_map(session, [node_id], start_at=window_start, end_at=window_end)
    traffic_summary = traffic_map.get(node_id, NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0))
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
    node_ids = [node.id for node in nodes]
    now = datetime.now(UTC)
    traffic_map = await get_traffic_window_map(session, node_ids, start_at=now - timedelta(hours=24), end_at=now)
    rate_map = await get_current_rate_map(session, node_ids)
    alert_count_map = await count_active_alerts_by_node(session, node_ids)

    cards: list[NodeCardSummary] = []
    for node in nodes:
        cards.append(
            NodeCardSummary(
                node=node,
                active_alert_count=alert_count_map.get(node.id, 0),
                current_rate=rate_map.get(node.id, NodeRateSummary(None, None, None)),
                traffic_24h=traffic_map.get(node.id, NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0)),
            )
        )

    overview = NodesOverviewSummary(
        online_count=sum(1 for node in nodes if node.is_online),
        offline_count=sum(1 for node in nodes if node.status.value == "offline"),
        pending_count=sum(1 for node in nodes if node.status.value == "pending"),
        active_alert_count=sum(alert_count_map.values()),
        total_rx_bytes_24h=sum(card.traffic_24h.rx_bytes for card in cards),
        total_tx_bytes_24h=sum(card.traffic_24h.tx_bytes for card in cards),
    )
    return overview, cards


async def build_node_detail_summary(session: AsyncSession, node: Node) -> NodeDetailSummary:
    now = datetime.now(UTC)
    rate_map = await get_current_rate_map(session, [node.id])
    traffic_map = await get_traffic_window_map(session, [node.id], start_at=now - timedelta(hours=24), end_at=now)
    alert_count_map = await count_active_alerts_by_node(session, [node.id])
    return NodeDetailSummary(
        current_rate=rate_map.get(node.id, NodeRateSummary(None, None, None)),
        trend_1h=await get_trend_summary(session, node.id, end_at=now),
        traffic_24h=traffic_map.get(node.id, NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0)),
        active_alert_count=alert_count_map.get(node.id, 0),
    )
