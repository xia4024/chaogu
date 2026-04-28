# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A-share ETF signal scanner → email alert tool. Scans ETFs with multiple strategies, generates BUY/SELL signals with position sizing, sends formatted email reports. Alert-only — no auto trading.

- **Capital**: 100k RMB, moderate risk
- **Decision time**: 14:50 (configurable via `schedule.market_close_time`)
- **Python**: >=3.11, `setuptools` build, package dir at `src/`

## Python 环境

**必须使用 Python 3.11**，路径：`C:\Users\admin\AppData\Local\Programs\Python\Python311\python`

系统默认 `python` 可能指向 LibreOffice 自带的 Python 3.12（`C:\Program Files\LibreOffice\program\python.exe`），无法正常运行。

## Commands

```bash
# 使用正确 Python 启动 Web 面板
/c/Users/admin/AppData/Local/Programs/Python/Python311/python web.py

# Demo scan (no real data, no email)
python run_scan.py --config config.example.toml --date 2026-04-15

# Scheduled scan (with time/trading-day gates)
python run_scheduled.py --config config.toml --data-source auto --send-email

# Force run (bypass gates, useful for testing)
python run_scheduled.py --config config.toml --data-source auto --send-email --force

# Run tests
python -m pytest tests/ -v

# Install with akshare
pip install -e ".[akshare]"

# Install with OCR support (PaddleOCR)
pip install -e ".[ocr]"
```

## Architecture

```
run_scan.py / run_scheduled.py   (entry points, add src/ to sys.path)
        ↓
config.py                         (AppConfig dataclass, TOML + ${ENV} substitution)
        ↓
engine.py:ScannerEngine.scan()    (orchestrates one scan)
  ├── UniverseResolver            (symbol groups: benchmark, broad_etfs, sector_etfs, holdings, default_scan)
  ├── data.py (MarketDataProvider Protocol)
  │     ├── DemoMarketDataProvider   (sine-wave synthetic data)
  │     ├── CsvMarketDataProvider    (CSV file input)
  │     ├── AkshareEtfDataProvider   (akshare API, with MD5-based file cache + request pacing)
  │     ├── SinaEtfDataProvider      (sina API, extends AkshareEtfDataProvider)
  │     └── MultiSourceMarketDataProvider (fallback chain: tries each provider, resolves per-symbol)
  ├── strategies/                    (Strategy Registry pattern)
  │     ├── base.py:Strategy(ABC)    (generate(context) → list[SignalIdea])
  │     ├── registry.py              (_REGISTRY dict, auto-registration via register_strategy())
  │     ├── etf_trend.py             (MA crossover + volume + breakout, signal_group="open")
  │     ├── etf_rotation.py          (relative strength vs benchmark, top-N, signal_group="open")
  │     └── etf_t_trade.py           (intraday pullback/rebound vs VWAP, signal_group="t_trade")
  ├── risk.py:ModerateRiskManager    (position sizing, risk budget, sector exposure caps)
  └── report.py                      (Chinese text/HTML email rendering)
        ↓
emailer.py + persistence.py (JsonlScanPersistence)
```

### Strategy Registration

Strategies self-register at import time. To add a new strategy:

1. Create `src/chaogu_alert/strategies/my_strategy.py` with a `Strategy` subclass
2. Define a `StrategyDefinition` and call `register_strategy(DEFINITION)` at module level
3. Import it in `strategies/__init__.py` so it registers itself
4. Add config section in `config.py` and `config.toml`

### Data Flow

`ScannerEngine.scan()` builds a `ScanContext` (frozen dataclass) with all histories, intraday data, and market regime info. Each strategy reads from this context and returns `SignalIdea` objects. The risk manager converts ideas to `TradePlan` with sized positions. Output is a `ScanReport` dataclass.

### Two Signal Groups

- **"open"** — new position / add position ideas (etf_trend, etf_rotation). Risk manager allocates from available capital.
- **"t_trade"** — intraday T trades against existing holdings (etf_t_trade only). Only fires for symbols in `[[portfolio.holdings]]`. Size is 10-20% of current holding.

### Key Design Decisions

- All data models are frozen dataclasses with `slots=True` (`models.py`, `scan_context.py`)
- Data providers use a Protocol (no base class), checked at type-check time via `TYPE_CHECKING`
- `MultiSourceMarketDataProvider` does per-symbol fallback — akshare fails for symbol A, sina may still serve it
- `intraday_histories` are overlaid onto daily histories via `overlay_intraday_on_daily()` — replaces the last daily bar with aggregated intraday OHLCV
- TOML config supports `${ENV_VAR}` placeholders resolved at load time
- Schedule enforcement: trading calendar check + market-close time gate (both bypassable with `--force`)
- Windows task scheduler scripts in `scripts/` for daily automated runs

## Web Dashboard

Flask app (`web.py`) with Jinja2 templates (`templates/`), dark theme CSS (`static/style.css`).

- **Port**: 5000 (all interfaces 0.0.0.0)
- **Auth**: Session-based account switching (`session["account_id"]`), `before_request` hook loads current account
- **CSS**: RuoYi-Vue-Plus inspired dark theme. CSS variables at `:root` in `style.css`, responsive at 900px breakpoint with hamburger nav
- **Templates**: 14 pages — scan list/`, scan detail `/scan/<id>`, holdings `/holdings`, performance `/performance`, optimization `/optimize` + detail, backtest `/backtest` + detail, symbols `/symbols` + history, trades `/trades`, compare `/compare`

### Key Routes

| Route | Page | Description |
|---|---|---|
| `/` | scan_list.html | Dashboard + scan records list |
| `/scan/<id>` | scan_detail.html | One scan's trade plans, execution buttons |
| `/holdings` | holdings.html | CRUD holdings per account |
| `/performance` | performance.html | Strategy PnL stats per account |
| `/optimize` | optimize.html | Strategy parameter optimization results |
| `/backtest` | backtest_list.html | Backtest runs with `?year=&month=` filter |
| `/trades` | trades.html | OCR trade upload + trade list |
| `/compare` | compare.html | Strategy vs actual trade comparison |
| `/symbols` | symbols.html | Price charts per symbol |
| `/account/<id>` | (redirect) | Switch active account |

### Account Switching

Session-based via `@app.before_request load_account()`. Account switcher `<select>` in `base.html` topbar. `get_account_id()` returns current account ID for all DB queries. Context processor injects `accounts` and `current_account_id` into all templates.

## MySQL Persistence

`src/chaogu_alert/db.py` — `pymysql` with `DictCursor`. Idempotent schema via `CREATE TABLE IF NOT EXISTS`.

### Tables

| Table | Purpose |
|---|---|
| `scan_reports` | Each scan run output |
| `trade_plans` | Per-scan BUY/SELL plans with execution status |
| `trade_outcomes` | Settled PnL per trade plan |
| `holdings` | Current positions per account |
| `accounts` | Multi-account config |
| `strategy_performance` | Aggregated strategy stats per account |
| `backtest_runs` | Backtest execution records |
| `backtest_signals` | Per-backtest signal rows |
| `backtest_trades` | Per-backtest trade rows |
| `price_snapshots` | Historical close prices per symbol |
| `actual_trades` | OCR-extracted real trade records |
| `trade_images` | Uploaded screenshot metadata |

### Key DB Functions

- `list_scan_reports(conn, limit, data_mode)` — scan list with `plan_count` subquery
- `list_backtest_runs(conn, limit, year, month)` — backtest list with YEAR/MONTH filter on `start_date`
- `get_backtest_years(conn)` — distinct years from backtest_runs for filter dropdown
- `get_dashboard_stats(conn, data_mode)` — weekly/d7/all-time win rates + holdings count
- `recalculate_performance(conn, account_id)` — aggregates trade_outcomes into strategy_performance with weekly/monthly PnL JSON
- `get_strategy_performance(conn, account_id)` — per-strategy stats with new metrics (avg_holding_days, max_drawdown_pct, sharpe_ratio, best/worst trade)

## OCR Pipeline

`src/chaogu_alert/ocr.py` — Lazy imports PaddleOCR for Chinese text extraction from brokerage screenshots.

- `extract_trades(image_path)` — Main entry, returns list of trade dicts
- `_cluster_rows(text_boxes)` — Groups text boxes by Y-coordinate
- Regex extraction: 6-digit symbols, price (xxx.xxx), buy/sell action, date (YYYY-MM-DD), time, shares (100-1000000 divisible by 100)
- `_validate(trade)` — Checks symbol, action, trade_date, price>0, shares>0

Image uploads stored in `uploads/trades/` directory.

## Comparison Engine

`src/chaogu_alert/comparison.py` — Matches strategy signals to actual trades with ±1 day tolerance.

- `MatchedRow` dataclass: trade_date, symbol, strategy_signal, actual_trade, deviation, pnl_impact
- `ComparisonResult` dataclass: total PnLs, execution_gap, signal_execution_rate, defect_summary
- 6 deviation types: match, miss (漏单), hold_loss (不止损), overtrade (过度交易), early_exit (过早止盈), slippage (滑点)
