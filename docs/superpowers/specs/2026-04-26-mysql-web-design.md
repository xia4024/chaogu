# Chaogu-Alert MySQL Persistence + Web Frontend

Date: 2026-04-26

## Overview

Add MySQL persistence for scan results and a Flask web frontend for browsing history and managing holdings.

## Tech Stack

- **Backend**: Python 3.11, Flask + Jinja2
- **Database**: MySQL 8.0 @ 192.168.1.137:3306 / claude
- **Frontend**: Dark dashboard style, server-rendered Jinja2 templates

## Database Tables

### scan_reports
| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| scan_date | DATE UNIQUE | Scan date |
| benchmark_symbol | VARCHAR(20) | |
| market_regime | VARCHAR(20) | risk_on / risk_off / unknown |
| candidate_count | INT | |
| filtered_count | INT | |
| created_at | DATETIME | |

### trade_plans
| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| report_id | INT FK → scan_reports.id | |
| strategy_id | VARCHAR(50) | |
| signal_group | VARCHAR(20) | open / t_trade |
| action | VARCHAR(10) | buy / sell |
| symbol | VARCHAR(20) | |
| name | VARCHAR(100) | |
| asset_type | VARCHAR(30) | |
| sector | VARCHAR(50) | |
| score | DECIMAL(5,2) | |
| entry_price | DECIMAL(10,3) | |
| stop_loss | DECIMAL(10,3) | |
| take_profit | DECIMAL(10,3) | |
| suggested_shares | INT | |
| suggested_value | DECIMAL(12,2) | |
| position_pct | DECIMAL(6,4) | |
| risk_amount | DECIMAL(12,2) | |
| reasons | TEXT | JSON array |
| tags | TEXT | JSON array |

### holdings
| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| symbol | VARCHAR(20) UNIQUE | |
| shares | INT | |
| cost_basis | DECIMAL(10,3) | |
| min_t_trade_pct | DECIMAL(4,3) | Default 0.10 |
| max_t_trade_pct | DECIMAL(4,3) | Default 0.20 |
| updated_at | DATETIME | |

## New/Modified Files

| File | Purpose |
|------|---------|
| `config.toml` | Add `[mysql]` section |
| `src/chaogu_alert/config.py` | Add `MysqlSettings` dataclass |
| `src/chaogu_alert/db.py` | Connection pool, auto-create tables, CRUD helpers |
| `src/chaogu_alert/mysql_persistence.py` | `MySqlScanPersistence` implementing `ScanPersistence` protocol |
| `src/chaogu_alert/engine.py` | Load holdings from MySQL (fallback to config.toml) |
| `src/chaogu_alert/main.py` | Wire MySqlScanPersistence when mysql configured |
| `web.py` | Flask app entry point |
| `templates/base.html` | Base layout (dark dashboard, navigation) |
| `templates/scan_list.html` | Scan history list |
| `templates/scan_detail.html` | Single scan detail with trade plan tables |
| `templates/holdings.html` | Holding CRUD with instructions |
| `static/style.css` | Dark dashboard CSS |
| `pyproject.toml` | Add pymysql, flask dependencies |

## Routes

```
GET  /                       scan_list.html
GET  /scan/<id>              scan_detail.html
GET  /holdings               holdings.html (list + add form)
POST /holdings/new           Create holding
POST /holdings/<id>/edit     Update holding
POST /holdings/<id>/delete   Delete holding
```

## Key Design Decisions

- **MySQL as holdings source of truth**: Scanner reads holdings from MySQL; falls back to config.toml `[[portfolio.holdings]]` if MySQL unavailable
- **Parallel persistence**: JSONL and MySQL both run (if configured); neither blocks the other
- **Auto-create tables**: `db.py` runs `CREATE TABLE IF NOT EXISTS` on first connection
- **No ORM**: Raw SQL via pymysql for simplicity and zero-magic debugging
- **Connection pooling**: Simple connection-per-request; connection params from config.toml
- **Deduplication**: `scan_date` has UNIQUE constraint; duplicate scans on same date are skipped (INSERT IGNORE)
