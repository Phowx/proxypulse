from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from proxypulse.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from proxypulse.core import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.database_url.startswith("sqlite"):
            await _migrate_sqlite_schema(conn)


async def _migrate_sqlite_schema(conn) -> None:
    await _ensure_sqlite_columns(
        conn,
        "nodes",
        {
            "latest_cpu_count": "ALTER TABLE nodes ADD COLUMN latest_cpu_count INTEGER",
            "latest_uptime_seconds": "ALTER TABLE nodes ADD COLUMN latest_uptime_seconds INTEGER",
            "latest_memory_total_bytes": "ALTER TABLE nodes ADD COLUMN latest_memory_total_bytes INTEGER",
            "latest_memory_used_bytes": "ALTER TABLE nodes ADD COLUMN latest_memory_used_bytes INTEGER",
            "latest_disk_total_bytes": "ALTER TABLE nodes ADD COLUMN latest_disk_total_bytes INTEGER",
            "latest_disk_used_bytes": "ALTER TABLE nodes ADD COLUMN latest_disk_used_bytes INTEGER",
            "latest_network_interface": "ALTER TABLE nodes ADD COLUMN latest_network_interface VARCHAR(64)",
            "latest_rx_packets": "ALTER TABLE nodes ADD COLUMN latest_rx_packets INTEGER",
            "latest_tx_packets": "ALTER TABLE nodes ADD COLUMN latest_tx_packets INTEGER",
            "latest_rx_errors": "ALTER TABLE nodes ADD COLUMN latest_rx_errors INTEGER",
            "latest_tx_errors": "ALTER TABLE nodes ADD COLUMN latest_tx_errors INTEGER",
            "latest_rx_dropped": "ALTER TABLE nodes ADD COLUMN latest_rx_dropped INTEGER",
            "latest_tx_dropped": "ALTER TABLE nodes ADD COLUMN latest_tx_dropped INTEGER",
        }
        | {
        "traffic_quota_limit_bytes": "ALTER TABLE nodes ADD COLUMN traffic_quota_limit_bytes INTEGER",
        "traffic_quota_cycle_type": "ALTER TABLE nodes ADD COLUMN traffic_quota_cycle_type VARCHAR(32)",
        "traffic_quota_reset_day": "ALTER TABLE nodes ADD COLUMN traffic_quota_reset_day INTEGER",
        "traffic_quota_interval_days": "ALTER TABLE nodes ADD COLUMN traffic_quota_interval_days INTEGER",
        "traffic_quota_anchor_at": "ALTER TABLE nodes ADD COLUMN traffic_quota_anchor_at DATETIME",
        "traffic_quota_reset_hour": "ALTER TABLE nodes ADD COLUMN traffic_quota_reset_hour INTEGER",
        "traffic_quota_reset_minute": "ALTER TABLE nodes ADD COLUMN traffic_quota_reset_minute INTEGER",
        "traffic_quota_calibrated_usage_bytes": "ALTER TABLE nodes ADD COLUMN traffic_quota_calibrated_usage_bytes INTEGER",
        "traffic_quota_calibrated_total_bytes": "ALTER TABLE nodes ADD COLUMN traffic_quota_calibrated_total_bytes INTEGER",
        "traffic_quota_calibrated_at": "ALTER TABLE nodes ADD COLUMN traffic_quota_calibrated_at DATETIME",
        },
    )

    await _ensure_sqlite_columns(
        conn,
        "metric_snapshots",
        {
            "cpu_count": "ALTER TABLE metric_snapshots ADD COLUMN cpu_count INTEGER",
            "memory_total_bytes": "ALTER TABLE metric_snapshots ADD COLUMN memory_total_bytes INTEGER",
            "memory_used_bytes": "ALTER TABLE metric_snapshots ADD COLUMN memory_used_bytes INTEGER",
            "disk_total_bytes": "ALTER TABLE metric_snapshots ADD COLUMN disk_total_bytes INTEGER",
            "disk_used_bytes": "ALTER TABLE metric_snapshots ADD COLUMN disk_used_bytes INTEGER",
            "network_interface": "ALTER TABLE metric_snapshots ADD COLUMN network_interface VARCHAR(64)",
            "rx_packets": "ALTER TABLE metric_snapshots ADD COLUMN rx_packets INTEGER",
            "tx_packets": "ALTER TABLE metric_snapshots ADD COLUMN tx_packets INTEGER",
            "rx_errors": "ALTER TABLE metric_snapshots ADD COLUMN rx_errors INTEGER",
            "tx_errors": "ALTER TABLE metric_snapshots ADD COLUMN tx_errors INTEGER",
            "rx_dropped": "ALTER TABLE metric_snapshots ADD COLUMN rx_dropped INTEGER",
            "tx_dropped": "ALTER TABLE metric_snapshots ADD COLUMN tx_dropped INTEGER",
        },
    )


async def _ensure_sqlite_columns(conn, table_name: str, required_columns: dict[str, str]) -> None:
    result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
    existing_columns = {row[1] for row in result.fetchall()}
    for column_name, ddl in required_columns.items():
        if column_name not in existing_columns:
            await conn.execute(text(ddl))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
