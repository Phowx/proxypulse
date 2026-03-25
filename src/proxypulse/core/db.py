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
    result = await conn.execute(text("PRAGMA table_info(nodes)"))
    existing_columns = {row[1] for row in result.fetchall()}
    required_columns = {
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
    }
    for column_name, ddl in required_columns.items():
        if column_name not in existing_columns:
            await conn.execute(text(ddl))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
