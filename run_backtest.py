#!/usr/bin/env python
"""Run backtest over a historical date range.

Usage:
    python run_backtest.py --config config.toml --start 2025-01-01 --end 2026-04-01
    python run_backtest.py --config config.toml --start 2024-01-01 --end 2026-04-27 --data-source akshare --persist
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chaogu_alert.backtest import (
    BacktestRunner,
    format_backtest_report,
    save_backtest_to_mysql,
)
from chaogu_alert.config import AppConfig, load_config
from chaogu_alert.data import (
    AkshareEtfDataProvider,
    DemoMarketDataProvider,
    MultiSourceMarketDataProvider,
    SinaEtfDataProvider,
)


def _build_provider(config: AppConfig, data_source: str):
    if data_source == "demo":
        return DemoMarketDataProvider()

    symbol_metadata = {}
    providers = []
    if "akshare" in config.app.data_sources or data_source in ("akshare", "auto"):
        akshare_provider = AkshareEtfDataProvider(
            config.akshare, symbol_metadata=symbol_metadata
        )
        if data_source == "akshare":
            return akshare_provider
        providers.append(akshare_provider)
    if "sina" in config.app.data_sources or data_source in ("sina", "auto"):
        sina_provider = SinaEtfDataProvider(
            config.akshare, symbol_metadata=symbol_metadata
        )
        if data_source == "sina":
            return sina_provider
        providers.append(sina_provider)

    if providers:
        return MultiSourceMarketDataProvider(providers)
    return DemoMarketDataProvider()


def main():
    parser = argparse.ArgumentParser(description="Chaogu-Alert backtest runner")
    parser.add_argument("--config", default="config.toml", help="Config file path")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--data-source", default="demo", help="demo/akshare/sina/auto")
    parser.add_argument("--persist", action="store_true", help="Save results to MySQL")
    args = parser.parse_args()

    config = load_config(args.config)
    config.app.data_source = args.data_source
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    if start_date >= end_date:
        print("错误: start 必须早于 end")
        sys.exit(1)

    print(f"数据源: {args.data_source}")
    print(f"回测区间: {start_date} ~ {end_date}")
    print("初始化数据提供者...")

    provider = _build_provider(config, args.data_source)
    runner = BacktestRunner(config, provider)

    print("运行回测 (首次运行需缓存历史数据，可能较慢)...")
    result = runner.run(start_date, end_date)

    print(format_backtest_report(result))

    if args.persist and config.mysql.enabled:
        print("保存回测结果到 MySQL...")
        run_id = save_backtest_to_mysql(result, args.data_source, config.mysql)
        print(f"已保存，回测 ID: {run_id}")
    elif args.persist:
        print("警告: MySQL 未启用，跳过持久化。请在 config.toml 中配置 [mysql]")


if __name__ == "__main__":
    main()
