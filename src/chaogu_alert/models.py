from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    name: str
    asset_type: str
    sector: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_st: bool = False
    listing_days: int = 365


@dataclass(frozen=True, slots=True)
class MinuteBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    avg_price: float | None = None


@dataclass(frozen=True, slots=True)
class SignalIdea:
    strategy_id: str
    signal_group: str
    action: str
    symbol: str
    name: str
    asset_type: str
    sector: str
    as_of: date
    score: float
    entry_price: float
    stop_loss: float
    take_profit: float
    reasons: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TradePlan:
    strategy_id: str
    signal_group: str
    action: str
    symbol: str
    name: str
    asset_type: str
    sector: str
    as_of: date
    score: float
    entry_price: float
    stop_loss: float
    take_profit: float
    suggested_shares: int
    suggested_value: float
    position_pct: float
    risk_amount: float
    estimated_commission: float = 0.0
    reasons: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyRun:
    strategy_id: str
    display_name: str
    description: str
    signal_group: str
    symbol_scope: tuple[str, ...]
    signal_count: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScanReport:
    as_of: date
    benchmark_symbol: str
    market_regime: str
    candidates: tuple[SignalIdea, ...]
    trade_plans: tuple[TradePlan, ...]
    strategy_runs: tuple[StrategyRun, ...]
    filtered_count: int
    circuit_triggered: bool = False
