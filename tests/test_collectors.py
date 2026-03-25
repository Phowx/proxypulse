from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase, mock

from proxypulse.agent.collectors import AGGREGATE_INTERFACE_NAME, collect_metrics


def _counter(
    *,
    bytes_recv: int,
    bytes_sent: int,
    packets_recv: int,
    packets_sent: int,
    errin: int = 0,
    errout: int = 0,
    dropin: int = 0,
    dropout: int = 0,
):
    return SimpleNamespace(
        bytes_recv=bytes_recv,
        bytes_sent=bytes_sent,
        packets_recv=packets_recv,
        packets_sent=packets_sent,
        errin=errin,
        errout=errout,
        dropin=dropin,
        dropout=dropout,
    )


def _memory(*, percent: float, total: int, used: int):
    return SimpleNamespace(percent=percent, total=total, used=used)


def _disk(*, percent: float, total: int, used: int):
    return SimpleNamespace(percent=percent, total=total, used=used)


class CollectorsTests(TestCase):
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_count", return_value=8)
    @mock.patch("proxypulse.agent.collectors.psutil.boot_time", return_value=1_000.0)
    @mock.patch("proxypulse.agent.collectors.time.time", return_value=1_900.0)
    @mock.patch("proxypulse.agent.collectors.os.getloadavg", return_value=(0.42, 0.0, 0.0))
    @mock.patch("proxypulse.agent.collectors.psutil.disk_usage")
    @mock.patch("proxypulse.agent.collectors.psutil.virtual_memory")
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_percent", return_value=12.5)
    @mock.patch("proxypulse.agent.collectors.psutil.net_io_counters")
    def test_collect_metrics_uses_configured_interface(
        self,
        net_io_counters,
        _cpu_percent,
        virtual_memory,
        disk_usage,
        _getloadavg,
        _time,
        _boot_time,
        _cpu_count,
    ) -> None:
        net_io_counters.return_value = {
            "eth0": _counter(bytes_recv=100, bytes_sent=200, packets_recv=10, packets_sent=20, errin=1, dropin=2),
            "eth1": _counter(bytes_recv=900, bytes_sent=800, packets_recv=90, packets_sent=80),
        }
        virtual_memory.return_value = _memory(percent=64.0, total=16_000, used=10_240)
        disk_usage.return_value = _disk(percent=51.0, total=100_000, used=51_000)

        metrics = collect_metrics("eth0", "auto")

        self.assertEqual(metrics.network_interface, "eth0")
        self.assertEqual(metrics.rx_bytes, 100)
        self.assertEqual(metrics.tx_bytes, 200)
        self.assertEqual(metrics.rx_packets, 10)
        self.assertEqual(metrics.tx_packets, 20)
        self.assertEqual(metrics.rx_errors, 1)
        self.assertEqual(metrics.rx_dropped, 2)
        self.assertEqual(metrics.memory_total_bytes, 16_000)
        self.assertEqual(metrics.disk_used_bytes, 51_000)
        self.assertEqual(metrics.cpu_count, 8)
        self.assertEqual(metrics.uptime_seconds, 900)

    @mock.patch("proxypulse.agent.collectors._detect_primary_interface", return_value="eth9")
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_count", return_value=4)
    @mock.patch("proxypulse.agent.collectors.psutil.boot_time", return_value=100.0)
    @mock.patch("proxypulse.agent.collectors.time.time", return_value=400.0)
    @mock.patch("proxypulse.agent.collectors.os.getloadavg", return_value=(1.0, 0.0, 0.0))
    @mock.patch("proxypulse.agent.collectors.psutil.disk_usage")
    @mock.patch("proxypulse.agent.collectors.psutil.virtual_memory")
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_percent", return_value=50.0)
    @mock.patch("proxypulse.agent.collectors.psutil.net_io_counters")
    def test_collect_metrics_auto_detects_primary_interface(
        self,
        net_io_counters,
        _cpu_percent,
        virtual_memory,
        disk_usage,
        _getloadavg,
        _time,
        _boot_time,
        _cpu_count,
        _detect_primary_interface,
    ) -> None:
        net_io_counters.return_value = {
            "eth9": _counter(bytes_recv=5_000, bytes_sent=7_000, packets_recv=50, packets_sent=70),
            "eth10": _counter(bytes_recv=1, bytes_sent=1, packets_recv=1, packets_sent=1),
        }
        virtual_memory.return_value = _memory(percent=25.0, total=8_000, used=2_000)
        disk_usage.return_value = _disk(percent=10.0, total=50_000, used=5_000)

        metrics = collect_metrics("", "auto")

        self.assertEqual(metrics.network_interface, "eth9")
        self.assertEqual(metrics.rx_bytes, 5_000)
        self.assertEqual(metrics.tx_bytes, 7_000)

    @mock.patch("proxypulse.agent.collectors._detect_primary_interface", return_value=None)
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_count", return_value=2)
    @mock.patch("proxypulse.agent.collectors.psutil.boot_time", return_value=50.0)
    @mock.patch("proxypulse.agent.collectors.time.time", return_value=80.0)
    @mock.patch("proxypulse.agent.collectors.os.getloadavg", return_value=(0.1, 0.0, 0.0))
    @mock.patch("proxypulse.agent.collectors.psutil.disk_usage")
    @mock.patch("proxypulse.agent.collectors.psutil.virtual_memory")
    @mock.patch("proxypulse.agent.collectors.psutil.cpu_percent", return_value=5.0)
    @mock.patch("proxypulse.agent.collectors.psutil.net_io_counters")
    def test_collect_metrics_falls_back_to_aggregate_scope(
        self,
        net_io_counters,
        _cpu_percent,
        virtual_memory,
        disk_usage,
        _getloadavg,
        _time,
        _boot_time,
        _cpu_count,
        _detect_primary_interface,
    ) -> None:
        net_io_counters.return_value = {
            "lo0": _counter(bytes_recv=1, bytes_sent=1, packets_recv=1, packets_sent=1),
            "eth0": _counter(bytes_recv=100, bytes_sent=200, packets_recv=10, packets_sent=20, errout=3),
            "eth1": _counter(bytes_recv=300, bytes_sent=400, packets_recv=30, packets_sent=40, dropout=4),
        }
        virtual_memory.return_value = _memory(percent=12.0, total=4_000, used=480)
        disk_usage.return_value = _disk(percent=20.0, total=10_000, used=2_000)

        metrics = collect_metrics("", "auto")

        self.assertEqual(metrics.network_interface, AGGREGATE_INTERFACE_NAME)
        self.assertEqual(metrics.rx_bytes, 400)
        self.assertEqual(metrics.tx_bytes, 600)
        self.assertEqual(metrics.tx_errors, 3)
        self.assertEqual(metrics.tx_dropped, 4)
