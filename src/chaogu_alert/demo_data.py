from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import sin

from .models import Bar, MinuteBar


@dataclass(frozen=True, slots=True)
class DemoProfile:
    name: str
    asset_type: str
    sector: str
    base: float
    trend: float
    wave: float
    cycle: float
    phase: float
    volume: float
    intraday_bias: float


DEMO_PROFILES: dict[str, DemoProfile] = {
    "510300": DemoProfile("CSI300 ETF", "broad_etf", "broad", 3.72, 0.17, 0.018, 7.5, 0.4, 9_000_000, -0.006),
    "510500": DemoProfile("CSI500 ETF", "broad_etf", "broad", 6.10, 0.09, 0.018, 8.0, 0.8, 7_000_000, 0.010),
    "515080": DemoProfile("Dividend LowVol ETF", "broad_etf", "dividend", 1.18, 0.12, 0.012, 9.0, 0.2, 5_000_000, -0.004),
    "159915": DemoProfile("ChiNext ETF", "broad_etf", "growth", 3.08, 0.21, 0.020, 7.8, 0.6, 9_500_000, 0.009),
    "588000": DemoProfile("STAR50 ETF", "broad_etf", "star50", 1.12, 0.19, 0.021, 8.3, 1.1, 8_900_000, 0.007),
    "512880": DemoProfile("Brokerage ETF", "sector_etf", "brokerage", 1.05, 0.32, 0.018, 8.2, 0.2, 8_600_000, 0.012),
    "516160": DemoProfile("NewEnergy ETF", "sector_etf", "new_energy", 0.82, 0.24, 0.020, 8.6, 0.5, 8_000_000, -0.011),
    "512660": DemoProfile("Defense ETF", "sector_etf", "defense", 0.93, 0.18, 0.018, 8.9, 0.7, 6_500_000, 0.008),
    "512010": DemoProfile("Healthcare ETF", "sector_etf", "healthcare", 0.66, 0.03, 0.018, 8.4, 2.0, 4_600_000, -0.003),
    "159852": DemoProfile("Software ETF", "sector_etf", "software", 0.89, 0.23, 0.024, 8.0, 0.3, 5_600_000, 0.012),
    "159995": DemoProfile("Chip ETF", "sector_etf", "chip", 1.46, 0.29, 0.026, 7.7, 0.9, 9_200_000, 0.015),
    "159819": DemoProfile("AI ETF", "sector_etf", "artificial_intelligence", 1.28, 0.27, 0.024, 7.9, 1.4, 8_300_000, 0.013),
    "159825": DemoProfile("Agriculture ETF", "sector_etf", "agriculture", 0.81, 0.10, 0.015, 9.5, 0.5, 4_200_000, -0.005),
    "520500": DemoProfile("Hang Seng Innovative Drug ETF", "sector_etf", "innovative_drug", 1.52, 0.25, 0.028, 7.6, 0.8, 7_400_000, 0.011),
}


def generate_demo_histories(
    symbols: list[str], as_of: date, sessions: int = 140
) -> dict[str, list[Bar]]:
    return {symbol: generate_demo_history(symbol, as_of, sessions) for symbol in symbols}


def generate_demo_history(symbol: str, as_of: date, sessions: int = 140) -> list[Bar]:
    profile = DEMO_PROFILES[symbol]
    bars: list[Bar] = []

    for index, trading_day in enumerate(_trading_days(as_of, sessions)):
        trend_component = 1.0 + profile.trend * index / 100.0
        wave_component = profile.wave * sin(index / profile.cycle + profile.phase)
        close = round(profile.base * (trend_component + wave_component), 3)
        open_price = round(close * (1.0 + 0.002 * sin(index / 5.0 + profile.phase)), 3)
        high = round(max(close, open_price) * (1.005 + 0.002 * abs(sin(index / 4.0))), 3)
        low = round(min(close, open_price) * (0.995 - 0.001 * abs(sin(index / 3.7))), 3)
        volume = int(
            profile.volume
            * (1.0 + 0.14 * sin(index / 4.2 + profile.phase) + 0.15 * profile.trend)
        )

        bars.append(
            Bar(
                symbol=symbol,
                name=profile.name,
                asset_type=profile.asset_type,
                sector=profile.sector,
                date=trading_day,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                is_st=False,
                listing_days=900,
            )
        )

    return bars


def generate_demo_intraday_histories(
    symbols: list[str], as_of: date, decision_time: time
) -> dict[str, list[MinuteBar]]:
    histories: dict[str, list[MinuteBar]] = {}
    for symbol in symbols:
        daily_bars = generate_demo_history(symbol, as_of, sessions=160)
        latest = daily_bars[-1]
        previous_close = daily_bars[-2].close
        profile = DEMO_PROFILES[symbol]
        histories[symbol] = _generate_intraday_path(
            symbol=symbol,
            trading_day=as_of,
            open_price=latest.open,
            target_close=latest.close,
            previous_close=previous_close,
            day_bias=profile.intraday_bias,
            volume=latest.volume,
            decision_time=decision_time,
        )
    return histories


def _generate_intraday_path(
    symbol: str,
    trading_day: date,
    open_price: float,
    target_close: float,
    previous_close: float,
    day_bias: float,
    volume: float,
    decision_time: time,
) -> list[MinuteBar]:
    start = datetime.combine(trading_day, time(9, 30))
    minutes = []
    total_minutes = ((decision_time.hour * 60 + decision_time.minute) - (9 * 60 + 30)) + 1
    for index in range(max(total_minutes, 1)):
        timestamp = start + timedelta(minutes=index)
        progress = index / max(total_minutes - 1, 1)
        baseline = open_price + (target_close - open_price) * progress
        curve = day_bias * (progress - 0.35)
        wave = 0.002 * sin(index / 21.0)
        relative_to_prev = 0.001 * ((baseline / max(previous_close, 0.001)) - 1.0)
        price = round(baseline * (1.0 + curve + wave + relative_to_prev), 3)
        minute_open = round((open_price if index == 0 else minutes[-1].close), 3)
        minute_high = round(max(minute_open, price) * 1.0015, 3)
        minute_low = round(min(minute_open, price) * 0.9985, 3)
        avg_price = round((minute_open + price + minute_high + minute_low) / 4.0, 3)
        minute_volume = float(int(volume / max(total_minutes, 1)))
        minutes.append(
            MinuteBar(
                symbol=symbol,
                timestamp=timestamp,
                open=minute_open,
                high=minute_high,
                low=minute_low,
                close=price,
                volume=minute_volume,
                avg_price=avg_price,
            )
        )
    return minutes


def _trading_days(as_of: date, sessions: int) -> list[date]:
    days: list[date] = []
    cursor = as_of
    while len(days) < sessions:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days
