from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from proxypulse.core.models import AlertStatus, NodeStatus


class NodeEnrollResponse(BaseModel):
    node_name: str
    enrollment_token: str


class AgentRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enrollment_token: str = Field(min_length=8)
    hostname: str | None = None
    platform: str | None = None
    ips: list[str] = Field(default_factory=list)


class AgentRegisterResponse(BaseModel):
    node_id: str
    agent_token: str


class HeartbeatRequest(BaseModel):
    hostname: str | None = None
    platform: str | None = None
    ips: list[str] = Field(default_factory=list)


class MetricSnapshotIn(BaseModel):
    cpu_percent: float
    memory_percent: float
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    disk_percent: float
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    load_avg_1m: float
    cpu_count: int | None = None
    network_interface: str | None = None
    rx_bytes: int
    tx_bytes: int
    rx_packets: int | None = None
    tx_packets: int | None = None
    rx_errors: int | None = None
    tx_errors: int | None = None
    rx_dropped: int | None = None
    tx_dropped: int | None = None
    uptime_seconds: int


class NodeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    hostname: str | None
    platform: str | None
    status: NodeStatus
    is_online: bool
    last_seen_at: datetime | None
    latest_cpu_percent: float | None
    latest_memory_percent: float | None
    latest_disk_percent: float | None
    latest_load_avg_1m: float | None
    latest_cpu_count: int | None
    latest_uptime_seconds: int | None
    latest_memory_total_bytes: int | None
    latest_memory_used_bytes: int | None
    latest_disk_total_bytes: int | None
    latest_disk_used_bytes: int | None
    latest_network_interface: str | None
    latest_rx_bytes: int | None
    latest_tx_bytes: int | None
    latest_rx_packets: int | None
    latest_tx_packets: int | None
    latest_rx_errors: int | None
    latest_tx_errors: int | None
    latest_rx_dropped: int | None
    latest_tx_dropped: int | None


class NodeDetail(NodeSummary):
    ips: list[str]


class AlertSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_key: str
    metric_name: str
    status: AlertStatus
    severity: str
    summary: str
    current_value: float | None
    threshold_value: float | None
    triggered_at: datetime
    resolved_at: datetime | None
