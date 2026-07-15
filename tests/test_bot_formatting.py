from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from proxypulse.bot import main as bot_main
from proxypulse.bot.main import (
    build_dashboard_menu_text,
    build_dns_home_keyboard,
    build_node_detail_keyboard,
    build_node_list_keyboard,
    dashboard_button_rows,
    render_dns_record_text,
    render_dns_zone_text,
    render_node_card,
    render_overview_quota_html,
)
from proxypulse.core.config import CloudflareZoneConfig
from proxypulse.services.cloudflare_dns import CloudflareDNSService
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

    def test_dns_home_hides_internal_keys_but_keeps_unique_callbacks(self) -> None:
        service = CloudflareDNSService(
            api_token="token",
            zones={
                "domain2": CloudflareZoneConfig("domain2", "zone-2", "second.example"),
                "domain3": CloudflareZoneConfig("domain3", "zone-3", "third.example"),
            },
        )

        keyboard = build_dns_home_keyboard(service)

        self.assertEqual(
            [row[0].text for row in keyboard.inline_keyboard[:2]],
            ["second.example", "third.example"],
        )
        self.assertEqual(
            [row[0].callback_data for row in keyboard.inline_keyboard[:2]],
            ["dns:zone:domain2", "dns:zone:domain3"],
        )
        self.assertNotIn("domain2", render_dns_zone_text("second.example"))

    def test_dns_action_keyboards_use_compact_rows(self) -> None:
        zone_keyboard = bot_main.build_dns_zone_keyboard("main")
        self.assertEqual(
            [button.text for button in zone_keyboard.inline_keyboard[0]],
            ["查看记录", "添加记录"],
        )
        self.assertEqual(
            [button.text for button in zone_keyboard.inline_keyboard[1]],
            ["切换 Zone"],
        )

        list_keyboard = bot_main.build_dns_record_list_keyboard(
            SimpleNamespace(
                records=[],
                page=1,
                total_pages=1,
                zone=SimpleNamespace(key="main"),
            )
        )
        self.assertEqual(
            [button.text for button in list_keyboard.inline_keyboard[0]],
            ["刷新", "添加记录"],
        )
        detail_keyboard = bot_main.build_dns_record_detail_keyboard("main", "record-id")
        self.assertEqual(
            [button.text for button in detail_keyboard.inline_keyboard[0]],
            ["修改", "删除"],
        )

        type_keyboard = bot_main.build_dns_type_keyboard("main")
        self.assertTrue(all(len(row) <= 3 for row in type_keyboard.inline_keyboard[:-1]))
        self.assertEqual([button.text for button in type_keyboard.inline_keyboard[-1]], ["取消"])

        ttl_keyboard = bot_main.build_dns_ttl_keyboard(current_ttl=1, allow_keep=False)
        self.assertEqual([len(row) for row in ttl_keyboard.inline_keyboard], [3, 2, 1])

        confirm_keyboard = bot_main.build_dns_confirm_keyboard("delete")
        self.assertEqual(
            [button.text for button in confirm_keyboard.inline_keyboard[0]],
            ["确认删除", "取消"],
        )

    def test_dashboard_button_rows_use_single_row(self) -> None:
        rows = dashboard_button_rows()
        keyboard = bot_main.build_dashboard_keyboard()

        self.assertEqual(rows[0], ["节点", "DNS", "流量", "日报", "设置"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            [button.text for button in keyboard.keyboard[0]],
            ["节点", "DNS", "流量", "日报", "设置"],
        )
        self.assertTrue(keyboard.resize_keyboard)
        self.assertFalse(keyboard.one_time_keyboard)
        self.assertFalse(keyboard.is_persistent)

    def test_node_list_keyboard_groups_nodes_and_overview_actions(self) -> None:
        keyboard = build_node_list_keyboard(["la163", "lagia", "tokyo", "hk01"])

        self.assertIsNotNone(keyboard)
        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["la163", "lagia"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["tokyo", "hk01"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[2]], ["接入节点", "刷新"])

    def test_node_detail_keyboard_groups_context_actions(self) -> None:
        keyboard = build_node_detail_keyboard("lagia")

        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["刷新", "流量诊断"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["套餐信息", "返回列表"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[2]], ["🗑️ 删除节点"])

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
        )
        card = NodeCardSummary(
            node=node,
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
        self.assertIn("暂未上报", rendered)
        self.assertIn("CPU <code>暂未上报</code>", rendered)
        self.assertNotIn("基础信息", rendered)
        self.assertNotIn("网络流量", rendered)
        self.assertNotIn("24h", rendered)
        self.assertNotIn("数据包", rendered)
        self.assertNotIn("活动告警", rendered)
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
    async def asyncTearDown(self) -> None:
        bot_main.BOT_SESSIONS.clear()

    async def test_daily_response_has_refresh_and_schedule_actions(self) -> None:
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
        self.assertEqual(
            [button.text for button in keyboard.inline_keyboard[0]],
            ["刷新", "推送时间设置"],
        )

    async def test_command_menu_refreshes_private_and_admin_scopes(self) -> None:
        bot = AsyncMock()
        with patch.object(
            bot_main,
            "settings",
            SimpleNamespace(admin_telegram_ids={123456}),
        ):
            await bot_main.sync_bot_commands(bot)
        self.assertEqual(
            [command.command for command in bot_main.BOT_COMMANDS],
            ["start", "help", "cancel", "enroll"],
        )
        for call in bot.set_my_commands.await_args_list:
            self.assertEqual(
                [command.command for command in call.args[0]],
                ["start", "help", "cancel", "enroll"],
            )
        self.assertEqual(bot.set_my_commands.await_count, 3)
        self.assertEqual(bot.delete_my_commands.await_count, 6)
        self.assertEqual(bot.set_chat_menu_button.await_count, 2)
        scope_types = {
            call.kwargs["scope"].type.value
            for call in bot.set_my_commands.await_args_list
        }
        self.assertEqual(
            scope_types,
            {"default", "all_private_chats", "chat"},
        )

    async def test_navigation_cancels_dns_draft_before_routing(self) -> None:
        bot_main.BOT_SESSIONS.clear()
        bot_main.BOT_SESSIONS[42] = bot_main.BotSession(
            draft=bot_main.DnsDraft(
                mode="create",
                zone_key="main",
                record_type="A",
                pending_field="name",
            )
        )
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            text="节点",
            answer=AsyncMock(),
        )

        with (
            patch.object(bot_main, "settings", SimpleNamespace(admin_telegram_ids={42})),
            patch.object(
                bot_main,
                "render_nodes_response",
                AsyncMock(return_value=("节点页面", None)),
            ),
        ):
            await bot_main.navigation_handler(message)

        self.assertIsNone(bot_main.BOT_SESSIONS[42].draft)
        self.assertIn("已取消未完成的操作", message.answer.await_args.args[0])
        self.assertIn("节点页面", message.answer.await_args.args[0])

    async def test_cancel_and_unknown_text_have_explicit_responses(self) -> None:
        bot_main.BOT_SESSIONS.clear()
        bot_main.BOT_SESSIONS[42] = bot_main.BotSession(pending_input="enroll_name")
        cancel_message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            text="/cancel",
            answer=AsyncMock(),
        )
        unknown_message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            text="未知输入",
            answer=AsyncMock(),
        )

        with patch.object(bot_main, "settings", SimpleNamespace(admin_telegram_ids={42})):
            await bot_main.cancel_handler(cancel_message)
            await bot_main.text_input_handler(unknown_message)

        self.assertIsNone(bot_main.BOT_SESSIONS[42].pending_input)
        self.assertEqual(cancel_message.answer.await_args.args[0], "✅ 已取消当前操作。")
        self.assertEqual(
            unknown_message.answer.await_args.args[0],
            "无法识别该输入，请使用底部导航或 /help。",
        )

    async def test_enroll_without_args_starts_name_input(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            text="/enroll",
            answer=AsyncMock(),
        )
        command = SimpleNamespace(args=None)

        with patch.object(bot_main, "settings", SimpleNamespace(admin_telegram_ids={42})):
            await bot_main.enroll_handler(message, command)

        self.assertEqual(bot_main.BOT_SESSIONS[42].pending_input, "enroll_name")
        self.assertIn("节点名称", message.answer.await_args.args[0])
        keyboard = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "取消")

    async def test_hidden_menu_command_remains_compatible(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            text="/menu",
            answer=AsyncMock(),
        )

        with (
            patch.object(bot_main, "settings", SimpleNamespace(admin_telegram_ids={42})),
            patch.object(bot_main, "send_dashboard", AsyncMock()) as send_dashboard,
        ):
            await bot_main.menu_handler(message)

        send_dashboard.assert_awaited_once_with(message)
