from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    Message,
    ReplyKeyboardMarkup,
)

from proxypulse.core.config import get_settings
from proxypulse.bot.collection_formatting import (
    format_collection_scope,
    format_scoped_value,
    format_scoped_values,
)
from proxypulse.core.db import SessionLocal, init_db
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
    mark_stale_nodes_offline,
)
from proxypulse.services.quota import (
    QuotaServiceError,
    calibrate_quota_usage,
    clear_quota,
    configure_interval_quota,
    configure_monthly_quota,
    days_until_reset,
    get_quota_status,
    parse_limit_gib,
    parse_used_gib,
)
from proxypulse.services.report_schedule import (
    ReportScheduleError,
    get_daily_report_schedule,
    parse_daily_report_clock,
    set_daily_report_schedule,
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
from proxypulse.services.traffic_diagnostics import (
    TrafficDiagnosisError,
    build_traffic_diagnosis,
    format_traffic_diagnosis,
)
from proxypulse.services.telegram_node_names import (
    TelegramNodeDisplayNameError,
    clear_telegram_node_display_name,
    get_telegram_node_display_name,
    get_telegram_node_display_names,
    set_telegram_node_display_name,
)

settings = get_settings()
router = Router()
logger = logging.getLogger(__name__)

NAV_NODES = "节点"
NAV_DNS = "DNS"
NAV_TRAFFIC = "流量"
NAV_DAILY = "日报"
NAV_SETTINGS = "设置"
CALLBACK_SHOW_NODES = "show:nodes"
CALLBACK_SHOW_TRAFFIC = "show:traffic"
CALLBACK_SHOW_DAILY = "show:daily"
CALLBACK_SHOW_SETTINGS = "show:settings"
CALLBACK_SHOW_MENU = "show:menu"
CALLBACK_START_ENROLL = "enroll:start"
CALLBACK_INPUT_CANCEL = "input:cancel"
CALLBACK_DAILY_SCHEDULE = "settings:daily"
CALLBACK_DAILY_TIME_EDIT = "settings:daily:edit"
CALLBACK_QUOTA_HELP = "settings:quota"
CALLBACK_COMMAND_HELP = "settings:help"
CALLBACK_NODE_PREFIX = "node:"
CALLBACK_NODE_DIAG_PREFIX = "node_diag:"
CALLBACK_NODE_QUOTA_PREFIX = "node_quota:"
CALLBACK_NODE_RENAME_PREFIX = "node_rename:"
CALLBACK_NODE_RENAME_CLEAR_PREFIX = "node_rename_clear:"
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
BOT_COMMANDS = [
    BotCommand(command="start", description="打开控制台首页"),
    BotCommand(command="help", description="查看功能与命令说明"),
    BotCommand(command="cancel", description="取消当前操作"),
    BotCommand(command="enroll", description="生成节点接入令牌"),
]
BOT_COMMAND_LANGUAGE_CODES = ("zh", "en")


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
class BotSession:
    zone_key: str | None = None
    page: int = 1
    selected_record_id: str | None = None
    draft: DnsDraft | None = None
    pending_action: DnsPendingAction | None = None
    pending_input: Literal["enroll_name", "daily_time", "node_display_name"] | None = None
    pending_node_name: str | None = None


BOT_SESSIONS: dict[int, BotSession] = {}


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_telegram_ids)


async def reject_if_not_admin(message: Message) -> bool:
    if is_admin(message):
        command_name = (message.text or "").split(maxsplit=1)[0].split("@", 1)[0]
        if (
            message.from_user is not None
            and command_name.startswith("/")
            and command_name != "/cancel"
            and cancel_active_flow(message.from_user.id)
        ):
            await message.answer("ℹ️ 已取消未完成的操作。")
        return False
    await message.answer("无权访问。")
    return True


def get_bot_session(user_id: int) -> BotSession:
    return BOT_SESSIONS.setdefault(user_id, BotSession())


def get_dns_session(user_id: int) -> BotSession:
    session = get_bot_session(user_id)
    session.pending_input = None
    return session


def reset_dns_session(user_id: int) -> BotSession:
    session = get_bot_session(user_id)
    session.zone_key = None
    session.page = 1
    session.selected_record_id = None
    session.draft = None
    session.pending_action = None
    session.pending_input = None
    session.pending_node_name = None
    return session


def cancel_active_flow(user_id: int) -> bool:
    session = BOT_SESSIONS.get(user_id)
    if session is None:
        return False
    had_active_flow = bool(
        session.pending_input
        or session.pending_node_name
        or session.draft
        or session.pending_action
    )
    session.pending_input = None
    session.pending_node_name = None
    session.draft = None
    session.pending_action = None
    return had_active_flow


def begin_text_input(
    user_id: int,
    pending_input: Literal["enroll_name", "daily_time"],
) -> bool:
    cancelled = cancel_active_flow(user_id)
    get_bot_session(user_id).pending_input = pending_input
    return cancelled


def begin_node_display_name_input(user_id: int, node_name: str) -> bool:
    cancelled = cancel_active_flow(user_id)
    session = get_bot_session(user_id)
    session.pending_input = "node_display_name"
    session.pending_node_name = node_name
    return cancelled


def prepend_cancelled_notice(text: str, cancelled: bool) -> str:
    if not cancelled:
        return text
    return "ℹ️ 已取消未完成的操作。\n\n" + text


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
        [InlineKeyboardButton(text=zone.zone_name, callback_data=f"{CALLBACK_DNS_ZONE_PREFIX}{zone.key}")]
        for zone in service.list_configured_zones()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_zone_keyboard(zone_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="查看记录", callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{zone_key}:1"),
                InlineKeyboardButton(text="添加记录", callback_data=f"{CALLBACK_DNS_CREATE_PREFIX}{zone_key}"),
            ],
            [InlineKeyboardButton(text="切换 Zone", callback_data=CALLBACK_DNS_HOME)],
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
    rows.append(
        [
            InlineKeyboardButton(text="刷新", callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{record_page.zone.key}:{record_page.page}"),
            InlineKeyboardButton(text="添加记录", callback_data=f"{CALLBACK_DNS_CREATE_PREFIX}{record_page.zone.key}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="返回 Zone", callback_data=f"{CALLBACK_DNS_ZONE_PREFIX}{record_page.zone.key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dns_record_detail_keyboard(zone_key: str, record_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="修改", callback_data=f"{CALLBACK_DNS_UPDATE_PREFIX}{zone_key}:{record_id}"),
                InlineKeyboardButton(text="删除", callback_data=f"{CALLBACK_DNS_DELETE_PREFIX}{zone_key}:{record_id}"),
            ],
            [
                InlineKeyboardButton(text="返回列表", callback_data=f"{CALLBACK_DNS_LIST_PREFIX}{zone_key}:1"),
                InlineKeyboardButton(text="切换 Zone", callback_data=f"{CALLBACK_DNS_ZONE_PREFIX}{zone_key}"),
            ],
        ]
    )


def build_dns_type_keyboard(zone_key: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=record_type, callback_data=f"{CALLBACK_DNS_TYPE_PREFIX}{zone_key}:{record_type}")
        for record_type in SUPPORTED_DNS_RECORD_TYPES
    ]
    rows = _chunk_buttons(buttons, size=3)
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
    buttons = [
        InlineKeyboardButton(
            text=f"{label}{' ✓' if current_ttl == ttl else ''}",
            callback_data=f"{CALLBACK_DNS_TTL_PREFIX}{ttl}",
        )
        for ttl, label in DNS_TTL_OPTIONS
    ]
    rows = _chunk_buttons(buttons, size=3)
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
            ),
            InlineKeyboardButton(
                text=f"仅 DNS{' ✓' if current_value is False else ''}",
                callback_data=f"{CALLBACK_DNS_PROXIED_PREFIX}0",
            ),
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
            [
                InlineKeyboardButton(text=action_label, callback_data=f"{CALLBACK_DNS_CONFIRM_PREFIX}{action}"),
                InlineKeyboardButton(text="取消", callback_data=CALLBACK_DNS_CANCEL),
            ],
        ]
    )


def render_dns_record_text(record: CloudflareDNSRecord, zone_name: str) -> str:
    lines = [
        f"Zone {html_code(zone_name)}",
        f"类型 {html_code(record.type)} · TTL {html_code(format_dns_ttl(record.ttl))}",
        f"值 {html_code(record.content)}",
        f"代理 {html_code(format_dns_proxied(record.proxied))}",
    ]
    if record.comment:
        lines.append(f"备注 {html.escape(record.comment)}")
    return f"<b>☁️ DNS 记录 · {html.escape(record.name)}</b>\n{html_card('记录信息', lines)}"


def render_dns_draft_preview(draft: DnsDraft, zone_name: str) -> str:
    action_label = "新增记录" if draft.mode == "create" else "更新记录"
    lines = [
        f"Zone {html_code(zone_name)}",
        f"类型 {html_code(draft.record_type)} · TTL {html_code(format_dns_ttl(draft.ttl))}",
        f"名称 {html_code(draft.name)}",
        f"值 {html_code(draft.content)}",
    ]
    if draft.record_type in {"A", "AAAA", "CNAME"}:
        lines.append(f"代理 {html_code(format_dns_proxied(draft.proxied))}")
    cards = [html_card("待提交", lines)]
    if draft.mode == "update" and draft.original_record is not None:
        cards.append(
            html_card(
                "当前记录",
                [
                    f"名称 {html_code(draft.original_record.name)}",
                    f"值 {html_code(draft.original_record.content)}",
                    f"TTL {html_code(format_dns_ttl(draft.original_record.ttl))}",
                    f"代理 {html_code(format_dns_proxied(draft.original_record.proxied))}",
                ],
            )
        )
    return f"<b>☁️ DNS 预览 · {action_label}</b>\n" + "\n\n".join(cards)


def render_dns_list_text(record_page: CloudflareDNSRecordPage) -> str:
    lines = [
        f"页码 {html_code(f'{record_page.page}/{record_page.total_pages}')} · 记录 {html_code(str(record_page.total_count))} 条",
        "点击下方记录进入详情。",
    ]
    if not record_page.records:
        lines.append("当前 Zone 还没有受支持的记录类型。")
    return f"<b>☁️ DNS 列表 · {html.escape(record_page.zone.zone_name)}</b>\n{html_card('记录概况', lines)}"


def render_dns_zone_text(zone_name: str) -> str:
    return (
        f"<b>☁️ DNS 管理 · {html.escape(zone_name)}</b>\n"
        f"{html_card('Zone', ['选择要执行的操作。'])}"
    )


def render_dns_home_text(service: CloudflareDNSService) -> str:
    lines = [
        f"已配置 Zone {html_code(str(len(service.list_configured_zones())))} 个",
        "先选择一个 Zone，再查看记录或新增记录。",
    ]
    return f"<b>☁️ DNS 管理</b>\n{html_card('概况', lines)}"


def render_dns_prompt_text(*, title: str, field_label: str, hint: str, current_value: str | None = None) -> str:
    lines = [f"请发送 <b>{html.escape(field_label)}</b>。", html.escape(hint)]
    if current_value:
        lines.append(f"当前值 {html_code(current_value)}")
    return f"<b>☁️ DNS 流程 · {html.escape(title)}</b>\n{html_card('下一步', lines)}"


def render_dns_delete_preview(record: CloudflareDNSRecord, zone_name: str) -> str:
    return (
        "<b>☁️ DNS 预览 · 删除记录</b>\n"
        + html_card(
            "待删除",
            [
                f"Zone {html_code(zone_name)}",
                f"名称 {html_code(record.name)}",
                f"类型 {html_code(record.type)} · TTL {html_code(format_dns_ttl(record.ttl))}",
                f"值 {html_code(record.content)}",
                f"代理 {html_code(format_dns_proxied(record.proxied))}",
                "⚠️ 确认删除吗？",
            ],
        )
    )


def dashboard_button_rows() -> list[list[str]]:
    return [[NAV_NODES, NAV_DNS, NAV_TRAFFIC, NAV_DAILY, NAV_SETTINGS]]


def build_dashboard_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=label) for label in row]
            for row in dashboard_button_rows()
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="选择功能或输入命令",
    )


def build_dashboard_menu_text() -> str:
    return "<b>⚡ ProxyPulse 控制台</b>\n" + html_card("服务状态", ["控制台已就绪，请选择功能。"])


def build_settings_menu_text() -> str:
    return (
        "<b>⚙️ 设置</b>\n"
        + html_card(
            "管理入口",
            [
                "日报时间支持按钮引导修改。",
                "流量套餐参数较多，继续使用高级命令管理。",
                "命令帮助中保留所有兼容命令的用法。",
            ],
        )
    )


def build_command_help_text() -> str:
    return (
        "<b>📖 ProxyPulse 命令帮助</b>\n"
        + html_card(
            "常用命令",
            [
                f"{html_code('/start')} 打开首页并恢复底部导航",
                f"{html_code('/enroll')} 接入节点",
                f"{html_code('/cancel')} 取消当前输入或草稿",
                f"{html_code('/help')} 查看本说明",
            ],
        )
        + "\n\n"
        + html_card(
            "高级兼容命令",
            [
                html_code("/nodes · /node <节点名> · /delete_node <节点名>"),
                html_code("/traffic · /traffic_diag <节点名>"),
                html_code("/daily · /daily_time [HH:MM]"),
                html_code("/quota <节点名> · /quota_monthly ..."),
                html_code("/quota_interval ... · /quota_calibrate ... · /quota_clear <节点名>"),
                html_code("/dns · /dns_zones"),
                "这些命令不会显示在“/”菜单中，但仍可直接输入。",
            ],
        )
    )


def build_enrollment_prompt_text() -> str:
    return (
        "<b>🔐 节点接入</b>\n"
        + html_card(
            "下一步",
            [
                "请发送节点名称，例如：tokyo。",
                f"发送 {html_code('/cancel')} 可取消。",
            ],
        )
    )


def build_daily_time_prompt_text() -> str:
    return (
        "<b>🕘 修改日报推送时间</b>\n"
        + html_card(
            "下一步",
            [
                "请发送 HH:MM 格式的时间，例如：08:30。",
                f"时区使用服务端配置：{html_code(settings.report_timezone)}。",
            ],
        )
    )


def build_node_display_name_prompt_text(node_name: str, display_name: str) -> str:
    return (
        f"<b>✏️ 节点显示名称 · {html.escape(display_name)}</b>\n"
        + html_card(
            "下一步",
            [
                f"内部标识 {html_code(node_name)}",
                "请发送新的 Telegram 显示名称。",
                "只改变 Telegram 中的显示，不影响 Agent、API 或历史数据。",
                "最多 40 个字符。",
            ],
        )
    )


def _chunk_buttons(buttons: list[InlineKeyboardButton], size: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + size] for index in range(0, len(buttons), size)]


def build_node_list_keyboard(node_names: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    buttons = [
        InlineKeyboardButton(text=display_name, callback_data=f"{CALLBACK_NODE_PREFIX}{node_name}")
        for node_name, display_name in node_names
    ]
    rows = _chunk_buttons(buttons)
    rows.append(
        [
            InlineKeyboardButton(text="接入节点", callback_data=CALLBACK_START_ENROLL),
            InlineKeyboardButton(text="刷新", callback_data=CALLBACK_SHOW_NODES),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_node_detail_keyboard(node_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="刷新", callback_data=f"{CALLBACK_NODE_PREFIX}{node_name}"),
                InlineKeyboardButton(text="重命名", callback_data=f"{CALLBACK_NODE_RENAME_PREFIX}{node_name}"),
            ],
            [
                InlineKeyboardButton(text="流量诊断", callback_data=f"{CALLBACK_NODE_DIAG_PREFIX}{node_name}"),
                InlineKeyboardButton(text="套餐信息", callback_data=f"{CALLBACK_NODE_QUOTA_PREFIX}{node_name}"),
            ],
            [
                InlineKeyboardButton(text="返回列表", callback_data=CALLBACK_SHOW_NODES),
            ],
            [
                InlineKeyboardButton(text="🗑️ 删除节点", callback_data=f"{CALLBACK_NODE_DELETE_PREFIX}{node_name}"),
            ],
        ]
    )


def build_node_display_name_keyboard(
    node_name: str,
    *,
    can_restore: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_restore:
        rows.append(
            [
                InlineKeyboardButton(
                    text="恢复原名",
                    callback_data=f"{CALLBACK_NODE_RENAME_CLEAR_PREFIX}{node_name}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="取消", callback_data=CALLBACK_INPUT_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_node_delete_confirm_keyboard(node_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="确认删除", callback_data=f"{CALLBACK_NODE_DELETE_CONFIRM_PREFIX}{node_name}"),
                InlineKeyboardButton(text="取消", callback_data=f"{CALLBACK_NODE_DELETE_CANCEL_PREFIX}{node_name}"),
            ],
            [InlineKeyboardButton(text="返回列表", callback_data=CALLBACK_SHOW_NODES)],
        ]
    )


def build_single_action_keyboard(refresh_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="刷新", callback_data=refresh_callback)]]
    )


def build_daily_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="刷新", callback_data=CALLBACK_SHOW_DAILY),
                InlineKeyboardButton(text="推送时间设置", callback_data=CALLBACK_DAILY_SCHEDULE),
            ]
        ]
    )


def build_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="日报时间", callback_data=CALLBACK_DAILY_SCHEDULE),
                InlineKeyboardButton(text="套餐管理说明", callback_data=CALLBACK_QUOTA_HELP),
            ],
            [InlineKeyboardButton(text="命令帮助", callback_data=CALLBACK_COMMAND_HELP)],
        ]
    )


def build_daily_schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="修改时间", callback_data=CALLBACK_DAILY_TIME_EDIT)],
            [InlineKeyboardButton(text="返回设置", callback_data=CALLBACK_SHOW_SETTINGS)],
        ]
    )


def build_input_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="取消", callback_data=CALLBACK_INPUT_CANCEL)]]
    )


def build_enrollment_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="返回节点概览", callback_data=CALLBACK_SHOW_NODES)]]
    )


def build_node_quota_keyboard(node_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="返回节点详情", callback_data=f"{CALLBACK_NODE_PREFIX}{node_name}")],
            [InlineKeyboardButton(text="返回节点概览", callback_data=CALLBACK_SHOW_NODES)],
        ]
    )


def build_command_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="返回设置", callback_data=CALLBACK_SHOW_SETTINGS)]]
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


def format_avg_peak_values(avg_value: float | None, peak_value: float | None) -> str:
    return f"{format_metric_value(avg_value)} / {format_metric_value(peak_value)}"


def format_resource_usage(used_bytes: int | None, total_bytes: int | None, percent: float | None) -> str:
    if used_bytes is not None and total_bytes is not None:
        return f"{format_byte_value(used_bytes)} / {format_byte_value(total_bytes)} ({format_metric_value(percent)})"
    if percent is not None:
        return format_metric_value(percent)
    return "暂无"


def html_code(value: str) -> str:
    return f"<code>{html.escape(value)}</code>"


def html_card(title: str, lines: list[str]) -> str:
    body = "\n".join([f"<b>{html.escape(title)}</b>", *lines])
    return f"<blockquote>{body}</blockquote>"


def html_error(title: str, error: object) -> str:
    return f"<b>{html.escape(title)}</b>\n{html_card('操作失败', [html.escape(str(error))])}"


def format_reset_phrase(next_reset_at: datetime | None, *, now: datetime | None = None) -> str:
    remaining_days = days_until_reset(next_reset_at, now=now)
    if remaining_days is None:
        return "重置时间未知"
    if remaining_days == 0:
        return "今天重置"
    return f"{remaining_days} 天后重置"


def render_overview_quota_html(status, *, now: datetime | None = None) -> list[str]:
    if not status.configured:
        return ["📦 套餐未配置"]
    percent = f"{status.percent_used:.1f}%" if status.percent_used is not None else "暂无"
    return [
        (
            "📦 已用 "
            f"{html_code(format_byte_value(status.used_bytes))} / {html_code(format_byte_value(status.limit_bytes))}"
            f"（{html.escape(percent)}）"
        ),
        (
            f"可用 {html_code(format_byte_value(status.remaining_bytes))}"
            f" · {html_code(format_reset_phrase(status.next_reset_at, now=now))}"
        ),
    ]


def render_quota_detail_html(status) -> list[str]:
    if not status.configured:
        return ["套餐未配置"]

    percent = f"{status.percent_used:.1f}%" if status.percent_used is not None else "暂无"
    lines = [
        f"上限 {html_code(format_byte_value(status.limit_bytes))} · 已用 {html_code(format_byte_value(status.used_bytes))}",
        f"剩余 {html_code(format_byte_value(status.remaining_bytes))} · 进度 {html_code(percent)}",
        f"重置 {html_code(format_reset_phrase(status.next_reset_at))}",
        f"周期 {html_code(status.cycle_description or '未配置')}",
    ]
    if status.period_start is not None and status.next_reset_at is not None:
        lines.append(
            "区间 "
            f"{html_code(status.period_start.astimezone(ZoneInfo(settings.report_timezone)).strftime('%m-%d %H:%M'))}"
            " → "
            f"{html_code(status.next_reset_at.astimezone(ZoneInfo(settings.report_timezone)).strftime('%m-%d %H:%M'))}"
        )
    elif status.period_start is not None:
        lines.append(
            f"开始 {html_code(status.period_start.astimezone(ZoneInfo(settings.report_timezone)).strftime('%m-%d %H:%M'))}"
        )
    elif status.next_reset_at is not None:
        lines.append(
            f"重置 {html_code(status.next_reset_at.astimezone(ZoneInfo(settings.report_timezone)).strftime('%m-%d %H:%M'))}"
        )
    if status.calibration_bytes is not None:
        lines.append(f"校准 {html_code(format_byte_value(status.calibration_bytes))}")
    return lines


def render_node_card(card, *, display_name: str | None = None) -> str:
    node = card.node
    body_lines = [
        "<b>资源</b>",
        (
            f"⚙️ CPU {html_code(format_scoped_value(node, 'cpu', node.latest_cpu_percent, format_metric_value))}"
            f" · 内存 {html_code(format_scoped_value(node, 'memory', node.latest_memory_percent, format_metric_value))}"
            f" · 磁盘 {html_code(format_scoped_value(node, 'disk', node.latest_disk_percent, format_metric_value))}"
        ),
        "<b>近 1 小时 · 均值 / 峰值</b>",
        (
            f"CPU {html_code(format_scoped_values(node, 'cpu', [card.trend_1h.avg_cpu_percent, card.trend_1h.peak_cpu_percent], lambda: format_avg_peak_values(card.trend_1h.avg_cpu_percent, card.trend_1h.peak_cpu_percent)))}"
            f"\n内存 {html_code(format_scoped_values(node, 'memory', [card.trend_1h.avg_memory_percent, card.trend_1h.peak_memory_percent], lambda: format_avg_peak_values(card.trend_1h.avg_memory_percent, card.trend_1h.peak_memory_percent)))}"
            f"\n磁盘 {html_code(format_scoped_values(node, 'disk', [card.trend_1h.avg_disk_percent, card.trend_1h.peak_disk_percent], lambda: format_avg_peak_values(card.trend_1h.avg_disk_percent, card.trend_1h.peak_disk_percent)))}"
        ),
        (
            f"🧭 {html_code(format_scoped_value(node, 'cpu', node.latest_cpu_count, format_integer_value))} 核"
            f" · 负载 {html_code(format_scoped_value(node, 'cpu', node.latest_load_avg_1m, lambda value: f'{value:.2f}'))}"
            f" · 运行 {html_code(format_scoped_value(node, 'uptime', node.latest_uptime_seconds, format_uptime))}"
        ),
        "",
        "<b>套餐</b>",
        *render_overview_quota_html(card.quota_status),
    ]
    display_name = display_name or node.name
    title = (
        f"<b>🖥️ {html.escape(display_name)}</b>"
        f"  {format_status_label(node)} · {html.escape(format_relative_time(node.last_seen_at))}"
    )
    body = "\n".join(body_lines)
    return f"{title}\n<blockquote>{body}</blockquote>"


def render_quota_help_lines() -> list[str]:
    return [
        "<b>📦 流量套餐命令</b>",
        html_card(
            "查看与管理",
            [
                f"查看 {html_code('/quota <节点名>')}",
                f"月度 {html_code('/quota_monthly <节点名> <上限GiB> <重置日> <HH:MM>')}",
                f"循环 {html_code('/quota_interval <节点名> <上限GiB> <间隔天数> <起始时间>')}",
                f"校准 {html_code('/quota_calibrate <节点名> <已用GiB>')}",
                f"清除 {html_code('/quota_clear <节点名>')}",
            ],
        ),
        html_card(
            "示例",
            [
                html_code("/quota_monthly tokyo 1000 1 00:00"),
                html_code("/quota_interval la 750 30 2026-03-25T08:00"),
                html_code("/quota_calibrate tokyo 123.5"),
                "未带时区的时间按服务端配置时区解释。",
            ],
        ),
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
    await message.answer(build_dashboard_menu_text(), parse_mode="HTML", reply_markup=build_dashboard_keyboard())


async def safe_edit_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer()


async def render_nodes_response() -> tuple[str, InlineKeyboardMarkup | None]:
    async with SessionLocal() as session:
        nodes = await list_nodes(session)
        overview, cards = await build_nodes_dashboard(session, nodes)
        display_names = await get_telegram_node_display_names(session, nodes)

    if not nodes:
        return "<b>📡 节点概览</b>\n当前还没有接入任何节点。", build_node_list_keyboard([])

    status_parts = [f"🟢 在线 {overview.online_count}", f"🔴 离线 {overview.offline_count}"]
    if overview.pending_count:
        status_parts.append(f"🟡 待接入 {overview.pending_count}")
    lines = ["<b>📡 节点概览</b>", " · ".join(status_parts)]
    for card in cards:
        lines.extend(["", render_node_card(card, display_name=display_names[card.node.name])])
    return "\n".join(lines), build_node_list_keyboard(
        [(node.name, display_names[node.name]) for node in nodes]
    )


async def render_node_detail(node_name: str) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        if node is None:
            quota_status = None
            detail_summary = None
            display_name = node_name
        else:
            quota_status = await get_quota_status(session, node)
            detail_summary = await build_node_detail_summary(session, node)
            display_name = await get_telegram_node_display_name(session, node)
    if node is None:
        return "未找到对应节点。", build_single_action_keyboard(CALLBACK_SHOW_NODES)

    title = (
        f"<b>🖥️ {html.escape(display_name)}</b>  {format_status_label(node)}"
        f" · {html.escape(format_relative_time(node.last_seen_at))}"
    )
    identity_lines = [
        f"主机 {html_code(format_scoped_value(node, 'identity', node.hostname, str))} · 系统 {html_code(format_scoped_value(node, 'identity', node.platform, str))}",
        f"IP {html_code(format_scoped_value(node, 'identity', node.ips or None, lambda value: ', '.join(value)))}",
        f"网卡 {html_code(format_scoped_value(node, 'network', node.latest_network_interface, format_network_interface_label))}",
        f"采集 {html_code(format_collection_scope(node))}",
    ]
    if display_name != node.name:
        identity_lines.insert(0, f"内部标识 {html_code(node.name)}")
    cards = [
        html_card(
            "基础信息",
            identity_lines,
        ),
        html_card(
            "实时状态",
            [
                f"CPU {html_code(format_scoped_value(node, 'cpu', node.latest_cpu_percent, format_metric_value))} · 核心 {html_code(format_scoped_value(node, 'cpu', node.latest_cpu_count, format_integer_value))}",
                f"负载 {html_code(format_scoped_value(node, 'cpu', node.latest_load_avg_1m, lambda value: f'{value:.2f}'))} · 运行 {html_code(format_scoped_value(node, 'uptime', node.latest_uptime_seconds, format_uptime))}",
                f"内存 {html_code(format_scoped_values(node, 'memory', [node.latest_memory_used_bytes, node.latest_memory_total_bytes, node.latest_memory_percent], lambda: format_resource_usage(node.latest_memory_used_bytes, node.latest_memory_total_bytes, node.latest_memory_percent)))}",
                f"磁盘 {html_code(format_scoped_values(node, 'disk', [node.latest_disk_used_bytes, node.latest_disk_total_bytes, node.latest_disk_percent], lambda: format_resource_usage(node.latest_disk_used_bytes, node.latest_disk_total_bytes, node.latest_disk_percent)))}",
            ],
        ),
        html_card(
            "网络流量",
            [
                f"实时 ↓ {html_code(format_scoped_value(node, 'network', detail_summary.current_rate.rx_bps, format_rate_value))} · ↑ {html_code(format_scoped_value(node, 'network', detail_summary.current_rate.tx_bps, format_rate_value))}",
                f"累计 ↓ {html_code(format_scoped_value(node, 'network', node.latest_rx_bytes, format_byte_value))} · ↑ {html_code(format_scoped_value(node, 'network', node.latest_tx_bytes, format_byte_value))}",
            ],
        ),
        html_card(
            "近 1 小时 · 均值 / 峰值",
            [
                f"CPU {html_code(format_scoped_values(node, 'cpu', [detail_summary.trend_1h.avg_cpu_percent, detail_summary.trend_1h.peak_cpu_percent], lambda: format_avg_peak_values(detail_summary.trend_1h.avg_cpu_percent, detail_summary.trend_1h.peak_cpu_percent)))}",
                f"内存 {html_code(format_scoped_values(node, 'memory', [detail_summary.trend_1h.avg_memory_percent, detail_summary.trend_1h.peak_memory_percent], lambda: format_avg_peak_values(detail_summary.trend_1h.avg_memory_percent, detail_summary.trend_1h.peak_memory_percent)))}",
                f"磁盘 {html_code(format_scoped_values(node, 'disk', [detail_summary.trend_1h.avg_disk_percent, detail_summary.trend_1h.peak_disk_percent], lambda: format_avg_peak_values(detail_summary.trend_1h.avg_disk_percent, detail_summary.trend_1h.peak_disk_percent)))}",
                f"流量 ↓ {html_code(format_scoped_value(node, 'network', detail_summary.trend_1h.rx_bytes, format_byte_value))} · ↑ {html_code(format_scoped_value(node, 'network', detail_summary.trend_1h.tx_bytes, format_byte_value))}",
                f"样本 {html_code(str(detail_summary.trend_1h.sample_count))}",
            ],
        ),
        html_card("流量套餐", render_quota_detail_html(quota_status)),
    ]
    return title + "\n" + "\n\n".join(cards), build_node_detail_keyboard(node.name)


async def render_node_delete_confirm(node_name: str) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        display_name = (
            await get_telegram_node_display_name(session, node)
            if node is not None
            else node_name
        )
    if node is None:
        return "未找到对应节点。", build_single_action_keyboard(CALLBACK_SHOW_NODES)
    text = (
        f"<b>🗑️ 删除节点 · {html.escape(display_name)}</b>\n"
        + html_card(
            "影响范围",
            [
                f"内部标识 {html_code(node.name)}",
                "• 历史指标快照",
                "• 流量套餐配置",
                "",
                "Agent 令牌将立即失效，仍在运行的 Agent 会上报失败。",
                "⚠️ 确认删除吗？",
            ],
        )
    )
    return text, build_node_delete_confirm_keyboard(node.name)


async def render_node_display_name_prompt(
    node_name: str,
) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        if node is None:
            return "未找到对应节点。", build_single_action_keyboard(CALLBACK_SHOW_NODES)
        display_name = await get_telegram_node_display_name(session, node)
    return (
        build_node_display_name_prompt_text(node.name, display_name),
        build_node_display_name_keyboard(
            node.name,
            can_restore=display_name != node.name,
        ),
    )


async def render_traffic_response() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        summary = await summarize_recent_24h(session)
        nodes = await list_nodes(session)
        display_names = await get_telegram_node_display_names(session, nodes)
    return (
        format_traffic_summary(summary, node_display_names=display_names),
        build_single_action_keyboard(CALLBACK_SHOW_TRAFFIC),
    )


async def render_daily_response() -> tuple[str, InlineKeyboardMarkup | None]:
    async with SessionLocal() as session:
        _, summary = await summarize_previous_local_day(session)
        nodes = await list_nodes(session)
        display_names = await get_telegram_node_display_names(session, nodes)
    return format_traffic_summary(summary, node_display_names=display_names), build_daily_keyboard()


async def render_quota_response(node_name: str) -> str:
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        if node is None:
            raise QuotaServiceError("未找到对应节点。")
        status = await get_quota_status(session, node)
        display_name = await get_telegram_node_display_name(session, node)
    return f"<b>🧾 套餐状态 · {html.escape(display_name)}</b>\n{html_card('流量套餐', render_quota_detail_html(status))}"


async def render_node_quota_management_response(node_name: str) -> str:
    status_text = await render_quota_response(node_name)
    command_lines = [
        f"月度 {html_code(f'/quota_monthly {node_name} <上限GiB> <重置日> <HH:MM>')}",
        f"循环 {html_code(f'/quota_interval {node_name} <上限GiB> <间隔天数> <起始时间>')}",
        f"校准 {html_code(f'/quota_calibrate {node_name} <已用GiB>')}",
        f"清除 {html_code(f'/quota_clear {node_name}')}",
    ]
    return status_text + "\n\n" + html_card("管理命令", command_lines)


async def render_daily_schedule_response() -> str:
    async with SessionLocal() as session:
        schedule = await get_daily_report_schedule(session)
    return (
        "<b>🕘 流量日报推送时间</b>\n"
        + html_card(
            "当前设置",
            [
                f"时区 {html_code(schedule.timezone)}",
                f"时间 每天 {html_code(schedule.clock_text)}",
                "",
                "点击下方按钮修改，或直接使用 /daily_time HH:MM。",
            ],
        )
    )


async def render_traffic_diag_response(node_name: str) -> str:
    async with SessionLocal() as session:
        diagnosis = await build_traffic_diagnosis(session, node_name)
        display_name = await get_telegram_node_display_name(session, diagnosis.node)
    return format_traffic_diagnosis(diagnosis, node_display_name=display_name)


async def render_dns_home() -> tuple[str, InlineKeyboardMarkup | None]:
    try:
        service = get_dns_service()
        return render_dns_home_text(service), build_dns_home_keyboard(service)
    except (CloudflareServiceError, ValueError) as exc:
        return html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME)


async def render_dns_zone(zone_key: str) -> tuple[str, InlineKeyboardMarkup]:
    service = get_dns_service()
    zone = service.get_zone(zone_key)
    return render_dns_zone_text(zone.zone_name), build_dns_zone_keyboard(zone.key)


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
        await message_or_callback.answer(text, parse_mode="HTML", reply_markup=keyboard)


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
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def prompt_dns_ttl(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    text = "<b>☁️ DNS 流程 · 选择 TTL</b>\n" + html_card(
        "当前选择", [html_code(format_dns_ttl(draft.ttl))]
    )
    keyboard = build_dns_ttl_keyboard(current_ttl=draft.ttl, allow_keep=draft.mode == "update")
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def prompt_dns_proxied(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    if draft.record_type not in {"A", "AAAA", "CNAME"}:
        await present_dns_preview(target, draft)
        return
    text = "<b>☁️ DNS 流程 · 选择代理模式</b>\n" + html_card(
        "当前选择", [html_code(format_dns_proxied(draft.proxied))]
    )
    keyboard = build_dns_proxied_keyboard(current_value=draft.proxied, allow_keep=draft.mode == "update")
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def present_dns_preview(target: Message | CallbackQuery, draft: DnsDraft) -> None:
    service = get_dns_service()
    zone = service.get_zone(draft.zone_key)
    text = render_dns_draft_preview(draft, zone.zone_name)
    keyboard = build_dns_confirm_keyboard(draft.mode)
    if isinstance(target, CallbackQuery):
        await safe_edit_callback_message(target, text, keyboard)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


def require_dns_session(user_id: int) -> BotSession:
    session = BOT_SESSIONS.get(user_id)
    if session is None:
        raise CloudflareServiceError("DNS 流程已过期，请重新进入 /dns。")
    return session


async def build_enrollment_response(node_name: str) -> str:
    async with SessionLocal() as session:
        node = await create_or_refresh_enrollment(session, node_name)
        display_name = await get_telegram_node_display_name(session, node)
    agent_command = (
        f"PROXYPULSE_SERVER_URL={settings.server_url} "
        f"PROXYPULSE_AGENT_NAME={node.name} "
        f"PROXYPULSE_AGENT_ENROLLMENT_TOKEN={node.enrollment_token} "
        "python -m proxypulse.agent"
    )
    enrollment_lines = [html_code(node.enrollment_token)]
    if display_name != node.name:
        enrollment_lines.insert(0, f"内部标识 {html_code(node.name)}")
    return (
        f"<b>🔐 节点接入 · {html.escape(display_name)}</b>\n"
        + html_card("接入令牌", enrollment_lines)
        + "\n\n"
        + html_card("Agent 启动命令", [html_code(agent_command)])
    )


async def update_daily_schedule_response(clock_text: str) -> str:
    hour, minute = parse_daily_report_clock(clock_text)
    async with SessionLocal() as session:
        schedule = await set_daily_report_schedule(session, hour=hour, minute=minute)
    return (
        "<b>✅ 已更新流量日报推送时间</b>\n"
        + html_card(
            "当前设置",
            [f"时区 {html_code(schedule.timezone)}", f"时间 每天 {html_code(schedule.clock_text)}"],
        )
    )


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    await send_dashboard(message)


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    await message.answer(
        build_command_help_text(),
        parse_mode="HTML",
        reply_markup=build_dashboard_keyboard(),
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    if message.from_user is None:
        await message.answer("无法识别当前用户。")
        return
    cancelled = cancel_active_flow(message.from_user.id)
    text = "✅ 已取消当前操作。" if cancelled else "当前没有待取消的操作。"
    await message.answer(text, reply_markup=build_dashboard_keyboard())


@router.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    await start_handler(message)


@router.message(Command("enroll"))
async def enroll_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        if message.from_user is None:
            await message.answer("无法识别当前用户。")
            return
        begin_text_input(message.from_user.id, "enroll_name")
        await message.answer(
            build_enrollment_prompt_text(),
            parse_mode="HTML",
            reply_markup=build_input_cancel_keyboard(),
        )
        return

    try:
        text = await build_enrollment_response(node_name)
    except NodeServiceError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_enrollment_done_keyboard(),
    )


@router.message(Command("nodes"))
async def nodes_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_nodes_response()
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("node"))
async def node_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/node <节点名>")
        return

    text, keyboard = await render_node_detail(node_name)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("delete_node"))
async def delete_node_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/delete_node <节点名>")
        return
    text, keyboard = await render_node_delete_confirm(node_name)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("traffic"))
async def traffic_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_traffic_response()
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("traffic_diag"))
async def traffic_diag_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("用法：/traffic_diag <节点名>")
        return
    try:
        text = await render_traffic_diag_response(node_name)
    except TrafficDiagnosisError as exc:
        await message.answer(str(exc))
        return
    await message.answer(text, parse_mode="HTML")


@router.message(Command("daily"))
async def daily_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    text, keyboard = await render_daily_response()
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("daily_time"))
async def daily_time_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    clock_text = (command.args or "").strip()
    if not clock_text:
        await message.answer(
            await render_daily_schedule_response(),
            parse_mode="HTML",
            reply_markup=build_daily_schedule_keyboard(),
        )
        return

    try:
        text = await update_daily_schedule_response(clock_text)
    except ReportScheduleError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_daily_schedule_keyboard(),
    )


@router.message(Command("quota"))
async def quota_handler(message: Message, command: CommandObject) -> None:
    if await reject_if_not_admin(message):
        return
    node_name = (command.args or "").strip()
    if not node_name:
        await message.answer("\n\n".join(render_quota_help_lines()), parse_mode="HTML")
        return
    try:
        await message.answer(await render_quota_response(node_name), parse_mode="HTML")
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
            display_name = await get_telegram_node_display_name(session, node)
    except (ValueError, QuotaServiceError) as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"<b>✅ 已设置每月流量套餐 · {html.escape(display_name)}</b>\n"
        + html_card("当前套餐", render_quota_detail_html(status)),
        parse_mode="HTML",
    )


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
            display_name = await get_telegram_node_display_name(session, node)
    except (ValueError, QuotaServiceError) as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"<b>✅ 已设置按天循环套餐 · {html.escape(display_name)}</b>\n"
        + html_card("当前套餐", render_quota_detail_html(status)),
        parse_mode="HTML",
    )


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
            display_name = await get_telegram_node_display_name(session, node)
    except QuotaServiceError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"<b>✅ 已校准已用流量 · {html.escape(display_name)}</b>\n"
        + html_card("当前套餐", render_quota_detail_html(status)),
        parse_mode="HTML",
    )


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
            display_name = await get_telegram_node_display_name(session, node)
    except QuotaServiceError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"<b>✅ 已清除流量套餐配置</b>\n节点 {html_code(display_name)}",
        parse_mode="HTML",
    )


@router.message(Command("dns"))
async def dns_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    if message.from_user is None:
        await message.answer("无法识别当前用户。")
        return
    reset_dns_session(message.from_user.id)
    text, keyboard = await render_dns_home()
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


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
    lines = [html.escape(zone.zone_name) for zone in zones]
    await message.answer(f"<b>☁️ DNS Zones</b>\n{html_card('已配置', lines)}", parse_mode="HTML")


@router.message(F.text.in_({NAV_NODES, NAV_DNS, NAV_TRAFFIC, NAV_DAILY, NAV_SETTINGS}))
async def navigation_handler(message: Message) -> None:
    if await reject_if_not_admin(message):
        return
    if message.from_user is None:
        await message.answer("无法识别当前用户。")
        return

    cancelled = cancel_active_flow(message.from_user.id)
    if message.text == NAV_NODES:
        text, keyboard = await render_nodes_response()
    elif message.text == NAV_DNS:
        reset_dns_session(message.from_user.id)
        text, keyboard = await render_dns_home()
    elif message.text == NAV_TRAFFIC:
        text, keyboard = await render_traffic_response()
    elif message.text == NAV_DAILY:
        text, keyboard = await render_daily_response()
    else:
        text, keyboard = build_settings_menu_text(), build_settings_keyboard()

    await message.answer(
        prepend_cancelled_notice(text, cancelled),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.message(F.text)
async def text_input_handler(message: Message) -> None:
    if not is_admin(message) or message.from_user is None:
        return
    session = BOT_SESSIONS.get(message.from_user.id)
    value = (message.text or "").strip()
    if not value:
        await message.answer("输入不能为空，请重新发送。")
        return

    if session is not None and session.pending_input == "node_display_name":
        node_name = session.pending_node_name
        if not node_name:
            cancel_active_flow(message.from_user.id)
            await message.answer("重命名流程已过期，请重新进入节点详情。")
            return
        try:
            async with SessionLocal() as db_session:
                node = await get_node_by_name(db_session, node_name)
                if node is None:
                    raise TelegramNodeDisplayNameError("未找到对应节点。")
                display_name = await set_telegram_node_display_name(db_session, node, value)
        except TelegramNodeDisplayNameError as exc:
            await message.answer(f"{exc}\n请重新发送显示名称，或使用 /cancel 取消。")
            return
        session.pending_input = None
        session.pending_node_name = None
        text, keyboard = await render_node_detail(node_name)
        await message.answer(
            f"<b>✅ Telegram 显示名称已更新：{html.escape(display_name)}</b>\n\n{text}",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if session is not None and session.pending_input == "enroll_name":
        try:
            text = await build_enrollment_response(value)
        except NodeServiceError as exc:
            await message.answer(f"{exc}\n请重新发送节点名称，或使用 /cancel 取消。")
            return
        session.pending_input = None
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=build_enrollment_done_keyboard(),
        )
        return

    if session is not None and session.pending_input == "daily_time":
        try:
            text = await update_daily_schedule_response(value)
        except ReportScheduleError as exc:
            await message.answer(f"{exc}\n请重新发送 HH:MM，或使用 /cancel 取消。")
            return
        session.pending_input = None
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=build_daily_schedule_keyboard(),
        )
        return

    if session is None or session.draft is None or session.draft.pending_field is None:
        await message.answer("无法识别该输入，请使用底部导航或 /help。")
        return

    draft = session.draft
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
    cancel_active_flow(callback.from_user.id)
    await safe_edit_callback_message(callback, build_dashboard_menu_text(), None)
    if callback.message is not None:
        await callback.message.answer(
            "底部导航已恢复。",
            reply_markup=build_dashboard_keyboard(),
        )


@router.callback_query(F.data == CALLBACK_SHOW_NODES)
async def show_nodes_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    text, keyboard = await render_nodes_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_TRAFFIC)
async def show_traffic_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    text, keyboard = await render_traffic_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_DAILY)
async def show_daily_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    text, keyboard = await render_daily_response()
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data == CALLBACK_SHOW_SETTINGS)
async def show_settings_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    await safe_edit_callback_message(callback, build_settings_menu_text(), build_settings_keyboard())


@router.callback_query(F.data == CALLBACK_DAILY_SCHEDULE)
async def daily_schedule_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    text = await render_daily_schedule_response()
    await safe_edit_callback_message(callback, text, build_daily_schedule_keyboard())


@router.callback_query(F.data == CALLBACK_DAILY_TIME_EDIT)
async def daily_time_edit_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    begin_text_input(callback.from_user.id, "daily_time")
    await safe_edit_callback_message(callback, build_daily_time_prompt_text(), build_input_cancel_keyboard())


@router.callback_query(F.data == CALLBACK_QUOTA_HELP)
async def quota_help_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    text = "\n\n".join(render_quota_help_lines())
    await safe_edit_callback_message(callback, text, build_command_help_keyboard())


@router.callback_query(F.data == CALLBACK_COMMAND_HELP)
async def command_help_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
    await safe_edit_callback_message(callback, build_command_help_text(), build_command_help_keyboard())


@router.callback_query(F.data == CALLBACK_START_ENROLL)
async def start_enroll_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    begin_text_input(callback.from_user.id, "enroll_name")
    await safe_edit_callback_message(callback, build_enrollment_prompt_text(), build_input_cancel_keyboard())


@router.callback_query(F.data == CALLBACK_INPUT_CANCEL)
async def input_cancel_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    session = BOT_SESSIONS.get(callback.from_user.id)
    pending_input = session.pending_input if session is not None else None
    if not cancel_active_flow(callback.from_user.id):
        await callback.answer("当前没有待取消的操作。")
        return
    if pending_input == "daily_time":
        text, keyboard = build_settings_menu_text(), build_settings_keyboard()
    else:
        text, keyboard = await render_nodes_response()
    await safe_edit_callback_message(callback, "✅ 已取消当前操作。\n\n" + text, keyboard)


@router.callback_query(F.data == CALLBACK_DNS_HOME)
async def dns_home_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    cancel_active_flow(callback.from_user.id)
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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))
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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))
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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))
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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))
        return
    text = f"<b>☁️ DNS 新增 · {html.escape(zone.zone_name)}</b>\n" + html_card(
        "记录类型", ["先选择要新增的记录类型。"]
    )
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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))
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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))


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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))


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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))


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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))


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
        await safe_edit_callback_message(callback, html_error("☁️ DNS 管理", exc), build_single_action_keyboard(CALLBACK_DNS_HOME))


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
        text = html_error("☁️ DNS 管理", exc)
        keyboard = build_single_action_keyboard(CALLBACK_DNS_HOME)
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_NODE_RENAME_PREFIX))
async def node_display_name_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_RENAME_PREFIX) :]
    text, keyboard = await render_node_display_name_prompt(node_name)
    if text == "未找到对应节点。":
        await safe_edit_callback_message(callback, text, keyboard)
        return
    begin_node_display_name_input(callback.from_user.id, node_name)
    await safe_edit_callback_message(callback, text, keyboard)


@router.callback_query(F.data.startswith(CALLBACK_NODE_RENAME_CLEAR_PREFIX))
async def clear_node_display_name_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_RENAME_CLEAR_PREFIX) :]
    async with SessionLocal() as session:
        node = await get_node_by_name(session, node_name)
        if node is None:
            await safe_edit_callback_message(
                callback,
                "未找到对应节点。",
                build_single_action_keyboard(CALLBACK_SHOW_NODES),
            )
            return
        await clear_telegram_node_display_name(session, node)
    cancel_active_flow(callback.from_user.id)
    text, keyboard = await render_node_detail(node_name)
    await safe_edit_callback_message(
        callback,
        "✅ 已恢复原始节点名。\n\n" + text,
        keyboard,
    )


@router.callback_query(F.data.startswith(CALLBACK_NODE_DIAG_PREFIX))
async def node_diag_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_DIAG_PREFIX) :]
    try:
        text = await render_traffic_diag_response(node_name)
    except TrafficDiagnosisError as exc:
        text = html_error("流量诊断", exc)
    await safe_edit_callback_message(callback, text, build_node_quota_keyboard(node_name))


@router.callback_query(F.data.startswith(CALLBACK_NODE_QUOTA_PREFIX))
async def node_quota_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer("无权访问。", show_alert=True)
        return
    node_name = callback.data[len(CALLBACK_NODE_QUOTA_PREFIX) :]
    try:
        text = await render_node_quota_management_response(node_name)
    except QuotaServiceError as exc:
        text = html_error("流量套餐", exc)
    await safe_edit_callback_message(callback, text, build_node_quota_keyboard(node_name))


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
            existing_node = await get_node_by_name(session, node_name)
            if existing_node is None:
                raise NodeServiceError("未找到对应节点。")
            display_name = await get_telegram_node_display_name(session, existing_node)
            await clear_telegram_node_display_name(session, existing_node, commit=False)
            node = await delete_node_by_name(session, node_name)
    except NodeServiceError as exc:
        await safe_edit_callback_message(callback, html_error("节点操作", exc), build_single_action_keyboard(CALLBACK_SHOW_NODES))
        return
    text, keyboard = await render_nodes_response()
    if callback.message is not None:
        await callback.message.answer(f"✅ 已删除节点：{display_name}")
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

    async with SessionLocal() as session:
        schedule = await get_daily_report_schedule(session)
        if not should_send_daily_report(local_now, hour=schedule.hour, minute=schedule.minute):
            return
        report_day = local_now.date() - timedelta(days=1)
        if await has_daily_report_run(session, report_day):
            return
        _, summary = await summarize_previous_local_day(session, today_local=local_now.date())

        nodes = await list_nodes(session)
        display_names = await get_telegram_node_display_names(session, nodes)
        text = format_traffic_summary(summary, node_display_names=display_names)
        for admin_id in settings.admin_telegram_ids:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        mark_daily_report_run(session, report_day)
        await session.commit()


async def maintenance_loop(bot: Bot) -> None:
    while True:
        try:
            async with SessionLocal() as session:
                await mark_stale_nodes_offline(session)
                await session.commit()
            await maybe_send_daily_report(bot)
        except Exception:
            logger.exception("Maintenance loop iteration failed.")

        await asyncio.sleep(settings.maintenance_interval_seconds)


async def sync_bot_commands(bot: Bot) -> None:
    global_scopes = (
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
    )
    for scope in global_scopes:
        for language_code in BOT_COMMAND_LANGUAGE_CODES:
            await bot.delete_my_commands(
                scope=scope,
                language_code=language_code,
            )
        await bot.set_my_commands(BOT_COMMANDS, scope=scope)

    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    for admin_id in settings.admin_telegram_ids:
        scope = BotCommandScopeChat(chat_id=admin_id)
        try:
            for language_code in BOT_COMMAND_LANGUAGE_CODES:
                await bot.delete_my_commands(
                    scope=scope,
                    language_code=language_code,
                )
            await bot.set_my_commands(BOT_COMMANDS, scope=scope)
            await bot.set_chat_menu_button(
                chat_id=admin_id,
                menu_button=MenuButtonCommands(),
            )
        except TelegramBadRequest as exc:
            logger.warning("Unable to refresh command menu for admin %s: %s", admin_id, exc)


async def run_polling() -> None:
    if not settings.bot_token:
        raise RuntimeError("PROXYPULSE_BOT_TOKEN is required to run the bot.")
    if not settings.admin_telegram_ids:
        raise RuntimeError("PROXYPULSE_ADMIN_TELEGRAM_IDS must include at least one Telegram user id.")

    await init_db()

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await sync_bot_commands(bot)
    maintenance_task = asyncio.create_task(maintenance_loop(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        maintenance_task.cancel()
        await asyncio.gather(maintenance_task, return_exceptions=True)


def main() -> None:
    asyncio.run(run_polling())
