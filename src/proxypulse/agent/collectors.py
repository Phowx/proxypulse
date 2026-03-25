from __future__ import annotations

import os
import time
from dataclasses import dataclass

import psutil


@dataclass(slots=True)
class CollectedMetrics:
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    load_avg_1m: float
    rx_bytes: int
    tx_bytes: int
    uptime_seconds: int


def _collect_network_totals(network_interface: str) -> tuple[int, int]:
    if network_interface:
        counters = psutil.net_io_counters(pernic=True).get(network_interface)
        if counters is None:
            raise RuntimeError(f"Network interface {network_interface!r} not found.")
        return counters.bytes_recv, counters.bytes_sent

    rx_total = 0
    tx_total = 0
    for interface, counters in psutil.net_io_counters(pernic=True).items():
        if interface.startswith("lo"):
            continue
        rx_total += counters.bytes_recv
        tx_total += counters.bytes_sent
    return rx_total, tx_total


def collect_metrics(network_interface: str) -> CollectedMetrics:
    cpu_percent = psutil.cpu_percent(interval=0.2)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage("/").percent
    load_avg_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    rx_bytes, tx_bytes = _collect_network_totals(network_interface)
    uptime_seconds = int(time.time() - psutil.boot_time())
    return CollectedMetrics(
        cpu_percent=cpu_percent,
        memory_percent=memory_percent,
        disk_percent=disk_percent,
        load_avg_1m=load_avg_1m,
        rx_bytes=rx_bytes,
        tx_bytes=tx_bytes,
        uptime_seconds=uptime_seconds,
    )
