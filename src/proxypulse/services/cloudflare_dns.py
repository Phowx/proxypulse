from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

import httpx

from proxypulse.core.config import CloudflareZoneConfig, Settings, get_settings

SUPPORTED_DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "TXT")


class CloudflareServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CloudflareDNSRecord:
    id: str
    type: str
    name: str
    content: str
    ttl: int
    proxied: bool | None
    comment: str | None = None
    modified_on: str | None = None


@dataclass(frozen=True, slots=True)
class CloudflareDNSRecordPage:
    zone: CloudflareZoneConfig
    records: list[CloudflareDNSRecord]
    page: int
    per_page: int
    total_count: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class CloudflareZoneSummary:
    key: str
    zone_id: str
    zone_name: str


class CloudflareDNSService:
    base_url = "https://api.cloudflare.com/client/v4"

    def __init__(
        self,
        *,
        api_token: str,
        zones: dict[str, CloudflareZoneConfig],
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_token = api_token.strip()
        self.zones = zones
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._last_result_info: dict[str, Any] | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> CloudflareDNSService:
        resolved = settings or get_settings()
        return cls(
            api_token=resolved.cloudflare_api_token,
            zones=resolved.cloudflare_zones,
            timeout_seconds=resolved.request_timeout_seconds,
            client=client,
        )

    def ensure_configured(self) -> None:
        if not self.api_token:
            raise CloudflareServiceError("未配置 PROXYPULSE_CLOUDFLARE_API_TOKEN。")
        if not self.zones:
            raise CloudflareServiceError("未配置 PROXYPULSE_CLOUDFLARE_ZONES。")

    def list_configured_zones(self) -> list[CloudflareZoneSummary]:
        self.ensure_configured()
        return [
            CloudflareZoneSummary(key=zone.key, zone_id=zone.zone_id, zone_name=zone.zone_name)
            for zone in sorted(self.zones.values(), key=lambda item: item.zone_name)
        ]

    def get_zone(self, zone_key: str) -> CloudflareZoneConfig:
        self.ensure_configured()
        zone = self.zones.get(zone_key)
        if zone is None:
            raise CloudflareServiceError("未找到对应的 Zone 配置。")
        return zone

    async def list_dns_records(self, zone_key: str, *, page: int = 1, per_page: int = 10) -> CloudflareDNSRecordPage:
        zone = self.get_zone(zone_key)
        records = await self._list_all_zone_records(zone)
        page = max(page, 1)
        total_count = len(records)
        total_pages = max(ceil(total_count / per_page), 1)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        end = start + per_page
        return CloudflareDNSRecordPage(
            zone=zone,
            records=records[start:end],
            page=page,
            per_page=per_page,
            total_count=total_count,
            total_pages=total_pages,
        )

    async def get_dns_record(self, zone_key: str, record_id: str) -> CloudflareDNSRecord:
        zone = self.get_zone(zone_key)
        payload = await self._request("GET", f"/zones/{zone.zone_id}/dns_records/{record_id}")
        return self._parse_record(payload)

    async def create_dns_record(
        self,
        zone_key: str,
        *,
        record_type: str,
        name: str,
        content: str,
        ttl: int,
        proxied: bool | None,
    ) -> CloudflareDNSRecord:
        zone = self.get_zone(zone_key)
        payload = await self._request(
            "POST",
            f"/zones/{zone.zone_id}/dns_records",
            json=self._build_record_payload(
                record_type=record_type,
                name=name,
                content=content,
                ttl=ttl,
                proxied=proxied,
            ),
        )
        return self._parse_record(payload)

    async def update_dns_record(
        self,
        zone_key: str,
        *,
        record_id: str,
        record_type: str,
        name: str,
        content: str,
        ttl: int,
        proxied: bool | None,
    ) -> CloudflareDNSRecord:
        zone = self.get_zone(zone_key)
        payload = await self._request(
            "PUT",
            f"/zones/{zone.zone_id}/dns_records/{record_id}",
            json=self._build_record_payload(
                record_type=record_type,
                name=name,
                content=content,
                ttl=ttl,
                proxied=proxied,
            ),
        )
        return self._parse_record(payload)

    async def delete_dns_record(self, zone_key: str, record_id: str) -> None:
        zone = self.get_zone(zone_key)
        await self._request("DELETE", f"/zones/{zone.zone_id}/dns_records/{record_id}")

    async def _list_all_zone_records(self, zone: CloudflareZoneConfig) -> list[CloudflareDNSRecord]:
        page = 1
        records: list[CloudflareDNSRecord] = []
        total_pages = 1
        while page <= total_pages:
            payload = await self._request(
                "GET",
                f"/zones/{zone.zone_id}/dns_records",
                params={"page": page, "per_page": 100, "order": "name", "direction": "asc"},
            )
            page_records = [self._parse_record(item) for item in payload]
            records.extend(record for record in page_records if record.type in SUPPORTED_DNS_RECORD_TYPES)
            result_info = self._last_result_info or {}
            total_pages = int(result_info.get("total_pages") or 1)
            page += 1
        return sorted(records, key=lambda item: (item.name.lower(), item.type, item.content.lower()))

    def _build_record_payload(
        self,
        *,
        record_type: str,
        name: str,
        content: str,
        ttl: int,
        proxied: bool | None,
    ) -> dict[str, Any]:
        normalized_type = record_type.upper()
        if normalized_type not in SUPPORTED_DNS_RECORD_TYPES:
            raise CloudflareServiceError(f"暂不支持 {record_type} 记录。")
        payload: dict[str, Any] = {
            "type": normalized_type,
            "name": name.strip(),
            "content": content.strip(),
            "ttl": ttl,
        }
        if not payload["name"] or not payload["content"]:
            raise CloudflareServiceError("记录名称和值不能为空。")
        if normalized_type in {"A", "AAAA", "CNAME"}:
            payload["proxied"] = bool(proxied)
        return payload

    def _parse_record(self, payload: dict[str, Any]) -> CloudflareDNSRecord:
        return CloudflareDNSRecord(
            id=str(payload.get("id", "")),
            type=str(payload.get("type", "")),
            name=str(payload.get("name", "")),
            content=str(payload.get("content", "")),
            ttl=int(payload.get("ttl", 1) or 1),
            proxied=payload.get("proxied"),
            comment=payload.get("comment"),
            modified_on=payload.get("modified_on"),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        self.ensure_configured()
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        if self._client is not None:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                json=json,
            )
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    params=params,
                    json=json,
                )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudflareServiceError("Cloudflare 返回了无法解析的响应。") from exc
        if not response.is_success:
            raise CloudflareServiceError(self._extract_error_message(payload))
        if payload.get("success") is False:
            raise CloudflareServiceError(self._extract_error_message(payload))
        self._last_result_info = payload.get("result_info") or {}
        return payload.get("result")

    @staticmethod
    def _extract_error_message(payload: dict[str, Any]) -> str:
        errors = payload.get("errors") or []
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = str(first.get("message") or "").strip()
                code = first.get("code")
                if code and message:
                    return f"Cloudflare 错误 {code}: {message}"
                if message:
                    return f"Cloudflare 错误: {message}"
        if isinstance(payload.get("messages"), list) and payload["messages"]:
            return f"Cloudflare 错误: {payload['messages'][0]}"
        return "Cloudflare 请求失败。"
