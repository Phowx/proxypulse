from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

import httpx

from proxypulse.core.schemas import ExternalNetworkIdentityRequest


@dataclass(slots=True)
class ExternalNotificationServiceError(RuntimeError):
    message: str
    status_code: int = 500

    def __str__(self) -> str:
        return self.message


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_observed_at(value: datetime | None) -> str:
    observed_at = _ensure_aware(value or datetime.now(UTC))
    return observed_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def format_external_network_identity_message(payload: ExternalNetworkIdentityRequest) -> str:
    lines = [
        "📡 <b>外部网络通知</b>",
        f"时间：{escape(_format_observed_at(payload.observed_at))}",
    ]
    if payload.location:
        lines.append(f"位置：{escape(payload.location)}")
    if payload.ipv4:
        lines.append(f"IPv4：<code>{escape(payload.ipv4)}</code>")
    if payload.ipv6:
        lines.append(f"IPv6：<code>{escape(payload.ipv6)}</code>")
    if payload.domains:
        domain_list = ", ".join(payload.domains)
        lines.append(f"域名：<code>{escape(domain_list)}</code>")
    if payload.notes:
        lines.append(f"备注：{escape(payload.notes)}")
    return "\n".join(lines)


async def send_external_network_identity_notification(
    payload: ExternalNetworkIdentityRequest,
    *,
    bot_token: str,
    admin_ids: set[int],
    client: httpx.AsyncClient | None = None,
) -> int:
    if not bot_token:
        raise ExternalNotificationServiceError("PROXYPULSE_BOT_TOKEN is not configured.", status_code=500)
    if not admin_ids:
        raise ExternalNotificationServiceError("PROXYPULSE_ADMIN_TELEGRAM_IDS must include at least one id.", status_code=500)

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    message = format_external_network_identity_message(payload)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    delivered = 0

    try:
        for admin_id in sorted(admin_ids):
            try:
                response = await http_client.post(
                    telegram_url,
                    json={
                        "chat_id": admin_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
            except httpx.HTTPError as exc:
                raise ExternalNotificationServiceError(f"Telegram request failed: {exc}", status_code=502) from exc

            if not response.is_success:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                description = payload.get("description") or response.text or "Unknown Telegram API error."
                raise ExternalNotificationServiceError(f"Telegram API error: {description}", status_code=502)
            delivered += 1
    finally:
        if owns_client:
            await http_client.aclose()

    return delivered
