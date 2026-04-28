from __future__ import annotations

from datetime import date
from datetime import time

from .config import AppConfig
from .data import MarketDataProvider, overlay_intraday_on_daily
from .indicators import sma
from .models import Bar, ScanReport, SignalIdea, StrategyRun, TradePlan
from .persistence import NullScanPersistence, ScanPersistence
from .risk import ModerateRiskManager, filter_eligible_universe
from .scan_context import ScanContext
from .strategies import build_strategy_runtimes
from .universe import UniverseResolver


class ScannerEngine:
    def __init__(
        self,
        config: AppConfig,
        provider: MarketDataProvider,
        persistence: ScanPersistence | None = None,
        data_mode: str = "real",
    ):
        self.config = config
        self.provider = provider
        self.persistence = persistence or NullScanPersistence()
        self.universe = UniverseResolver(config)
        self.risk_manager = ModerateRiskManager(config.risk, config.portfolio)
        self.strategy_runtimes = build_strategy_runtimes(config, self.universe)
        self.data_mode = data_mode

    def scan(
        self,
        as_of: date,
        positions: list[dict] | None = None,
        weekly_pnl: list[float] | None = None,
        daily_pnl: list[float] | None = None,
        account_id: int = 1,
    ) -> ScanReport:
        symbols = list(self.universe.all_scan_symbols())
        histories = self.provider.load_histories(symbols, as_of, lookback=160)
        decision_time = _parse_hhmm(self.config.schedule.market_close_time)
        intraday_histories = self.provider.load_intraday_histories(
            symbols, as_of, decision_time
        )
        merged_histories = overlay_intraday_on_daily(histories, intraday_histories)
        eligible_histories, filtered_count = filter_eligible_universe(
            merged_histories,
            self.config.universe,
        )
        benchmark_history = eligible_histories.get(
            self.universe.benchmark_symbol,
            merged_histories.get(self.universe.benchmark_symbol, []),
        )
        market_regime = _market_regime(benchmark_history)

        context = ScanContext(
            config=self.config,
            universe=self.universe,
            account_id=account_id,
            as_of=as_of,
            universe_symbols=tuple(symbols),
            histories=eligible_histories,
            raw_histories=merged_histories,
            intraday_histories=intraday_histories,
            benchmark_symbol=self.universe.benchmark_symbol,
            benchmark_history=benchmark_history,
            market_regime=market_regime,
            data_mode=self.data_mode,
        )
        candidates: list[SignalIdea] = []
        strategy_runs: list[StrategyRun] = []
        for runtime in self.strategy_runtimes:
            ideas = runtime.strategy.generate(context)
            candidates.extend(ideas)
            strategy_runs.append(
                StrategyRun(
                    strategy_id=runtime.definition.strategy_id,
                    display_name=runtime.definition.display_name,
                    description=runtime.definition.description,
                    signal_group=runtime.definition.signal_group,
                    symbol_scope=runtime.symbol_scope,
                    signal_count=len(ideas),
                    notes=runtime.notes,
                )
            )

        trade_plans = self.risk_manager.build_trade_plans(candidates)

        # Circuit breaker: suppress entry signals if daily or weekly drawdown limit hit
        circuit_triggered = self.risk_manager.check_circuit_breaker(
            weekly_pnl or [], daily_pnl or []
        )
        if circuit_triggered:
            trade_plans = [p for p in trade_plans if p.action != "buy"]

        # Trailing stop + dynamic take-profit exits for existing positions
        exit_ideas = self.risk_manager.trailing_stop_exits(
            positions or [], eligible_histories, as_of
        )
        exit_ideas += self.risk_manager.trailing_take_profit_exits(
            positions or [], eligible_histories, as_of
        )
        exit_plans: list[TradePlan] = []
        seen_exit_symbols: set[str] = set()
        for idea in exit_ideas:
            if idea.symbol in seen_exit_symbols:
                continue
            seen_exit_symbols.add(idea.symbol)
            pos = next(
                (p for p in (positions or []) if p["symbol"] == idea.symbol), None
            )
            shares = pos.get("shares", 0) if pos else 0
            exit_plans.append(
                TradePlan(
                    strategy_id=idea.strategy_id,
                    signal_group=idea.signal_group,
                    action=idea.action,
                    symbol=idea.symbol,
                    name=idea.name,
                    asset_type=idea.asset_type,
                    sector=idea.sector,
                    as_of=idea.as_of,
                    score=idea.score,
                    entry_price=idea.entry_price,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    suggested_shares=shares,
                    suggested_value=round(shares * idea.entry_price, 2),
                    position_pct=0.0,
                    risk_amount=0.0,
                    reasons=idea.reasons,
                    tags=idea.tags,
                )
            )
        candidates_with_exits = tuple(candidates) + tuple(exit_ideas)
        all_plans = trade_plans + exit_plans

        report = ScanReport(
            as_of=as_of,
            benchmark_symbol=self.universe.benchmark_symbol,
            market_regime=market_regime,
            candidates=tuple(
                sorted(candidates_with_exits, key=lambda item: item.score, reverse=True)
            ),
            trade_plans=tuple(all_plans),
            strategy_runs=tuple(strategy_runs),
            filtered_count=filtered_count,
            circuit_triggered=circuit_triggered,
        )
        self.persistence.save_scan(context, report)
        return report


def _market_regime(benchmark_history: list[Bar]) -> str:
    if len(benchmark_history) < 60:
        return "unknown"
    closes = [bar.close for bar in benchmark_history]
    slow = sma(closes, 60)
    if slow is None:
        return "unknown"
    return "risk_on" if closes[-1] >= slow else "risk_off"


def _parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))
