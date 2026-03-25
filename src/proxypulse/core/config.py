from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    admin_telegram_ids_raw: str = Field(default="", alias="admin_telegram_ids")
    poll_interval_seconds: int = 15
    agent_name: str = Field(default_factory=socket.gethostname)
    agent_state_path: Path = Path("./agent-state.json")
    agent_enrollment_token: str = ""
    network_interface: str = ""
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
