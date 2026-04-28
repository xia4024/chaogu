from __future__ import annotations

from ..config import AppConfig, EtfTrendSettings
from ..indicators import atr, average, clamp, pct_change, recent_high, sma
from ..models import SignalIdea
from ..scan_context import ScanContext
from ..universe import UniverseResolver
from .base import Strategy
from .registry import StrategyDefinition, StrategyRuntime, register_strategy


class EtfTrendStrategy(Strategy):
    strategy_id = "etf_trend"

    def __init__(self, settings: EtfTrendSettings, symbols: list[str]):
        self.settings = settings
        self.symbols = symbols

    def generate(self, context: ScanContext) -> list[SignalIdea]:
        ideas: list[SignalIdea] = []

        for symbol in self.symbols:
            bars = context.histories.get(symbol, [])
            required = max(self.settings.ma_fast, self.settings.ma_slow, 21)
            if len(bars) < required:
                continue

            closes = [bar.close for bar in bars]
            volumes = [bar.volume for bar in bars]
            highs = [bar.high for bar in bars]
            lows = [bar.low for bar in bars]
            close = closes[-1]
            ma_fast = sma(closes, self.settings.ma_fast)
            ma_slow = sma(closes, self.settings.ma_slow)
            ret_20 = pct_change(closes, 20)
            vol_ma_20 = average(volumes, 20)
            prior_high = recent_high(closes[:-1], 20)

            if None in {ma_fast, ma_slow, ret_20, vol_ma_20, prior_high}:
                continue

            atr_val = atr(highs, lows, closes, 14)
            if atr_val is None:
                continue

            volume_ratio = volumes[-1] / vol_ma_20 if vol_ma_20 else 0.0
            breakout = close >= prior_high
            if not (
                close > ma_fast > ma_slow
                and ret_20 >= self.settings.min_return_20
                and volume_ratio >= self.settings.min_volume_ratio
            ):
                continue

            score = clamp(
                68.0
                + ret_20 * 120.0
                + (volume_ratio - 1.0) * 18.0
                + (4.0 if breakout else 0.0),
                0.0,
                100.0,
            )
            atr_stop = close - 2.0 * atr_val
            pct_stop = close * (1.0 - self.settings.stop_loss_pct)
            stop_loss = round(max(atr_stop, pct_stop), 3)
            take_profit = round(close + 2.0 * (close - stop_loss), 3)
            latest = bars[-1]

            reasons = [
                f"close {close:.3f} > MA{self.settings.ma_fast} {ma_fast:.3f} > MA{self.settings.ma_slow} {ma_slow:.3f}",
                f"20d return {ret_20:.2%}",
                f"volume ratio {volume_ratio:.2f}",
            ]
            if breakout:
                reasons.append("20-day breakout")

            ideas.append(
                SignalIdea(
                    strategy_id=self.strategy_id,
                    signal_group="open",
                    action="buy",
                    symbol=symbol,
                    name=latest.name,
                    asset_type=latest.asset_type,
                    sector=latest.sector,
                    as_of=context.as_of,
                    score=round(score, 2),
                    entry_price=round(close, 3),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reasons=tuple(reasons),
                    tags=("trend", "etf", "moderate"),
                )
            )

        return ideas


def _build_strategy(
    config: AppConfig,
    universe: UniverseResolver,
) -> StrategyRuntime | None:
    if not config.strategy.etf_trend.enabled:
        return None
    symbols = universe.symbols_for("broad_etfs")
    if not symbols:
        return None
    return StrategyRuntime(
        definition=DEFINITION,
        strategy=EtfTrendStrategy(
            settings=config.strategy.etf_trend,
            symbols=list(symbols),
        ),
        symbol_scope=symbols,
    )


DEFINITION = StrategyDefinition(
    strategy_id=EtfTrendStrategy.strategy_id,
    display_name="\u8d8b\u52bf\u7a81\u7834",
    description="\u4e3b\u8981\u8ddf\u8e2a\u5bbd\u57fa ETF \u7684\u5747\u7ebf\u8d8b\u52bf\u3001\u6da8\u5e45\u548c\u91cf\u80fd\u5171\u632f\uff0c\u9002\u5408\u987a\u52bf\u5f00\u4ed3\u6216\u52a0\u4ed3\u3002",
    signal_group="open",
    builder=_build_strategy,
)


register_strategy(DEFINITION)
