from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from proxypulse.core.config import get_settings
from proxypulse.core.db import SessionLocal, init_db
from proxypulse.services.alerts import (
    format_alert_message,
    list_active_alerts,
    list_active_alerts_for_node,
    list_pending_notifications,
    mark_notified,
    mark_stale_nodes_offline,
)
from proxypulse.services.dashboard import build_node_detail_summary, build_nodes_dashboard
from proxypulse.services.nodes import (
    NodeServiceError,
    create_or_refresh_enrollment,
    delete_node_by_name,
    get_node_by_name,
    list_nodes,
)
from proxypulse.services.quota import (
    QuotaServiceError,
    calibrate_quota_usage,
    clear_quota,
    configure_interval_quota,
    configure_monthly_quota,
    format_quota_status,
    get_quota_status,
    parse_limit_gib,
    parse_used_gib,
)
from proxypulse.services.reports import (
    format_bytes,
    format_traffic_summary,
    has_daily_report_run,
    mark_daily_report_run,
    should_send_daily_report,
    summarize_previous_local_day,
    summarize_recent_24h,
)

settings = get_settings()
router = Router()
logger = logging.getLogger(__name__)

MENU_NODES = "节点概览"
MENU_ALERTS = "告警中心"
MENU_TRAFFIC = "24h 流量"
MENU_DAILY = "流量日报"
MENU_QUOTA = "流量套餐"
CALLBACK_SHOW_NODES = "show:nodes"
CALLBACK_SHOW_ALERTS = "show:alerts"
CALLBACK_SHOW_TRAFFIC = "show:traffic"
CALLBACK_SHOW_DAILY = "show:daily"
CALLBACK_SHOW_QUOTA_HELP = "show:quota_help"
CALLBACK_SHOW_MENU = "show:menu"
CALLBACK_NODE_PREFIX = "node:"
CALLBACK_NODE_DELETE_PREFIX = "node_delete:"
CALLBACK_NODE_DELETE_CONFIRM_PREFIX = "node_delete_confirm:"
CALLBACK_NODE_DELETE_CANCEL_PREFIX = "node_delete_cancel:"
STATUS_STYLE = {
    "online": ("🟢", "在线"),
    "pending": ("🟡", "待接入"),
    "offline": ("🔴", "离线"),
}


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_telegram_ids)


async def reject_if_not_admin(message: Message) -> bool:
    if is_admin(message):
        return False
    await message.answer("无权访问。")
    return True


def build_dashboard_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=MENU_NODES, callback_data=CALLBACK_SHOW_NODES),
            InlineKeyboardButton(text=MENU_ALERTS, callback_data=CALLBACK_SHOW_ALERTS),
        ],
        [
            InlineKeyboardButton(text=MENU_TRAFFIC, callback_data=CALLBACK_SHOW_TRAFFIC),
            InlineKeyboardButton(text=MENU_DAILY, callback_data=CALLBACK_SHOW_DAILY),
        ],
        [
            InlineKeyboardButton(text=MENU_QUOTA, callback_data=CALLBACK_SHOW_QUOTA_HELP),
        ],
    ]
    webapp_url = resolve_webapp_url()
    if is_supported_webapp_url(webapp_url):
        rows.append([InlineKeyboardButton(text="打开 Web 面板", web_app=WebAppInfo(url=webapp_url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def resolve_webapp_url() -> str:
    if settings.webapp_url:
        return settings.webapp_url.rstrip("/")
    return f"{settings.server_url.rstrip('/')}/app"


def is_supported_webapp_url(url: str) -> bool:
    return url.lower().startswith("https://")


def build_node_list_keyboard(node_names: list[str]) -> InlineKeyboardMarkup | None:
    if not node_names:
        return None
    rows = [[InlineKeyboardButton(text=node_name, callback_data=f"{CALLBACK_NODE_PREFIX}{node_name}")] for node_name in node_names]
    rows.append([InlineKeyboardButton(text="刷新列表", callback_data=CALLBACK_SHOW_NODES)])
    rows.append([InlineKeyboardButton(text="返回菜单", callback_data=CALLBACK_SHOW_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_node_detail_keyboard(node_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="刷新详情", callback_data=f"{CALLBACK_NODE_PREFIX}{node_name}")],
            [InlineKeyboardButton(text="🗑️ 删除节点", callback_data=f"{CALLBACK_NODE_DELETE_PREFIX}{node_name}")],
            [InlineKeyboardButton(text="返回节点列表", callback_data=CALLBACK_SHOW_NODES)],
            [InlineKeyboardButton(text="返回菜单", callback_data=CALLBACK_SHOW_MENU)],
        ]
    )


def build_node_delete_confirm_keyboard(node_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="确认删除", callback_data=f"{CALLBACK_NODE_DELETE_CONFIRM_PREFIX}{node_name}"),
                InlineKeyboardButton(text="取消", callback_data=f"{CALLBACK_NODE_DELETE_CANCEL_PREFIX}{node_name}"),
            ],
            [InlineKeyboardButton(text="返回节点列表", callback_data=CALLBACK_SHOW_NODES)],
        ]
    )


def build_single_action_keyboard(refresh_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="刷新", callback_data=refresh_callback)],
            [InlineKeyboardButton(text="返回菜单", callback_data=CALLBACK_SHOW_MENU)],
        ]
    )


def format_status_label(node) -> str:
    if node.is_online:
        icon, label = STATUS_STYLE["online"]
        return f"{icon} {label}"
    if node.status.value == "pending":
        icon, label = STATUS_STYLE["pending"]
        return f"{icon} {label}"
    icon, label = STATUS_STYLE["offline"]
    return f"{icon} {label}"


def format_metric_value(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return "暂无"
    return f"{value:.1f}{suffix}"


def format_byte_value(value: int | None) -> str:
    if value is None:
        return "暂无"
    return format_bytes(value)


def format_integer_value(value: int | None) -> str:
    if value is None:
        return "暂无"
    return f"{value:,}"


def format_rate_value(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{format_bytes(max(int(value), 0))}/s"


def format_network_interface_label(value: str | None) -> str:
    if not value:
        return "暂无"
    if value == "aggregate":
        return "汇总"
    return value


def format_relative_time(value: datetime | None) -> str:
    if value is None:
        return "暂无"
    aware_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta_seconds = max(int((datetime.now(UTC) - aware_value.astimezone(UTC)).total_seconds()), 0)
    if delta_seconds < 60:
        return f"{delta_seconds}s前"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m前"
    if delta_seconds < 86400:
        hours, remainder = divmod(delta_seconds, 3600)
        minutes = remainder // 60
        return f"{hours}h{minutes}m前"
    days, remainder = divmod(delta_seconds, 86400)
    hours = remainder // 3600
    return f"{days}d{hours}h前"


def format_uptime(value: int | None) -> str:
    if value is None:
        return "暂无"
    days, remainder = divmod(value, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_avg_peak(avg_value: float | None, peak_value: float | None) -> str:
    return f"均值 {format_metric_value(avg_value)} | 峰值 {format_metric_value(peak_value)}"


def format_resource_usage(used_bytes: int | None, total_bytes: int | None, percent: float | None) -> str:
    if used_bytes is not None and total_bytes is not None:
        return f"{format_byte_value(used_bytes)} / {format_byte_value(total_bytes)} ({format_metric_value(percent)})"
    if percent is not None:
        return format_metric_value(percent)
    return "暂无"


def format_network_counter_pair(left_value: int | None, right_value: int | None) -> str:
    if left_value is None and right_value is None:
        return "暂无"
    return f"{format_integer_value(left_value)} / {format_integer_value(right_value)}"


def format_alert_badge(count: int) -> str:
    if count <= 0:
        return "无活动告警"
    return f"{count} 条活动告警"


def render_section(title: str, rows: list[str]) -> list[str]:
    return [f"── {title}", *rows]


def render_metric_pair(left_label: str, left_value: str, right_label: str, right_value: str) -> str:
    left_cell = f"{left_label} {left_value}"
    padding = " " * max(26 - len(left_cell), 0)
    return f"{left_cell}{padding}  │  {right_label} {right_value}"


def render_metric_single(label: str, value: str) -> str:
    return f"{label} {value}"


def render_metric_block(title: str, rows: list[tuple[str, str, str | None, str | None]]) -> list[str]:
    rendered = [f"── {title}"]
    for left_label, left_value, right_label, right_value in rows:
        if right_label is None or right_value is None:
            rendered.append(render_metric_single(left_label, left_value))
        else:
            rendered.append(render_metric_pair(left_label, left_value, right_label, right_value))
    return rendered


def format_quota_compact_lines(status) -> list[tuple[str, str, str | None, str | None]]:
    if not status.configured:
        return [("套餐", "未配置", None, None)]

    percent = f"{status.percent_used:.1f}%" if status.percent_used is not None else "暂无"
    rows: list[tuple[str, str, str | None, str | None]] = [
        ("上限", format_byte_value(status.limit_bytes), "已用", format_byte_value(status.used_bytes)),
        ("剩余", format_byte_value(status.remaining_bytes), "进度", percent),
        ("周期", status.cycle_description or "未配置", None, None),
    ]
    if status.period_start is not None and status.next_reset_at is not None:
        rows.append(
            (
                "开始",
                status.period_start.astimezone(ZoneInfo(settings.report_timezone)).strftime("%m-%d %H:%M"),
                "重置",
                status.next_reset_at.astimezone(ZoneInfo(settings.report_timezone)).strftime("%m-%d %H:%M"),
            )
        )
    elif status.period_start is not None:
        rows.append(
            ("开始", status.period_start.astimezone(ZoneInfo(settings.report_timezone)).strftime("%m-%d %H:%M"), None, None)
        )
    elif status.next_reset_at is not None:
        rows.append(
            ("重置", status.next_reset_at.astimezone(ZoneInfo(settings.report_timezone)).strftime("%m-%d %H:%M"), None, None)
        )
    if status.calibration_bytes is not None:
        rows.append(("校准", format_byte_value(status.calibration_bytes), None, None))
    return rows


def render_panel(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def render_node_card(card) -> str:
    node = card.node
    lines = [
        "━━━━━━━━━━",
        f"{node.name}",
        f"{format_status_label(node)} · {format_relative_time(node.last_seen_at)} · {format_network_interface_label(node.latest_network_interface)}",
        "",
        *render_metric_block(
            "资源",
            [
                ("CPU", format_metric_value(node.latest_cpu_percent), "内存", format_metric_value(node.latest_memory_percent)),
                ("磁盘", format_metric_value(node.latest_disk_percent), "告警", format_alert_badge(card.active_alert_count)),
            ],
        ),
        "",
        *render_metric_block(
            "流量",
            [
                ("下行", format_rate_value(card.current_rate.rx_bps), "上行", format_rate_value(card.current_rate.tx_bps)),
                ("24h↓", format_byte_value(card.traffic_24h.rx_bytes), "24h↑", format_byte_value(card.traffic_24h.tx_bytes)),
            ],
        ),
    ]
    return "\n".join(lines)


def render_quota_help_lines() -> list[str]:
    return [
        "📦 流量套餐命令",
        "/quota <节点名> 查看当前套餐状态",
        "/quota_monthly <节点名> <上限GiB> <每月重置日> <HH:MM>",
        "/quota_interval <节点名> <上限GiB> <间隔天数> <YYYY-MM-DDTHH:MM>",
        "/quota_calibrate <节点名> <已用GiB>",
        "/quota_clear <节点名>",
        "",
        "说明：未带时区的时间按服务端配置时区解释。",
        "",
        "示例：",
        "/quota_monthly tokyo 1000 1 00:00",
        "/quota_interval la 750 30 2026-03-25T08:00",
        "/quota_calibrate tokyo 123.5",
    ]


def _parse_clock(value: str) -> tuple[int, int]:
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except ValueError as exc:
        raise QuotaServiceError("时间格式必须是 HH:MM。") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise QuotaServiceError("时间格式必须是 HH:MM。")
    return hour, minute


def _parse_local_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QuotaServiceError("时间格式必须是 YYYY-MM-DDTHH:MM。") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(settings.report_timezone))
    return parsed.astimezone(UTC)


async def send_dashboard(message: Message) -> None:
    await message.answer(
        render_panel(
            "ProxyPulse 控制台\n\n"
            "可直接使用下方菜单，\n"
            "也可以点击本消息里的快捷入口。"
        ),
        parse_mode="HTML",
    )
    await message.answer(
        render_panel("快捷入口"),
        parse_mode="HTML",
        reply_markup=build_dashboard_keyboard(),
    )


async def safe_edit_callback_message(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_text(render_panel(text), parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer()


async def render_nodes_response() -> tuple[str, InlineKeyboardMarkup | None]:
    async with SessionLocal() as session:
        nodes = await list_nodes(session)
        overview, cards = await build_nodes_dashboard(session, nodes)

    if not nodes:
        return "📡 节点概览\n当前还没有接入任何节点。", build_single_action_keyboard(CALLBACK_SHOW_NODES)

    lines = [
        "📡 节点概览",
        "",
        *render_metric_block(
            "总览",
            [
                ("在线", str(overview.online_count), "离线", str(overview.offline_count)),
                ("待接入", str(overview.pending_count), "告警", str(overview.active_alert_count)),
                ("24h", format_byte_value(overview.total_bytes_24h), None, None),
            ],
        ),
    ]
    for card in cards:
        lines.extend(["", render_node_card(card)])
    return "\n".join(lines), build_node_list_keyboard([node.name for node in nodes])


async def render_node_detail(node_name: str) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        if node is None:
            quota_status = None
            detail_summary = None
            active_alerts = []
        else:
            quota_status = await get_quota_status(session, node)
            detail_summary = await build_node_detail_summary(session, node)
            active_alerts = await list_active_alerts_for_node(session, node.id)
    if node is None:
        return "未找到对应节点。", build_single_action_keyboard(CALLBACK_SHOW_NODES)

    quota_lines = format_quota_compact_lines(quota_status)
    lines = [
        f"🖥️ 节点详情 | {node.name}",
        "",
        render_metric_pair("状态", format_status_label(node), "上报", format_relative_time(node.last_seen_at)),
        render_metric_pair("网卡", format_network_interface_label(node.latest_network_interface), "告警", format_alert_badge(detail_summary.active_alert_count)),
        "",
        *render_metric_block(
            "基础信息",
            [
                ("主机", node.hostname or "未上报", "系统", node.platform or "未上报"),
                ("IP", ", ".join(node.ips) if node.ips else "未上报", None, None),
            ],
        ),
        "",
        *render_metric_block(
            "实时状态",
            [
                ("CPU", format_metric_value(node.latest_cpu_percent), "核心", format_integer_value(node.latest_cpu_count)),
                ("负载", f"{node.latest_load_avg_1m:.2f}" if node.latest_load_avg_1m is not None else "暂无", "运行", format_uptime(node.latest_uptime_seconds)),
                ("内存", format_resource_usage(node.latest_memory_used_bytes, node.latest_memory_total_bytes, node.latest_memory_percent), None, None),
                ("磁盘", format_resource_usage(node.latest_disk_used_bytes, node.latest_disk_total_bytes, node.latest_disk_percent), None, None),
            ],
        ),
        "",
        *render_metric_block(
            "网络流量",
            [
                ("下行", format_rate_value(detail_summary.current_rate.rx_bps), "上行", format_rate_value(detail_summary.current_rate.tx_bps)),
                ("累计↓", format_byte_value(node.latest_rx_bytes), "累计↑", format_byte_value(node.latest_tx_bytes)),
                ("收包", format_integer_value(node.latest_rx_packets), "发包", format_integer_value(node.latest_tx_packets)),
                ("RX错/丢", format_network_counter_pair(node.latest_rx_errors, node.latest_rx_dropped), "TX错/丢", format_network_counter_pair(node.latest_tx_errors, node.latest_tx_dropped)),
            ],
        ),
        "",
        *render_metric_block(
            "近 1 小时趋势",
            [
                ("CPU", format_avg_peak(detail_summary.trend_1h.avg_cpu_percent, detail_summary.trend_1h.peak_cpu_percent), None, None),
                ("内存", format_avg_peak(detail_summary.trend_1h.avg_memory_percent, detail_summary.trend_1h.peak_memory_percent), None, None),
                ("磁盘", format_avg_peak(detail_summary.trend_1h.avg_disk_percent, detail_summary.trend_1h.peak_disk_percent), None, None),
                ("1h↓", format_byte_value(detail_summary.trend_1h.rx_bytes), "1h↑", format_byte_value(detail_summary.trend_1h.tx_bytes)),
                ("样本", str(detail_summary.trend_1h.sample_count), None, None),
            ],
        ),
        "",
        *render_metric_block(
            "近 24 小时 / 套餐",
            [
                ("24h↓", format_byte_value(detail_summary.traffic_24h.rx_bytes), "24h↑", format_byte_value(detail_summary.traffic_24h.tx_bytes)),
                ("24h合计", format_byte_value(detail_summary.traffic_24h.total_bytes), None, None),
                *quota_lines,
            ],
        ),
    ]
    if active_alerts:
        lines.extend(["", "── 当前告警"])
        for alert in active_alerts:
            severity_icon = "⛔" if alert.severity == "critical" else "⚠️"
            lines.append(f"{severity_icon} {alert.summary}")
    return "\n".join(lines), build_node_detail_keyboard(node.name)


async def render_node_delete_confirm(node_name: str) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
    if node is None:
        return "未找到对应节点。", build_single_action_keyboard(CALLBACK_SHOW_NODES)
    lines = [
        f"🗑️ 删除节点 | {node.name}",
        "",
        "此操作会删除该节点及其关联数据：",
        "• 历史指标快照",
        "• 告警记录",
        "• 流量套餐配置",
        "",
        "删除后，如果该机器上的 Agent 仍在运行，会因令牌失效而上报失败。",
        "",
        "确认删除吗？",
    ]
    return "\n".join(lines), build_node_delete_confirm_keyboard(node.name)


async def render_alerts_response() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        await mark_stale_nodes_offline(session)
        await session.commit()
        active_alerts = await list_active_alerts(session, limit=10)

    if not active_alerts:
        return "🚨 告警中心\n当前没有活动告警。", build_single_action_keyboard(CALLBACK_SHOW_ALERTS)

    lines = [
        "🚨 告警中心",
        "",
        *render_metric_block("总览", [("活动告警", str(len(active_alerts)), None, None)]),
    ]
    for alert, node in active_alerts:
        severity = "严重" if alert.severity == "critical" else "警告"
        severity_icon = "⛔" if alert.severity == "critical" else "⚠️"
        lines.append(
            f"━━━━━━━━━━\n"
            f"{severity_icon} {node.name}\n"
            f"{render_metric_pair('级别', severity, '状态', format_status_label(node))}\n"
            f"摘要 {alert.summary}"
        )
    return "\n".join(lines), build_single_action_keyboard(CALLBACK_SHOW_ALERTS)


async def render_traffic_response() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        summary = await summarize_recent_24h(session)
    return format_traffic_summary(summary), build_single_action_keyboard(CALLBACK_SHOW_TRAFFIC)


async def render_daily_response() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        _, summary = await summarize_previous_local_day(session)
    return format_traffic_summary(summary), build_single_action_keyboard(CALLBACK_SHOW_DAILY)


async def render_quota_response(node_name: str) -> str:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        if node is None:
            raise QuotaServiceError("未找到对应节点。")
        status = await get_quota_status(session, node)
    return "\n".join([f"🧾 套餐状态 | {node_name}", "", *render_metric_block("套餐", format_quota_compact_lines(status))])


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    await send_dashboard(message)


@router.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    await start_handler(message)


@router.message(Command("enroll"))
async def enroll_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/enroll <节点名>")
        return

    async with SessionLocal() as session:
        try:
            node = await create_or_refresh_enrollment(session, node_name)
        except NodeServiceError as exc:
            await message.answer(str(exc))
            return

    agent_command = (
        f"PROXYPULSE_SERVER_URL={settings.server_url} "
        f"PROXYPULSE_AGENT_NAME={node.name} "
        f"PROXYPULSE_AGENT_ENROLLMENT_TOKEN={node.enrollment_token} "
        "python -m proxypulse.agent"
    )
    await message.answer(
        f"🔐 接入令牌\n"
        f"节点：{node.name}\n\n"
        f"令牌：\n"
        f"`{node.enrollment_token}`\n\n"
        "🚀 Agent 启动示例：\n"
        f"`{agent_command}`",
        parse_mode="Markdown",
    )


@router.message(Command("nodes"))
async def nodes_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_nodes_response()
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("node"))
async def node_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/node <节点名>")
        return

    text, keyboard = await render_node_detail(node_name)
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("delete_node"))
async def delete_node_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/delete_node <节点名>")
        return
    text, keyboard = await render_node_delete_confirm(node_name)
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("alerts"))
async def alerts_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_alerts_response()
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("traffic"))
async def traffic_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_traffic_response()
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("daily"))
async def daily_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_daily_response()
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("quota"))
async def quota_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer(render_panel("\n".join(render_quota_help_lines())), parse_mode="HTML")
        return
    try:
        await message.answer(render_panel(await render_quota_response(node_name)), parse_mode="HTML")
    except QuotaServiceError as exc:
        await message.answer(str(exc))


@router.message(Command("quota_monthly"))
async def quota_monthly_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    args = (command.args or "").split()
    if len(args) != 4:
        await message.answer("用法：/quota_monthly <节点名> <上限GiB> <每月重置日> <HH:MM>")
        return
    node_name, limit_text, reset_day_text, clock_text = args
    try:
        limit_gib = parse_limit_gib(limit_text)
        reset_day = int(reset_day_text)
        hour, minute = _parse_clock(clock_text)
        async with SessionLocal() as session:
            node = await get_node_by_name(session, node_name)
            if node is None:
                raise QuotaServiceError("未找到对应节点。")
            await configure_monthly_quota(
                session,
                node,
                limit_gib=limit_gib,
                reset_day=reset_day,
                hour=hour,
                minute=minute,
            )
            status = await get_quota_status(session, node)
    except (ValueError, QuotaServiceError) as exc:
        await message.answer(str(exc))
        return
    await message.answer("\n".join([f"✅ 已设置每月流量套餐：{node_name}", *format_quota_status(status)]))


@router.message(Command("quota_interval"))
async def quota_interval_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    args = (command.args or "").split()
    if len(args) != 4:
        await message.answer("用法：/quota_interval <节点名> <上限GiB> <间隔天数> <YYYY-MM-DDTHH:MM>")
        return
    node_name, limit_text, interval_days_text, anchor_text = args
    try:
        limit_gib = parse_limit_gib(limit_text)
        interval_days = int(interval_days_text)
        anchor_at = _parse_local_datetime(anchor_text)
        async with SessionLocal() as session:
            node = await get_node_by_name(session, node_name)
            if node is None:
                raise QuotaServiceError("未找到对应节点。")
            await configure_interval_quota(
                session,
                node,
                limit_gib=limit_gib,
                interval_days=interval_days,
                anchor_at=anchor_at,
            )
            status = await get_quota_status(session, node)
    except (ValueError, QuotaServiceError) as exc:
        await message.answer(str(exc))
        return
    await message.answer("\n".join([f"✅ 已设置按天循环套餐：{node_name}", *format_quota_status(status)]))


@router.message(Command("quota_calibrate"))
async def quota_calibrate_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    args = (command.args or "").split()
    if len(args) != 2:
        await message.answer("用法：/quota_calibrate <节点名> <已用GiB>")
        return
    node_name, used_text = args
    try:
        used_gib = parse_used_gib(used_text)
        async with SessionLocal() as session:
            node = await get_node_by_name(session, node_name)
            if node is None:
                raise QuotaServiceError("未找到对应节点。")
            await calibrate_quota_usage(session, node, used_gib=used_gib)
            status = await get_quota_status(session, node)
    except QuotaServiceError as exc:
        await message.answer(str(exc))
        return
    await message.answer("\n".join([f"✅ 已校准已用流量：{node_name}", *format_quota_status(status)]))


@router.message(Command("quota_clear"))
async def quota_clear_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/quota_clear <节点名>")
        return
    try:
        async with SessionLocal() as session:
            node = await get_node_by_name(session, node_name)
            if node is None:
                raise QuotaServiceError("未找到对应节点。")
            await clear_quota(session, node)
    except QuotaServiceError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"✅ 已清除流量套餐配置：{node_name}")

@router.callback_query(F.data == CALLBACK_SHOW_MENU)
async def menu_callback_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    if callback.message is not None:
        await callback.message.answer(
            render_panel("快捷入口"),
            parse_mode="HTML",
            reply_markup=build_dashboard_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == CALLBACK_SHOW_NODES)
async def show_nodes_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    text, keyboard = await render_nodes_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_ALERTS)
async def show_alerts_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    text, keyboard = await render_alerts_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_TRAFFIC)
async def show_traffic_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    text, keyboard = await render_traffic_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_DAILY)
async def show_daily_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    text, keyboard = await render_daily_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_QUOTA_HELP)
async def show_quota_help_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    await safe_edit_callback_message(
        callback,
        "\n".join(render_quota_help_lines()),
        build_single_action_keyboard(CALLBACK_SHOW_QUOTA_HELP),
    )


@router.callback_query(F.data.startswith(CALLBACK_NODE_PREFIX))
async def show_node_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_PREFIX) :]
    text, keyboard = await render_node_detail(node_name)
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_NODE_DELETE_PREFIX))
async def show_node_delete_confirm_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_DELETE_PREFIX) :]
    text, keyboard = await render_node_delete_confirm(node_name)
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_NODE_DELETE_CONFIRM_PREFIX))
async def delete_node_confirm_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_DELETE_CONFIRM_PREFIX) :]
    try:
        async with SessionLocal() as session:
            node = await delete_node_by_name(session, node_name)
    except NodeServiceError as exc:
        await safe_edit_callback_message(callback, str(exc), build_single_action_keyboard(CALLBACK_SHOW_NODES))
        return
    text, keyboard = await render_nodes_response()
    if callback.message is not None:
        await callback.message.answer(f"✅ 已删除节点：{node.name}")
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_NODE_DELETE_CANCEL_PREFIX))
async def cancel_node_delete_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_DELETE_CANCEL_PREFIX) :]
    text, keyboard = await render_node_detail(node_name)
    await safe_edit_callback_message(callback, text, keyboard)


async def maybe_send_daily_report(bot: Bot) -> None:
    local_now = datetime.now(ZoneInfo(settings.report_timezone))
    if not should_send_daily_report(local_now):
        return

    async with SessionLocal() as session:
        report_day, summary = await summarize_previous_local_day(session, today_local=local_now.date())
        if await has_daily_report_run(session, report_day):
            return

        text = format_traffic_summary(summary)
        for admin_id in settings.admin_telegram_ids:
            await bot.send_message(admin_id, text)
        mark_daily_report_run(session, report_day)
        await session.commit()


async def alert_loop(bot: Bot) -> None:
    while True:
        try:
            async with SessionLocal() as session:
                await mark_stale_nodes_offline(session)
                await session.flush()
                pending = await list_pending_notifications(session, limit=20)
                for alert, node in pending:
                    text = format_alert_message(alert, node)
                    for admin_id in settings.admin_telegram_ids:
                        await bot.send_message(admin_id, text)
                    mark_notified(alert)
                await session.commit()
            await maybe_send_daily_report(bot)
        except Exception:
            logger.exception("Alert loop iteration failed.")

        await asyncio.sleep(settings.alert_scan_interval_seconds)


async def run_polling() -> None:
    if not settings.bot_token:
        raise RuntimeError("PROXYPULSE_BOT_TOKEN is required to run the bot.")
    if not settings.admin_telegram_ids:
        raise RuntimeError("PROXYPULSE_ADMIN_TELEGRAM_IDS must include at least one Telegram user id.")

    await init_db()

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="打开控制台首页"),
            BotCommand(command="menu", description="打开控制台菜单"),
            BotCommand(command="enroll", description="生成节点接入令牌"),
            BotCommand(command="nodes", description="查看节点列表"),
            BotCommand(command="node", description="查看节点详情"),
            BotCommand(command="delete_node", description="删除节点并清理数据"),
            BotCommand(command="alerts", description="查看活动告警"),
            BotCommand(command="traffic", description="查看近24小时流量"),
            BotCommand(command="daily", description="查看前一日流量日报"),
            BotCommand(command="quota", description="查看节点流量套餐"),
            BotCommand(command="quota_monthly", description="设置按月流量套餐"),
            BotCommand(command="quota_interval", description="设置按天循环套餐"),
            BotCommand(command="quota_calibrate", description="校准节点已用流量"),
            BotCommand(command="quota_clear", description="清除节点流量套餐"),
        ]
    )
    alert_task = asyncio.create_task(alert_loop(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        alert_task.cancel()
        await asyncio.gather(alert_task, return_exceptions=True)


def main() -> None:
    asyncio.run(run_polling())
