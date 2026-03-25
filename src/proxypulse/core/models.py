from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from proxypulse.core.db import Base


class NodeStatus(str, enum.Enum):
    pending = "pending"
    online = "online"
    offline = "offline"


class AlertStatus(str, enum.Enum):
    active = "active"
    resolved = "resolved"


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
    latest_rx_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_tx_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    alert_events: Mapped[list["AlertEvent"]] = relationship(back_populates="node", cascade="all, delete-orphan")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    cpu_percent: Mapped[float] = mapped_column(Float, nullable=False)
    memory_percent: Mapped[float] = mapped_column(Float, nullable=False)
    disk_percent: Mapped[float] = mapped_column(Float, nullable=False)
    load_avg_1m: Mapped[float] = mapped_column(Float, nullable=False)
    rx_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    node: Mapped[Node] = relationship(back_populates="metric_snapshots")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    alert_key: Mapped[str] = mapped_column(String(120), index=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.active, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), default="warning", nullable=False)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notification_status: Mapped[AlertStatus | None] = mapped_column(Enum(AlertStatus), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    node: Mapped[Node] = relationship(back_populates="alert_events")


class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
