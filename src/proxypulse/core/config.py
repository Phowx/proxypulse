from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True, slots=True)
class CloudflareZoneConfig:
    key: str
    zone_id: str
    zone_name: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PROXYPULSE_",
        extra="ignore",
    )

    app_name: str = "ProxyPulse"
    database_url: str = "sqlite+aiosqlite:///./proxypulse.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    server_url: str = "http://localhost:8080"
    bot_token: str = ""
    external_notify_secret: str = ""
    cloudflare_api_token: str = ""
    cloudflare_zones_raw: str = Field(default="", alias="cloudflare_zones")
    admin_telegram_ids_raw: str = Field(default="", alias="admin_telegram_ids")
    poll_interval_seconds: int = 15
    agent_name: str = Field(default_factory=socket.gethostname)
    agent_state_path: Path = Path("./agent-state.json")
    agent_enrollment_token: str = ""
    network_interface: str = ""
    network_interface_strategy: Literal["auto", "fixed", "aggregate"] = "auto"
    request_timeout_seconds: float = 10.0
    offline_after_seconds: int = 90
    alert_scan_interval_seconds: int = 30
    resource_alerts_enabled: bool = True
    cpu_alert_threshold: float = 85.0
    memory_alert_threshold: float = 90.0
    disk_alert_threshold: float = 90.0
    report_timezone: str = "Asia/Shanghai"
    daily_report_hour: int = 9
    daily_report_minute: int = 0

    @property
    def admin_telegram_ids(self) -> set[int]:
        raw_value = os.getenv("PROXYPULSE_ADMIN_TELEGRAM_IDS", self.admin_telegram_ids_raw)
        values = set()
        for value in raw_value.split(","):
            value = value.strip()
            if value:
                values.add(int(value))
        return values

    @property
    def cloudflare_zones(self) -> dict[str, CloudflareZoneConfig]:
        raw_value = os.getenv("PROXYPULSE_CLOUDFLARE_ZONES", self.cloudflare_zones_raw).strip()
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ValueError("PROXYPULSE_CLOUDFLARE_ZONES must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("PROXYPULSE_CLOUDFLARE_ZONES must be a JSON object.")
        zones: dict[str, CloudflareZoneConfig] = {}
        for key, value in parsed.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("PROXYPULSE_CLOUDFLARE_ZONES keys must be non-empty strings.")
            if not isinstance(value, dict):
                raise ValueError(f"Cloudflare zone '{key}' must be a JSON object.")
            zone_id = str(value.get("zone_id", "")).strip()
            zone_name = str(value.get("zone_name", "")).strip()
            if not zone_id or not zone_name:
                raise ValueError(f"Cloudflare zone '{key}' must include zone_id and zone_name.")
            zones[key] = CloudflareZoneConfig(key=key, zone_id=zone_id, zone_name=zone_name)
        return zones


@lru_cache
def get_settings() -> Settings:
    return Settings()
