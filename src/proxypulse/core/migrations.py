from __future__ import annotations

import json

from sqlalchemy import text

from proxypulse.core.collections import STANDARD_COLLECTIONS

NULLABLE_METRIC_COLUMNS = (
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "load_avg_1m",
    "rx_bytes",
    "tx_bytes",
    "uptime_seconds",
)


async def migrate_collection_schema(conn) -> None:
    if conn.dialect.name == "sqlite":
        await _migrate_sqlite_collection_schema(conn)
    elif conn.dialect.name == "postgresql":
        await _migrate_postgresql_collection_schema(conn)


async def _migrate_sqlite_collection_schema(conn) -> None:
    node_columns = await _sqlite_table_info(conn, "nodes")
    if "collection_scope" not in {row[1] for row in node_columns}:
        await conn.execute(text("ALTER TABLE nodes ADD COLUMN collection_scope JSON"))
    await conn.execute(
        text("UPDATE nodes SET collection_scope = :scope WHERE collection_scope IS NULL"),
        {"scope": json.dumps(list(STANDARD_COLLECTIONS))},
    )

    metric_columns = await _sqlite_table_info(conn, "metric_snapshots")
    if not any(row[1] in NULLABLE_METRIC_COLUMNS and row[3] for row in metric_columns):
        return

    await conn.execute(text("DROP TABLE IF EXISTS metric_snapshots_new"))
    await conn.execute(
        text(
            """
            CREATE TABLE metric_snapshots_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                node_id VARCHAR(36) NOT NULL,
                cpu_percent FLOAT,
                memory_percent FLOAT,
                disk_percent FLOAT,
                load_avg_1m FLOAT,
                cpu_count INTEGER,
                memory_total_bytes INTEGER,
                memory_used_bytes INTEGER,
                disk_total_bytes INTEGER,
                disk_used_bytes INTEGER,
                network_interface VARCHAR(64),
                rx_bytes INTEGER,
                tx_bytes INTEGER,
                uptime_seconds INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(node_id) REFERENCES nodes (id) ON DELETE CASCADE
            )
            """
        )
    )
    columns = (
        "id, node_id, cpu_percent, memory_percent, disk_percent, load_avg_1m, "
        "cpu_count, memory_total_bytes, memory_used_bytes, disk_total_bytes, "
        "disk_used_bytes, network_interface, rx_bytes, tx_bytes, uptime_seconds, created_at"
    )
    await conn.execute(
        text(
            f"INSERT INTO metric_snapshots_new ({columns}) "
            f"SELECT {columns} FROM metric_snapshots"
        )
    )
    await conn.execute(text("DROP TABLE metric_snapshots"))
    await conn.execute(text("ALTER TABLE metric_snapshots_new RENAME TO metric_snapshots"))


async def _migrate_postgresql_collection_schema(conn) -> None:
    await conn.execute(text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS collection_scope JSON"))
    await conn.execute(
        text(
            "UPDATE nodes SET collection_scope = CAST(:scope AS JSON) "
            "WHERE collection_scope IS NULL"
        ),
        {"scope": json.dumps(list(STANDARD_COLLECTIONS))},
    )
    await conn.execute(text("ALTER TABLE nodes ALTER COLUMN collection_scope SET NOT NULL"))
    for column_name in NULLABLE_METRIC_COLUMNS:
        await conn.execute(
            text(f"ALTER TABLE metric_snapshots ALTER COLUMN {column_name} DROP NOT NULL")
        )


async def _sqlite_table_info(conn, table_name: str) -> list[tuple]:
    result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
    return list(result.fetchall())
