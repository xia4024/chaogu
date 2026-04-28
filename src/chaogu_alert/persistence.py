from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Protocol
import json

from .config import AppConfig
from .models import ScanReport
from .scan_context import ScanContext


class ScanPersistence(Protocol):
    def save_scan(self, context: ScanContext, report: ScanReport) -> None:
        ...


class NullScanPersistence:
    def save_scan(self, context: ScanContext, report: ScanReport) -> None:
        return


class CompositeScanPersistence:
    def __init__(self, backends: list[ScanPersistence]):
        self.backends = backends

    def save_scan(self, context: ScanContext, report: ScanReport) -> None:
        for backend in self.backends:
            try:
                backend.save_scan(context, report)
            except Exception:
                pass


class JsonlScanPersistence:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save_scan(self, context: ScanContext, report: ScanReport) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "as_of": report.as_of,
            "benchmark_symbol": report.benchmark_symbol,
            "market_regime": report.market_regime,
            "universe_symbols": list(context.universe_symbols),
            "strategy_runs": [asdict(item) for item in report.strategy_runs],
            "trade_plans": [asdict(item) for item in report.trade_plans],
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    default=_json_default,
                )
                + "\n"
            )


def build_scan_persistence(
    config: AppConfig, mysql_enabled: bool = False
) -> ScanPersistence:
    backends: list[ScanPersistence] = []
    if config.app.scan_journal_path:
        backends.append(JsonlScanPersistence(config.app.scan_journal_path))
    if mysql_enabled:
        from .mysql_persistence import MySqlScanPersistence

        backends.append(MySqlScanPersistence(config.mysql))
    if not backends:
        return NullScanPersistence()
    if len(backends) == 1:
        return backends[0]
    return CompositeScanPersistence(backends)


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported type: {type(value)!r}")
