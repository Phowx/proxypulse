from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from proxypulse.bot.main import build_dashboard_menu_text, dashboard_button_rows, is_supported_webapp_url, render_node_card
from proxypulse.services.dashboard import NodeCardSummary, NodeRateSummary, NodeTrafficWindowSummary


class BotFormattingTests(TestCase):
    def test_dashboard_menu_text_is_compact(self) -> None:
        rendered = build_dashboard_menu_text()

        self.assertIn("ProxyPulse 控制台", rendered)
        self.assertNotIn("快捷入口", rendered)

    def test_dashboard_button_rows_use_three_columns_when_webapp_available(self) -> None:
        rows = dashboard_button_rows(include_webapp=True)

        self.assertEqual(rows[0], ["节点概览", "流量日报", "DNS 管理"])
        self.assertEqual(rows[1], ["Web 面板"])

    def test_webapp_button_requires_https(self) -> None:
        self.assertTrue(is_supported_webapp_url("https://example.com/app"))
        self.assertFalse(is_supported_webapp_url("http://example.com/app"))

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
        self.assertIn("暂无", rendered)
        self.assertIn("CPU 暂无", rendered)
        self.assertNotIn("24h↓", rendered)
        self.assertNotIn("告警", rendered)
