from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache

import psutil

from proxypulse.core.collections import STANDARD_COLLECTIONS

IGNORED_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "cni", "flannel", "virbr")
logger = logging.getLogger(__name__)
AGGREGATE_INTERFACE_NAME = "aggregate"


@dataclass(slots=True)
class CollectedMetrics:
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

    def as_payload(self) -> dict[str, float | int | str | None]:
        return asdict(self)


def _is_ignored_interface(interface: str) -> bool:
    return interface.startswith(IGNORED_INTERFACE_PREFIXES)


def _route_interface() -> str | None:
    route_path = "/proc/net/route"
    if not os.path.exists(route_path):
        return None

    try:
        with open(route_path, encoding="utf-8") as route_file:
            next(route_file, None)
            for line in route_file:
                fields = line.strip().split()
                if len(fields) < 4:
                    continue
                interface, destination, flags = fields[:3]
                if destination != "00000000":
                    continue
                if not (int(flags, 16) & 0x2):
                    continue
                if _is_ignored_interface(interface):
                    continue
                return interface
    except OSError:
        return None

    return None


def _match_interface_by_local_ip(local_ip: str) -> str | None:
    for interface, addr_list in psutil.net_if_addrs().items():
        if _is_ignored_interface(interface):
            continue
        for addr in addr_list:
            if addr.family in (socket.AF_INET, socket.AF_INET6) and addr.address.split("%", 1)[0] == local_ip:
                return interface
    return None


@lru_cache(maxsize=1)
def _detect_primary_interface() -> str | None:
    routed_interface = _route_interface()
    if routed_interface:
        return routed_interface

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
    except OSError:
        return None

    return _match_interface_by_local_ip(local_ip)


def _counters_for_interface(network_interface: str) -> tuple[str, psutil._common.snetio]:
    counters = psutil.net_io_counters(pernic=True).get(network_interface)
    if counters is None:
        raise RuntimeError(f"Network interface {network_interface!r} not found.")
    return network_interface, counters


def _aggregate_counters() -> tuple[str, tuple[int, int]]:
    rx_bytes = 0
    tx_bytes = 0

    for interface, counters in psutil.net_io_counters(pernic=True).items():
        if _is_ignored_interface(interface):
            continue
        rx_bytes += counters.bytes_recv
        tx_bytes += counters.bytes_sent

    return AGGREGATE_INTERFACE_NAME, (rx_bytes, tx_bytes)


def _collect_network_totals(network_interface: str, network_interface_strategy: str) -> tuple[str, tuple[int, int]]:
    if network_interface:
        interface_name, counters = _counters_for_interface(network_interface)
        return interface_name, (counters.bytes_recv, counters.bytes_sent)

    if network_interface_strategy == "aggregate":
        return _aggregate_counters()

    if network_interface_strategy in {"auto", "fixed"}:
        detected_interface = _detect_primary_interface()
        if detected_interface:
            interface_name, counters = _counters_for_interface(detected_interface)
            return interface_name, (counters.bytes_recv, counters.bytes_sent)

    return _aggregate_counters()


def collect_metrics(
    network_interface: str,
    network_interface_strategy: str,
    collections: Iterable[str] = STANDARD_COLLECTIONS,
) -> CollectedMetrics:
    enabled = set(collections)
    metrics = CollectedMetrics()

    if "cpu" in enabled:
        try:
            metrics.cpu_percent = psutil.cpu_percent(interval=None)
            metrics.load_avg_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
            metrics.cpu_count = psutil.cpu_count() or 0
        except Exception:
            logger.exception("CPU collection failed")

    if "memory" in enabled:
        try:
            memory = psutil.virtual_memory()
            metrics.memory_percent = memory.percent
            metrics.memory_total_bytes = int(memory.total)
            metrics.memory_used_bytes = int(memory.used)
        except Exception:
            logger.exception("Memory collection failed")

    if "disk" in enabled:
        try:
            disk = psutil.disk_usage("/")
            metrics.disk_percent = disk.percent
            metrics.disk_total_bytes = int(disk.total)
            metrics.disk_used_bytes = int(disk.used)
        except Exception:
            logger.exception("Disk collection failed")

    if "network" in enabled:
        try:
            selected_interface, (rx_bytes, tx_bytes) = _collect_network_totals(
                network_interface,
                network_interface_strategy,
            )
            metrics.network_interface = selected_interface
            metrics.rx_bytes = rx_bytes
            metrics.tx_bytes = tx_bytes
        except Exception:
            logger.exception("Network collection failed")

    if "uptime" in enabled:
        try:
            metrics.uptime_seconds = int(time.time() - psutil.boot_time())
        except Exception:
            logger.exception("Uptime collection failed")

    return metrics
