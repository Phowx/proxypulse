from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from proxypulse.core.db import Base


class NodeStatus(str, enum.Enum):
    pending = "pending"
    online = "online"
    offline = "offline"


class TrafficQuotaCycle(str, enum.Enum):
    monthly = "monthly"
    interval_days = "interval_days"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ips: Mapped[list[str]] = mapped_column(JSON, default=list)
    enrollment_token: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    agent_token: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    status: Mapped[NodeStatus] = mapped_column(Enum(NodeStatus), default=NodeStatus.pending, nullable=False)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_disk_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_load_avg_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_uptime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_memory_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_memory_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_disk_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_disk_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_network_interface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest_rx_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_tx_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_limit_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_cycle_type: Mapped[TrafficQuotaCycle | None] = mapped_column(Enum(TrafficQuotaCycle), nullable=True)
    traffic_quota_reset_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_anchor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    traffic_quota_reset_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_reset_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_calibrated_usage_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_calibrated_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_quota_calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    metric_snapshots: Mapped[list["MetricSnapshot"]] = relationship(back_populates="node", cascade="all, delete-orphan")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_created_at", "created_at"),
        Index("ix_metric_snapshots_node_created_at", "node_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    cpu_percent: Mapped[float] = mapped_column(Float, nullable=False)
    memory_percent: Mapped[float] = mapped_column(Float, nullable=False)
    disk_percent: Mapped[float] = mapped_column(Float, nullable=False)
    load_avg_1m: Mapped[float] = mapped_column(Float, nullable=False)
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_interface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rx_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    node: Mapped[Node] = relationship(back_populates="metric_snapshots")


class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
