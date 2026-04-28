from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError
import logging

from .calendar import TradingCalendar, build_trading_calendar
from .config import AppConfig
from .main import run_scan_once


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    should_run: bool
    as_of: date
    reason: str
    now_local: datetime


def evaluate_schedule(
    config: AppConfig,
    data_source: str,
    now: datetime | None = None,
    calendar: TradingCalendar | None = None,
    date_override: date | None = None,
    force: bool = False,
) -> ScheduleDecision:
    tz = resolve_timezone(config.schedule.timezone)
    now_local = now.astimezone(tz) if now else datetime.now(tz)
    as_of = date_override or now_local.date()

    if force:
        return ScheduleDecision(True, as_of, "forced run", now_local)

    calendar = calendar or build_trading_calendar(data_source)
    if config.schedule.skip_non_trading_day and not calendar.is_trading_day(as_of):
        return ScheduleDecision(False, as_of, f"{as_of.isoformat()} is not a trading day", now_local)

    if date_override is None and config.schedule.enforce_after_close:
        if now_local.time() < parse_hhmm(config.schedule.market_close_time):
            return ScheduleDecision(
                False,
                as_of,
                f"current time {now_local.strftime('%H:%M')} is earlier than market close gate {config.schedule.market_close_time}",
                now_local,
            )

    return ScheduleDecision(True, as_of, "schedule conditions satisfied", now_local)


def run_scheduled_scan(
    config: AppConfig,
    data_source: str,
    csv_path: str | None = None,
    should_send_email: bool = True,
    now: datetime | None = None,
    date_override: date | None = None,
    force: bool = False,
) -> tuple[int, str]:
    logger = configure_logging(config)
    decision = evaluate_schedule(
        config=config,
        data_source=data_source,
        now=now,
        date_override=date_override,
        force=force,
    )
    logger.info("schedule decision: %s", decision.reason)
    if not decision.should_run:
        return 0, decision.reason

    text_body, subject = run_scan_once(
        config=config,
        as_of=decision.as_of,
        data_source=data_source,
        csv_path=csv_path,
        should_send_email=should_send_email,
    )
    logger.info("scan completed: %s", subject)
    return 0, text_body


def configure_logging(config: AppConfig) -> logging.Logger:
    logs_dir = Path(config.schedule.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("chaogu_alert.scheduler")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = logs_dir / "scheduled_scan.log"
    if not any(
        isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(handler)

    return logger


def parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def resolve_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Shanghai", "PRC", "Asia/Chongqing"}:
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        return timezone.utc
