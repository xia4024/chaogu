from __future__ import annotations

from collections import defaultdict
from datetime import date

from .config import PortfolioSettings, RiskSettings, UniverseSettings
from .indicators import atr
from .models import Bar, SignalIdea, TradePlan


def is_etf(symbol: str) -> bool:
    """Determine if a symbol is an ETF based on code pattern."""
    return bool(
        symbol.startswith("51")
        or symbol.startswith("15")
        or symbol.startswith("16")
        or symbol.startswith("18")
        or symbol.startswith("52")
        or symbol.startswith("56")
        or symbol.startswith("58")
        or symbol.startswith("588")
    )


def calc_commission(symbol: str, trade_value: float, settings: RiskSettings) -> float:
    """Calculate one-way commission for a trade."""
    if is_etf(symbol):
        return settings.etf_commission
    return max(settings.stock_commission, trade_value * settings.stock_commission_rate)


def filter_eligible_universe(
    histories: dict[str, list[Bar]], universe: UniverseSettings
) -> tuple[dict[str, list[Bar]], int]:
    eligible: dict[str, list[Bar]] = {}
    filtered_count = 0

    for symbol, bars in histories.items():
        if not bars:
            filtered_count += 1
            continue
        latest = bars[-1]
        if latest.is_st or latest.listing_days < universe.min_listing_days:
            filtered_count += 1
            continue
        eligible[symbol] = bars

    return eligible, filtered_count


class ModerateRiskManager:
    def __init__(self, settings: RiskSettings, portfolio: PortfolioSettings):
        self.settings = settings
        self.portfolio = portfolio
        self.holdings = {holding.symbol: holding for holding in portfolio.holdings}

    def build_trade_plans(self, candidates: list[SignalIdea]) -> list[TradePlan]:
        plans: list[TradePlan] = []
        plans.extend(self._build_open_trade_plans(candidates))
        plans.extend(self._build_t_trade_plans(candidates))
        return sorted(plans, key=lambda item: (item.signal_group, -item.score, item.symbol))

    def _build_open_trade_plans(self, candidates: list[SignalIdea]) -> list[TradePlan]:
        tradable = [
            idea
            for idea in sorted(candidates, key=lambda item: item.score, reverse=True)
            if idea.signal_group == "open" and idea.score >= self.settings.min_score
        ]
        plans: list[TradePlan] = []
        used_pct = 0.0
        sector_exposure: dict[str, float] = defaultdict(float)
        deployable_pct = 1.0 - self.settings.cash_buffer_pct

        for idea in tradable:
            if len(plans) >= self.settings.max_positions:
                break

            base_target_pct = 0.25 if idea.asset_type == "broad_etf" else 0.20
            remaining_pct = deployable_pct - used_pct
            sector_room = self.settings.max_sector_position_pct - sector_exposure[idea.sector]
            target_pct = min(
                base_target_pct,
                self.settings.max_position_pct,
                remaining_pct,
                sector_room,
            )
            if target_pct <= 0:
                continue

            risk_budget = self.settings.capital * self.settings.risk_per_trade_pct
            stop_distance = max(idea.entry_price - idea.stop_loss, idea.entry_price * 0.01)
            shares_by_risk = int(risk_budget / stop_distance / self.settings.lot_size)
            shares_by_budget = int(
                (self.settings.capital * target_pct)
                / idea.entry_price
                / self.settings.lot_size
            )
            suggested_lots = min(shares_by_risk, shares_by_budget)
            suggested_shares = suggested_lots * self.settings.lot_size
            if suggested_shares < self.settings.lot_size:
                continue

            suggested_value = round(suggested_shares * idea.entry_price, 2)
            position_pct = round(suggested_value / self.settings.capital, 4)
            commission = round(calc_commission(idea.symbol, suggested_value, self.settings), 2)
            plans.append(
                TradePlan(
                    strategy_id=idea.strategy_id,
                    signal_group=idea.signal_group,
                    action=idea.action,
                    symbol=idea.symbol,
                    name=idea.name,
                    asset_type=idea.asset_type,
                    sector=idea.sector,
                    as_of=idea.as_of,
                    score=round(idea.score, 2),
                    entry_price=idea.entry_price,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    suggested_shares=suggested_shares,
                    suggested_value=suggested_value,
                    position_pct=position_pct,
                    risk_amount=round(suggested_shares * stop_distance, 2),
                    estimated_commission=commission,
                    reasons=idea.reasons,
                    tags=idea.tags,
                )
            )
            used_pct += position_pct
            sector_exposure[idea.sector] += position_pct

        return plans

    def _build_t_trade_plans(self, candidates: list[SignalIdea]) -> list[TradePlan]:
        tradable = [
            idea
            for idea in sorted(candidates, key=lambda item: item.score, reverse=True)
            if idea.signal_group == "t_trade" and idea.score >= self.settings.min_score
        ]
        plans: list[TradePlan] = []

        for idea in tradable:
            holding = self.holdings.get(idea.symbol)
            if holding is None or holding.shares < self.settings.lot_size:
                continue

            strength = min(max((idea.score - self.settings.min_score) / 20.0, 0.0), 1.0)
            trade_pct = holding.min_t_trade_pct + (
                holding.max_t_trade_pct - holding.min_t_trade_pct
            ) * strength
            suggested_shares = (
                int(holding.shares * trade_pct / self.settings.lot_size) * self.settings.lot_size
            )
            if suggested_shares < self.settings.lot_size:
                continue

            suggested_value = round(suggested_shares * idea.entry_price, 2)
            position_pct = round(suggested_value / self.settings.capital, 4)
            stop_distance = max(abs(idea.entry_price - idea.stop_loss), idea.entry_price * 0.005)
            commission = round(calc_commission(idea.symbol, suggested_value, self.settings), 2)
            plans.append(
                TradePlan(
                    strategy_id=idea.strategy_id,
                    signal_group=idea.signal_group,
                    action=idea.action,
                    symbol=idea.symbol,
                    name=idea.name,
                    asset_type=idea.asset_type,
                    sector=idea.sector,
                    as_of=idea.as_of,
                    score=round(idea.score, 2),
                    entry_price=idea.entry_price,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    suggested_shares=suggested_shares,
                    suggested_value=suggested_value,
                    position_pct=position_pct,
                    risk_amount=round(suggested_shares * stop_distance, 2),
                    estimated_commission=commission,
                    reasons=idea.reasons + (f"t size {trade_pct:.1%} of current holding",),
                    tags=idea.tags,
                )
            )

        return plans

    def trailing_stop_exits(
        self,
        positions: list[dict],
        histories: dict[str, list[Bar]],
        as_of: date,
    ) -> list[SignalIdea]:
        """Generate SELL signals for positions where trailing stop is hit.

        Trailing stop = max(highest_close - multiplier * ATR, cost_basis * 0.95).
        The stop only moves up as price makes new highs.
        """
        if not self.settings.trailing_stop_enabled or not positions:
            return []

        exits: list[SignalIdea] = []
        for pos in positions:
            symbol = pos["symbol"]
            bars = histories.get(symbol, [])
            if len(bars) < max(self.settings.atr_period + 1, 21):
                continue

            closes = [bar.close for bar in bars]
            highs = [bar.high for bar in bars]
            lows = [bar.low for bar in bars]
            current_close = closes[-1]

            atr_val = atr(highs, lows, closes, self.settings.atr_period)
            if atr_val is None:
                continue

            highest_close = max(closes[-60:]) if len(closes) >= 60 else max(closes)
            trail_stop = highest_close - self.settings.atr_stop_multiplier * atr_val
            hard_stop = float(pos.get("cost_basis", current_close)) * 0.95
            effective_stop = max(trail_stop, hard_stop)

            if current_close <= effective_stop and current_close > 0:
                drawdown_pct = (current_close - highest_close) / highest_close
                latest = bars[-1]
                exits.append(
                    SignalIdea(
                        strategy_id="trailing_stop",
                        signal_group="open",
                        action="sell",
                        symbol=symbol,
                        name=latest.name,
                        asset_type=latest.asset_type,
                        sector=latest.sector,
                        as_of=as_of,
                        score=95.0,
                        entry_price=round(current_close, 3),
                        stop_loss=0.0,
                        take_profit=0.0,
                        reasons=(
                            f"触及追踪止损 | 最高价 {highest_close:.3f} | 当前 {current_close:.3f}",
                            f"回撤 {drawdown_pct:.2%} | ATR {atr_val:.3f}",
                            f"有效止损 {effective_stop:.3f}",
                        ),
                        tags=("exit", "trailing_stop"),
                    )
                )

        return exits

    def trailing_take_profit_exits(
        self,
        positions: list[dict],
        histories: dict[str, list[Bar]],
        as_of: date,
    ) -> list[SignalIdea]:
        """Generate SELL signals for positions where dynamic take-profit triggers.

        Once price reaches 1:1 R:R, activate trailing take-profit:
        trailing_tp = max(highest_close - ATR, 1:1_level).
        Exit when price drops below trailing_tp.
        """
        if not self.settings.trailing_stop_enabled or not positions:
            return []

        exits: list[SignalIdea] = []
        for pos in positions:
            symbol = pos["symbol"]
            bars = histories.get(symbol, [])
            if len(bars) < max(self.settings.atr_period + 1, 21):
                continue

            closes = [bar.close for bar in bars]
            highs = [bar.high for bar in bars]
            lows = [bar.low for bar in bars]
            current_close = closes[-1]

            atr_val = atr(highs, lows, closes, self.settings.atr_period)
            if atr_val is None:
                continue

            cost_basis = float(pos.get("cost_basis", current_close))
            initial_stop = max(cost_basis - self.settings.atr_stop_multiplier * atr_val, cost_basis * 0.95)
            risk_per_share = cost_basis - initial_stop
            if risk_per_share <= 0:
                continue

            one_r_level = cost_basis + risk_per_share  # 1:1 R:R
            two_r_level = cost_basis + 2.0 * risk_per_share  # 2:1 R:R (original TP)

            # Only activate trailing TP once price exceeds 1:1 R:R
            if current_close <= one_r_level:
                continue

            highest_close = max(closes[-60:]) if len(closes) >= 60 else max(closes)
            trailing_tp = max(highest_close - atr_val, one_r_level)

            if current_close <= trailing_tp and current_close > 0:
                gains_pct = (current_close - cost_basis) / cost_basis
                latest = bars[-1]
                exits.append(
                    SignalIdea(
                        strategy_id="dynamic_take_profit",
                        signal_group="open",
                        action="sell",
                        symbol=symbol,
                        name=latest.name,
                        asset_type=latest.asset_type,
                        sector=latest.sector,
                        as_of=as_of,
                        score=90.0,
                        entry_price=round(current_close, 3),
                        stop_loss=0.0,
                        take_profit=0.0,
                        reasons=(
                            f"动态止盈触发 | 成本 {cost_basis:.3f} | 当前 {current_close:.3f}",
                            f"浮盈 {gains_pct:.2%} | 最高价 {highest_close:.3f}",
                            f"追踪止盈位 {trailing_tp:.3f} | ATR {atr_val:.3f}",
                        ),
                        tags=("exit", "dynamic_take_profit"),
                    )
                )

        return exits

    def check_circuit_breaker(
        self, weekly_pnl: list[float] | None = None, daily_pnl: list[float] | None = None,
    ) -> bool:
        """Return True if circuit breaker is triggered.

        Checks both weekly and daily realized P&L. When triggered,
        all new entry signals should be suppressed.
        """
        if not self.settings.circuit_breaker_enabled:
            return False

        if daily_pnl:
            daily_total = sum(daily_pnl)
            daily_limit = -(self.settings.capital * self.settings.circuit_breaker_daily_pct)
            if daily_total <= daily_limit:
                return True

        if weekly_pnl:
            weekly_total = sum(weekly_pnl)
            weekly_limit = -(self.settings.capital * self.settings.circuit_breaker_weekly_pct)
            if weekly_total <= weekly_limit:
                return True

        return False
