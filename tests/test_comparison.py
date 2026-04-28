from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "pymysql" not in sys.modules:
    pymysql_stub = types.ModuleType("pymysql")
    pymysql_stub.connect = lambda *args, **kwargs: None
    pymysql_stub.err = types.SimpleNamespace(OperationalError=Exception)
    cursors_stub = types.ModuleType("pymysql.cursors")
    cursors_stub.DictCursor = object
    pymysql_stub.cursors = cursors_stub
    sys.modules["pymysql"] = pymysql_stub
    sys.modules["pymysql.cursors"] = cursors_stub

from chaogu_alert.comparison import match_strategy_to_actual
from chaogu_alert.db import _determine_outcome


class ComparisonTests(unittest.TestCase):
    def test_strategy_total_pnl_uses_suggested_value_times_pnl_pct(self) -> None:
        result = match_strategy_to_actual(
            trade_outcomes=[
                {
                    "symbol": "510300",
                    "action": "buy",
                    "scan_date": "2026-04-15",
                    "settled_date": "2026-04-16",
                    "pnl_pct": 0.10,
                    "pnl_amount": 10.0,
                    "suggested_value": 10000.0,
                }
            ],
            actual_trades=[],
            start_date=date(2026, 4, 15),
            end_date=date(2026, 4, 16),
        )

        self.assertEqual(result.total_strategy_pnl, 1000.0)
        self.assertEqual(result.execution_gap, 1000.0)

    def test_actual_total_pnl_uses_fifo_realized_profit(self) -> None:
        result = match_strategy_to_actual(
            trade_outcomes=[],
            actual_trades=[
                {
                    "id": 1,
                    "symbol": "510300",
                    "action": "buy",
                    "trade_date": "2026-04-15",
                    "trade_time": "09:30:00",
                    "price": 10.0,
                    "shares": 100,
                    "amount": 1000.0,
                    "commission": 0.0,
                },
                {
                    "id": 2,
                    "symbol": "510300",
                    "action": "buy",
                    "trade_date": "2026-04-15",
                    "trade_time": "10:00:00",
                    "price": 11.0,
                    "shares": 100,
                    "amount": 1100.0,
                    "commission": 0.0,
                },
                {
                    "id": 3,
                    "symbol": "510300",
                    "action": "sell",
                    "trade_date": "2026-04-16",
                    "trade_time": "10:00:00",
                    "price": 12.0,
                    "shares": 150,
                    "amount": 1800.0,
                    "commission": 0.0,
                },
            ],
            start_date=date(2026, 4, 15),
            end_date=date(2026, 4, 16),
        )

        self.assertEqual(result.total_actual_pnl, 250.0)
        self.assertEqual(result.unresolved_sell_trades, 0)
        self.assertEqual(result.open_positions, {"510300": 50})

    def test_unresolved_sell_is_excluded_from_actual_pnl(self) -> None:
        result = match_strategy_to_actual(
            trade_outcomes=[],
            actual_trades=[
                {
                    "id": 1,
                    "symbol": "510300",
                    "action": "sell",
                    "trade_date": "2026-04-16",
                    "trade_time": "10:00:00",
                    "price": 12.0,
                    "shares": 100,
                    "amount": 1200.0,
                    "commission": 0.0,
                },
            ],
            start_date=date(2026, 4, 16),
            end_date=date(2026, 4, 16),
        )

        self.assertEqual(result.total_actual_pnl, 0.0)
        self.assertEqual(result.unresolved_sell_trades, 1)

    def test_determine_outcome_returns_monetary_pnl_amount(self) -> None:
        outcome = _determine_outcome(
            action="buy",
            entry=10.0,
            stop_loss=9.0,
            take_profit=11.0,
            suggested_shares=100,
            suggested_value=1000.0,
            future_closes=[11.0],
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["outcome"], "win")
        self.assertAlmostEqual(outcome["pnl_pct"], 0.1, places=4)
        self.assertEqual(outcome["pnl_amount"], 100.0)


if __name__ == "__main__":
    unittest.main()
