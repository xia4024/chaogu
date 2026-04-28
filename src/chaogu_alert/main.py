from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
import argparse
import json

from .config import AppConfig, get_effective_account_id, load_config, with_account
from .data import (
    AkshareEtfDataProvider,
    CsvMarketDataProvider,
    DemoMarketDataProvider,
    MultiSourceMarketDataProvider,
    SinaEtfDataProvider,
    build_symbol_metadata,
)
from .emailer import send_email as deliver_email
from .engine import ScannerEngine
from .persistence import build_scan_persistence
from .report import build_subject, render_html, render_text
from .universe import UniverseResolver


def main() -> int:
    args = _parse_args()
    config = load_config(args.config)
    as_of = date.fromisoformat(args.date) if args.date else date.today()
    text_body, _ = run_scan_once(
        config=config,
        as_of=as_of,
        data_source=args.data_source,
        csv_path=args.csv_path,
        should_send_email=args.send_email,
    )
    print(text_body)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run A-share alert scan.")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--date", help="Scan date, format YYYY-MM-DD")
    parser.add_argument("--data-source", choices=("demo", "csv", "akshare", "sina", "auto"))
    parser.add_argument("--csv-path", help="CSV path when data source is csv.")
    parser.add_argument("--send-email", action="store_true")
    return parser.parse_args()


def _build_provider(config: AppConfig, args: argparse.Namespace):
    data_sources = _resolve_data_sources(config, args.data_source)
    if data_sources == ["demo"]:
        return DemoMarketDataProvider()

    if data_sources == ["csv"]:
        if not args.csv_path:
            raise ValueError("csv data source requires --csv-path")
        return CsvMarketDataProvider(args.csv_path)

    universe = UniverseResolver(config)
    metadata = build_symbol_metadata(
        config.universe.benchmark_symbol,
        config.universe.broad_etfs,
        config.universe.sector_etfs,
        extra_symbols=list(universe.all_scan_symbols()),
    )
    providers = []
    for source in data_sources:
        if source == "akshare":
            providers.append(AkshareEtfDataProvider(config.akshare, symbol_metadata=metadata))
            continue
        if source == "sina":
            providers.append(SinaEtfDataProvider(config.akshare, symbol_metadata=metadata))
            continue
        raise ValueError(f"Unsupported remote data source: {source}")

    if len(providers) == 1:
        return providers[0]
    return MultiSourceMarketDataProvider(providers)


def _resolve_data_sources(config: AppConfig, requested: str | None) -> list[str]:
    data_source = requested or config.app.data_source
    if data_source == "auto":
        configured = [item.strip() for item in config.app.data_sources if item.strip()]
        return configured or ["akshare", "sina"]
    return [data_source]


def _resolve_holdings_from_mysql(
    config: AppConfig, account_id: int | None = None,
) -> list | None:
    if not config.mysql.enabled:
        return None
    try:
        from .db import load_holdings_from_db

        rows = load_holdings_from_db(config.mysql, account_id=account_id or 1)
        if not rows:
            return None
        from .config import HoldingSettings

        return [HoldingSettings(
            symbol=row["symbol"],
            shares=int(row["shares"]),
            cost_basis=float(row["cost_basis"]),
            min_t_trade_pct=float(row["min_t_trade_pct"]),
            max_t_trade_pct=float(row["max_t_trade_pct"]),
        ) for row in rows]
    except Exception:
        return None


def run_scan_once(
    config: AppConfig,
    as_of: date,
    data_source: str | None = None,
    csv_path: str | None = None,
    should_send_email: bool = False,
) -> tuple[str, str]:
    args = argparse.Namespace(data_source=data_source, csv_path=csv_path)

    data_mode = "demo" if data_source == "demo" else "real"
    account_id = get_effective_account_id(config)
    config = with_account(config, account_id)

    if data_mode == "real":
        mysql_holdings = _resolve_holdings_from_mysql(config, account_id=account_id)
        if mysql_holdings is not None:
            config = replace(
                config,
                portfolio=replace(config.portfolio, holdings=mysql_holdings),
            )

    provider = _build_provider(config, args)
    persistence = build_scan_persistence(config, mysql_enabled=config.mysql.enabled)
    engine = ScannerEngine(
        config, provider, persistence=persistence, data_mode=data_mode,
    )
    report = engine.scan(as_of, account_id=account_id)
    subject = build_subject(config, report)
    text_body = render_text(config, report)
    html_body = render_html(config, report)

    _write_outputs(config, report, subject, text_body)
    if should_send_email:
        deliver_email(config.email, subject, text_body, html_body)
    return text_body, subject


def _write_outputs(config: AppConfig, report, subject: str, text_body: str) -> None:
    reports_dir = Path(config.app.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.as_of.isoformat()
    text_path = reports_dir / f"scan_{stamp}.txt"
    json_path = reports_dir / f"scan_{stamp}.json"
    text_path.write_text(f"{subject}\n\n{text_body}\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported type: {type(value)!r}")
