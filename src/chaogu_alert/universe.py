from __future__ import annotations

from .config import AppConfig


class UniverseResolver:
    def __init__(self, config: AppConfig):
        self.benchmark_symbol = config.universe.benchmark_symbol
        holdings = [holding.symbol for holding in config.portfolio.holdings]
        groups = {
            "benchmark": [config.universe.benchmark_symbol],
            "broad_etfs": list(config.universe.broad_etfs),
            "sector_etfs": list(config.universe.sector_etfs),
            "holdings": holdings,
        }
        groups.update(config.universe.groups)
        groups["default_scan"] = self._merge_symbols(
            groups["benchmark"],
            groups["broad_etfs"],
            groups["sector_etfs"],
            groups["holdings"],
        )
        self._groups = {
            name: self._freeze_symbols(symbols)
            for name, symbols in groups.items()
        }

    def symbols_for(self, *group_names: str) -> tuple[str, ...]:
        merged: list[str] = []
        for name in group_names:
            if name not in self._groups:
                raise KeyError(f"Unknown universe group: {name}")
            merged.extend(self._groups[name])
        return self._freeze_symbols(merged)

    def all_scan_symbols(self) -> tuple[str, ...]:
        return self._groups["default_scan"]

    def groups(self) -> dict[str, tuple[str, ...]]:
        return dict(self._groups)

    @staticmethod
    def _merge_symbols(*collections: list[str] | tuple[str, ...]) -> list[str]:
        merged: list[str] = []
        for symbols in collections:
            merged.extend(symbols)
        return merged

    @staticmethod
    def _freeze_symbols(symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(dict.fromkeys(symbols)))
