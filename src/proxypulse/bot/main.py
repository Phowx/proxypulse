from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
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
from proxypulse.services.cloudflare_dns import (
    SUPPORTED_DNS_RECORD_TYPES,
    CloudflareDNSRecord,
    CloudflareDNSRecordPage,
    CloudflareDNSService,
    CloudflareServiceError,
)
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
MENU_WEBAPP = "Web 面板"
MENU_DNS = "DNS 管理"
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
CALLBACK_DNS_HOME = "dns:home"
CALLBACK_DNS_ZONE_PREFIX = "dns:zone:"
CALLBACK_DNS_LIST_PREFIX = "dns:list:"
CALLBACK_DNS_RECORD_PREFIX = "dns:record:"
CALLBACK_DNS_CREATE_PREFIX = "dns:create:"
CALLBACK_DNS_TYPE_PREFIX = "dns:type:"
CALLBACK_DNS_UPDATE_PREFIX = "dns:update:"
CALLBACK_DNS_DELETE_PREFIX = "dns:delete:"
CALLBACK_DNS_KEEP_PREFIX = "dns:keep:"
CALLBACK_DNS_TTL_PREFIX = "dns:ttl:"
CALLBACK_DNS_PROXIED_PREFIX = "dns:proxied:"
CALLBACK_DNS_CONFIRM_PREFIX = "dns:confirm:"
CALLBACK_DNS_CANCEL = "dns:cancel"
STATUS_STYLE = {
    "online": ("🟢", "在线"),
    "pending": ("🟡", "待接入"),
    "offline": ("🔴", "离线"),
}
DNS_TTL_OPTIONS = [
    (1, "Auto"),
    (60, "60s"),
    (300, "5m"),
    (600, "10m"),
    (3600, "1h"),
]
DNS_PAGE_SIZE = 10


@dataclass(slots=True)
class DnsDraft:
    mode: Literal["create", "update"]
    zone_key: str
    record_type: str
    record_id: str | None = None
    name: str = ""
    content: str = ""
    ttl: int = 1
    proxied: bool | None = None
    pending_field: Literal["name", "content"] | None = None
    original_record: CloudflareDNSRecord | None = None


@dataclass(slots=True)
class DnsPendingAction:
    action: Literal["create", "update", "delete"]
    zone_key: str
    record_id: str | None = None
    draft: DnsDraft | None = None


@dataclass(slots=True)
class DnsSession:
    zone_key: str | None = None
    page: int = 1
    selected_record_id: str | None = None
    draft: DnsDraft | None = None
    pending_action: DnsPendingAction | None = None


DNS_SESSIONS: dict[int, DnsSession] = {}


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_telegram_ids)


async def reject_if_not_admin(message: Message) -> bool:
    if is_admin(message):
        return False
    await message.answer("无权访问。")
    return True


def get_dns_session(user_id: int) -> DnsSession:
    return DNS_SESSIONS.setdefault(user_id, DnsSession())


def reset_dns_session(user_id: int) -> DnsSession:
    DNS_SESSIONS[user_id] = DnsSession()
    return DNS_SESSIONS[user_id]


def get_dns_service() -> CloudflareDNSService:
    return CloudflareDNSService.from_settings(settings)


def format_dns_ttl(ttl: int) -> str:
    if ttl == 1:
        return "Auto"
    return f"{ttl}s"


def format_dns_proxied(value: bool | None) -> str:
    if value is None:
        return "不适用"
    return "已代理" if value else "仅 DNS"


def summarize_dns_content(content: str, limit: int = 26) -> str:
    if len(content) <= limit:
        return content
    return f"{content[: limit - 1]}…"


def parse_dns_list_callback(data: str) -> tuple[str, int]:
    _, _, zone_key, page_text = data.split(":", 3)
    return zone_key, max(int(page_text), 1)


def parse_dns_record_callback(data: str) -> tuple[str, str]:
    _, _, zone_key, record_id = data.split(":", 3)
    return zone_key, record_id


def build_dns_home_keyboard(service: CloudflareDNSService) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{zone.zone_name} · {zone.key}", callback_data=f"{CALLBACK_DNS_ZONE_PREFIX}{zone.key}")]
        for zone in service.list_configured_zones()
    ]
    rows.append([InlineKeyboardButton(text="返回菜单", callback_data=CALLBACK_SHOW_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_zone_keyboard(zone_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="查看记录", callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{zone_key}:1")],
            [InlineKeyboardButton(text="新增记录", callback_data=f"{CALLBACK_DNS_CREATE_PREFIX}{zone_key}")],
            [InlineKeyboardButton(text="切换 Zone", callback_data=CALLBACK_DNS_HOME)],
            [InlineKeyboardButton(text="返回菜单", callback_data=CALLBACK_SHOW_MENU)],
        ]
    )


def build_dns_record_list_keyboard(record_page: CloudflareDNSRecordPage) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for record in record_page.records:
        proxied_suffix = ""
        if record.proxied is not None:
            proxied_suffix = " · 代理" if record.proxied else " · DNS"
        label = f"{record.name} · {record.type} · {summarize_dns_content(record.content, 18)}{proxied_suffix}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{CALLBACK_DNS_RECORD_PREFIX}{record_page.zone.key}:{record.id}",
                )
            ]
        )
    pager_row: list[InlineKeyboardButton] = []
    if record_page.page > 1:
        pager_row.append(
            InlineKeyboardButton(
                text="上一页",
                callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{record_page.zone.key}:{record_page.page - 1}",
            )
        )
    if record_page.page < record_page.total_pages:
        pager_row.append(
            InlineKeyboardButton(
                text="下一页",
                callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{record_page.zone.key}:{record_page.page + 1}",
            )
        )
    if pager_row:
        rows.append(pager_row)
    rows.append([InlineKeyboardButton(text="刷新列表", callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{record_page.zone.key}:{record_page.page}")])
    rows.append([InlineKeyboardButton(text="新增记录", callback_data=f"{CALLBACK_DNS_CREATE_PREFIX}{record_page.zone.key}")])
    rows.append([InlineKeyboardButton(text="返回 Zone", callback_data=f"{CALLBACK_DNS_ZONE_PREFIX}{record_page.zone.key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_record_detail_keyboard(zone_key: str, record_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="更新记录", callback_data=f"{CALLBACK_DNS_UPDATE_PREFIX}{zone_key}:{record_id}")],
            [InlineKeyboardButton(text="删除记录", callback_data=f"{CALLBACK_DNS_DELETE_PREFIX}{zone_key}:{record_id}")],
            [InlineKeyboardButton(text="返回列表", callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{zone_key}:1")],
            [InlineKeyboardButton(text="切换 Zone", callback_data=f"{CALLBACK_DNS_ZONE_PREFIX}{zone_key}")],
        ]
    )


def build_dns_type_keyboard(zone_key: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=record_type, callback_data=f"{CALLBACK_DNS_TYPE_PREFIX}{zone_key}:{record_type}")] for record_type in SUPPORTED_DNS_RECORD_TYPES]
    rows.append([InlineKeyboardButton(text="取消", callback_data=CALLBACK_DNS_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_prompt_keyboard(*, can_keep: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_keep:
        rows.append(
            [
                InlineKeyboardButton(text="保留原值", callback_data=f"{CALLBACK_DNS_KEEP_PREFIX}current"),
            ]
        )
    rows.append([InlineKeyboardButton(text="取消", callback_data=CALLBACK_DNS_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_ttl_keyboard(*, current_ttl: int | None, allow_keep: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{label}{' ✓' if current_ttl == ttl else ''}",
                callback_data=f"{CALLBACK_DNS_TTL_PREFIX}{ttl}",
            )
        ]
        for ttl, label in DNS_TTL_OPTIONS
    ]
    if allow_keep:
        rows.append([InlineKeyboardButton(text="保留原值", callback_data=f"{CALLBACK_DNS_KEEP_PREFIX}ttl")])
    rows.append([InlineKeyboardButton(text="取消", callback_data=CALLBACK_DNS_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_proxied_keyboard(*, current_value: bool | None, allow_keep: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"已代理{' ✓' if current_value is True else ''}",
                callback_data=f"{CALLBACK_DNS_PROXIED_PREFIX}1",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"仅 DNS{' ✓' if current_value is False else ''}",
                callback_data=f"{CALLBACK_DNS_PROXIED_PREFIX}0",
            )
        ],
    ]
    if allow_keep:
        rows.append([InlineKeyboardButton(text="保留原值", callback_data=f"{CALLBACK_DNS_KEEP_PREFIX}proxied")])
    rows.append([InlineKeyboardButton(text="取消", callback_data=CALLBACK_DNS_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_confirm_keyboard(action: Literal["create", "update", "delete"]) -> InlineKeyboardMarkup:
    action_label = {
        "create": "确认新增",
        "update": "确认更新",
        "delete": "确认删除",
    }[action]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=action_label, callback_data=f"{CALLBACK_DNS_CONFIRM_PREFIX}{action}")],
            [InlineKeyboardButton(text="取消", callback_data=CALLBACK_DNS_CANCEL)],
        ]
    )


def render_dns_record_text(record: CloudflareDNSRecord, zone_name: str) -> str:
    lines = [
        f"☁️ DNS 记录 | {record.name}",
        "",
        f"Zone {zone_name}",
        f"类型 {record.type}",
        f"值 {record.content}",
        f"TTL {format_dns_ttl(record.ttl)}",
        f"代理 {format_dns_proxied(record.proxied)}",
    ]
    if record.comment:
        lines.append(f"备注 {record.comment}")
    return "\n".join(lines)


def render_dns_draft_preview(draft: DnsDraft, zone_name: str) -> str:
    action_label = "新增记录" if draft.mode == "create" else "更新记录"
    lines = [
        f"☁️ DNS 预览 | {action_label}",
        "",
        f"Zone {zone_name}",
        f"类型 {draft.record_type}",
        f"名称 {draft.name}",
        f"值 {draft.content}",
        f"TTL {format_dns_ttl(draft.ttl)}",
    ]
    if draft.record_type in {"A", "AAAA", "CNAME"}:
        lines.append(f"代理 {format_dns_proxied(draft.proxied)}")
    if draft.mode == "update" and draft.original_record is not None:
        lines.extend(
            [
                "",
                "当前值",
                f"• 名称 {draft.original_record.name}",
                f"• 值 {draft.original_record.content}",
                f"• TTL {format_dns_ttl(draft.original_record.ttl)}",
                f"• 代理 {format_dns_proxied(draft.original_record.proxied)}",
            ]
        )
    return "\n".join(lines)


def render_dns_list_text(record_page: CloudflareDNSRecordPage) -> str:
    lines = [
        f"☁️ DNS 列表 | {record_page.zone.zone_name}",
        "",
        f"页码 {record_page.page}/{record_page.total_pages}",
        f"记录 {record_page.total_count} 条",
        "",
        "点击下方记录进入详情。",
    ]
    if not record_page.records:
        lines.extend(["", "当前 Zone 还没有受支持的记录类型。"])
    return "\n".join(lines)


def render_dns_zone_text(zone_name: str, zone_key: str) -> str:
    return "\n".join(
        [
            f"☁️ DNS 管理 | {zone_name}",
            "",
            f"标识 {zone_key}",
            "选择要执行的操作。",
        ]
    )


def render_dns_home_text(service: CloudflareDNSService) -> str:
    lines = [
        "☁️ DNS 管理",
        "",
        f"已配置 Zone {len(service.list_configured_zones())} 个",
        "先选择一个 Zone，再查看记录或新增记录。",
    ]
    return "\n".join(lines)


def render_dns_prompt_text(*, title: str, field_label: str, hint: str, current_value: str | None = None) -> str:
    lines = [f"☁️ DNS 流程 | {title}", "", f"请发送 {field_label}。", hint]
    if current_value:
        lines.extend(["", f"当前值 {current_value}"])
    return "\n".join(lines)


def render_dns_delete_preview(record: CloudflareDNSRecord, zone_name: str) -> str:
    return "\n".join(
        [
            f"☁️ DNS 预览 | 删除记录",
            "",
            f"Zone {zone_name}",
            f"名称 {record.name}",
            f"类型 {record.type}",
            f"值 {record.content}",
            f"TTL {format_dns_ttl(record.ttl)}",
            f"代理 {format_dns_proxied(record.proxied)}",
            "",
            "确认删除吗？",
        ]
    )


def dashboard_button_rows(include_webapp: bool) -> list[list[str]]:
    rows = [
        [MENU_NODES, MENU_DAILY, MENU_DNS],
    ]
    if include_webapp:
        rows.append([MENU_WEBAPP])
    return rows


def build_dashboard_keyboard() -> ReplyKeyboardMarkup:
    webapp_url = resolve_webapp_url()
    rows = []
    for row in dashboard_button_rows(is_supported_webapp_url(webapp_url)):
        buttons = []
        for label in row:
            buttons.append(KeyboardButton(text=label))
        rows.append(buttons)
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="选择入口或输入命令",
    )


def build_webapp_entry_keyboard() -> InlineKeyboardMarkup | None:
    webapp_url = resolve_webapp_url()
    if not is_supported_webapp_url(webapp_url):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="打开 Web 面板", web_app=WebAppInfo(url=webapp_url))],
        ]
    )


def resolve_webapp_url() -> str:
    if settings.webapp_url:
        return settings.webapp_url.rstrip("/")
    return f"{settings.server_url.rstrip('/')}/app"


def is_supported_webapp_url(url: str) -> bool:
    return url.lower().startswith("https://")


def build_dashboard_menu_text() -> str:
    return "ProxyPulse 控制台已就绪。"


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
    await message.answer(build_dashboard_menu_text(), reply_markup=build_dashboard_keyboard())


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


async def safe_clear_callback_markup(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer("已返回底部菜单。")


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


async def render_dns_home() -> tuple[str, InlineKeyboardMarkup | None]:
    try:
        service = get_dns_service()
        return render_dns_home_text(service), build_dns_home_keyboard(service)
    except (CloudflareServiceError, ValueError) as exc:
        return f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_SHOW_MENU)


async def render_dns_zone(zone_key: str) -> tuple[str, InlineKeyboardMarkup]:
    service = get_dns_service()
    zone = service.get_zone(zone_key)
    return render_dns_zone_text(zone.zone_name, zone.key), build_dns_zone_keyboard(zone.key)


async def render_dns_record_list(zone_key: str, *, page: int) -> tuple[str, InlineKeyboardMarkup]:
    service = get_dns_service()
    record_page = await service.list_dns_records(zone_key, page=page, per_page=DNS_PAGE_SIZE)
    return render_dns_list_text(record_page), build_dns_record_list_keyboard(record_page)


async def render_dns_record_detail(zone_key: str, record_id: str) -> tuple[str, InlineKeyboardMarkup]:
    service = get_dns_service()
    zone = service.get_zone(zone_key)
    record = await service.get_dns_record(zone_key, record_id)
    return render_dns_record_text(record, zone.zone_name), build_dns_record_detail_keyboard(zone_key, record_id)


async def prompt_dns_name(message_or_callback: Message | CallbackQuery, draft: DnsDraft) -> None:
    draft.pending_field = "name"
    current_value = draft.original_record.name if draft.original_record else None
    text = render_dns_prompt_text(
        title="填写名称",
        field_label="记录名称",
        hint="例如：api 或 api.example.com",
        current_value=current_value,
    )
    keyboard = build_dns_prompt_keyboard(can_keep=draft.mode == "update")
    if isinstance(message_or_callback, CallbackQuery):
        await safe_edit_callback_message(message_or_callback, text, keyboard)
    else:
        await message_or_callback.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


async def prompt_dns_content(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    draft.pending_field = "content"
    text = render_dns_prompt_text(
        title="填写记录值",
        field_label="记录值",
        hint="A/AAAA 填 IP，CNAME 填目标域名，TXT 填文本内容。",
        current_value=draft.original_record.content if draft.original_record else None,
    )
    keyboard = build_dns_prompt_keyboard(can_keep=draft.mode == "update")
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


async def prompt_dns_ttl(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    text = "\n".join(
        [
            "☁️ DNS 流程 | 选择 TTL",
            "",
            f"当前选择 {format_dns_ttl(draft.ttl)}",
        ]
    )
    keyboard = build_dns_ttl_keyboard(current_ttl=draft.ttl, allow_keep=draft.mode == "update")
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


async def prompt_dns_proxied(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    if draft.record_type not in {"A", "AAAA", "CNAME"}:
        await present_dns_preview(target, draft)
        return
    text = "\n".join(
        [
            "☁️ DNS 流程 | 选择代理模式",
            "",
            f"当前选择 {format_dns_proxied(draft.proxied)}",
        ]
    )
    keyboard = build_dns_proxied_keyboard(current_value=draft.proxied, allow_keep=draft.mode == "update")
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


async def present_dns_preview(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    service = get_dns_service()
    zone = service.get_zone(draft.zone_key)
    text = render_dns_draft_preview(draft, zone.zone_name)
    keyboard = build_dns_confirm_keyboard(draft.mode)
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


def require_dns_session(user_id: int) -> DnsSession:
    session = DNS_SESSIONS.get(user_id)
    if session is None:
        raise CloudflareServiceError("DNS 流程已过期，请重新进入 /dns。")
    return session


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    await send_dashboard(message)


@router.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    await start_handler(message)


@router.message(F.text == MENU_NODES)
async def menu_nodes_handler(message: Message) -> None:
    await nodes_handler(message)


@router.message(F.text == MENU_ALERTS)
async def menu_alerts_handler(message: Message) -> None:
    await alerts_handler(message)


@router.message(F.text == MENU_TRAFFIC)
async def menu_traffic_handler(message: Message) -> None:
    await traffic_handler(message)


@router.message(F.text == MENU_DAILY)
async def menu_daily_handler(message: Message) -> None:
    await daily_handler(message)


@router.message(F.text == MENU_QUOTA)
async def menu_quota_help_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    await message.answer("\n".join(render_quota_help_lines()))


@router.message(F.text == MENU_DNS)
async def menu_dns_handler(message: Message) -> None:
    await dns_handler(message)


@router.message(F.text == MENU_WEBAPP)
async def menu_webapp_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    keyboard = build_webapp_entry_keyboard()
    if keyboard is None:
        await message.answer("Web 面板需要可访问的 HTTPS 地址。")
        return
    await message.answer("点击下方按钮打开 Web 面板。", reply_markup=keyboard)


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


@router.message(Command("dns"))
async def dns_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    if message.from_user is None:
        await message.answer("无法识别当前用户。")
        return
    reset_dns_session(message.from_user.id)
    text, keyboard = await render_dns_home()
    await message.answer(render_panel(text), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("dns_zones"))
async def dns_zones_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    try:
        service = get_dns_service()
        zones = service.list_configured_zones()
    except (CloudflareServiceError, ValueError) as exc:
        await message.answer(str(exc))
        return
    lines = ["☁️ DNS Zones", ""]
    for zone in zones:
        lines.append(f"• {zone.key} -> {zone.zone_name}")
    await message.answer(render_panel("\n".join(lines)), parse_mode="HTML")


@router.message(F.text)
async def dns_text_input_handler(message: Message) -> None:
    if not is_admin(message) or message.from_user is None:
        return
    session = DNS_SESSIONS.get(message.from_user.id)
    if session is None or session.draft is None or session.draft.pending_field is None:
        return
    draft = session.draft
    value = (message.text or "").strip()
    if not value:
        await message.answer("输入不能为空，请重新发送。")
        return
    if draft.pending_field == "name":
        draft.name = value
        draft.pending_field = None
        await prompt_dns_content(message, draft)
        return
    if draft.pending_field == "content":
        draft.content = value
        draft.pending_field = None
        await prompt_dns_ttl(message, draft)

@router.callback_query(F.data == CALLBACK_SHOW_MENU)
async def menu_callback_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    await safe_clear_callback_markup(callback)


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


@router.callback_query(F.data == CALLBACK_DNS_HOME)
async def dns_home_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    reset_dns_session(callback.from_user.id)
    text, keyboard = await render_dns_home()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_DNS_ZONE_PREFIX))
async def dns_zone_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    zone_key = callback.data[len(CALLBACK_DNS_ZONE_PREFIX) :]
    session = get_dns_session(callback.from_user.id)
    session.zone_key = zone_key
    session.page = 1
    session.selected_record_id = None
    session.draft = None
    session.pending_action = None
    try:
        text, keyboard = await render_dns_zone(zone_key)
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))
        return
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_DNS_LIST_PREFIX))
async def dns_list_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        zone_key, page = parse_dns_list_callback(callback.data)
        session = get_dns_session(callback.from_user.id)
        session.zone_key = zone_key
        session.page = page
        session.draft = None
        session.pending_action = None
        text, keyboard = await render_dns_record_list(zone_key, page=page)
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))
        return
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_DNS_RECORD_PREFIX))
async def dns_record_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        zone_key, record_id = parse_dns_record_callback(callback.data)
        session = get_dns_session(callback.from_user.id)
        session.zone_key = zone_key
        session.selected_record_id = record_id
        session.draft = None
        session.pending_action = None
        text, keyboard = await render_dns_record_detail(zone_key, record_id)
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))
        return
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_DNS_CREATE_PREFIX))
async def dns_create_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    zone_key = callback.data[len(CALLBACK_DNS_CREATE_PREFIX) :]
    session = get_dns_session(callback.from_user.id)
    session.zone_key = zone_key
    session.draft = None
    session.pending_action = None
    try:
        service = get_dns_service()
        zone = service.get_zone(zone_key)
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))
        return
    text = "\n".join([f"☁️ DNS 新增 | {zone.zone_name}", "", "先选择记录类型。"])
    await safe_edit_callback_message(callback, text, build_dns_type_keyboard(zone_key))


@router.callback_query(F.data.startswith(CALLBACK_DNS_TYPE_PREFIX))
async def dns_type_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    _, _, zone_key, record_type = callback.data.split(":", 3)
    session = get_dns_session(callback.from_user.id)
    session.zone_key = zone_key
    session.draft = DnsDraft(
        mode="create",
        zone_key=zone_key,
        record_type=record_type,
        proxied=True if record_type in {"A", "AAAA", "CNAME"} else None,
    )
    session.pending_action = None
    await prompt_dns_name(callback, session.draft)


@router.callback_query(F.data.startswith(CALLBACK_DNS_UPDATE_PREFIX))
async def dns_update_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        zone_key, record_id = parse_dns_record_callback(callback.data.replace(CALLBACK_DNS_UPDATE_PREFIX, CALLBACK_DNS_RECORD_PREFIX, 1))
        service = get_dns_service()
        record = await service.get_dns_record(zone_key, record_id)
        session = get_dns_session(callback.from_user.id)
        session.zone_key = zone_key
        session.selected_record_id = record_id
        session.draft = DnsDraft(
            mode="update",
            zone_key=zone_key,
            record_id=record_id,
            record_type=record.type,
            name=record.name,
            content=record.content,
            ttl=record.ttl,
            proxied=record.proxied,
            original_record=record,
        )
        session.pending_action = None
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))
        return
    await prompt_dns_name(callback, session.draft)


@router.callback_query(F.data.startswith(CALLBACK_DNS_DELETE_PREFIX))
async def dns_delete_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        zone_key, record_id = parse_dns_record_callback(callback.data.replace(CALLBACK_DNS_DELETE_PREFIX, CALLBACK_DNS_RECORD_PREFIX, 1))
        service = get_dns_service()
        zone = service.get_zone(zone_key)
        record = await service.get_dns_record(zone_key, record_id)
        session = get_dns_session(callback.from_user.id)
        session.zone_key = zone_key
        session.selected_record_id = record_id
        session.draft = None
        session.pending_action = DnsPendingAction(action="delete", zone_key=zone_key, record_id=record_id)
        await safe_edit_callback_message(callback, render_dns_delete_preview(record, zone.zone_name), build_dns_confirm_keyboard("delete"))
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))


@router.callback_query(F.data.startswith(CALLBACK_DNS_KEEP_PREFIX))
async def dns_keep_value_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        session = require_dns_session(callback.from_user.id)
        draft = session.draft
        if draft is None or draft.original_record is None:
            raise CloudflareServiceError("当前没有可保留的 DNS 草稿。")
        field_name = callback.data[len(CALLBACK_DNS_KEEP_PREFIX) :]
        if field_name == "current":
            if draft.pending_field == "name":
                draft.name = draft.original_record.name
                draft.pending_field = None
                await prompt_dns_content(callback, draft)
                return
            if draft.pending_field == "content":
                draft.content = draft.original_record.content
                draft.pending_field = None
                await prompt_dns_ttl(callback, draft)
                return
        if field_name == "ttl":
            draft.ttl = draft.original_record.ttl
            await prompt_dns_proxied(callback, draft)
            return
        if field_name == "proxied":
            draft.proxied = draft.original_record.proxied
            await present_dns_preview(callback, draft)
            return
        raise CloudflareServiceError("无法保留当前字段。")
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))


@router.callback_query(F.data.startswith(CALLBACK_DNS_TTL_PREFIX))
async def dns_ttl_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        session = require_dns_session(callback.from_user.id)
        if session.draft is None:
            raise CloudflareServiceError("当前没有可编辑的 DNS 草稿。")
        ttl = int(callback.data[len(CALLBACK_DNS_TTL_PREFIX) :])
        session.draft.ttl = ttl
        await prompt_dns_proxied(callback, session.draft)
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))


@router.callback_query(F.data.startswith(CALLBACK_DNS_PROXIED_PREFIX))
async def dns_proxied_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        session = require_dns_session(callback.from_user.id)
        if session.draft is None:
            raise CloudflareServiceError("当前没有可编辑的 DNS 草稿。")
        value = callback.data[len(CALLBACK_DNS_PROXIED_PREFIX) :]
        session.draft.proxied = value == "1"
        await present_dns_preview(callback, session.draft)
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))


@router.callback_query(F.data.startswith(CALLBACK_DNS_CONFIRM_PREFIX))
async def dns_confirm_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    action = callback.data[len(CALLBACK_DNS_CONFIRM_PREFIX) :]
    try:
        session = require_dns_session(callback.from_user.id)
        service = get_dns_service()
        if action == "delete":
            if session.pending_action is None or session.pending_action.action != "delete" or session.pending_action.record_id is None:
                raise CloudflareServiceError("当前没有待确认的删除操作。")
            await service.delete_dns_record(session.pending_action.zone_key, session.pending_action.record_id)
            text, keyboard = await render_dns_record_list(session.pending_action.zone_key, page=session.page or 1)
            if callback.message is not None:
                await callback.message.answer("✅ DNS 记录已删除。")
            session.pending_action = None
            session.selected_record_id = None
            session.draft = None
            await safe_edit_callback_message(callback, text, keyboard)
            return
        if session.draft is None:
            raise CloudflareServiceError("当前没有待确认的 DNS 草稿。")
        draft = session.draft
        if action == "create":
            created = await service.create_dns_record(
                draft.zone_key,
                record_type=draft.record_type,
                name=draft.name,
                content=draft.content,
                ttl=draft.ttl,
                proxied=draft.proxied,
            )
            session.selected_record_id = created.id
            session.draft = None
            if callback.message is not None:
                await callback.message.answer("✅ DNS 记录已新增。")
            text, keyboard = await render_dns_record_detail(draft.zone_key, created.id)
            await safe_edit_callback_message(callback, text, keyboard)
            return
        if action == "update":
            updated = await service.update_dns_record(
                draft.zone_key,
                record_id=draft.record_id or "",
                record_type=draft.record_type,
                name=draft.name,
                content=draft.content,
                ttl=draft.ttl,
                proxied=draft.proxied,
            )
            session.selected_record_id = updated.id
            session.draft = None
            if callback.message is not None:
                await callback.message.answer("✅ DNS 记录已更新。")
            text, keyboard = await render_dns_record_detail(draft.zone_key, updated.id)
            await safe_edit_callback_message(callback, text, keyboard)
            return
        raise CloudflareServiceError("未知的确认操作。")
    except (CloudflareServiceError, ValueError) as exc:
        await safe_edit_callback_message(callback, f"☁️ DNS 管理\n\n{exc}", build_single_action_keyboard(CALLBACK_DNS_HOME))


@router.callback_query(F.data == CALLBACK_DNS_CANCEL)
async def dns_cancel_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    try:
        session = require_dns_session(callback.from_user.id)
        if session.selected_record_id and session.zone_key:
            text, keyboard = await render_dns_record_detail(session.zone_key, session.selected_record_id)
        elif session.zone_key:
            text, keyboard = await render_dns_zone(session.zone_key)
        else:
            text, keyboard = await render_dns_home()
        session.draft = None
        session.pending_action = None
    except (CloudflareServiceError, ValueError) as exc:
        text = f"☁️ DNS 管理\n\n{exc}"
        keyboard = build_single_action_keyboard(CALLBACK_DNS_HOME)
    await safe_edit_callback_message(callback, text, keyboard)


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
            BotCommand(command="daily", description="查看前一日流量日报"),
            BotCommand(command="dns", description="打开 Cloudflare DNS 管理"),
            BotCommand(command="dns_zones", description="列出可管理 DNS Zone"),
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
