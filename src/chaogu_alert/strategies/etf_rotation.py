from __future__ import annotations

from ..config import AppConfig, EtfRotationSettings
from ..indicators import atr, clamp, pct_change, sma
from ..models import SignalIdea
from ..scan_context import ScanContext
from ..universe import UniverseResolver
from .base import Strategy
from .registry import StrategyDefinition, StrategyRuntime, register_strategy


class EtfRotationStrategy(Strategy):
    strategy_id = "etf_rotation"

    def __init__(self, settings: EtfRotationSettings, symbols: list[str]):
        self.settings = settings
        self.symbols = symbols

    def generate(self, context: ScanContext) -> list[SignalIdea]:
        required = max(self.settings.lookback + 1, self.settings.benchmark_filter_period)
        if len(context.benchmark_history) < required:
            return []

        benchmark_closes = [bar.close for bar in context.benchmark_history]
        benchmark_slow = sma(benchmark_closes, self.settings.benchmark_filter_period)
        benchmark_ret = pct_change(benchmark_closes, self.settings.lookback) or 0.0
        if benchmark_slow is None or benchmark_closes[-1] < benchmark_slow:
            return []

        ranked: list[tuple[float, SignalIdea]] = []
        for symbol in self.symbols:
            bars = context.histories.get(symbol, [])
            if len(bars) < self.settings.lookback + 21:
                continue

            closes = [bar.close for bar in bars]
            highs = [bar.high for bar in bars]
            lows = [bar.low for bar in bars]
            close = closes[-1]
            ma20 = sma(closes, 20)
            ret_20 = pct_change(closes, self.settings.lookback)
            if ma20 is None or ret_20 is None:
                continue
            if close < ma20 or ret_20 < self.settings.min_return_20:
                continue

            atr_val = atr(highs, lows, closes, 14)
            if atr_val is None:
                continue

            relative_strength = ret_20 - benchmark_ret
            latest = bars[-1]
            score = clamp(72.0 + relative_strength * 180.0, 0.0, 100.0)
            atr_stop = close - 2.0 * atr_val
            pct_stop = close * (1.0 - self.settings.stop_loss_pct)
            stop_loss = round(max(atr_stop, pct_stop), 3)
            take_profit = round(close + 2.0 * (close - stop_loss), 3)
            ranked.append(
                (
                    relative_strength,
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
                        reasons=(
                            f"20d return {ret_20:.2%}",
                            f"relative strength vs benchmark {relative_strength:.2%}",
                            "benchmark regime risk_on",
                        ),
                        tags=("rotation", "sector", "moderate"),
                    ),
                )
            )

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected: list[SignalIdea] = []
        seen_sectors: set[str] = set()
        for _, idea in ranked:
            if len(selected) >= self.settings.top_n:
                break
            if idea.sector in seen_sectors:
                continue
            seen_sectors.add(idea.sector)
            rank = len(selected) + 1
            selected.append(
                SignalIdea(
                    strategy_id=idea.strategy_id,
                    signal_group=idea.signal_group,
                    action=idea.action,
                    symbol=idea.symbol,
                    name=idea.name,
                    asset_type=idea.asset_type,
                    sector=idea.sector,
                    as_of=idea.as_of,
                    score=max(0.0, round(idea.score - (rank - 1) * 2.5, 2)),
                    entry_price=idea.entry_price,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    reasons=idea.reasons + (f"rotation rank #{rank}",),
                    tags=idea.tags,
                )
            )
        return selected


def _build_strategy(
    config: AppConfig,
    universe: UniverseResolver,
) -> StrategyRuntime | None:
    if not config.strategy.etf_rotation.enabled:
        return None
    symbols = universe.symbols_for("sector_etfs")
    if not symbols:
        return None
    return StrategyRuntime(
        definition=DEFINITION,
        strategy=EtfRotationStrategy(
            settings=config.strategy.etf_rotation,
            symbols=list(symbols),
        ),
        symbol_scope=symbols,
    )


DEFINITION = StrategyDefinition(
    strategy_id=EtfRotationStrategy.strategy_id,
    display_name="\u8f6e\u52a8\u5f3a\u52bf",
    description="\u4e3b\u8981\u4ece\u884c\u4e1a ETF \u4e2d\u7b5b\u9009\u76f8\u5bf9\u57fa\u51c6\u66f4\u5f3a\u7684\u65b9\u5411\uff0c\u9002\u5408\u677f\u5757\u8f6e\u52a8\u4ea4\u6613\u3002",
    signal_group="open",
    builder=_build_strategy,
)


register_strategy(DEFINITION)
