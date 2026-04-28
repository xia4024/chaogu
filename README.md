# A-share Email Alert MVP

This project is a personal `A-share ETF signal scanner -> email alert -> manual trade` tool.

Current scope:

- Market: A-share ETFs
- Capital: 100k
- Style: moderate risk
- Mode: alert only, no auto order
- Notify: email
- Decision time: `14:50`

## Strategy Groups

The system now keeps two independent strategy groups and marks them separately in email:

1. `Open Signals`
   - `etf_trend`
   - `etf_rotation`
   - Used for new position / add position ideas

2. `T Signals`
   - `etf_t_trade`
   - Used for intraday `BUY` / `SELL` T actions against existing holdings
   - Suggested size is based on `10%-20%` of the configured holding

## Important Rule

T-trade alerts only work for symbols that you explicitly put into `[portfolio]` and `[[portfolio.holdings]]`.

If no holdings are configured, the system will still generate `Open Signals`, but `T Signals` will stay empty.

## Quick Start

Demo mode:

```bash
python run_scan.py --config config.example.toml --date 2026-04-15
```

Scheduled scan:

```bash
python run_scheduled.py --config config.toml --data-source auto --send-email
```

Force test:

```bash
python run_scheduled.py --config config.toml --data-source auto --send-email --force
```

## Config Notes

Main config file: `config.toml`

Key sections:

- `[schedule]`
  - `market_close_time = "14:50"`
- `[app]`
  - `data_source = "auto"`
  - `data_sources = ["akshare", "sina"]`
  - `scan_journal_path` can be enabled for later review/persistence
- `[portfolio]`
  - `available_cash`
- `[[portfolio.holdings]]`
  - `symbol`
  - `shares`
  - `cost_basis`
  - `min_t_trade_pct`
  - `max_t_trade_pct`
- `[strategy.t_trade]`
  - intraday pullback / rebound thresholds
  - VWAP distance filter
  - trend guard
- `[universe.groups]`
  - optional custom symbol groups for future strategies

Example:

```toml
[app]
data_source = "auto"
data_sources = ["akshare", "sina"]
# scan_journal_path = "reports/scan_history.jsonl"

[portfolio]
available_cash = 20000

[[portfolio.holdings]]
symbol = "510300"
shares = 3000
cost_basis = 4.68
min_t_trade_pct = 0.10
max_t_trade_pct = 0.20
```

## Data Sources

Supported:

- `demo`
- `csv`
- `akshare`
- `sina`
- `auto`

`auto` uses the configured provider order and will fall back source-by-source. The default production order is:

- `akshare`
- `sina`

## Extensibility

- Strategies are registered through a strategy registry instead of being hard-coded in the engine.
- The engine now builds a unified scan context, so new strategies can read the same market snapshot without changing the engine signature.
- Holdings are part of the scan universe, which makes it easier to support future holding-only strategies and tracking workflows.
- If `scan_journal_path` is configured, each completed scan is appended as structured JSONL for later persistence, review, and follow-up analysis.

AkShare install:

```bash
C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe -m pip install akshare
```

AkShare request control:

- Requests are cached under `cache/akshare`
- Upstream access is logged to `logs/upstream_requests.jsonl`
- `min_request_interval_seconds = 1.2` keeps requests paced
- `history_cache_ttl_seconds = 43200` reuses daily history for 12 hours
- `intraday_cache_ttl_seconds = 600` reuses intraday pulls for 10 minutes

For production, prefer one scheduled full scan after market close. If you need manual retries, keep full scans at least `10-15` minutes apart.

## Windows Task

Manual run script:

```powershell
.\scripts\run_daily_scan.ps1 -ConfigPath "config.toml" -DataSource "auto" -SendEmail
```

Install scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1 -TaskName "ChaoguAlertDaily" -StartTime "14:50" -ConfigPath "config.toml" -DataSource "auto"
```

## Output

Reports are written to:

- `reports/scan_YYYY-MM-DD.txt`
- `reports/scan_YYYY-MM-DD.json`

Scheduled logs are written to:

- `logs/scheduled_scan.log`
