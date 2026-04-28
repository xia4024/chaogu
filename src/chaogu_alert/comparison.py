from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class MatchedRow:
    trade_date: date
    symbol: str
    strategy_signal: dict | None   # trade_outcomes row or None
    actual_trade: dict | None      # actual_trades row or None
    deviation: str = ""            # match|miss|hold_loss|overtrade|early_exit|slippage
    pnl_impact: float = 0.0


@dataclass
class ComparisonResult:
    start_date: date
    end_date: date
    total_strategy_pnl: float
    total_actual_pnl: float
    execution_gap: float
    signal_execution_rate: float
    actual_total_commission: float = 0.0
    unresolved_sell_trades: int = 0
    open_positions: dict[str, int] = field(default_factory=dict)
    matched_rows: list[MatchedRow] = field(default_factory=list)
    defect_summary: dict[str, dict] = field(default_factory=dict)


@dataclass
class ActualTradeSummary:
    trades: list[dict[str, Any]] = field(default_factory=list)
    total_realized_pnl: float = 0.0
    total_commission: float = 0.0
    unresolved_sell_trades: int = 0
    open_positions: dict[str, int] = field(default_factory=dict)


def match_strategy_to_actual(
    trade_outcomes: list[dict],
    actual_trades: list[dict],
    start_date: date,
    end_date: date,
) -> ComparisonResult:
    """Match strategy signals to actual trades and classify deviations."""
    actual_summary = summarize_actual_trades(actual_trades)

    # Index actual trades by (date, symbol) for O(1) lookup
    actual_by_key: dict[tuple, list[dict]] = {}
    for t in actual_summary.trades:
        td = _to_date(t.get("trade_date"))
        if td is None:
            continue
        key = (td, t.get("symbol", ""))
        actual_by_key.setdefault(key, []).append(t)

    # Index strategy signals by (date, symbol)
    strategy_by_key: dict[tuple, list[dict]] = {}
    total_strategy_pnl = 0.0
    for o in trade_outcomes:
        # Use settled_date if available, otherwise scan_date
        raw_date = o.get("settled_date") or o.get("scan_date")
        td = _to_date(raw_date)
        if td is None:
            continue
        key = (td, o.get("symbol", ""))
        strategy_by_key.setdefault(key, []).append(o)
        total_strategy_pnl += _strategy_pnl_amount(o)

    total_actual_pnl = actual_summary.total_realized_pnl

    # Collect all unique (date, symbol) pairs
    all_keys = set(strategy_by_key.keys()) | set(actual_by_key.keys())

    rows: list[MatchedRow] = []
    execution_gap = 0.0

    for (d, sym) in sorted(all_keys):
        strategy_signals = strategy_by_key.get((d, sym), [])
        actuals = actual_by_key.get((d, sym), [])

        # Also check +/-1 day tolerance for actual trades
        if not actuals:
            for offset in [-1, 1]:
                adj_key = (d + timedelta(days=offset), sym)
                adjacent = actual_by_key.get(adj_key, [])
                if adjacent:
                    actuals = adjacent
                    break

        s_has_buy = any(str(s.get("action", "")).lower() == "buy" for s in strategy_signals)
        s_has_sell = any(str(s.get("action", "")).lower() == "sell" for s in strategy_signals)
        a_has_buy = any(str(t.get("action", "")).lower() == "buy" for t in actuals)
        a_has_sell = any(str(t.get("action", "")).lower() == "sell" for t in actuals)

        deviation = "match"
        impact = 0.0

        if s_has_buy and a_has_buy:
            deviation = "match"
        elif s_has_sell and a_has_sell:
            deviation = "match"
        elif s_has_buy and not a_has_buy and not a_has_sell:
            deviation = "miss"
            impact = sum(_strategy_pnl_amount(s) for s in strategy_signals)
        elif s_has_sell and not a_has_sell and not a_has_buy:
            deviation = "hold_loss"
            impact = sum(_strategy_pnl_amount(s) for s in strategy_signals)
        elif not s_has_buy and not s_has_sell and a_has_buy:
            deviation = "overtrade"
            # Estimate loss from overtrading: rough 2% risk per unauthorized trade
            impact = -sum(float(t.get("amount") or 0) * 0.02 for t in actuals)
        elif s_has_buy and a_has_sell and not a_has_buy:
            # Strategy said buy but user sold (early exit of existing position)
            deviation = "early_exit"
            impact = sum(_strategy_pnl_amount(s) for s in strategy_signals)
        elif not strategy_signals and actuals:
            deviation = "overtrade"
            impact = -sum(float(t.get("amount") or 0) * 0.02 for t in actuals)

        if deviation != "match":
            execution_gap += impact

        rows.append(MatchedRow(
            trade_date=d,
            symbol=sym,
            strategy_signal=strategy_signals[0] if strategy_signals else None,
            actual_trade=actuals[0] if actuals else None,
            deviation=deviation,
            pnl_impact=round(impact, 2),
        ))

    # Summarize defects
    defects: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_loss": 0.0})
    for row in rows:
        if row.deviation != "match":
            defects[row.deviation]["count"] += 1
            defects[row.deviation]["total_loss"] += row.pnl_impact

    signal_count = sum(1 for r in rows if r.strategy_signal is not None)
    executed_count = sum(1 for r in rows if r.strategy_signal is not None and r.actual_trade is not None)
    exec_rate = executed_count / signal_count if signal_count > 0 else 0.0

    return ComparisonResult(
        start_date=start_date,
        end_date=end_date,
        total_strategy_pnl=round(total_strategy_pnl, 2),
        total_actual_pnl=round(total_actual_pnl, 2),
        execution_gap=round(execution_gap, 2),
        signal_execution_rate=round(exec_rate, 4),
        actual_total_commission=round(actual_summary.total_commission, 2),
        unresolved_sell_trades=actual_summary.unresolved_sell_trades,
        open_positions=actual_summary.open_positions,
        matched_rows=rows,
        defect_summary=dict(defects),
    )


def summarize_actual_trades(actual_trades: list[dict[str, Any]]) -> ActualTradeSummary:
    lots_by_symbol: dict[str, list[dict[str, float]]] = defaultdict(list)
    summary = ActualTradeSummary()

    for trade in sorted(actual_trades, key=_actual_trade_sort_key):
        normalized = dict(trade)
        symbol = str(normalized.get("symbol", "")).strip()
        action = str(normalized.get("action", "")).strip().lower()
        shares = int(normalized.get("shares") or 0)
        price = float(normalized.get("price") or 0)
        amount = float(normalized.get("amount") or shares * price)
        commission = float(normalized.get("commission") or 0)
        normalized["realized_pnl"] = 0.0
        normalized["matched_shares"] = 0
        normalized["unresolved_shares"] = 0

        summary.total_commission += commission

        if not symbol or shares <= 0:
            summary.trades.append(normalized)
            continue

        if action == "buy":
            unit_cost = (amount + commission) / shares if shares else 0.0
            lots_by_symbol[symbol].append({
                "shares": float(shares),
                "unit_cost": unit_cost,
            })
            summary.trades.append(normalized)
            continue

        if action != "sell":
            summary.trades.append(normalized)
            continue

        proceeds_per_share = (amount - commission) / shares if shares else 0.0
        remaining = shares
        matched = 0
        matched_cost = 0.0
        lots = lots_by_symbol[symbol]

        while remaining > 0 and lots:
            lot = lots[0]
            lot_shares = int(lot["shares"])
            if lot_shares <= 0:
                lots.pop(0)
                continue
            consume = min(remaining, lot_shares)
            matched += consume
            matched_cost += consume * float(lot["unit_cost"])
            lot["shares"] = float(lot_shares - consume)
            remaining -= consume
            if lot["shares"] <= 0:
                lots.pop(0)

        realized = matched * proceeds_per_share - matched_cost
        normalized["realized_pnl"] = round(realized, 2)
        normalized["matched_shares"] = matched
        normalized["unresolved_shares"] = remaining
        summary.total_realized_pnl += realized
        if remaining > 0:
            summary.unresolved_sell_trades += 1
        summary.trades.append(normalized)

    summary.open_positions = {
        symbol: int(round(sum(lot["shares"] for lot in lots)))
        for symbol, lots in lots_by_symbol.items()
        if sum(lot["shares"] for lot in lots) > 0
    }
    summary.total_realized_pnl = round(summary.total_realized_pnl, 2)
    summary.total_commission = round(summary.total_commission, 2)
    return summary


def _strategy_pnl_amount(outcome: dict[str, Any]) -> float:
    pnl_pct = outcome.get("pnl_pct")
    suggested_value = float(outcome.get("suggested_value") or 0)
    if pnl_pct is not None and suggested_value > 0:
        return round(float(pnl_pct) * suggested_value, 2)
    return float(outcome.get("pnl_amount") or 0)


def _actual_trade_sort_key(trade: dict[str, Any]) -> tuple[str, str, int]:
    trade_date = str(trade.get("trade_date") or "")
    trade_time = str(trade.get("trade_time") or "")
    trade_id = int(trade.get("id") or 0)
    return trade_date, trade_time, trade_id


def _to_date(val) -> date | None:
    """Convert various date representations to date object."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val[:10])
        except (ValueError, TypeError):
            return None
    return None
