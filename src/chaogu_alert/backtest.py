from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
import logging

from .config import AppConfig, with_account
from .data import MarketDataProvider, overlay_intraday_on_daily
from .engine import ScannerEngine
from .indicators import atr
from .models import Bar, TradePlan
from .persistence import NullScanPersistence
from .risk import ModerateRiskManager, filter_eligible_universe
from .scan_context import ScanContext
from .strategies import build_strategy_runtimes
from .universe import UniverseResolver

_logger = logging.getLogger(__name__)


@dataclass
class BacktestPosition:
    symbol: str
    name: str
    entry_date: date
    entry_price: float
    shares: int
    stop_loss: float
    take_profit: float
    strategy_id: str
    signal_group: str
    sector: str = ""

    @property
    def entry_value(self) -> float:
        return self.shares * self.entry_price


@dataclass
class BacktestTrade:
    symbol: str
    action: str
    entry_date: date
    exit_date: date | None
    entry_price: float
    exit_price: float
    shares: int
    pnl_amount: float
    pnl_pct: float
    strategy_id: str
    exit_reason: str


@dataclass
class BacktestResult:
    start_date: date
    end_date: date
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float | None
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    daily_signals: list[dict] = field(default_factory=list)


class BacktestRunner:
    def __init__(self, config: AppConfig, provider: MarketDataProvider):
        self.config = with_account(config)
        self.provider = provider
        self.universe = UniverseResolver(self.config)
        self.risk_manager = ModerateRiskManager(self.config.risk, self.config.portfolio)

    def run(self, start: date, end: date) -> BacktestResult:
        symbols = list(self.universe.all_scan_symbols())
        if not symbols:
            raise ValueError("no symbols in universe")

        days_needed = (end - start).days
        lookback = max(400, int(days_needed * 1.1))
        histories = self.provider.load_histories(symbols, end, lookback=lookback)
        trading_days = self._trading_days(histories, start, end)
        if len(trading_days) < 10:
            raise ValueError(f"only {len(trading_days)} trading days in range")

        capital = self.config.risk.capital
        cash = capital
        positions: list[BacktestPosition] = []
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[date, float]] = []
        peak_equity = capital
        max_drawdown = 0.0
        pending_buys: list[TradePlan] = []
        daily_signals: list[dict] = []

        for i, day in enumerate(trading_days):
            day_histories = {
                sym: [b for b in bars if b.date <= day]
                for sym, bars in histories.items()
            }

            # 1. Execute pending buys from previous day at today's open
            for plan in pending_buys:
                executed = 1
                skip_reason = ""
                if any(p.symbol == plan.symbol for p in positions):
                    executed = 0
                    skip_reason = "dedup"
                elif len(positions) >= self.config.risk.max_positions:
                    executed = 0
                    skip_reason = "max_positions"
                else:
                    day_bar = self._find_bar(histories.get(plan.symbol, []), day)
                    if day_bar is None or day_bar.open <= 0:
                        executed = 0
                        skip_reason = "no_price"
                    else:
                        entry_price = day_bar.open
                        trade_value = round(plan.suggested_shares * entry_price, 2)
                        if trade_value > cash * 0.85:
                            executed = 0
                            skip_reason = "insufficient_cash"
                        else:
                            cash -= trade_value
                            stop_ratio = plan.stop_loss / plan.entry_price if plan.entry_price else 0.95
                            tp_ratio = plan.take_profit / plan.entry_price if plan.entry_price else 1.10
                            positions.append(
                                BacktestPosition(
                                    symbol=plan.symbol,
                                    name=plan.name,
                                    entry_date=day,
                                    entry_price=entry_price,
                                    shares=plan.suggested_shares,
                                    stop_loss=round(entry_price * stop_ratio, 3),
                                    take_profit=round(entry_price * tp_ratio, 3),
                                    strategy_id=plan.strategy_id,
                                    signal_group=plan.signal_group,
                                    sector=plan.sector,
                                )
                            )
                daily_signals.append(
                    _plan_to_signal(plan, day, run_id=0, executed=executed, skip_reason=skip_reason)
                )
            pending_buys.clear()

            # 2. Mark positions to market (including today's new entries)
            positions, closed_trades, cash = self._settle_positions(
                positions, day_histories, day, cash
            )
            trades.extend(closed_trades)

            # 3. Run scan — signals based on today's data, executed tomorrow
            day_report = self._scan_day(
                day, symbols, day_histories, positions, cash
            )

            # 4. Queue buy plans for next trading day; record all scan signals
            for plan in day_report.trade_plans:
                if plan.action == "buy":
                    pending_buys.append(plan)

            # Daily equity
            equity = cash + sum(
                p.shares * self._last_close(day_histories.get(p.symbol, []))
                for p in positions
            )
            equity_curve.append((day, equity))
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity
            if dd > max_drawdown:
                max_drawdown = dd

        # Close remaining positions at last available price
        for pos in list(positions):
            bars = histories.get(pos.symbol, [])
            exit_price = self._last_close(bars) if bars else pos.entry_price
            pnl = (exit_price - pos.entry_price) * pos.shares
            pnl_pct = (exit_price / pos.entry_price - 1.0) if pos.entry_price else 0.0
            cash += pos.shares * exit_price
            trades.append(
                BacktestTrade(
                    symbol=pos.symbol,
                    action="buy",
                    entry_date=pos.entry_date,
                    exit_date=end,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    shares=pos.shares,
                    pnl_amount=round(pnl, 2),
                    pnl_pct=round(pnl_pct, 4),
                    strategy_id=pos.strategy_id,
                    exit_reason="end_of_period",
                )
            )
        positions.clear()

        final_equity = cash
        total_return = (final_equity - capital) / capital

        settled = [t for t in trades if t.exit_date is not None]
        wins = [t for t in settled if t.pnl_amount > 0]
        losses = [t for t in settled if t.pnl_amount < 0]

        return BacktestResult(
            start_date=start,
            end_date=end,
            initial_capital=capital,
            final_equity=round(final_equity, 2),
            total_return_pct=round(total_return * 100, 2),
            max_drawdown_pct=round(max_drawdown * 100, 2),
            total_trades=len(settled),
            wins=len(wins),
            losses=len(losses),
            win_rate=round(len(wins) / len(settled), 4) if settled else 0.0,
            avg_win_pct=round(sum(t.pnl_pct for t in wins) / len(wins) * 100, 2) if wins else 0.0,
            avg_loss_pct=round(sum(t.pnl_pct for t in losses) / len(losses) * 100, 2) if losses else 0.0,
            profit_factor=self._profit_factor(wins, losses),
            trades=trades,
            equity_curve=equity_curve,
            daily_signals=daily_signals,
        )

    def _settle_positions(
        self,
        positions: list[BacktestPosition],
        histories: dict[str, list[Bar]],
        day: date,
        cash: float,
    ) -> tuple[list[BacktestPosition], list[BacktestTrade], float]:
        surviving: list[BacktestPosition] = []
        closed: list[BacktestTrade] = []
        atr_period = self.config.risk.atr_period
        atr_mult = self.config.risk.atr_stop_multiplier

        for pos in positions:
            bars = histories.get(pos.symbol, [])
            if not bars:
                surviving.append(pos)
                continue

            closes = [b.close for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            latest = bars[-1]
            entry = pos.entry_price

            atr_val = atr(highs, lows, closes, atr_period)
            if atr_val is None:
                surviving.append(pos)
                continue

            # Only consider closes since entry for trailing stop reference
            post_entry_closes = [b.close for b in bars if b.date >= pos.entry_date]
            highest_close = max(post_entry_closes) if post_entry_closes else entry
            # ATR-based trailing stop, floored at 5% hard stop
            trailing_stop = max(highest_close - atr_mult * atr_val, entry * 0.95)
            # Dynamic take-profit
            risk_per_share = entry - max(entry - atr_mult * atr_val, entry * 0.95)
            one_r = entry + risk_per_share if risk_per_share > 0 else pos.take_profit
            two_r = entry + 2.0 * risk_per_share if risk_per_share > 0 else pos.take_profit
            dynamic_tp = max(highest_close - atr_val, one_r)

            exit_price: float | None = None
            exit_reason = ""

            # Check trailing stop first (conservative: safety before profit)
            if latest.low <= trailing_stop:
                exit_price = trailing_stop
                exit_reason = "trailing_stop"
            # Dynamic take-profit: once above 1:1, trail; otherwise use 2:1
            elif highest_close > one_r:
                if latest.low <= dynamic_tp:
                    exit_price = dynamic_tp
                    exit_reason = "dynamic_tp"
                elif latest.high >= two_r * 1.01:
                    exit_price = two_r
                    exit_reason = "take_profit"
            # Before 1:1, use original take-profit (fallback to 2:1)
            elif latest.high >= two_r:
                exit_price = two_r
                exit_reason = "take_profit"

            if exit_price is not None:
                pnl = (exit_price - entry) * pos.shares
                pnl_pct = exit_price / entry - 1.0
                cash += pos.shares * exit_price
                closed.append(
                    BacktestTrade(
                        symbol=pos.symbol,
                        action="buy",
                        entry_date=pos.entry_date,
                        exit_date=day,
                        entry_price=entry,
                        exit_price=exit_price,
                        shares=pos.shares,
                        pnl_amount=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 4),
                        strategy_id=pos.strategy_id,
                        exit_reason=exit_reason,
                    )
                )
            else:
                surviving.append(pos)
        return surviving, closed, cash

    def _scan_day(
        self,
        day: date,
        symbols: list[str],
        histories: dict[str, list[Bar]],
        positions: list[BacktestPosition],
        cash: float,
    ) -> Any:
        """Run a lightweight scan for a single historical day."""
        eligible, _ = filter_eligible_universe(histories, self.config.universe)
        benchmark_history = eligible.get(
            self.universe.benchmark_symbol,
            histories.get(self.universe.benchmark_symbol, []),
        )

        context = ScanContext(
            config=self.config,
            universe=self.universe,
            account_id=1,
            as_of=day,
            universe_symbols=tuple(symbols),
            histories=eligible,
            raw_histories=histories,
            intraday_histories={},
            benchmark_symbol=self.universe.benchmark_symbol,
            benchmark_history=benchmark_history,
            market_regime="unknown",
            data_mode="backtest",
        )

        strategy_runtimes = build_strategy_runtimes(self.config, self.universe)
        candidates = []
        for runtime in strategy_runtimes:
            ideas = runtime.strategy.generate(context)
            candidates.extend(ideas)

        trade_plans = self.risk_manager.build_trade_plans(candidates)
        # Wrap in a simple namespace for compatibility
        return _PlanList(trade_plans)

    @staticmethod
    def _last_close(bars: list[Bar]) -> float:
        return bars[-1].close if bars else 0.0

    @staticmethod
    def _find_bar(bars: list[Bar], target: date) -> Bar | None:
        for bar in bars:
            if bar.date == target:
                return bar
        return None

    @staticmethod
    def _trading_days(
        histories: dict[str, list[Bar]], start: date, end: date
    ) -> list[date]:
        """Extract all unique trading days from history data within range."""
        all_dates: set[date] = set()
        for bars in histories.values():
            for bar in bars:
                if start <= bar.date <= end:
                    all_dates.add(bar.date)
        return sorted(all_dates)

    @staticmethod
    def _profit_factor(wins: list[BacktestTrade], losses: list[BacktestTrade]) -> float | None:
        gross_profit = sum(t.pnl_amount for t in wins)
        gross_loss = abs(sum(t.pnl_amount for t in losses))
        if gross_loss > 0:
            return round(gross_profit / gross_loss, 2)
        return None  # no losses = infinite profit factor


def _plan_to_signal(
    plan: TradePlan, trading_day: date, run_id: int = 0,
    executed: int = 0, skip_reason: str = "",
) -> dict:
    return {
        "run_id": run_id,
        "trading_day": trading_day,
        "strategy_id": plan.strategy_id,
        "signal_group": plan.signal_group,
        "action": plan.action,
        "symbol": plan.symbol,
        "name": plan.name,
        "asset_type": plan.asset_type,
        "sector": plan.sector,
        "score": plan.score,
        "entry_price": plan.entry_price,
        "stop_loss": plan.stop_loss,
        "take_profit": plan.take_profit,
        "suggested_shares": plan.suggested_shares,
        "suggested_value": plan.suggested_value,
        "executed": executed,
        "skip_reason": skip_reason,
        "reasons": " | ".join(plan.reasons) if plan.reasons else "",
        "tags": " | ".join(plan.tags) if plan.tags else "",
    }


class _PlanList:
    """Minimal wrapper to hold trade plans from risk manager."""
    def __init__(self, trade_plans: list[TradePlan]):
        self.trade_plans = trade_plans


def format_backtest_report(result: BacktestResult) -> str:
    """Generate a Chinese text summary of backtest results."""
    lines = [
        f"{'='*56}",
        f"  回测报告  {result.start_date} ~ {result.end_date}",
        f"{'='*56}",
        f"  初始资金:       {result.initial_capital:>12,.0f} 元",
        f"  最终权益:       {result.final_equity:>12,.0f} 元",
        f"  总收益率:       {result.total_return_pct:>+11.2f}%",
        f"  最大回撤:       {result.max_drawdown_pct:>11.2f}%",
        f"  ──────────────────────────────────",
        f"  总交易次数:     {result.total_trades:>11}",
        f"  盈利/亏损:      {result.wins:>11} / {result.losses}",
        f"  胜率:           {result.win_rate:>11.1%}",
        f"  平均盈利:       {result.avg_win_pct:>+11.2f}%",
        f"  平均亏损:       {result.avg_loss_pct:>+11.2f}%",
        f"  盈亏比:         {result.profit_factor:>11.2f}" if result.profit_factor is not None else "  盈亏比:              ∞ (无亏损)",
        f"{'='*56}",
    ]
    if result.trades:
        lines.append("")
        lines.append(f"  {'交易明细':-^48}")
        lines.append(f"  {'日期':<12} {'标的':<8} {'方向':<4} {'盈亏':>10} {'原因':<12}")
        for t in result.trades:
            lines.append(
                f"  {str(t.exit_date or t.entry_date):<12} {t.symbol:<8} "
                f"{'多':<4} {t.pnl_amount:>+9.0f} {t.exit_reason:<12}"
            )
    return "\n".join(lines)


def save_backtest_to_mysql(
    result: BacktestResult, data_source: str, mysql_settings,
) -> int:
    """Persist backtest results to MySQL. Returns the run_id."""
    from .config import MysqlSettings
    from .db import (
        connection_context,
        ensure_tables,
        insert_backtest_run,
        insert_backtest_signals,
        insert_backtest_trades,
    )

    ensure_tables(mysql_settings)
    with connection_context(mysql_settings) as conn:
        run_id = insert_backtest_run(
            conn,
            start_date=result.start_date,
            end_date=result.end_date,
            data_source=data_source,
            initial_capital=result.initial_capital,
            final_equity=result.final_equity,
            total_return_pct=result.total_return_pct,
            max_drawdown_pct=result.max_drawdown_pct,
            total_trades=result.total_trades,
            wins=result.wins,
            losses=result.losses,
            win_rate=result.win_rate,
            avg_win_pct=result.avg_win_pct,
            avg_loss_pct=result.avg_loss_pct,
            profit_factor=result.profit_factor,
        )
        trade_dicts = [
            {
                "run_id": run_id,
                "symbol": t.symbol,
                "action": t.action,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "pnl_amount": t.pnl_amount,
                "pnl_pct": t.pnl_pct,
                "strategy_id": t.strategy_id,
                "exit_reason": t.exit_reason,
            }
            for t in result.trades
        ]
        insert_backtest_trades(conn, run_id, trade_dicts)
        signal_dicts = [{**s, "run_id": run_id} for s in result.daily_signals]
        insert_backtest_signals(conn, run_id, signal_dicts)
        conn.commit()
        return run_id
