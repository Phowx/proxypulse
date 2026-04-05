from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, TestCase

import httpx

from proxypulse.core.schemas import ExternalNetworkIdentityRequest
from proxypulse.services.external_notifications import (
    ExternalNotificationServiceError,
    format_external_network_identity_message,
    send_external_network_identity_notification,
)


class ExternalNotificationFormattingTests(TestCase):
    def test_format_external_network_identity_message_escapes_html(self) -> None:
        payload = ExternalNetworkIdentityRequest(
            event="network_identity",
            source="edge<1>",
            location="HK & SG",
            ipv4="1.2.3.4",
            domains=["a.example.com", "b.example.com"],
            notes='name="prod"',
        )

        message = format_external_network_identity_message(payload)

        self.assertIn("📡 <b>外部网络通知</b>", message)
        self.assertNotIn("来源：", message)
        self.assertIn("位置：HK &amp; SG", message)
        self.assertIn("域名：<code>a.example.com, b.example.com</code>", message)
        self.assertIn("备注：name=&quot;prod&quot;", message)


class ExternalNotificationDeliveryTests(IsolatedAsyncioTestCase):
    async def test_send_external_network_identity_notification_delivers_to_all_admins(self) -> None:
        calls: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.read().decode("utf-8"))
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        payload = ExternalNetworkIdentityRequest(
            event="network_identity",
            source="edge-01",
            ipv4="1.2.3.4",
        )
        async with httpx.AsyncClient(transport=transport) as client:
            delivered = await send_external_network_identity_notification(
                payload,
                bot_token="123456:ABCDEF",
                admin_ids={1001, 1002},
                client=client,
            )

        self.assertEqual(delivered, 2)
        self.assertEqual(len(calls), 2)

    async def test_send_external_network_identity_notification_surfaces_telegram_errors(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"description": "Bad Request: chat not found"})

        transport = httpx.MockTransport(handler)
        payload = ExternalNetworkIdentityRequest(
            event="network_identity",
            source="edge-01",
            ipv4="1.2.3.4",
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(ExternalNotificationServiceError) as exc:
                await send_external_network_identity_notification(
                    payload,
                    bot_token="123456:ABCDEF",
                    admin_ids={1001},
                    client=client,
                )

        self.assertEqual(exc.exception.status_code, 502)
        self.assertIn("Telegram API error", str(exc.exception))
