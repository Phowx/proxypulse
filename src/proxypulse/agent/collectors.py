from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass

import psutil

IGNORED_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "cni", "flannel", "virbr")
AGGREGATE_INTERFACE_NAME = "aggregate"


@dataclass(slots=True)
class CollectedMetrics:
    cpu_percent: float
    memory_percent: float
    memory_total_bytes: int
    memory_used_bytes: int
    disk_percent: float
    disk_total_bytes: int
    disk_used_bytes: int
    load_avg_1m: float
    cpu_count: int
    network_interface: str
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int
    rx_dropped: int
    tx_dropped: int
    uptime_seconds: int


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


def _aggregate_counters() -> tuple[str, tuple[int, int, int, int, int, int, int, int]]:
    rx_bytes = 0
    tx_bytes = 0
    rx_packets = 0
    tx_packets = 0
    rx_errors = 0
    tx_errors = 0
    rx_dropped = 0
    tx_dropped = 0

    for interface, counters in psutil.net_io_counters(pernic=True).items():
        if _is_ignored_interface(interface):
            continue
        rx_bytes += counters.bytes_recv
        tx_bytes += counters.bytes_sent
        rx_packets += counters.packets_recv
        tx_packets += counters.packets_sent
        rx_errors += counters.errin
        tx_errors += counters.errout
        rx_dropped += counters.dropin
        tx_dropped += counters.dropout

    return (
        AGGREGATE_INTERFACE_NAME,
        (rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors, rx_dropped, tx_dropped),
    )


def _collect_network_totals(network_interface: str, network_interface_strategy: str) -> tuple[str, tuple[int, int, int, int, int, int, int, int]]:
    if network_interface:
        interface_name, counters = _counters_for_interface(network_interface)
        return (
            interface_name,
            (
                counters.bytes_recv,
                counters.bytes_sent,
                counters.packets_recv,
                counters.packets_sent,
                counters.errin,
                counters.errout,
                counters.dropin,
                counters.dropout,
            ),
        )

    if network_interface_strategy == "aggregate":
        return _aggregate_counters()

    if network_interface_strategy in {"auto", "fixed"}:
        detected_interface = _detect_primary_interface()
        if detected_interface:
            interface_name, counters = _counters_for_interface(detected_interface)
            return (
                interface_name,
                (
                    counters.bytes_recv,
                    counters.bytes_sent,
                    counters.packets_recv,
                    counters.packets_sent,
                    counters.errin,
                    counters.errout,
                    counters.dropin,
                    counters.dropout,
                ),
            )

    return _aggregate_counters()


def collect_metrics(network_interface: str, network_interface_strategy: str) -> CollectedMetrics:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    (
        selected_network_interface,
        (rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors, rx_dropped, tx_dropped),
    ) = _collect_network_totals(network_interface, network_interface_strategy)
    cpu_percent = psutil.cpu_percent(interval=0.2)
    memory_percent = memory.percent
    disk_percent = disk.percent
    load_avg_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    uptime_seconds = int(time.time() - psutil.boot_time())
    return CollectedMetrics(
        cpu_percent=cpu_percent,
        memory_percent=memory_percent,
        memory_total_bytes=int(memory.total),
        memory_used_bytes=int(memory.used),
        disk_percent=disk_percent,
        disk_total_bytes=int(disk.total),
        disk_used_bytes=int(disk.used),
        load_avg_1m=load_avg_1m,
        cpu_count=psutil.cpu_count() or 0,
        network_interface=selected_network_interface,
        rx_bytes=rx_bytes,
        tx_bytes=tx_bytes,
        rx_packets=rx_packets,
        tx_packets=tx_packets,
        rx_errors=rx_errors,
        tx_errors=tx_errors,
        rx_dropped=rx_dropped,
        tx_dropped=tx_dropped,
        uptime_seconds=uptime_seconds,
    )
