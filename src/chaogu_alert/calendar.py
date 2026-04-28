from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from .data import _frame_records

TRADE_DATE = "trade_date"


class TradingCalendar(Protocol):
    def is_trading_day(self, target: date) -> bool:
        ...


class WeekdayTradingCalendar:
    def is_trading_day(self, target: date) -> bool:
        return target.weekday() < 5


class AkshareTradingCalendar:
    def __init__(self, client=None):
        self.client = client or _import_akshare()
        self._cache: set[date] | None = None

    def is_trading_day(self, target: date) -> bool:
        cache = self._load_cache()
        if not cache:
            return target.weekday() < 5
        if target > max(cache):
            return target.weekday() < 5
        return target in cache

    def _load_cache(self) -> set[date]:
        if self._cache is not None:
            return self._cache

        try:
            frame = self.client.tool_trade_date_hist_sina()
        except Exception:
            self._cache = set()
            return self._cache

        parsed: set[date] = set()
        for row in _frame_records(frame):
            value = row.get(TRADE_DATE)
            if value is None:
                continue
            parsed.add(_parse_date(value))

        self._cache = parsed
        return self._cache


def build_trading_calendar(data_source: str, client=None) -> TradingCalendar:
    if data_source in {"akshare", "sina", "auto"}:
        return AkshareTradingCalendar(client=client)
    return WeekdayTradingCalendar()


def _import_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError(
            "AkShare is not installed. Run `pip install .[akshare]` or `pip install akshare`."
        ) from exc
    return ak


def _parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    if " " in text:
        text = text.split(" ", 1)[0]
    return datetime.strptime(text, "%Y-%m-%d").date()
