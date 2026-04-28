from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import json
import shutil
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chaogu_alert.config import load_config
from chaogu_alert.data import (
    AkshareEtfDataProvider,
    DemoMarketDataProvider,
    MultiSourceMarketDataProvider,
    SinaEtfDataProvider,
    build_symbol_metadata,
)
from chaogu_alert.engine import ScannerEngine
from chaogu_alert.persistence import build_scan_persistence
from chaogu_alert.report import build_subject, render_html, render_text
from chaogu_alert.scheduler import evaluate_schedule

CN_CODE = "\u4ee3\u7801"
CN_NAME = "\u540d\u79f0"
CN_DATE = "\u65e5\u671f"
CN_OPEN = "\u5f00\u76d8"
CN_HIGH = "\u6700\u9ad8"
CN_LOW = "\u6700\u4f4e"
CN_CLOSE = "\u6536\u76d8"
CN_VOLUME = "\u6210\u4ea4\u91cf"
CN_TIME = "\u65f6\u95f4"
CN_AVG_PRICE = "\u5747\u4ef7"
CHINA_TZ = timezone(timedelta(hours=8))


@contextmanager
def workspace_tempdir():
    root = ROOT / ".tmp-tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid4().hex
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        if orient != "records":
            raise ValueError("Only records orient is supported.")
        return list(self.rows)


class FakeAkshareClient:
    def __init__(self):
        self.spot_calls = 0
        self.daily_calls: dict[str, int] = {}
        self.minute_calls: dict[str, int] = {}

    def fund_etf_spot_em(self):
        self.spot_calls += 1
        return FakeFrame(
            [
                {CN_CODE: "510300", CN_NAME: "CSI300 ETF"},
                {CN_CODE: "512880", CN_NAME: "Brokerage ETF"},
            ]
        )

    def fund_etf_hist_em(self, symbol, period, start_date, end_date, adjust):
        self.daily_calls[symbol] = self.daily_calls.get(symbol, 0) + 1
        rows = {
            "510300": [
                {CN_DATE: "2026-04-14", CN_OPEN: 4.70, CN_HIGH: 4.76, CN_LOW: 4.68, CN_CLOSE: 4.75, CN_VOLUME: 9000000},
                {CN_DATE: "2026-04-15", CN_OPEN: 4.75, CN_HIGH: 4.81, CN_LOW: 4.73, CN_CLOSE: 4.80, CN_VOLUME: 9100000},
            ],
            "512880": [
                {CN_DATE: "2026-04-14", CN_OPEN: 1.53, CN_HIGH: 1.56, CN_LOW: 1.52, CN_CLOSE: 1.55, CN_VOLUME: 8600000},
                {CN_DATE: "2026-04-15", CN_OPEN: 1.55, CN_HIGH: 1.60, CN_LOW: 1.54, CN_CLOSE: 1.59, CN_VOLUME: 8800000},
            ],
        }
        return FakeFrame(rows[symbol])

    def fund_etf_hist_min_em(self, symbol, start_date, end_date, period, adjust):
        self.minute_calls[symbol] = self.minute_calls.get(symbol, 0) + 1
        rows = {
            "510300": [
                {CN_TIME: "2026-04-15 14:33:00", CN_OPEN: 4.77, CN_HIGH: 4.78, CN_LOW: 4.75, CN_CLOSE: 4.76, CN_VOLUME: 20000, CN_AVG_PRICE: 4.77},
                {CN_TIME: "2026-04-15 14:34:00", CN_OPEN: 4.76, CN_HIGH: 4.77, CN_LOW: 4.74, CN_CLOSE: 4.75, CN_VOLUME: 21000, CN_AVG_PRICE: 4.76},
            ],
            "512880": [
                {CN_TIME: "2026-04-15 14:33:00", CN_OPEN: 1.57, CN_HIGH: 1.60, CN_LOW: 1.57, CN_CLOSE: 1.59, CN_VOLUME: 18000, CN_AVG_PRICE: 1.58},
                {CN_TIME: "2026-04-15 14:34:00", CN_OPEN: 1.59, CN_HIGH: 1.61, CN_LOW: 1.58, CN_CLOSE: 1.60, CN_VOLUME: 20000, CN_AVG_PRICE: 1.59},
            ],
        }
        return FakeFrame(rows.get(symbol, []))


class FailingAkshareClient(FakeAkshareClient):
    def fund_etf_spot_em(self):
        raise RuntimeError("akshare unavailable")

    def fund_etf_hist_em(self, symbol, period, start_date, end_date, adjust):
        raise RuntimeError("akshare unavailable")

    def fund_etf_hist_min_em(self, symbol, start_date, end_date, period, adjust):
        raise RuntimeError("akshare unavailable")


class FakeSinaClient:
    def fund_etf_hist_sina(self, symbol):
        rows = {
            "sh510300": [
                {"date": "2026-04-14", "open": 4.68, "high": 4.74, "low": 4.66, "close": 4.72, "volume": 8800000},
                {"date": "2026-04-15", "open": 4.72, "high": 4.79, "low": 4.71, "close": 4.77, "volume": 9000000},
            ]
        }
        return FakeFrame(rows.get(symbol, []))

    def stock_zh_a_minute(self, symbol, period, adjust):
        rows = {
            "sh510300": [
                {"day": "2026-04-15 14:33:00", "open": 4.76, "high": 4.77, "low": 4.75, "close": 4.76, "volume": 20000, "amount": 95200},
                {"day": "2026-04-15 14:34:00", "open": 4.76, "high": 4.78, "low": 4.75, "close": 4.77, "volume": 21000, "amount": 100170},
            ]
        }
        return FakeFrame(rows.get(symbol, []))


class AlwaysTradingCalendar:
    def is_trading_day(self, target: date) -> bool:
        return True


class NeverTradingCalendar:
    def is_trading_day(self, target: date) -> bool:
        return False


class EngineTests(unittest.TestCase):
    def test_demo_scan_generates_open_and_t_trade_plans(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        engine = ScannerEngine(config, DemoMarketDataProvider())
        report = engine.scan(date(2026, 4, 15))

        self.assertGreaterEqual(len(report.candidates), 3)
        self.assertGreaterEqual(len(report.trade_plans), 2)
        self.assertEqual(len(report.strategy_runs), 3)
        self.assertIn(report.market_regime, {"risk_on", "risk_off", "unknown"})
        self.assertTrue(
            all(plan.suggested_shares % config.risk.lot_size == 0 for plan in report.trade_plans)
        )
        self.assertTrue(any(plan.signal_group == "open" for plan in report.trade_plans))
        self.assertTrue(any(plan.signal_group == "t_trade" for plan in report.trade_plans))

    def test_subject_includes_open_and_t_counts(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        report = ScannerEngine(config, DemoMarketDataProvider()).scan(date(2026, 4, 15))
        subject = build_subject(config, report)
        self.assertIn("开仓", subject)
        self.assertIn("/ 做T ", subject)

    def test_report_uses_chinese_labels_and_strategy_guide(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        report = ScannerEngine(config, DemoMarketDataProvider()).scan(date(2026, 4, 15))
        text_body = render_text(config, report)
        html_body = render_html(config, report)

        self.assertIn("策略说明", text_body)
        self.assertIn("趋势突破", text_body)
        self.assertIn("轮动强势", text_body)
        self.assertIn("持仓做T", text_body)
        self.assertIn("本次策略执行概览", text_body)
        self.assertIn("操作建议", html_body)
        self.assertIn("触发原因", html_body)

    def test_akshare_provider_transforms_rows(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        config.akshare.min_request_interval_seconds = 0.0
        metadata = build_symbol_metadata(
            config.universe.benchmark_symbol,
            config.universe.broad_etfs,
            config.universe.sector_etfs,
        )
        provider = AkshareEtfDataProvider(
            config.akshare,
            symbol_metadata=metadata,
            client=FakeAkshareClient(),
        )

        histories = provider.load_histories(["510300", "512880"], date(2026, 4, 15), lookback=5)
        intraday = provider.load_intraday_histories(["510300", "512880"], date(2026, 4, 15), datetime.strptime("14:35", "%H:%M").time())

        self.assertEqual(histories["510300"][-1].name, "CSI300 ETF")
        self.assertEqual(histories["510300"][-1].asset_type, "broad_etf")
        self.assertEqual(histories["512880"][-1].sector, "brokerage")
        self.assertEqual(histories["512880"][-1].close, 1.59)
        self.assertEqual(len(intraday["512880"]), 2)

    def test_akshare_provider_reuses_cache_and_logs_access(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        with workspace_tempdir() as temp_dir:
            config.akshare.cache_dir = str(Path(temp_dir) / "cache")
            config.akshare.request_log_path = str(Path(temp_dir) / "logs" / "upstream_requests.jsonl")
            config.akshare.min_request_interval_seconds = 0.0
            config.akshare.spot_cache_ttl_seconds = 3600
            config.akshare.history_cache_ttl_seconds = 3600
            config.akshare.intraday_cache_ttl_seconds = 3600

            metadata = build_symbol_metadata(
                config.universe.benchmark_symbol,
                config.universe.broad_etfs,
                config.universe.sector_etfs,
            )
            client = FakeAkshareClient()

            provider = AkshareEtfDataProvider(
                config.akshare,
                symbol_metadata=metadata,
                client=client,
            )
            provider.load_histories(["510300"], date(2026, 4, 15), lookback=5)
            provider.load_intraday_histories(
                ["510300"],
                date(2026, 4, 15),
                datetime.strptime("14:35", "%H:%M").time(),
            )

            provider = AkshareEtfDataProvider(
                config.akshare,
                symbol_metadata=metadata,
                client=client,
            )
            provider.load_histories(["510300"], date(2026, 4, 15), lookback=5)
            provider.load_intraday_histories(
                ["510300"],
                date(2026, 4, 15),
                datetime.strptime("14:35", "%H:%M").time(),
            )

            self.assertEqual(client.spot_calls, 1)
            self.assertEqual(client.daily_calls["510300"], 1)
            self.assertEqual(client.minute_calls["510300"], 1)

            log_path = Path(config.akshare.request_log_path)
            entries = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(entry["endpoint"] == "spot_lookup" and not entry["cache_hit"] for entry in entries))
            self.assertTrue(any(entry["endpoint"] == "spot_lookup" and entry["cache_hit"] for entry in entries))
            self.assertTrue(any(entry["endpoint"] == "daily_history" and entry["cache_hit"] for entry in entries))
            self.assertTrue(any(entry["endpoint"] == "intraday_history" and entry["cache_hit"] for entry in entries))

    def test_multi_source_provider_falls_back_to_sina(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        with workspace_tempdir() as temp_dir:
            config.akshare.cache_dir = str(Path(temp_dir) / "cache")
            config.akshare.request_log_path = str(Path(temp_dir) / "logs" / "upstream_requests.jsonl")
            config.akshare.min_request_interval_seconds = 0.0
            metadata = build_symbol_metadata(
                config.universe.benchmark_symbol,
                config.universe.broad_etfs,
                config.universe.sector_etfs,
            )
            provider = MultiSourceMarketDataProvider(
                [
                    AkshareEtfDataProvider(
                        config.akshare,
                        symbol_metadata=metadata,
                        client=FailingAkshareClient(),
                    ),
                    SinaEtfDataProvider(
                        config.akshare,
                        symbol_metadata=metadata,
                        client=FakeSinaClient(),
                    ),
                ]
            )

            histories = provider.load_histories(["510300"], date(2026, 4, 15), lookback=5)
            intraday = provider.load_intraday_histories(
                ["510300"],
                date(2026, 4, 15),
                datetime.strptime("14:35", "%H:%M").time(),
            )

            self.assertEqual(histories["510300"][-1].close, 4.77)
            self.assertEqual(len(intraday["510300"]), 2)

    def test_scan_journal_persistence_writes_strategy_runs(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        with workspace_tempdir() as temp_dir:
            journal_path = Path(temp_dir) / "scan_history.jsonl"
            config.app.scan_journal_path = str(journal_path)
            engine = ScannerEngine(
                config,
                DemoMarketDataProvider(),
                persistence=build_scan_persistence(config),
            )
            report = engine.scan(date(2026, 4, 15))

            self.assertTrue(journal_path.exists())
            payload = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(payload["as_of"], "2026-04-15")
            self.assertEqual(len(payload["strategy_runs"]), len(report.strategy_runs))

    def test_schedule_skips_non_trading_day(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        now = datetime(2026, 4, 18, 14, 40, tzinfo=CHINA_TZ)
        decision = evaluate_schedule(
            config=config,
            data_source="akshare",
            now=now,
            calendar=NeverTradingCalendar(),
        )
        self.assertFalse(decision.should_run)
        self.assertIn("not a trading day", decision.reason)

    def test_schedule_skips_before_close(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        now = datetime(2026, 4, 15, 14, 30, tzinfo=CHINA_TZ)
        decision = evaluate_schedule(
            config=config,
            data_source="akshare",
            now=now,
            calendar=AlwaysTradingCalendar(),
        )
        self.assertFalse(decision.should_run)
        self.assertIn("earlier than market close gate", decision.reason)

    def test_schedule_runs_after_close(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        now = datetime(2026, 4, 15, 14, 40, tzinfo=CHINA_TZ)
        decision = evaluate_schedule(
            config=config,
            data_source="akshare",
            now=now,
            calendar=AlwaysTradingCalendar(),
        )
        self.assertTrue(decision.should_run)
        self.assertEqual(decision.as_of.isoformat(), "2026-04-15")


if __name__ == "__main__":
    unittest.main()
