from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .models import Bar, MinuteBar

if TYPE_CHECKING:
    from .config import AppConfig
    from .universe import UniverseResolver


@dataclass(frozen=True, slots=True)
class ScanContext:
    config: AppConfig
    universe: UniverseResolver
    account_id: int
    as_of: date
    universe_symbols: tuple[str, ...]
    histories: dict[str, list[Bar]]
    raw_histories: dict[str, list[Bar]]
    intraday_histories: dict[str, list[MinuteBar]]
    benchmark_symbol: str
    benchmark_history: list[Bar]
    market_regime: str
    data_mode: str = "real"
