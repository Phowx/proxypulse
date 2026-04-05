from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from proxypulse.core.config import get_settings
from proxypulse.core.models import AppSetting

settings = get_settings()
DAILY_REPORT_TIME_KEY = "daily_report_time"


class ReportScheduleError(RuntimeError):
    """Business-layer exception for daily report schedule settings."""


@dataclass(slots=True)
class DailyReportSchedule:
    hour: int
    minute: int
    timezone: str

    @property
    def time_value(self) -> time:
        return time(self.hour, self.minute)

    @property
    def clock_text(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


def parse_daily_report_clock(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ReportScheduleError("时间格式必须是 HH:MM。") from exc

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ReportScheduleError("时间格式必须是 HH:MM。")
    return hour, minute


async def get_daily_report_schedule(session: AsyncSession) -> DailyReportSchedule:
    result = await session.execute(select(AppSetting).where(AppSetting.key == DAILY_REPORT_TIME_KEY))
    setting = result.scalar_one_or_none()
    if setting is None:
        return DailyReportSchedule(
            hour=settings.daily_report_hour,
            minute=settings.daily_report_minute,
            timezone=settings.report_timezone,
        )

    try:
        hour, minute = parse_daily_report_clock(setting.value)
    except ReportScheduleError:
        return DailyReportSchedule(
            hour=settings.daily_report_hour,
            minute=settings.daily_report_minute,
            timezone=settings.report_timezone,
        )

    return DailyReportSchedule(hour=hour, minute=minute, timezone=settings.report_timezone)


async def set_daily_report_schedule(session: AsyncSession, *, hour: int, minute: int) -> DailyReportSchedule:
    schedule_value = f"{hour:02d}:{minute:02d}"
    result = await session.execute(select(AppSetting).where(AppSetting.key == DAILY_REPORT_TIME_KEY))
    setting = result.scalar_one_or_none()
    if setting is None:
        session.add(AppSetting(key=DAILY_REPORT_TIME_KEY, value=schedule_value))
    else:
        setting.value = schedule_value
    await session.commit()
    return DailyReportSchedule(hour=hour, minute=minute, timezone=settings.report_timezone)
