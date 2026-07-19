from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node, ReportRun

settings = get_settings()


def _network_snapshot_filters():
    return (
        MetricSnapshot.rx_bytes.is_not(None),
        MetricSnapshot.tx_bytes.is_not(None),
        MetricSnapshot.uptime_seconds.is_not(None),
    )


@dataclass(slots=True)
class NodeTrafficSummary:
    node_name: str
    rx_bytes: int
    tx_bytes: int
    period_used_bytes: int | None = None
    period_start_at: datetime | None = None
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


def contextual_counter_delta(
    current_value: int,
    previous_value: int,
    *,
    current_interface: str | None,
    previous_interface: str | None,
    current_uptime: int,
    previous_uptime: int,
) -> int:
    if (current_interface or "") != (previous_interface or ""):
        return 0
    if current_uptime < previous_uptime:
        return current_value
    return counter_delta(current_value, previous_value)


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
    snapshots = [
        snapshot
        for snapshot in snapshots
        if snapshot.rx_bytes is not None and snapshot.tx_bytes is not None and snapshot.uptime_seconds is not None
    ]
    if not snapshots:
        return 0, 0
    rx_total = 0
    tx_total = 0
    previous_snapshot: MetricSnapshot | None = None
    for snapshot in snapshots:
        if previous_snapshot is not None:
            context = {
                "current_interface": snapshot.network_interface,
                "previous_interface": previous_snapshot.network_interface,
                "current_uptime": snapshot.uptime_seconds,
                "previous_uptime": previous_snapshot.uptime_seconds,
            }
            rx_total += contextual_counter_delta(
                snapshot.rx_bytes,
                previous_snapshot.rx_bytes,
                **context,
            )
            tx_total += contextual_counter_delta(
                snapshot.tx_bytes,
                previous_snapshot.tx_bytes,
                **context,
            )
        previous_snapshot = snapshot
    return rx_total, tx_total


async def sum_snapshot_traffic_by_node(
    session: AsyncSession,
    *,
    start_at: datetime,
    end_at: datetime,
    node_ids: list[str] | None = None,
    end_inclusive: bool = True,
) -> dict[str, tuple[int, int]]:
    order_columns = (MetricSnapshot.created_at.asc(), MetricSnapshot.id.asc())
    baseline_query = select(
        MetricSnapshot.id.label("id"),
        func.row_number()
        .over(
            partition_by=MetricSnapshot.node_id,
            order_by=(MetricSnapshot.created_at.desc(), MetricSnapshot.id.desc()),
        )
        .label("row_number"),
    ).where(
        *_network_snapshot_filters(),
        MetricSnapshot.created_at < _db_datetime(start_at),
    )
    if node_ids is not None:
        if not node_ids:
            return {}
        baseline_query = baseline_query.where(MetricSnapshot.node_id.in_(node_ids))
    baseline_rows = baseline_query.subquery()
    baseline_ids = select(baseline_rows.c.id).where(baseline_rows.c.row_number == 1)

    end_condition = (
        MetricSnapshot.created_at <= _db_datetime(end_at)
        if end_inclusive
        else MetricSnapshot.created_at < _db_datetime(end_at)
    )
    ordered_query = select(
        MetricSnapshot.node_id.label("node_id"),
        MetricSnapshot.created_at.label("created_at"),
        MetricSnapshot.network_interface.label("network_interface"),
        MetricSnapshot.rx_bytes.label("rx_bytes"),
        MetricSnapshot.tx_bytes.label("tx_bytes"),
        MetricSnapshot.uptime_seconds.label("uptime_seconds"),
        func.lag(MetricSnapshot.network_interface)
        .over(partition_by=MetricSnapshot.node_id, order_by=order_columns)
        .label("previous_network_interface"),
        func.lag(MetricSnapshot.rx_bytes)
        .over(partition_by=MetricSnapshot.node_id, order_by=order_columns)
        .label("previous_rx_bytes"),
        func.lag(MetricSnapshot.tx_bytes)
        .over(partition_by=MetricSnapshot.node_id, order_by=order_columns)
        .label("previous_tx_bytes"),
        func.lag(MetricSnapshot.uptime_seconds)
        .over(partition_by=MetricSnapshot.node_id, order_by=order_columns)
        .label("previous_uptime_seconds"),
    ).where(
        *_network_snapshot_filters(),
        or_(
            MetricSnapshot.id.in_(baseline_ids),
            (
                (MetricSnapshot.created_at >= _db_datetime(start_at))
                & end_condition
            ),
        )
    )
    if node_ids is not None:
        ordered_query = ordered_query.where(MetricSnapshot.node_id.in_(node_ids))

    ordered = ordered_query.subquery()
    interface_changed = func.coalesce(ordered.c.network_interface, "") != func.coalesce(
        ordered.c.previous_network_interface,
        "",
    )
    host_restarted = ordered.c.uptime_seconds < ordered.c.previous_uptime_seconds
    rx_delta = case(
        (ordered.c.previous_rx_bytes.is_(None), 0),
        (interface_changed, 0),
        (host_restarted, ordered.c.rx_bytes),
        (ordered.c.rx_bytes >= ordered.c.previous_rx_bytes, ordered.c.rx_bytes - ordered.c.previous_rx_bytes),
        else_=ordered.c.rx_bytes,
    )
    tx_delta = case(
        (ordered.c.previous_tx_bytes.is_(None), 0),
        (interface_changed, 0),
        (host_restarted, ordered.c.tx_bytes),
        (ordered.c.tx_bytes >= ordered.c.previous_tx_bytes, ordered.c.tx_bytes - ordered.c.previous_tx_bytes),
        else_=ordered.c.tx_bytes,
    )
    result = await session.execute(
        select(
            ordered.c.node_id,
            func.coalesce(func.sum(rx_delta), 0),
            func.coalesce(func.sum(tx_delta), 0),
        )
        .where(ordered.c.created_at >= _db_datetime(start_at))
        .group_by(ordered.c.node_id)
    )
    return {node_id: (int(rx_bytes), int(tx_bytes)) for node_id, rx_bytes, tx_bytes in result.all()}


async def summarize_traffic_window(
    session: AsyncSession,
    *,
    title: str,
    start_at: datetime,
    end_at: datetime,
    end_inclusive: bool = True,
) -> TrafficSummary:
    node_result = await session.execute(select(Node).order_by(Node.name.asc()))
    nodes = list(node_result.scalars().all())
    node_map = {node.id: node for node in nodes}

    totals = await sum_snapshot_traffic_by_node(
        session,
        start_at=start_at,
        end_at=end_at,
        end_inclusive=end_inclusive,
    )

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
        end_inclusive=False,
    )

    # Import locally to keep report primitives independent from quota calculations.
    from proxypulse.services.quota import days_until_reset, get_quota_status

    report_end_at = end_local.astimezone(UTC)
    report_cutoff = report_end_at - timedelta(microseconds=1)
    month_start_at = datetime.combine(report_day.replace(day=1), time.min, tzinfo=local_tz).astimezone(UTC)
    node_result = await session.execute(select(Node).order_by(Node.name.asc()))
    nodes_by_name = {node.name: node for node in node_result.scalars().all()}
    month_usage_by_node_id: dict[str, tuple[int, int]] | None = None
    for item in summary.node_summaries:
        node = nodes_by_name.get(item.node_name)
        if node is None:
            continue
        quota_status = await get_quota_status(session, node, now=report_cutoff)
        if quota_status.configured:
            item.period_used_bytes = quota_status.used_bytes
            item.period_start_at = quota_status.period_start
            item.available_bytes = quota_status.remaining_bytes
            item.days_until_reset = days_until_reset(
                quota_status.next_reset_at,
                now=report_cutoff,
            )
            continue

        if month_usage_by_node_id is None:
            month_usage_by_node_id = await sum_snapshot_traffic_by_node(
                session,
                start_at=month_start_at,
                end_at=report_end_at,
                end_inclusive=False,
            )
        rx_bytes, tx_bytes = month_usage_by_node_id.get(node.id, (0, 0))
        item.period_used_bytes = rx_bytes + tx_bytes
        item.period_start_at = month_start_at
    return report_day, summary


def format_traffic_summary(
    summary: TrafficSummary,
    *,
    node_display_names: Mapping[str, str] | None = None,
) -> str:
    start_text = summary.start_at.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    end_text = summary.end_at.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"<b>📈 {html.escape(summary.title)}</b>",
        "<blockquote><b>总览</b>",
        f"时间 <code>{start_text}</code> → <code>{end_text}</code>",
        f"下行 <code>{format_bytes(summary.total_rx_bytes)}</code> · 上行 <code>{format_bytes(summary.total_tx_bytes)}</code>",
        f"合计 <code>{format_bytes(summary.total_bytes)}</code></blockquote>",
    ]
    if not summary.node_summaries:
        lines.append("\n<blockquote>该时间段内暂无可用流量样本。</blockquote>")
        return "\n".join(lines)

    lines.extend(["", "<b>节点明细</b>"])
    for item in summary.node_summaries:
        display_name = (node_display_names or {}).get(item.node_name, item.node_name)
        item_lines = [
            f"<blockquote><b>🖥️ {html.escape(display_name)}</b>",
            f"下行 <code>{format_bytes(item.rx_bytes)}</code> · 上行 <code>{format_bytes(item.tx_bytes)}</code>",
            f"合计 <code>{format_bytes(item.total_bytes)}</code>",
        ]
        if item.period_used_bytes is not None:
            period_start_text = (
                item.period_start_at.astimezone(_local_tz()).strftime("%m-%d %H:%M")
                if item.period_start_at is not None
                else "未知"
            )
            item_lines.append(
                f"本期累计 <code>{format_bytes(item.period_used_bytes)}</code>"
                f" · <code>{period_start_text}</code> 起"
            )
            item_lines.append(
                f"套餐可用 <code>{format_bytes(item.available_bytes)}</code>"
                if item.available_bytes is not None
                else "套餐可用 <code>未配置</code>"
            )
            if item.days_until_reset is not None:
                item_lines.append(
                    "重置 <code>今天</code>"
                    if item.days_until_reset == 0
                    else f"重置 <code>{item.days_until_reset} 天后</code>"
                )
        item_lines[-1] += "</blockquote>"
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
