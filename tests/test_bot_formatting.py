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
    render_dns_record_text,
    render_node_card,
    render_overview_quota_html,
)
from proxypulse.services.dashboard import NodeCardSummary, NodeTrendSummary
from proxypulse.services.quota import QuotaStatus


class BotFormattingTests(TestCase):
    def test_dashboard_menu_text_is_compact(self) -> None:
        rendered = build_dashboard_menu_text()

        self.assertIn("ProxyPulse 控制台", rendered)
        self.assertIn("<blockquote>", rendered)
        self.assertNotIn("<pre>", rendered)
        self.assertNotIn("快捷入口", rendered)

    def test_dns_record_uses_native_card_and_escapes_dynamic_values(self) -> None:
        record = SimpleNamespace(
            name="api<prod>",
            type="A",
            content="1.2.3.4&backup",
            ttl=300,
            proxied=True,
            comment='owner="ops"',
        )

        rendered = render_dns_record_text(record, "example.com")

        self.assertIn("<blockquote>", rendered)
        self.assertNotIn("<pre>", rendered)
        self.assertIn("api&lt;prod&gt;", rendered)
        self.assertIn("1.2.3.4&amp;backup", rendered)
        self.assertIn("owner=&quot;ops&quot;", rendered)

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
        self.assertIn("CPU <code>暂无</code>", rendered)
        self.assertNotIn("基础信息", rendered)
        self.assertNotIn("网络流量", rendered)
        self.assertNotIn("24h", rendered)
        self.assertNotIn("数据包", rendered)
        self.assertIn("活动告警 <code>2</code>", rendered)
        self.assertIn("<blockquote>", rendered)
        self.assertIn("📦 套餐未配置", rendered)

    def test_overview_quota_is_two_line_summary(self) -> None:
        now = datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
        status = QuotaStatus(
            configured=True,
            limit_bytes=2 * 1024**4,
            used_bytes=15 * 1024**3,
            remaining_bytes=2 * 1024**4 - 15 * 1024**3,
            percent_used=0.7,
            period_start=now,
            next_reset_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            cycle_description="每月 10 日 00:00",
            calibration_bytes=None,
        )

        rendered = "\n".join(render_overview_quota_html(status, now=now))

        self.assertIn("已用", rendered)
        self.assertIn("可用", rendered)
        self.assertIn("20 天后重置", rendered)
        self.assertNotIn("周期", rendered)
        self.assertNotIn("07-10", rendered)


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
