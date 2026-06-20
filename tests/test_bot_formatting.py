from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from proxypulse.bot import main as bot_main
from proxypulse.bot.main import (
    build_dashboard_menu_text,
    build_node_detail_keyboard,
    build_node_list_keyboard,
    dashboard_button_rows,
    display_width,
    render_aligned_table,
    render_node_card,
)
from proxypulse.services.dashboard import NodeCardSummary, NodeRateSummary, NodeTrafficWindowSummary, NodeTrendSummary
from proxypulse.services.quota import QuotaStatus


class BotFormattingTests(TestCase):
    def test_dashboard_menu_text_is_compact(self) -> None:
        rendered = build_dashboard_menu_text()

        self.assertIn("ProxyPulse 控制台", rendered)
        self.assertNotIn("快捷入口", rendered)

    def test_dashboard_button_rows_use_single_row(self) -> None:
        rows = dashboard_button_rows()

        self.assertEqual(rows[0], ["节点概览", "DNS 管理"])
        self.assertEqual(len(rows), 1)

    def test_node_list_keyboard_only_keeps_overview_actions(self) -> None:
        keyboard = build_node_list_keyboard(["la163", "lagia", "tokyo", "hk01"])

        self.assertIsNotNone(keyboard)
        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["刷新概览"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["返回菜单"])

    def test_node_detail_keyboard_removes_daily_report(self) -> None:
        keyboard = build_node_detail_keyboard("lagia")

        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["刷新详情", "返回节点列表", "返回菜单"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["🗑️ 删除节点"])

    def test_aligned_table_accounts_for_chinese_width(self) -> None:
        rendered = render_aligned_table(
            ("范围", "下行", "上行"),
            [("实时", "1.0 MB/s", "20.0 KB/s"), ("24h", "10.0 GB", "2.0 GB")],
        )

        widths = [display_width(line) for line in rendered]
        self.assertEqual(len(set(widths)), 1)

    def test_render_node_card_handles_missing_values(self) -> None:
        node = SimpleNamespace(
            name="hk-01",
            is_online=False,
            status=SimpleNamespace(value="offline"),
            last_seen_at=datetime(2026, 3, 25, 11, 59, tzinfo=timezone.utc),
            latest_network_interface=None,
            hostname=None,
            platform=None,
            ips=[],
            latest_cpu_percent=None,
            latest_cpu_count=None,
            latest_load_avg_1m=None,
            latest_uptime_seconds=None,
            latest_memory_percent=None,
            latest_memory_used_bytes=None,
            latest_memory_total_bytes=None,
            latest_disk_percent=None,
            latest_disk_used_bytes=None,
            latest_disk_total_bytes=None,
            latest_rx_bytes=None,
            latest_tx_bytes=None,
            latest_rx_packets=None,
            latest_tx_packets=None,
            latest_rx_errors=None,
            latest_tx_errors=None,
            latest_rx_dropped=None,
            latest_tx_dropped=None,
        )
        card = NodeCardSummary(
            node=node,
            active_alert_count=2,
            current_rate=NodeRateSummary(rx_bps=None, tx_bps=None, sample_seconds=None),
            traffic_24h=NodeTrafficWindowSummary(rx_bytes=0, tx_bytes=0),
            trend_1h=NodeTrendSummary(
                sample_count=0,
                avg_cpu_percent=None,
                peak_cpu_percent=None,
                avg_memory_percent=None,
                peak_memory_percent=None,
                avg_disk_percent=None,
                peak_disk_percent=None,
                rx_bytes=0,
                tx_bytes=0,
            ),
            quota_status=QuotaStatus(
                configured=False,
                limit_bytes=None,
                used_bytes=0,
                remaining_bytes=None,
                percent_used=None,
                period_start=None,
                next_reset_at=None,
                cycle_description=None,
                calibration_bytes=None,
            ),
        )

        rendered = render_node_card(card)

        self.assertIn("hk-01", rendered)
        self.assertIn("🔴 离线", rendered)
        self.assertIn("暂无", rendered)
        self.assertIn("CPU   暂无", rendered)
        self.assertNotIn("基础信息", rendered)
        self.assertIn("项目  当前", rendered)
        self.assertIn("范围", rendered)
        self.assertIn("24h", rendered)
        self.assertIn("告警 2", rendered)
        self.assertIn("流量套餐\n未配置", rendered)


class BotResponseTests(IsolatedAsyncioTestCase):
    async def test_daily_response_has_no_action_keyboard(self) -> None:
        class SessionContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        with (
            patch.object(bot_main, "SessionLocal", return_value=SessionContext()),
            patch.object(
                bot_main,
                "summarize_previous_local_day",
                AsyncMock(return_value=(date(2026, 4, 10), object())),
            ),
            patch.object(bot_main, "format_traffic_summary", return_value="日报"),
        ):
            text, keyboard = await bot_main.render_daily_response()

        self.assertEqual(text, "日报")
        self.assertIsNone(keyboard)
