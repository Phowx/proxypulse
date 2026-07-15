from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from proxypulse.core.collections import COLLECTION_LABELS, COLLECTION_ORDER, STANDARD_COLLECTIONS


def node_collection_scope(node: Any) -> tuple[str, ...]:
    configured = getattr(node, "collection_scope", None)
    if not configured:
        return STANDARD_COLLECTIONS
    enabled = set(configured)
    return tuple(name for name in COLLECTION_ORDER if name in enabled)


def format_collection_scope(node: Any) -> str:
    return "、".join(COLLECTION_LABELS[name] for name in node_collection_scope(node))


def format_scoped_value(
    node: Any,
    group: str,
    value: Any,
    formatter: Callable[[Any], str],
) -> str:
    if group not in node_collection_scope(node):
        return "未启用"
    if value is None:
        return "暂未上报"
    return formatter(value)


def format_scoped_values(
    node: Any,
    group: str,
    values: Iterable[Any],
    formatter: Callable[[], str],
) -> str:
    if group not in node_collection_scope(node):
        return "未启用"
    if all(value is None for value in values):
        return "暂未上报"
    return formatter()
