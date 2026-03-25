from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import MetricSnapshot, Node, TrafficQuotaCycle
from proxypulse.services.reports import format_bytes

settings = get_settings()
GIB = 1024**3


class QuotaServiceError(RuntimeError):
    """Business-layer exception for quota operations."""


@dataclass(slots=True)
class QuotaStatus:
    configured: bool
    limit_bytes: int | None
    used_bytes: int
    remaining_bytes: int | None
    percent_used: float | None
    period_start: datetime | None
    next_reset_at: datetime | None
    cycle_description: str | None
    calibration_bytes: int | None


def _local_tz() -> ZoneInfo:
    return ZoneInfo(settings.report_timezone)


def _db_datetime(value: datetime) -> datetime:
    if settings.database_url.startswith("sqlite"):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = (year * 12 + (month - 1)) + delta
    return month_index // 12, month_index % 12 + 1


def _monthly_boundary(year: int, month: int, reset_day: int, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    day = min(reset_day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _compute_period_bounds(node: Node, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    if node.traffic_quota_limit_bytes is None or node.traffic_quota_cycle_type is None:
        raise QuotaServiceError("该节点尚未配置流量套餐。")

    now = _ensure_aware(now or datetime.now(UTC))
    local_tz = _local_tz()
    hour = node.traffic_quota_reset_hour or 0
    minute = node.traffic_quota_reset_minute or 0

    if node.traffic_quota_cycle_type == TrafficQuotaCycle.monthly:
        reset_day = node.traffic_quota_reset_day or 1
        now_local = now.astimezone(local_tz)
        current_boundary = _monthly_boundary(now_local.year, now_local.month, reset_day, hour, minute, local_tz)
        if now_local >= current_boundary:
            start_local = current_boundary
            next_year, next_month = _shift_month(now_local.year, now_local.month, 1)
        else:
            prev_year, prev_month = _shift_month(now_local.year, now_local.month, -1)
            start_local = _monthly_boundary(prev_year, prev_month, reset_day, hour, minute, local_tz)
            next_year, next_month = now_local.year, now_local.month
        end_local = _monthly_boundary(next_year, next_month, reset_day, hour, minute, local_tz)
        return start_local.astimezone(UTC), end_local.astimezone(UTC), f"每月 {reset_day} 日 {hour:02d}:{minute:02d}"

    interval_days = node.traffic_quota_interval_days or 30
    anchor_at = _ensure_aware(node.traffic_quota_anchor_at or now)
    interval = timedelta(days=interval_days)
    if now <= anchor_at:
        start_at = anchor_at
    else:
        cycles = int((now - anchor_at) // interval)
        start_at = anchor_at + interval * cycles
    end_at = start_at + interval
    return start_at, end_at, f"每 {interval_days} 天重置一次"


async def _find_first_snapshot_in_window(session: AsyncSession, node_id: str, start_at: datetime, end_at: datetime) -> MetricSnapshot | None:
    result = await session.execute(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.node_id == node_id,
            MetricSnapshot.created_at >= _db_datetime(start_at),
            MetricSnapshot.created_at < _db_datetime(end_at),
        )
        .order_by(MetricSnapshot.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _current_total_bytes(node: Node) -> int:
    return int((node.latest_rx_bytes or 0) + (node.latest_tx_bytes or 0))


async def get_quota_status(session: AsyncSession, node: Node, now: datetime | None = None) -> QuotaStatus:
    if node.traffic_quota_limit_bytes is None or node.traffic_quota_cycle_type is None:
        return QuotaStatus(
            configured=False,
            limit_bytes=None,
            used_bytes=0,
            remaining_bytes=None,
            percent_used=None,
            period_start=None,
            next_reset_at=None,
            cycle_description=None,
            calibration_bytes=None,
        )

    period_start, next_reset_at, cycle_description = _compute_period_bounds(node, now=now)
    current_total = _current_total_bytes(node)
    baseline_total = current_total
    base_usage = 0

    if node.traffic_quota_calibrated_at is not None:
        calibrated_at = _ensure_aware(node.traffic_quota_calibrated_at)
        if calibrated_at >= period_start:
            baseline_total = node.traffic_quota_calibrated_total_bytes or current_total
            base_usage = node.traffic_quota_calibrated_usage_bytes or 0

    if base_usage == 0:
        first_snapshot = await _find_first_snapshot_in_window(session, node.id, period_start, next_reset_at)
        if first_snapshot is not None:
            baseline_total = first_snapshot.rx_bytes + first_snapshot.tx_bytes

    used_bytes = base_usage + max(current_total - baseline_total, 0)
    remaining_bytes = max(node.traffic_quota_limit_bytes - used_bytes, 0)
    percent_used = 0.0 if node.traffic_quota_limit_bytes == 0 else (used_bytes / node.traffic_quota_limit_bytes) * 100
    return QuotaStatus(
        configured=True,
        limit_bytes=node.traffic_quota_limit_bytes,
        used_bytes=used_bytes,
        remaining_bytes=remaining_bytes,
        percent_used=percent_used,
        period_start=period_start,
        next_reset_at=next_reset_at,
        cycle_description=cycle_description,
        calibration_bytes=node.traffic_quota_calibrated_usage_bytes,
    )


def _bytes_from_gib(value: float) -> int:
    return int(value * GIB)


def _parse_positive_float(value: str, field_label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise QuotaServiceError(f"{field_label}必须是数字。") from exc
    if parsed <= 0:
        raise QuotaServiceError(f"{field_label}必须大于 0。")
    return parsed


def _parse_nonnegative_float(value: str, field_label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise QuotaServiceError(f"{field_label}必须是数字。") from exc
    if parsed < 0:
        raise QuotaServiceError(f"{field_label}不能小于 0。")
    return parsed


async def configure_monthly_quota(
    session: AsyncSession,
    node: Node,
    *,
    limit_gib: float,
    reset_day: int,
    hour: int,
    minute: int,
) -> None:
    if not 1 <= reset_day <= 31:
        raise QuotaServiceError("每月重置日必须在 1 到 31 之间。")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise QuotaServiceError("重置时间格式无效。")

    node.traffic_quota_limit_bytes = _bytes_from_gib(limit_gib)
    node.traffic_quota_cycle_type = TrafficQuotaCycle.monthly
    node.traffic_quota_reset_day = reset_day
    node.traffic_quota_interval_days = None
    node.traffic_quota_reset_hour = hour
    node.traffic_quota_reset_minute = minute
    node.traffic_quota_anchor_at = None
    node.traffic_quota_calibrated_usage_bytes = None
    node.traffic_quota_calibrated_total_bytes = None
    node.traffic_quota_calibrated_at = None
    await session.commit()


async def configure_interval_quota(
    session: AsyncSession,
    node: Node,
    *,
    limit_gib: float,
    interval_days: int,
    anchor_at: datetime,
) -> None:
    if interval_days <= 0:
        raise QuotaServiceError("重置天数必须大于 0。")

    node.traffic_quota_limit_bytes = _bytes_from_gib(limit_gib)
    node.traffic_quota_cycle_type = TrafficQuotaCycle.interval_days
    node.traffic_quota_reset_day = None
    node.traffic_quota_interval_days = interval_days
    node.traffic_quota_reset_hour = anchor_at.astimezone(UTC).hour
    node.traffic_quota_reset_minute = anchor_at.astimezone(UTC).minute
    node.traffic_quota_anchor_at = anchor_at.astimezone(UTC)
    node.traffic_quota_calibrated_usage_bytes = None
    node.traffic_quota_calibrated_total_bytes = None
    node.traffic_quota_calibrated_at = None
    await session.commit()


async def calibrate_quota_usage(session: AsyncSession, node: Node, *, used_gib: float) -> None:
    if node.traffic_quota_limit_bytes is None or node.traffic_quota_cycle_type is None:
        raise QuotaServiceError("请先为该节点配置流量套餐。")

    node.traffic_quota_calibrated_usage_bytes = _bytes_from_gib(used_gib)
    node.traffic_quota_calibrated_total_bytes = _current_total_bytes(node)
    node.traffic_quota_calibrated_at = datetime.now(UTC)
    await session.commit()


async def clear_quota(session: AsyncSession, node: Node) -> None:
    node.traffic_quota_limit_bytes = None
    node.traffic_quota_cycle_type = None
    node.traffic_quota_reset_day = None
    node.traffic_quota_interval_days = None
    node.traffic_quota_anchor_at = None
    node.traffic_quota_reset_hour = None
    node.traffic_quota_reset_minute = None
    node.traffic_quota_calibrated_usage_bytes = None
    node.traffic_quota_calibrated_total_bytes = None
    node.traffic_quota_calibrated_at = None
    await session.commit()


def parse_limit_gib(value: str) -> float:
    return _parse_positive_float(value, "流量上限")


def parse_used_gib(value: str) -> float:
    return _parse_nonnegative_float(value, "已用流量")


def format_quota_status(status: QuotaStatus) -> list[str]:
    if not status.configured:
        return [
            "📦 流量套餐",
            "未配置流量上限。",
        ]

    percent = f"{status.percent_used:.1f}%" if status.percent_used is not None else "暂无"
    lines = [
        "📦 流量套餐",
        f"上限：{format_bytes(status.limit_bytes or 0)}",
        f"已用：{format_bytes(status.used_bytes)}",
        f"剩余：{format_bytes(status.remaining_bytes or 0)}",
        f"进度：{percent}",
        f"周期：{status.cycle_description or '未配置'}",
    ]
    if status.period_start is not None:
        lines.append(f"本期开始：{status.period_start.astimezone(_local_tz()).strftime('%Y-%m-%d %H:%M')}")
    if status.next_reset_at is not None:
        lines.append(f"下次重置：{status.next_reset_at.astimezone(_local_tz()).strftime('%Y-%m-%d %H:%M')}")
    if status.calibration_bytes is not None:
        lines.append(f"校准已用：{format_bytes(status.calibration_bytes)}")
    return lines
