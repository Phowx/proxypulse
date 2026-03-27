from __future__ import annotations

from unittest import TestCase

from proxypulse.core.schemas import ExternalNetworkIdentityRequest, MetricSnapshotIn


class SchemasTests(TestCase):
    def test_metric_snapshot_in_accepts_legacy_payload(self) -> None:
        payload = MetricSnapshotIn(
            cpu_percent=18.5,
            memory_percent=30.0,
            disk_percent=44.0,
            load_avg_1m=0.6,
            rx_bytes=1_000,
            tx_bytes=2_000,
            uptime_seconds=500,
        )

        self.assertIsNone(payload.network_interface)
        self.assertIsNone(payload.rx_packets)
        self.assertIsNone(payload.memory_total_bytes)

    def test_external_network_identity_request_accepts_valid_payload(self) -> None:
        payload = ExternalNetworkIdentityRequest(
            event="network_identity",
            source="la163-cloudflare-check",
            ipv4="1.2.3.4",
            domains=["a.example.com", "b.example.com"],
        )

        self.assertEqual(payload.source, "la163-cloudflare-check")
        self.assertEqual(payload.domains, ["a.example.com", "b.example.com"])

    def test_external_network_identity_request_rejects_empty_content(self) -> None:
        with self.assertRaises(ValueError):
            ExternalNetworkIdentityRequest(
                event="network_identity",
                source="la163-cloudflare-check",
            )
