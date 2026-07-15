from __future__ import annotations

from collections.abc import Iterable

COLLECTION_ORDER = ("identity", "cpu", "memory", "disk", "network", "uptime")
STANDARD_COLLECTIONS = COLLECTION_ORDER
PRIVACY_COLLECTIONS = ("cpu", "memory", "disk", "network", "uptime")
TRAFFIC_COLLECTIONS = ("network", "uptime")
METRIC_COLLECTIONS = frozenset({"cpu", "memory", "disk", "network", "uptime"})
COLLECTION_LABELS = {
    "identity": "主机身份",
    "cpu": "CPU",
    "memory": "内存",
    "disk": "根磁盘",
    "network": "网络流量",
    "uptime": "运行时长",
}


def normalize_collections(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Return a validated collection scope in a stable display order."""
    if value is None:
        requested = set(STANDARD_COLLECTIONS)
    elif isinstance(value, str):
        requested = {item.strip() for item in value.split(",") if item.strip()}
    else:
        requested = {str(item).strip() for item in value if str(item).strip()}

    unknown = requested.difference(COLLECTION_ORDER)
    if unknown:
        raise ValueError(f"unknown collection groups: {', '.join(sorted(unknown))}")
    if not requested:
        raise ValueError("at least one collection group is required")
    if "network" in requested:
        requested.add("uptime")
    return tuple(name for name in COLLECTION_ORDER if name in requested)


def collection_labels(value: str | Iterable[str] | None) -> list[str]:
    return [COLLECTION_LABELS[name] for name in normalize_collections(value)]
