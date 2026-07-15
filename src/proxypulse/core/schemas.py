from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from proxypulse.core.collections import STANDARD_COLLECTIONS, normalize_collections
from proxypulse.core.models import NodeStatus


class NodeEnrollResponse(BaseModel):
    node_name: str
    enrollment_token: str


class AgentRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enrollment_token: str = Field(min_length=8)
    hostname: str | None = None
    platform: str | None = None
    ips: list[str] = Field(default_factory=list)
    collections: list[str] | None = None

    @field_validator("collections")
    @classmethod
    def validate_collections(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(normalize_collections(value))


class AgentRegisterResponse(BaseModel):
    node_id: str
    agent_token: str


class HeartbeatRequest(BaseModel):
    hostname: str | None = None
    platform: str | None = None
    ips: list[str] = Field(default_factory=list)
    collections: list[str] | None = None

    @field_validator("collections")
    @classmethod
    def validate_collections(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(normalize_collections(value))


class MetricSnapshotIn(BaseModel):
    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    disk_percent: float | None = None
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    load_avg_1m: float | None = None
    cpu_count: int | None = None
    network_interface: str | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    uptime_seconds: int | None = None

    @model_validator(mode="after")
    def require_metric(self) -> "MetricSnapshotIn":
        values = self.model_dump(exclude={"network_interface"}).values()
        if all(value is None for value in values):
            raise ValueError("at least one metric value is required")
        return self


class NodeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    hostname: str | None
    platform: str | None
    status: NodeStatus
    is_online: bool
    last_seen_at: datetime | None
    collections: list[str] = Field(
        default_factory=lambda: list(STANDARD_COLLECTIONS),
        validation_alias=AliasChoices("collections", "collection_scope"),
    )
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


class NodeDetail(NodeSummary):
    ips: list[str]


class ExternalNetworkIdentityRequest(BaseModel):
    event: str = Field()
    source: str = Field(min_length=1, max_length=160)
    location: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    domains: list[str] | None = None
    observed_at: datetime | None = None
    notes: str | None = None

    @field_validator("event")
    @classmethod
    def validate_event(cls, value: str) -> str:
        if value != "network_identity":
            raise ValueError("event must be 'network_identity'.")
        return value

    @field_validator("source", "location", "ipv4", "ipv6", "notes", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str | None) -> str:
        if not value:
            raise ValueError("source is required.")
        return value

    @field_validator("domains", mode="before")
    @classmethod
    def normalize_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value if item and item.strip()]
        return normalized

    @model_validator(mode="after")
    def validate_content(self) -> "ExternalNetworkIdentityRequest":
        if self.domains is not None and not self.domains:
            raise ValueError("domains must contain at least one item.")
        if not any([self.location, self.ipv4, self.ipv6, self.domains, self.notes]):
            raise ValueError("At least one of location, ipv4, ipv6, domains, or notes is required.")
        return self
