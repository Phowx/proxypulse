from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from proxypulse.bot.main import build_dashboard_menu_text, build_node_detail_keyboard, build_node_list_keyboard, dashboard_button_rows, render_node_card
from proxypulse.services.dashboard import NodeCardSummary, NodeRateSummary, NodeTrafficWindowSummary


class BotFormattingTests(TestCase):
    def test_dashboard_menu_text_is_compact(self) -> None:
        rendered = build_dashboard_menu_text()

        self.assertIn("ProxyPulse 控制台", rendered)
        self.assertNotIn("快捷入口", rendered)

    def test_dashboard_button_rows_use_single_row(self) -> None:
        rows = dashboard_button_rows()

        self.assertEqual(rows[0], ["节点概览", "DNS 管理"])
        self.assertEqual(len(rows), 1)

    def test_node_list_keyboard_uses_three_column_grid_and_footer(self) -> None:
        keyboard = build_node_list_keyboard(["la163", "lagia", "tokyo", "hk01"])

        self.assertIsNotNone(keyboard)
        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["la163", "lagia", "tokyo"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["hk01"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[2]], ["刷新列表"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[3]], ["返回菜单"])

    def test_node_detail_keyboard_removes_daily_report(self) -> None:
        keyboard = build_node_detail_keyboard("lagia")

        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["刷新详情", "返回节点列表", "返回菜单"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["🗑️ 删除节点"])

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
