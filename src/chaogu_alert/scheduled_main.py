from __future__ import annotations

from datetime import date
import argparse

from .config import load_config
from .scheduler import run_scheduled_scan


def main() -> int:
    args = _parse_args()
    config = load_config(args.config)
    date_override = date.fromisoformat(args.date) if args.date else None
    data_source = args.data_source or config.app.data_source

    status, message = run_scheduled_scan(
        config=config,
        data_source=data_source,
        csv_path=args.csv_path,
        should_send_email=args.send_email,
        date_override=date_override,
        force=args.force,
    )
    print(message)
    return status


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduled A-share alert scan.")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--date", help="Override scan date, format YYYY-MM-DD")
    parser.add_argument("--data-source", choices=("demo", "csv", "akshare", "sina", "auto"))
    parser.add_argument("--csv-path", help="CSV path when data source is csv.")
    parser.add_argument("--send-email", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore trading day and time gate.")
    return parser.parse_args()
