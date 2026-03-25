from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node, ReportRun

settings = get_settings()


@dataclass(slots=True)
class NodeTrafficSummary:
    node_name: str
    rx_bytes: int
    tx_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes


@dataclass(slots=True)
class TrafficSummary:
    title: str
    start_at: datetime
    end_at: datetime
    node_summaries: list[NodeTrafficSummary]

    @property
    def total_rx_bytes(self) -> int:
        return sum(item.rx_bytes for item in self.node_summaries)

    @property
    def total_tx_bytes(self) -> int:
        return sum(item.tx_bytes for item in self.node_summaries)

    @property
    def total_bytes(self) -> int:
        return self.total_rx_bytes + self.total_tx_bytes


def _db_datetime(value: datetime) -> datetime:
    if settings.database_url.startswith("sqlite"):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _local_tz() -> ZoneInfo:
    return ZoneInfo(settings.report_timezone)


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"


async def summarize_traffic_window(
    session: AsyncSession,
    *,
    title: str,
    start_at: datetime,
    end_at: datetime,
) -> TrafficSummary:
    node_result = await session.execute(select(Node).order_by(Node.name.asc()))
    nodes = list(node_result.scalars().all())
    node_map = {node.id: node for node in nodes}

    snapshot_result = await session.execute(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.created_at >= _db_datetime(start_at),
            MetricSnapshot.created_at <= _db_datetime(end_at),
        )
        .order_by(MetricSnapshot.node_id.asc(), MetricSnapshot.created_at.asc())
    )
    snapshots = list(snapshot_result.scalars().all())

    per_node_bounds: dict[str, tuple[MetricSnapshot, MetricSnapshot]] = {}
    for snapshot in snapshots:
        existing = per_node_bounds.get(snapshot.node_id)
        if existing is None:
            per_node_bounds[snapshot.node_id] = (snapshot, snapshot)
        else:
            per_node_bounds[snapshot.node_id] = (existing[0], snapshot)

    node_summaries: list[NodeTrafficSummary] = []
    for node_id, (first_snapshot, last_snapshot) in per_node_bounds.items():
        node = node_map.get(node_id)
        if node is None:
            continue
        rx_bytes = max(last_snapshot.rx_bytes - first_snapshot.rx_bytes, 0)
        tx_bytes = max(last_snapshot.tx_bytes - first_snapshot.tx_bytes, 0)
        node_summaries.append(NodeTrafficSummary(node_name=node.name, rx_bytes=rx_bytes, tx_bytes=tx_bytes))

    node_summaries.sort(key=lambda item: item.node_name)
    return TrafficSummary(title=title, start_at=start_at, end_at=end_at, node_summaries=node_summaries)


async def summarize_recent_24h(session: AsyncSession) -> TrafficSummary:
    end_at = datetime.now(UTC)
    start_at = end_at - timedelta(hours=24)
    return await summarize_traffic_window(
        session,
        title="近 24 小时流量汇总",
        start_at=start_at,
        end_at=end_at,
    )


async def summarize_previous_local_day(session: AsyncSession, today_local: date | None = None) -> tuple[date, TrafficSummary]:
    local_tz = _local_tz()
    if today_local is None:
        today_local = datetime.now(local_tz).date()
    report_day = today_local - timedelta(days=1)
    start_local = datetime.combine(report_day, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    summary = await summarize_traffic_window(
        session,
        title=f"{report_day.isoformat()} 流量日报",
        start_at=start_local.astimezone(UTC),
        end_at=end_local.astimezone(UTC),
    )
    return report_day, summary


def format_traffic_summary(summary: TrafficSummary) -> str:
    lines = [
        f"📈 {summary.title}",
        (
            f"时间：{summary.start_at.astimezone(_local_tz()).strftime('%Y-%m-%d %H:%M')}"
            f" → {summary.end_at.astimezone(_local_tz()).strftime('%Y-%m-%d %H:%M')}"
        ),
        (
            f"总览：↓ {format_bytes(summary.total_rx_bytes)}"
            f" | ↑ {format_bytes(summary.total_tx_bytes)}"
            f" | 合计 {format_bytes(summary.total_bytes)}"
        ),
    ]
    if not summary.node_summaries:
        lines.append("")
        lines.append("该时间段内暂无可用流量样本。")
        return "\n".join(lines)

    lines.append("")
    lines.append("🧾 节点明细")
    for item in summary.node_summaries:
        lines.append(
            f"━━━━━━━━━━\n"
            f"🖥️ {item.node_name}\n"
            f"↓ {format_bytes(item.rx_bytes)} | ↑ {format_bytes(item.tx_bytes)} | 合计 {format_bytes(item.total_bytes)}"
        )
    return "\n".join(lines)


async def has_daily_report_run(session: AsyncSession, report_day: date) -> bool:
    report_key = f"daily_traffic:{report_day.isoformat()}"
    result = await session.execute(select(ReportRun).where(ReportRun.report_key == report_key))
    return result.scalar_one_or_none() is not None


def mark_daily_report_run(session: AsyncSession, report_day: date) -> None:
    report_key = f"daily_traffic:{report_day.isoformat()}"
    session.add(ReportRun(report_key=report_key))


def should_send_daily_report(now_local: datetime) -> bool:
    scheduled_time = time(settings.daily_report_hour, settings.daily_report_minute)
    return now_local.timetz().replace(tzinfo=None) >= scheduled_time
