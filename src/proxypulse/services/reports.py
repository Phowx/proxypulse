from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node, ReportRun

settings = get_settings()


@dataclass(slots=True)
class NodeTrafficSummary:
    node_name: str
    rx_bytes: int
    tx_bytes: int
    month_used_bytes: int | None = None
    available_bytes: int | None = None
    days_until_reset: int | None = None

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


def counter_delta(current_value: int, previous_value: int) -> int:
    if current_value >= previous_value:
        return current_value - previous_value
    return current_value


def accumulate_counter_values(values: list[int]) -> int:
    total = 0
    previous_value: int | None = None
    for value in values:
        if previous_value is None:
            previous_value = value
            continue
        total += counter_delta(value, previous_value)
        previous_value = value
    return total


def accumulate_snapshot_traffic(snapshots: list[MetricSnapshot]) -> tuple[int, int]:
    if not snapshots:
        return 0, 0
    rx_total = accumulate_counter_values([snapshot.rx_bytes for snapshot in snapshots])
    tx_total = accumulate_counter_values([snapshot.tx_bytes for snapshot in snapshots])
    return rx_total, tx_total


async def sum_snapshot_traffic_by_node(
    session: AsyncSession,
    *,
    start_at: datetime,
    end_at: datetime,
    node_ids: list[str] | None = None,
) -> dict[str, tuple[int, int]]:
    order_columns = (MetricSnapshot.created_at.asc(), MetricSnapshot.id.asc())
    ordered_query = select(
        MetricSnapshot.node_id.label("node_id"),
        MetricSnapshot.rx_bytes.label("rx_bytes"),
        MetricSnapshot.tx_bytes.label("tx_bytes"),
        func.lag(MetricSnapshot.rx_bytes)
        .over(partition_by=MetricSnapshot.node_id, order_by=order_columns)
        .label("previous_rx_bytes"),
        func.lag(MetricSnapshot.tx_bytes)
        .over(partition_by=MetricSnapshot.node_id, order_by=order_columns)
        .label("previous_tx_bytes"),
    ).where(
        MetricSnapshot.created_at >= _db_datetime(start_at),
        MetricSnapshot.created_at <= _db_datetime(end_at),
    )
    if node_ids is not None:
        if not node_ids:
            return {}
        ordered_query = ordered_query.where(MetricSnapshot.node_id.in_(node_ids))

    ordered = ordered_query.subquery()
    rx_delta = case(
        (ordered.c.previous_rx_bytes.is_(None), 0),
        (ordered.c.rx_bytes >= ordered.c.previous_rx_bytes, ordered.c.rx_bytes - ordered.c.previous_rx_bytes),
        else_=ordered.c.rx_bytes,
    )
    tx_delta = case(
        (ordered.c.previous_tx_bytes.is_(None), 0),
        (ordered.c.tx_bytes >= ordered.c.previous_tx_bytes, ordered.c.tx_bytes - ordered.c.previous_tx_bytes),
        else_=ordered.c.tx_bytes,
    )
    result = await session.execute(
        select(
            ordered.c.node_id,
            func.coalesce(func.sum(rx_delta), 0),
            func.coalesce(func.sum(tx_delta), 0),
        ).group_by(ordered.c.node_id)
    )
    return {node_id: (int(rx_bytes), int(tx_bytes)) for node_id, rx_bytes, tx_bytes in result.all()}


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

    totals = await sum_snapshot_traffic_by_node(session, start_at=start_at, end_at=end_at)

    node_summaries: list[NodeTrafficSummary] = []
    for node_id, (rx_bytes, tx_bytes) in totals.items():
        node = node_map.get(node_id)
        if node is None:
            continue
        node_summaries.append(
            NodeTrafficSummary(
                node_name=node.name,
                rx_bytes=rx_bytes,
                tx_bytes=tx_bytes,
            )
        )

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

    month_start_local = datetime.combine(report_day.replace(day=1), time.min, tzinfo=local_tz)
    month_summary = await summarize_traffic_window(
        session,
        title=f"{report_day.strftime('%Y-%m')} 月累计流量",
        start_at=month_start_local.astimezone(UTC),
        end_at=end_local.astimezone(UTC),
    )
    month_usage_by_node = {item.node_name: item.total_bytes for item in month_summary.node_summaries}

    # Import locally to keep report primitives independent from quota calculations.
    from proxypulse.services.quota import days_until_reset, get_quota_status

    node_result = await session.execute(select(Node).order_by(Node.name.asc()))
    nodes_by_name = {node.name: node for node in node_result.scalars().all()}
    for item in summary.node_summaries:
        item.month_used_bytes = month_usage_by_node.get(item.node_name, 0)
        node = nodes_by_name.get(item.node_name)
        if node is None:
            continue
        quota_status = await get_quota_status(session, node, now=end_local.astimezone(UTC))
        if quota_status.configured:
            item.available_bytes = quota_status.remaining_bytes
            item.days_until_reset = days_until_reset(
                quota_status.next_reset_at,
                now=end_local.astimezone(UTC),
            )
    return report_day, summary


def format_traffic_summary(summary: TrafficSummary) -> str:
    lines = [
        f"📈 {summary.title}",
        "",
        "── 总览",
        (
            f"时间窗口  {summary.start_at.astimezone(_local_tz()).strftime('%Y-%m-%d %H:%M')}"
            f" → {summary.end_at.astimezone(_local_tz()).strftime('%Y-%m-%d %H:%M')}"
        ),
        f"下行总量  {format_bytes(summary.total_rx_bytes)}",
        f"上行总量  {format_bytes(summary.total_tx_bytes)}",
        f"合计流量  {format_bytes(summary.total_bytes)}",
    ]
    if not summary.node_summaries:
        lines.append("")
        lines.append("该时间段内暂无可用流量样本。")
        return "\n".join(lines)

    lines.extend(["", "── 节点明细"])
    for item in summary.node_summaries:
        item_lines = [
            "━━━━━━━━━━",
            f"🖥️ {item.node_name}",
            f"下行  {format_bytes(item.rx_bytes)}",
            f"上行  {format_bytes(item.tx_bytes)}",
            f"合计  {format_bytes(item.total_bytes)}",
        ]
        if item.month_used_bytes is not None:
            item_lines.append(f"本月累计  {format_bytes(item.month_used_bytes)}")
            item_lines.append(
                f"套餐可用  {format_bytes(item.available_bytes)}"
                if item.available_bytes is not None
                else "套餐可用  未配置"
            )
            if item.days_until_reset is not None:
                item_lines.append(
                    "距重置  今天"
                    if item.days_until_reset == 0
                    else f"距重置  {item.days_until_reset} 天"
                )
        lines.append("\n".join(item_lines))
    return "\n".join(lines)


async def has_daily_report_run(session: AsyncSession, report_day: date) -> bool:
    report_key = f"daily_traffic:{report_day.isoformat()}"
    result = await session.execute(select(ReportRun).where(ReportRun.report_key == report_key))
    return result.scalar_one_or_none() is not None


def mark_daily_report_run(session: AsyncSession, report_day: date) -> None:
    report_key = f"daily_traffic:{report_day.isoformat()}"
    session.add(ReportRun(report_key=report_key))


def should_send_daily_report(now_local: datetime, *, hour: int | None = None, minute: int | None = None) -> bool:
    scheduled_time = time(
        settings.daily_report_hour if hour is None else hour,
        settings.daily_report_minute if minute is None else minute,
    )
    return now_local.timetz().replace(tzinfo=None) >= scheduled_time
