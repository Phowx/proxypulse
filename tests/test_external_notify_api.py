from __future__ import annotations

import os
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx

from proxypulse.api import main
from proxypulse.services.external_notifications import ExternalNotificationServiceError


class ExternalNotifyApiTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.previous_admin_ids = os.environ.get("PROXYPULSE_ADMIN_TELEGRAM_IDS")
        os.environ["PROXYPULSE_ADMIN_TELEGRAM_IDS"] = "1001,1002"
        main.settings.external_notify_secret = "topsecret"
        main.settings.bot_token = "123456:ABCDEF"
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        if self.previous_admin_ids is None:
            os.environ.pop("PROXYPULSE_ADMIN_TELEGRAM_IDS", None)
        else:
            os.environ["PROXYPULSE_ADMIN_TELEGRAM_IDS"] = self.previous_admin_ids

    async def test_network_identity_rejects_missing_authorization(self) -> None:
        response = await self.client.post("/integrations/network-identity", json={})

        self.assertEqual(response.status_code, 401)

    async def test_network_identity_rejects_invalid_payload(self) -> None:
        response = await self.client.post(
            "/integrations/network-identity",
            headers={"Authorization": "Bearer topsecret"},
            json={"event": "network_identity", "source": "edge-01"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("At least one", response.json()["detail"])

    async def test_network_identity_sends_notification(self) -> None:
        with patch.object(main, "send_external_network_identity_notification", return_value=2) as mocked_send:
            response = await self.client.post(
                "/integrations/network-identity",
                headers={"Authorization": "Bearer topsecret"},
                json={
                    "event": "network_identity",
                    "source": "edge-01",
                    "ipv4": "1.2.3.4",
                    "domains": ["a.example.com"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "sent", "delivered_to": 2})
        mocked_send.assert_awaited_once()

    async def test_network_identity_maps_telegram_failure_to_502(self) -> None:
        with patch.object(
            main,
            "send_external_network_identity_notification",
            side_effect=ExternalNotificationServiceError("Telegram API error: chat not found", status_code=502),
        ):
            response = await self.client.post(
                "/integrations/network-identity",
                headers={"Authorization": "Bearer topsecret"},
                json={
                    "event": "network_identity",
                    "source": "edge-01",
                    "location": "Los Angeles, US",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Telegram API error", response.json()["detail"])
