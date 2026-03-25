from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import socket

import httpx
import psutil

from proxypulse.agent.collectors import collect_metrics
from proxypulse.agent.state import load_state, save_state
from proxypulse.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
IGNORED_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "cni", "flannel", "virbr")


def discover_ips() -> list[str]:
    addresses = set()

    for interface, addr_list in psutil.net_if_addrs().items():
        if interface.startswith(IGNORED_INTERFACE_PREFIXES):
            continue
        for addr in addr_list:
            if addr.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            try:
                ip = ipaddress.ip_address(addr.address.split("%", 1)[0])
            except ValueError:
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                continue
            addresses.add(str(ip))

    if addresses:
        return sorted(addresses)

    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            try:
                ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
            except ValueError:
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                continue
            addresses.add(str(ip))
    except socket.gaierror:
        logger.warning("Hostname resolution failed while discovering IPs; continuing without hostname-based addresses.")

    return sorted(addresses)


async def ensure_registration(client: httpx.AsyncClient, state: dict[str, str]) -> dict[str, str]:
    if state.get("agent_token"):
        return state
    if not settings.agent_enrollment_token:
        raise RuntimeError("PROXYPULSE_AGENT_ENROLLMENT_TOKEN is required for first registration.")

    response = await client.post(
        f"{settings.server_url}/agent/register",
        json={
            "name": settings.agent_name,
            "enrollment_token": settings.agent_enrollment_token,
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "ips": discover_ips(),
        },
    )
    response.raise_for_status()
    payload = response.json()
    state["node_id"] = payload["node_id"]
    state["agent_token"] = payload["agent_token"]
    save_state(settings.agent_state_path, state)
    return state


async def post_heartbeat(client: httpx.AsyncClient, token: str) -> None:
    response = await client.post(
        f"{settings.server_url}/agent/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "ips": discover_ips(),
        },
    )
    response.raise_for_status()


async def post_metrics(client: httpx.AsyncClient, token: str) -> None:
    metrics = collect_metrics(settings.network_interface, settings.network_interface_strategy)
    response = await client.post(
        f"{settings.server_url}/agent/metrics",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "cpu_percent": metrics.cpu_percent,
            "memory_percent": metrics.memory_percent,
            "memory_total_bytes": metrics.memory_total_bytes,
            "memory_used_bytes": metrics.memory_used_bytes,
            "disk_percent": metrics.disk_percent,
            "disk_total_bytes": metrics.disk_total_bytes,
            "disk_used_bytes": metrics.disk_used_bytes,
            "load_avg_1m": metrics.load_avg_1m,
            "cpu_count": metrics.cpu_count,
            "network_interface": metrics.network_interface,
            "rx_bytes": metrics.rx_bytes,
            "tx_bytes": metrics.tx_bytes,
            "rx_packets": metrics.rx_packets,
            "tx_packets": metrics.tx_packets,
            "rx_errors": metrics.rx_errors,
            "tx_errors": metrics.tx_errors,
            "rx_dropped": metrics.rx_dropped,
            "tx_dropped": metrics.tx_dropped,
            "uptime_seconds": metrics.uptime_seconds,
        },
    )
    response.raise_for_status()


async def run_agent() -> None:
    state = load_state(settings.agent_state_path)
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        state = await ensure_registration(client, state)
        token = state["agent_token"]

        while True:
            await post_heartbeat(client, token)
            await post_metrics(client, token)
            await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    asyncio.run(run_agent())
