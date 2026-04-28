from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import logging

from .config import MysqlSettings
from .db import (
    connection_context,
    ensure_tables,
    insert_price_snapshots,
    insert_scan_report,
    insert_trade_outcomes,
    insert_trade_plans,
    settle_outcomes,
    recalculate_performance,
)
from .models import ScanReport
from .scan_context import ScanContext

_logger = logging.getLogger(__name__)


class MySqlScanPersistence:
    def __init__(self, settings: MysqlSettings):
        self.settings = settings
        ensure_tables(settings)

    def save_scan(self, context: ScanContext, report: ScanReport, account_id: int = 1) -> None:
        data_mode = getattr(context, 'data_mode', 'real')
        account_id = getattr(context, "account_id", account_id)
        try:
            with connection_context(self.settings) as conn:
                now = datetime.now()
                report_id = insert_scan_report(
                    conn,
                    scan_date=report.as_of,
                    executed_at=now,
                    benchmark_symbol=report.benchmark_symbol,
                    market_regime=report.market_regime,
                    candidate_count=len(report.candidates),
                    filtered_count=report.filtered_count,
                    data_mode=data_mode,
                    circuit_triggered=report.circuit_triggered,
                )
                if not report_id:
                    return

                plans = [asdict(plan) for plan in report.trade_plans]
                insert_trade_plans(conn, report_id, plans, data_mode=data_mode)

                snapshots = _extract_price_snapshots(context, report.as_of)
                insert_price_snapshots(conn, report.as_of, snapshots, data_mode=data_mode)

                t_trade_plans = [
                    p for p in plans if p["signal_group"] == "t_trade"
                ]
                if t_trade_plans:
                    outcomes = [
                        {
                            "symbol": p["symbol"],
                            "name": p["name"],
                            "strategy_id": p["strategy_id"],
                            "signal_group": p["signal_group"],
                            "action": p["action"],
                            "score": p["score"],
                            "entry_price": p["entry_price"],
                            "stop_loss": p["stop_loss"],
                            "take_profit": p["take_profit"],
                            "suggested_shares": p["suggested_shares"],
                            "suggested_value": p["suggested_value"],
                        }
                        for p in t_trade_plans
                    ]
                    insert_trade_outcomes(conn, report_id, outcomes, data_mode=data_mode, account_id=account_id)

                settled = settle_outcomes(conn, data_mode=data_mode, account_id=account_id)
                recalculate_performance(conn, data_mode=data_mode, account_id=account_id)

                _logger.info(
                    "scan %s persisted, report_id=%s, plans=%s, snapshots=%s, outcomes=%s, settled=%s",
                    report.as_of.isoformat(),
                    report_id,
                    len(plans),
                    len(snapshots),
                    len(t_trade_plans),
                    settled,
                )
        except Exception:
            _logger.exception("failed to persist scan to mysql")


def _extract_price_snapshots(
    context: ScanContext, as_of
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for symbol, bars in context.histories.items():
        if not bars:
            continue
        last = bars[-1]
        snapshots.append({
            "symbol": symbol,
            "name": last.name,
            "open": last.open,
            "high": last.high,
            "low": last.low,
            "close": last.close,
            "volume": int(last.volume),
        })
    return snapshots
