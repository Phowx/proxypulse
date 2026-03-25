from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from proxypulse.bot.main import render_node_card
from proxypulse.services.dashboard import NodeCardSummary, NodeRateSummary, NodeTrafficWindowSummary


class BotFormattingTests(TestCase):
    def test_render_node_card_handles_missing_values(self) -> None:
        node = SimpleNamespace(
            name="hk-01",
            is_online=False,
            status=SimpleNamespace(value="offline"),
            last_seen_at=datetime(2026, 3, 25, 11, 59, tzinfo=timezone.utc),
            latest_network_interface=None,
            latest_cpu_percent=None,
            latest_memory_percent=None,
            latest_disk_percent=None,
        )
        card = NodeCardSummary(
            node=node,
            active_alert_count=2,
            current_rate=NodeRateSummary(rx_bps=None, tx_bps=None, sample_seconds=None),
            traffic_24h=NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0),
        )

        rendered = render_node_card(card)

        self.assertIn("hk-01", rendered)
        self.assertIn("🔴 离线", rendered)
        self.assertIn("2 条活动告警", rendered)
        self.assertIn("暂无", rendered)
        self.assertIn("CPU 暂无", rendered)
        self.assertIn("暂无", rendered)
