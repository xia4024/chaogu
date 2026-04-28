from __future__ import annotations

from ..config import AppConfig, HoldingSettings, TTradeSettings
from ..indicators import clamp, sma
from ..models import MinuteBar, SignalIdea
from ..scan_context import ScanContext
from ..universe import UniverseResolver
from .base import Strategy
from .registry import StrategyDefinition, StrategyRuntime, register_strategy


class EtfTTradeStrategy(Strategy):
    strategy_id = "etf_t_trade"

    def __init__(self, settings: TTradeSettings, holdings: list[HoldingSettings]):
        self.settings = settings
        self.holdings = {holding.symbol: holding for holding in holdings}

    def generate(self, context: ScanContext) -> list[SignalIdea]:
        ideas: list[SignalIdea] = []

        for symbol, holding in self.holdings.items():
            daily_bars = context.histories.get(symbol, [])
            minute_bars = context.intraday_histories.get(symbol, [])
            if len(daily_bars) < max(self.settings.require_trend_ma, 3) or len(minute_bars) < 10:
                continue

            closes = [bar.close for bar in daily_bars]
            trend_ma = sma(closes, self.settings.require_trend_ma)
            if trend_ma is None:
                continue

            current_bar = daily_bars[-1]
            prev_bar = daily_bars[-2] if len(daily_bars) >= 2 else daily_bars[-1]
            current_price = minute_bars[-1].close
            day_high = max(bar.high for bar in minute_bars)
            day_low = min(bar.low for bar in minute_bars)
            day_volume = sum(bar.volume for bar in minute_bars)
            vwap = _vwap(minute_bars)
            intraday_return = current_price / prev_bar.close - 1.0 if prev_bar.close else 0.0
            day_range_pct = (day_high - day_low) / prev_bar.close if prev_bar.close else 0.0

            if day_range_pct > self.settings.max_daily_range_pct:
                continue

            trend_ok = current_price >= trend_ma
            regime_penalty = 0.0 if context.market_regime == "risk_on" else 5.0
            if (
                intraday_return <= -self.settings.min_intraday_pullback_pct
                and current_price <= vwap * (1.0 - self.settings.min_distance_from_vwap_pct)
                and trend_ok
            ):
                score = clamp(
                    70.0
                    + abs(intraday_return) * 900.0
                    + max(0.0, (vwap - current_price) / max(vwap, 0.001)) * 600.0
                    - regime_penalty,
                    0.0,
                    100.0,
                )
                stop_loss = round(min(day_low, current_price * 0.992), 3)
                take_profit = round(vwap, 3)
                ideas.append(
                    SignalIdea(
                        strategy_id=self.strategy_id,
                        signal_group="t_trade",
                        action="buy",
                        symbol=symbol,
                        name=current_bar.name,
                        asset_type=current_bar.asset_type,
                        sector=current_bar.sector,
                        as_of=context.as_of,
                        score=round(score, 2),
                        entry_price=round(current_price, 3),
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        reasons=(
                            f"holding shares {holding.shares}",
                            f"intraday pullback {intraday_return:.2%}",
                            f"price below VWAP by {(vwap - current_price) / max(vwap, 0.001):.2%}",
                            f"trend MA{self.settings.require_trend_ma} still intact",
                        ),
                        tags=("t_trade", "buy", "intraday"),
                    )
                )

            if (
                intraday_return >= self.settings.min_intraday_rebound_pct
                and current_price >= vwap * (1.0 + self.settings.min_distance_from_vwap_pct)
                and holding.shares > 0
            ):
                score = clamp(
                    70.0
                    + intraday_return * 900.0
                    + max(0.0, (current_price - vwap) / max(vwap, 0.001)) * 600.0,
                    0.0,
                    100.0,
                )
                stop_loss = round(vwap, 3)
                take_profit = round(max(day_high, current_price * 1.004), 3)
                ideas.append(
                    SignalIdea(
                        strategy_id=self.strategy_id,
                        signal_group="t_trade",
                        action="sell",
                        symbol=symbol,
                        name=current_bar.name,
                        asset_type=current_bar.asset_type,
                        sector=current_bar.sector,
                        as_of=context.as_of,
                        score=round(score, 2),
                        entry_price=round(current_price, 3),
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        reasons=(
                            f"holding shares {holding.shares}",
                            f"intraday rebound {intraday_return:.2%}",
                            f"price above VWAP by {(current_price - vwap) / max(vwap, 0.001):.2%}",
                            f"intraday volume {day_volume:,.0f}",
                        ),
                        tags=("t_trade", "sell", "intraday"),
                    )
                )

        return ideas


def _build_strategy(
    config: AppConfig,
    universe: UniverseResolver,
) -> StrategyRuntime | None:
    if not config.strategy.t_trade.enabled or not config.portfolio.holdings:
        return None
    symbols = universe.symbols_for("holdings")
    if not symbols:
        return None
    return StrategyRuntime(
        definition=DEFINITION,
        strategy=EtfTTradeStrategy(
            settings=config.strategy.t_trade,
            holdings=config.portfolio.holdings,
        ),
        symbol_scope=symbols,
    )


DEFINITION = StrategyDefinition(
    strategy_id=EtfTTradeStrategy.strategy_id,
    display_name="\u6301\u4ed3\u505aT",
    description="\u53ea\u5bf9\u5df2\u6301\u4ed3\u6807\u7684\u505a\u65e5\u5185\u4f4e\u5438\u9ad8\u629b\uff0c\u4e0d\u627f\u62c5\u5f00\u65b0\u4ed3\u804c\u8d23\u3002",
    signal_group="t_trade",
    builder=_build_strategy,
)


register_strategy(DEFINITION)


def _vwap(minute_bars: list[MinuteBar]) -> float:
    amount = sum((bar.avg_price or bar.close) * bar.volume for bar in minute_bars)
    volume = sum(bar.volume for bar in minute_bars)
    if volume <= 0:
        return minute_bars[-1].close
    return amount / volume
