from __future__ import annotations

import json
import os
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import httpx

from proxypulse.core.config import CloudflareZoneConfig, Settings
from proxypulse.services.cloudflare_dns import CloudflareDNSService, CloudflareServiceError


class CloudflareConfigTests(TestCase):
    def test_report_defaults_match_daily_digest_schedule(self) -> None:
        settings = Settings()

        self.assertEqual(settings.report_timezone, "Asia/Shanghai")
        self.assertEqual(settings.daily_report_hour, 9)
        self.assertEqual(settings.daily_report_minute, 0)

    def test_cloudflare_zones_parse_json_mapping(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PROXYPULSE_CLOUDFLARE_ZONES": json.dumps(
                    {
                        "main": {"zone_id": "zone-1", "zone_name": "example.com"},
                        "lab": {"zone_id": "zone-2", "zone_name": "lab.example.com"},
                    }
                )
            },
            clear=False,
        ):
            settings = Settings()
            zones = settings.cloudflare_zones

        self.assertEqual(zones["main"].zone_id, "zone-1")
        self.assertEqual(zones["lab"].zone_name, "lab.example.com")

    def test_cloudflare_zones_reject_invalid_shape(self) -> None:
        with patch.dict(
            os.environ,
            {"PROXYPULSE_CLOUDFLARE_ZONES": json.dumps({"main": {"zone_id": "zone-1"}})},
            clear=False,
        ):
            settings = Settings()
            with self.assertRaises(ValueError):
                _ = settings.cloudflare_zones


class CloudflareDNSServiceTests(IsolatedAsyncioTestCase):
    async def test_list_records_and_mutations(self) -> None:
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, str(request.url)))
            if request.method == "GET" and request.url.path.endswith("/dns_records"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "result": [
                            {
                                "id": "rec-1",
                                "type": "A",
                                "name": "api.example.com",
                                "content": "1.2.3.4",
                                "ttl": 1,
                                "proxied": True,
                            },
                            {
                                "id": "rec-2",
                                "type": "TXT",
                                "name": "txt.example.com",
                                "content": "hello",
                                "ttl": 300,
                                "proxied": None,
                            },
                        ],
                        "result_info": {"total_pages": 1},
                    },
                )
            if request.method == "GET" and request.url.path.endswith("/dns_records/rec-1"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "result": {
                            "id": "rec-1",
                            "type": "A",
                            "name": "api.example.com",
                            "content": "1.2.3.4",
                            "ttl": 1,
                            "proxied": True,
                        },
                    },
                )
            if request.method == "POST":
                body = json.loads(request.content.decode())
                return httpx.Response(200, json={"success": True, "result": {"id": "rec-3", **body}})
            if request.method == "PUT":
                body = json.loads(request.content.decode())
                return httpx.Response(200, json={"success": True, "result": {"id": "rec-1", **body}})
            if request.method == "DELETE":
                return httpx.Response(200, json={"success": True, "result": {"id": "rec-1"}})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = CloudflareDNSService(
                api_token="token",
                zones={"main": CloudflareZoneConfig(key="main", zone_id="zone-1", zone_name="example.com")},
                client=client,
            )
            record_page = await service.list_dns_records("main", page=1, per_page=10)
            created = await service.create_dns_record(
                "main",
                record_type="A",
                name="new.example.com",
                content="5.6.7.8",
                ttl=1,
                proxied=True,
            )
            updated = await service.update_dns_record(
                "main",
                record_id="rec-1",
                record_type="A",
                name="api.example.com",
                content="8.8.8.8",
                ttl=300,
                proxied=False,
            )
            await service.delete_dns_record("main", "rec-1")

        self.assertEqual(record_page.total_count, 2)
        self.assertEqual(record_page.records[0].name, "api.example.com")
        self.assertEqual(created.id, "rec-3")
        self.assertEqual(updated.content, "8.8.8.8")
        self.assertEqual([method for method, _ in calls], ["GET", "POST", "PUT", "DELETE"])

    async def test_service_reports_cloudflare_errors(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"success": False, "errors": [{"code": 1004, "message": "invalid record"}]},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = CloudflareDNSService(
                api_token="token",
                zones={"main": CloudflareZoneConfig(key="main", zone_id="zone-1", zone_name="example.com")},
                client=client,
            )
            with self.assertRaises(CloudflareServiceError):
                await service.get_dns_record("main", "rec-1")
