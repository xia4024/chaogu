from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Generator
import json

import pymysql
from pymysql.cursors import DictCursor

from .config import MysqlSettings

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scan_reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    data_mode VARCHAR(10) NOT NULL DEFAULT 'real',
    scan_date DATE NOT NULL,
    executed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    benchmark_symbol VARCHAR(20) NOT NULL DEFAULT '',
    market_regime VARCHAR(20) NOT NULL DEFAULT 'unknown',
    candidate_count INT NOT NULL DEFAULT 0,
    filtered_count INT NOT NULL DEFAULT 0,
    circuit_triggered TINYINT NOT NULL DEFAULT 0,
    INDEX idx_mode_date (data_mode, scan_date),
    INDEX idx_mode_exec (data_mode, executed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_plans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    data_mode VARCHAR(10) NOT NULL DEFAULT 'real',
    report_id INT NOT NULL,
    strategy_id VARCHAR(50) NOT NULL DEFAULT '',
    signal_group VARCHAR(20) NOT NULL DEFAULT '',
    action VARCHAR(10) NOT NULL DEFAULT '',
    symbol VARCHAR(20) NOT NULL DEFAULT '',
    name VARCHAR(100) NOT NULL DEFAULT '',
    asset_type VARCHAR(30) NOT NULL DEFAULT '',
    sector VARCHAR(50) NOT NULL DEFAULT '',
    score DECIMAL(5,2) NOT NULL DEFAULT 0,
    entry_price DECIMAL(10,3) NOT NULL DEFAULT 0,
    stop_loss DECIMAL(10,3) NOT NULL DEFAULT 0,
    take_profit DECIMAL(10,3) NOT NULL DEFAULT 0,
    suggested_shares INT NOT NULL DEFAULT 0,
    suggested_value DECIMAL(12,2) NOT NULL DEFAULT 0,
    position_pct DECIMAL(6,4) NOT NULL DEFAULT 0,
    risk_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    reasons TEXT,
    tags TEXT,
    FOREIGN KEY (report_id) REFERENCES scan_reports(id) ON DELETE CASCADE,
    INDEX idx_report_id (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS holdings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    data_mode VARCHAR(10) NOT NULL DEFAULT 'real',
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL DEFAULT '',
    shares INT NOT NULL DEFAULT 0,
    cost_basis DECIMAL(10,3) NOT NULL DEFAULT 0,
    min_t_trade_pct DECIMAL(4,3) NOT NULL DEFAULT 0.100,
    max_t_trade_pct DECIMAL(4,3) NOT NULL DEFAULT 0.200,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_account_mode_symbol (account_id, data_mode, symbol),
    INDEX idx_holdings_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    data_mode VARCHAR(10) NOT NULL DEFAULT 'real',
    scan_date DATE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL DEFAULT '',
    open DECIMAL(10,3) NOT NULL DEFAULT 0,
    high DECIMAL(10,3) NOT NULL DEFAULT 0,
    low DECIMAL(10,3) NOT NULL DEFAULT 0,
    close DECIMAL(10,3) NOT NULL DEFAULT 0,
    volume BIGINT NOT NULL DEFAULT 0,
    UNIQUE KEY uk_mode_scan_symbol (data_mode, scan_date, symbol),
    INDEX idx_mode_symbol_date (data_mode, symbol, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_outcomes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    data_mode VARCHAR(10) NOT NULL DEFAULT 'real',
    report_id INT NOT NULL,
    symbol VARCHAR(20) NOT NULL DEFAULT '',
    name VARCHAR(100) NOT NULL DEFAULT '',
    strategy_id VARCHAR(50) NOT NULL DEFAULT '',
    signal_group VARCHAR(20) NOT NULL DEFAULT '',
    action VARCHAR(10) NOT NULL DEFAULT '',
    score DECIMAL(5,2) NOT NULL DEFAULT 0,
    entry_price DECIMAL(10,3) NOT NULL DEFAULT 0,
    stop_loss DECIMAL(10,3) NOT NULL DEFAULT 0,
    take_profit DECIMAL(10,3) NOT NULL DEFAULT 0,
    suggested_shares INT NOT NULL DEFAULT 0,
    suggested_value DECIMAL(12,2) NOT NULL DEFAULT 0,
    outcome VARCHAR(20) DEFAULT 'pending',
    exit_price DECIMAL(10,3) DEFAULT NULL,
    pnl_pct DECIMAL(8,4) DEFAULT NULL,
    pnl_amount DECIMAL(12,2) DEFAULT NULL,
    settled_date DATE DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES scan_reports(id) ON DELETE CASCADE,
    INDEX idx_outcomes_account (account_id),
    INDEX idx_mode_symbol (data_mode, symbol),
    INDEX idx_mode_strategy (data_mode, strategy_id),
    INDEX idx_mode_outcome (data_mode, outcome),
    INDEX idx_mode_signal_group (data_mode, signal_group)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS strategy_performance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    data_mode VARCHAR(10) NOT NULL DEFAULT 'real',
    strategy_id VARCHAR(50) NOT NULL,
    signal_group VARCHAR(20) NOT NULL DEFAULT '',
    total_signals INT NOT NULL DEFAULT 0,
    wins INT NOT NULL DEFAULT 0,
    losses INT NOT NULL DEFAULT 0,
    pending INT NOT NULL DEFAULT 0,
    win_rate DECIMAL(5,4) NOT NULL DEFAULT 0,
    avg_pnl_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    avg_holding_days DECIMAL(6,1) NOT NULL DEFAULT 0,
    max_drawdown_pct DECIMAL(8,2) NOT NULL DEFAULT 0,
    sharpe_ratio DECIMAL(6,2) DEFAULT NULL,
    best_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    worst_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    weekly_pnl_json TEXT,
    monthly_pnl_json TEXT,
    total_pnl_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    avg_score DECIMAL(5,2) NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_account_mode_strategy_group (account_id, data_mode, strategy_id, signal_group)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    data_source VARCHAR(20) NOT NULL DEFAULT 'demo',
    initial_capital DECIMAL(14,2) NOT NULL DEFAULT 0,
    final_equity DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_return_pct DECIMAL(8,2) NOT NULL DEFAULT 0,
    max_drawdown_pct DECIMAL(8,2) NOT NULL DEFAULT 0,
    total_trades INT NOT NULL DEFAULT 0,
    wins INT NOT NULL DEFAULT 0,
    losses INT NOT NULL DEFAULT 0,
    win_rate DECIMAL(6,4) NOT NULL DEFAULT 0,
    avg_win_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    avg_loss_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    profit_factor DECIMAL(8,2) DEFAULT NULL,
    config_snapshot TEXT,
    executed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_dates (start_date, end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS backtest_trades (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL DEFAULT 'buy',
    entry_date DATE NOT NULL,
    exit_date DATE DEFAULT NULL,
    entry_price DECIMAL(10,3) NOT NULL DEFAULT 0,
    exit_price DECIMAL(10,3) NOT NULL DEFAULT 0,
    shares INT NOT NULL DEFAULT 0,
    pnl_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    pnl_pct DECIMAL(8,4) NOT NULL DEFAULT 0,
    strategy_id VARCHAR(50) NOT NULL DEFAULT '',
    exit_reason VARCHAR(30) NOT NULL DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE,
    INDEX idx_run_id (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS backtest_signals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    trading_day DATE NOT NULL,
    strategy_id VARCHAR(50) NOT NULL DEFAULT '',
    signal_group VARCHAR(20) NOT NULL DEFAULT '',
    action VARCHAR(10) NOT NULL DEFAULT '',
    symbol VARCHAR(20) NOT NULL DEFAULT '',
    name VARCHAR(100) NOT NULL DEFAULT '',
    asset_type VARCHAR(30) NOT NULL DEFAULT '',
    sector VARCHAR(50) NOT NULL DEFAULT '',
    score DECIMAL(5,2) NOT NULL DEFAULT 0,
    entry_price DECIMAL(10,3) NOT NULL DEFAULT 0,
    stop_loss DECIMAL(10,3) NOT NULL DEFAULT 0,
    take_profit DECIMAL(10,3) NOT NULL DEFAULT 0,
    suggested_shares INT NOT NULL DEFAULT 0,
    suggested_value DECIMAL(12,2) NOT NULL DEFAULT 0,
    executed TINYINT NOT NULL DEFAULT 0,
    skip_reason VARCHAR(50) NOT NULL DEFAULT '',
    reasons TEXT,
    tags TEXT,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE,
    INDEX idx_run_day (run_id, trading_day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    broker VARCHAR(30) NOT NULL DEFAULT '',
    type VARCHAR(10) NOT NULL DEFAULT 'real',
    initial_capital DECIMAL(14,2) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_images (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    file_path VARCHAR(500) NOT NULL,
    file_hash CHAR(32) NOT NULL,
    trade_date_hint DATE DEFAULT NULL,
    ocr_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    ocr_result_json TEXT,
    uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_file_hash (file_hash),
    INDEX idx_images_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS actual_trades (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL DEFAULT 1,
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL DEFAULT '',
    action VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    trade_time TIME DEFAULT NULL,
    price DECIMAL(10,3) NOT NULL DEFAULT 0,
    shares INT NOT NULL DEFAULT 0,
    amount DECIMAL(14,2) NOT NULL DEFAULT 0,
    commission DECIMAL(10,2) NOT NULL DEFAULT 0,
    image_id INT DEFAULT NULL,
    ocr_confidence DECIMAL(4,3) DEFAULT NULL,
    ocr_raw_text TEXT,
    source VARCHAR(20) NOT NULL DEFAULT 'ocr',
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (image_id) REFERENCES trade_images(id) ON DELETE SET NULL,
    INDEX idx_actual_account_date (account_id, trade_date),
    INDEX idx_actual_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

"""


_ACCOUNT_MIGRATION_SQL = [
    "ALTER TABLE holdings ADD COLUMN account_id INT NOT NULL DEFAULT 1 AFTER id",
    "ALTER TABLE holdings DROP INDEX uk_mode_symbol",
    "ALTER TABLE holdings ADD UNIQUE INDEX uk_account_mode_symbol (account_id, data_mode, symbol)",
    "ALTER TABLE holdings ADD INDEX idx_holdings_account (account_id)",
    "ALTER TABLE strategy_performance ADD COLUMN account_id INT NOT NULL DEFAULT 1 AFTER id",
    "ALTER TABLE strategy_performance DROP INDEX uk_mode_strategy_group",
    "ALTER TABLE strategy_performance ADD UNIQUE INDEX uk_account_mode_strategy_group (account_id, data_mode, strategy_id, signal_group)",
    "ALTER TABLE trade_outcomes ADD COLUMN account_id INT NOT NULL DEFAULT 1 AFTER id",
    "ALTER TABLE trade_outcomes ADD INDEX idx_outcomes_account (account_id)",
]

_PERFORMANCE_MIGRATION_SQL = [
    "ALTER TABLE strategy_performance ADD COLUMN avg_holding_days DECIMAL(6,1) NOT NULL DEFAULT 0 AFTER avg_pnl_pct",
    "ALTER TABLE strategy_performance ADD COLUMN max_drawdown_pct DECIMAL(8,2) NOT NULL DEFAULT 0 AFTER avg_holding_days",
    "ALTER TABLE strategy_performance ADD COLUMN sharpe_ratio DECIMAL(6,2) DEFAULT NULL AFTER max_drawdown_pct",
    "ALTER TABLE strategy_performance ADD COLUMN best_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0 AFTER sharpe_ratio",
    "ALTER TABLE strategy_performance ADD COLUMN worst_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0 AFTER best_trade_pct",
    "ALTER TABLE strategy_performance ADD COLUMN weekly_pnl_json TEXT AFTER worst_trade_pct",
    "ALTER TABLE strategy_performance ADD COLUMN monthly_pnl_json TEXT AFTER weekly_pnl_json",
]


def get_connection(settings: MysqlSettings) -> pymysql.Connection:
    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
    )


@contextmanager
def connection_context(
    settings: MysqlSettings,
) -> Generator[pymysql.Connection, None, None]:
    conn = get_connection(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_tables(settings: MysqlSettings) -> None:
    with connection_context(settings) as conn:
        for statement in _SCHEMA_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                conn.cursor().execute(stmt)
        _run_migrations(conn)
        ensure_default_account(conn)
        conn.commit()


def _run_migrations(conn: pymysql.Connection) -> None:
    migrations = [
        # Legacy: holdings name column
        "ALTER TABLE holdings ADD COLUMN name VARCHAR(100) NOT NULL DEFAULT '' AFTER symbol",
        # Legacy: multi-scan per day support
        "ALTER TABLE scan_reports DROP INDEX scan_date",
        "ALTER TABLE scan_reports ADD COLUMN executed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER scan_date",
        # ---- data_mode isolation ----
        # Add data_mode columns
        "ALTER TABLE scan_reports ADD COLUMN data_mode VARCHAR(10) NOT NULL DEFAULT 'real' FIRST",
        "ALTER TABLE trade_plans ADD COLUMN data_mode VARCHAR(10) NOT NULL DEFAULT 'real' FIRST",
        "ALTER TABLE holdings ADD COLUMN data_mode VARCHAR(10) NOT NULL DEFAULT 'real' FIRST",
        "ALTER TABLE price_snapshots ADD COLUMN data_mode VARCHAR(10) NOT NULL DEFAULT 'real' FIRST",
        "ALTER TABLE trade_outcomes ADD COLUMN data_mode VARCHAR(10) NOT NULL DEFAULT 'real' FIRST",
        "ALTER TABLE strategy_performance ADD COLUMN data_mode VARCHAR(10) NOT NULL DEFAULT 'real' FIRST",
        # Fix unique constraints for data_mode
        "ALTER TABLE holdings DROP INDEX symbol",
        "ALTER TABLE holdings ADD UNIQUE KEY uk_mode_symbol (data_mode, symbol)",
        "ALTER TABLE price_snapshots DROP INDEX uk_scan_symbol",
        "ALTER TABLE price_snapshots ADD UNIQUE KEY uk_mode_scan_symbol (data_mode, scan_date, symbol)",
        "ALTER TABLE strategy_performance DROP INDEX uk_strategy_group",
        "ALTER TABLE strategy_performance ADD UNIQUE KEY uk_mode_strategy_group (data_mode, strategy_id, signal_group)",
        # Execution confirmation
        "ALTER TABLE trade_outcomes ADD COLUMN executed TINYINT(1) DEFAULT NULL AFTER outcome",
        "ALTER TABLE trade_outcomes ADD COLUMN actual_fill_price DECIMAL(10,3) DEFAULT NULL AFTER executed",
        "ALTER TABLE trade_outcomes ADD INDEX idx_executed (data_mode, executed, outcome)",
        "ALTER TABLE trade_plans ADD COLUMN executed TINYINT(1) DEFAULT NULL AFTER tags",
        "ALTER TABLE trade_plans ADD COLUMN actual_fill_price DECIMAL(10,3) DEFAULT NULL AFTER executed",
        # Commission tracking
        "ALTER TABLE trade_plans ADD COLUMN estimated_commission DECIMAL(10,2) NOT NULL DEFAULT 0 AFTER risk_amount",
        "ALTER TABLE trade_outcomes ADD COLUMN estimated_commission DECIMAL(10,2) NOT NULL DEFAULT 0 AFTER risk_amount",
        # Circuit breaker tracking
        "ALTER TABLE scan_reports ADD COLUMN circuit_triggered TINYINT NOT NULL DEFAULT 0 AFTER filtered_count",
    ]
    for stmt in migrations:
        try:
            conn.cursor().execute(stmt)
        except Exception:
            pass

    # Account migrations
    for stmt in _ACCOUNT_MIGRATION_SQL:
        try:
            conn.cursor().execute(stmt)
        except Exception:
            pass

    # Performance metric migrations
    for stmt in _PERFORMANCE_MIGRATION_SQL:
        try:
            conn.cursor().execute(stmt)
        except Exception:
            pass


def insert_scan_report(
    conn: pymysql.Connection,
    scan_date: date,
    executed_at,
    benchmark_symbol: str,
    market_regime: str,
    candidate_count: int,
    filtered_count: int,
    data_mode: str = "real",
    circuit_triggered: bool = False,
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO scan_reports (data_mode, scan_date, executed_at, benchmark_symbol, "
        "market_regime, candidate_count, filtered_count, circuit_triggered) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (data_mode, scan_date, executed_at, benchmark_symbol, market_regime,
         candidate_count, filtered_count, int(circuit_triggered)),
    )
    return cursor.lastrowid or 0


def insert_trade_plans(
    conn: pymysql.Connection, report_id: int, plans: list[dict[str, Any]],
    data_mode: str = "real",
) -> None:
    if not plans:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO trade_plans "
        "(data_mode, report_id, strategy_id, signal_group, action, symbol, name, asset_type, sector, "
        "score, entry_price, stop_loss, take_profit, suggested_shares, suggested_value, "
        "position_pct, risk_amount, estimated_commission, reasons, tags) "
        "VALUES (%(data_mode)s, %(report_id)s, %(strategy_id)s, %(signal_group)s, %(action)s, %(symbol)s, "
        "%(name)s, %(asset_type)s, %(sector)s, %(score)s, %(entry_price)s, %(stop_loss)s, "
        "%(take_profit)s, %(suggested_shares)s, %(suggested_value)s, %(position_pct)s, "
        "%(risk_amount)s, %(estimated_commission)s, %(reasons)s, %(tags)s)",
        [
            {
                "data_mode": data_mode,
                "report_id": report_id,
                **plan,
                "reasons": json.dumps(plan.get("reasons", []), ensure_ascii=False),
                "tags": json.dumps(plan.get("tags", []), ensure_ascii=False),
            }
            for plan in plans
        ],
    )


def list_scan_reports(
    conn: pymysql.Connection, limit: int = 60, data_mode: str = "real",
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM trade_plans t WHERE t.report_id = r.id) AS plan_count "
        "FROM scan_reports r WHERE r.data_mode = %s ORDER BY r.executed_at DESC LIMIT %s",
        (data_mode, limit),
    )
    return list(cursor.fetchall())


def get_scan_report(
    conn: pymysql.Connection, report_id: int, data_mode: str = "real",
) -> dict[str, Any] | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM scan_reports WHERE id = %s AND data_mode = %s",
        (report_id, data_mode),
    )
    report = cursor.fetchone()
    if not report:
        return None
    cursor.execute(
        "SELECT * FROM trade_plans WHERE report_id = %s ORDER BY signal_group, score DESC",
        (report_id,),
    )
    report["plans"] = []
    for row in cursor.fetchall():
        row["reasons"] = json.loads(row.get("reasons") or "[]")
        row["tags"] = json.loads(row.get("tags") or "[]")
        report["plans"].append(row)
    return report


# --- Holdings CRUD ---

def list_holdings(
    conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM holdings WHERE data_mode = %s AND account_id = %s ORDER BY symbol",
        (data_mode, account_id),
    )
    return list(cursor.fetchall())


def list_holdings_with_prices(
    conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1,
) -> list[dict[str, Any]]:
    """Return holdings with latest close price for P&L display."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT h.*, "
        "p.close AS latest_price, p.scan_date AS price_date "
        "FROM holdings h "
        "LEFT JOIN price_snapshots p ON h.symbol = p.symbol AND h.data_mode = p.data_mode "
        "AND p.id = (SELECT MAX(id) FROM price_snapshots ps WHERE ps.symbol = h.symbol AND ps.data_mode = h.data_mode) "
        "WHERE h.data_mode = %s AND h.account_id = %s ORDER BY h.symbol",
        (data_mode, account_id),
    )
    return list(cursor.fetchall())


def get_holding(
    conn: pymysql.Connection, holding_id: int, account_id: int = 1,
) -> dict[str, Any] | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM holdings WHERE id = %s AND account_id = %s",
        (holding_id, account_id),
    )
    return cursor.fetchone()


def create_holding(
    conn: pymysql.Connection,
    account_id: int,
    symbol: str,
    name: str,
    shares: int,
    cost_basis: float,
    min_t_trade_pct: float,
    max_t_trade_pct: float,
    data_mode: str = "real",
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO holdings (account_id, data_mode, symbol, name, shares, cost_basis, min_t_trade_pct, max_t_trade_pct) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), shares=VALUES(shares), cost_basis=VALUES(cost_basis), "
        "min_t_trade_pct=VALUES(min_t_trade_pct), max_t_trade_pct=VALUES(max_t_trade_pct)",
        (account_id, data_mode, symbol, name, shares, cost_basis, min_t_trade_pct, max_t_trade_pct),
    )


def update_holding(
    conn: pymysql.Connection,
    account_id: int,
    holding_id: int,
    symbol: str,
    name: str,
    shares: int,
    cost_basis: float,
    min_t_trade_pct: float,
    max_t_trade_pct: float,
    data_mode: str = "real",
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE holdings SET symbol=%s, name=%s, shares=%s, cost_basis=%s, "
        "min_t_trade_pct=%s, max_t_trade_pct=%s WHERE id=%s AND data_mode=%s AND account_id=%s",
        (symbol, name, shares, cost_basis, min_t_trade_pct, max_t_trade_pct, holding_id, data_mode, account_id),
    )


def delete_holding(
    conn: pymysql.Connection, holding_id: int, data_mode: str = "real", account_id: int = 1,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM holdings WHERE id = %s AND data_mode = %s AND account_id = %s",
        (holding_id, data_mode, account_id),
    )


def load_holdings_from_db(
    settings: MysqlSettings, data_mode: str = "real", account_id: int = 1,
) -> list[dict[str, Any]]:
    with connection_context(settings) as conn:
        return list_holdings(conn, data_mode=data_mode, account_id=account_id)


# --- Price Snapshots ---

def insert_price_snapshots(
    conn: pymysql.Connection, scan_date: date, snapshots: list[dict[str, Any]],
    data_mode: str = "real",
) -> None:
    if not snapshots:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO price_snapshots (data_mode, scan_date, symbol, name, open, high, low, close, volume) "
        "VALUES (%(data_mode)s, %(scan_date)s, %(symbol)s, %(name)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), open=VALUES(open), high=VALUES(high), "
        "low=VALUES(low), close=VALUES(close), volume=VALUES(volume)",
        [{"data_mode": data_mode, "scan_date": scan_date, **s} for s in snapshots],
    )


def get_symbol_prices(
    conn: pymysql.Connection, symbol: str, limit: int = 120, data_mode: str = "real",
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT scan_date, name, open, high, low, close, volume FROM price_snapshots "
        "WHERE symbol = %s AND data_mode = %s ORDER BY scan_date DESC LIMIT %s",
        (symbol, data_mode, limit),
    )
    rows = list(cursor.fetchall())
    rows.reverse()
    return rows


def get_all_tracked_symbols(
    conn: pymysql.Connection, data_mode: str = "real",
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symbol, name, scan_date, open, high, low, close "
        "FROM price_snapshots WHERE data_mode = %s "
        "ORDER BY symbol, scan_date DESC, id DESC",
        (data_mode,),
    )
    rows = list(cursor.fetchall())

    symbols: list[dict[str, Any]] = []
    current_symbol = None
    current_entry: dict[str, Any] | None = None
    previous_close: float | None = None

    for row in rows:
        symbol = row["symbol"]
        close_price = float(row["close"] or 0)
        open_price = float(row["open"] or 0)

        if symbol != current_symbol:
            if current_entry is not None:
                current_entry["previous_close"] = previous_close
                current_price = float(current_entry["latest_price"] or 0)
                if previous_close and previous_close > 0:
                    current_entry["change_amount"] = round(current_price - previous_close, 3)
                    current_entry["pct_change"] = round(
                        (current_price - previous_close) / previous_close * 100, 2,
                    )
                else:
                    current_entry["change_amount"] = None
                    current_entry["pct_change"] = None
                symbols.append(current_entry)

            current_symbol = symbol
            previous_close = None
            current_entry = {
                "symbol": symbol,
                "name": row.get("name") or "",
                "scan_date": row["scan_date"],
                "open": open_price,
                "latest_price": close_price,
                "high": float(row["high"] or 0),
                "low": float(row["low"] or 0),
                "previous_close": None,
                "change_amount": None,
                "pct_change": None,
            }
            continue

        if previous_close is None:
            previous_close = close_price

    if current_entry is not None:
        current_entry["previous_close"] = previous_close
        current_price = float(current_entry["latest_price"] or 0)
        if previous_close and previous_close > 0:
            current_entry["change_amount"] = round(current_price - previous_close, 3)
            current_entry["pct_change"] = round(
                (current_price - previous_close) / previous_close * 100, 2,
            )
        symbols.append(current_entry)

    return symbols


# --- Trade Outcomes ---

def insert_trade_outcomes(
    conn: pymysql.Connection, report_id: int, outcomes: list[dict[str, Any]],
    data_mode: str = "real", account_id: int = 1,
) -> None:
    if not outcomes:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO trade_outcomes "
        "(account_id, data_mode, report_id, symbol, name, strategy_id, signal_group, action, score, "
        "entry_price, stop_loss, take_profit, suggested_shares, suggested_value, outcome) "
        "VALUES (%(account_id)s, %(data_mode)s, %(report_id)s, %(symbol)s, %(name)s, %(strategy_id)s, %(signal_group)s, "
        "%(action)s, %(score)s, %(entry_price)s, %(stop_loss)s, %(take_profit)s, "
        "%(suggested_shares)s, %(suggested_value)s, 'pending')",
        [{"account_id": account_id, "data_mode": data_mode, "report_id": report_id, **o} for o in outcomes],
    )


def confirm_execution(
    conn: pymysql.Connection, outcome_id: int, executed: bool,
    actual_fill_price: float | None = None,
    data_mode: str = "real",
) -> None:
    """Mark a trade outcome as executed or skipped, with optional actual fill price."""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE trade_outcomes SET executed=%s, actual_fill_price=%s "
        "WHERE id=%s AND data_mode=%s",
        (1 if executed else 0, actual_fill_price, outcome_id, data_mode),
    )


def confirm_plan_execution(
    conn: pymysql.Connection, plan_id: int, executed: bool,
    actual_fill_price: float | None = None,
    data_mode: str = "real",
) -> None:
    """Mark a trade plan as executed or skipped."""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE trade_plans SET executed=%s, actual_fill_price=%s "
        "WHERE id=%s AND data_mode=%s",
        (1 if executed else 0, actual_fill_price, plan_id, data_mode),
    )


def settle_outcomes(conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1) -> int:
    """Settle pending outcomes using available subsequent price data."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT o.id, o.symbol, o.entry_price, o.stop_loss, o.take_profit, "
        "o.suggested_shares, o.suggested_value, o.action, o.report_id, r.scan_date, o.data_mode "
        "FROM trade_outcomes o JOIN scan_reports r ON o.report_id = r.id "
        "WHERE o.outcome = 'pending' AND o.data_mode = %s AND o.account_id = %s",
        (data_mode, account_id),
    )
    pending = list(cursor.fetchall())
    settled = 0
    for p in pending:
        cursor.execute(
            "SELECT close FROM price_snapshots WHERE symbol = %s AND scan_date > %s "
            "AND data_mode = %s ORDER BY scan_date ASC",
            (p["symbol"], p["scan_date"], data_mode),
        )
        future_prices = [row["close"] for row in cursor.fetchall()]
        if not future_prices:
            continue
        outcome = _determine_outcome(
            action=p["action"],
            entry=float(p["entry_price"]),
            stop_loss=float(p["stop_loss"]),
            take_profit=float(p["take_profit"]),
            suggested_shares=int(p.get("suggested_shares") or 0),
            suggested_value=float(p.get("suggested_value") or 0),
            future_closes=[float(c) for c in future_prices],
        )
        if outcome:
            cursor.execute(
                "UPDATE trade_outcomes SET outcome=%s, exit_price=%s, pnl_pct=%s, "
                "pnl_amount=%s, settled_date=CURDATE() WHERE id=%s",
                (
                    outcome["outcome"],
                    outcome["exit_price"],
                    outcome["pnl_pct"],
                    outcome["pnl_amount"],
                    p["id"],
                ),
            )
            settled += 1
    return settled


def _determine_outcome(
    action: str, entry: float, stop_loss: float, take_profit: float,
    suggested_shares: int, suggested_value: float,
    future_closes: list[float],
) -> dict[str, Any] | None:
    for close in future_closes:
        if action == "buy":
            if close <= stop_loss:
                pnl_pct = (close - entry) / entry
                return {"outcome": "loss", "exit_price": close,
                        "pnl_pct": pnl_pct,
                        "pnl_amount": _calculate_pnl_amount(
                            pnl_pct, entry, suggested_shares, suggested_value,
                        )}
            if close >= take_profit:
                pnl_pct = (close - entry) / entry
                return {"outcome": "win", "exit_price": close,
                        "pnl_pct": pnl_pct,
                        "pnl_amount": _calculate_pnl_amount(
                            pnl_pct, entry, suggested_shares, suggested_value,
                        )}
        elif action == "sell":
            if close >= stop_loss:
                pnl_pct = (entry - close) / entry
                return {"outcome": "loss", "exit_price": close,
                        "pnl_pct": pnl_pct,
                        "pnl_amount": _calculate_pnl_amount(
                            pnl_pct, entry, suggested_shares, suggested_value,
                        )}
            if close <= take_profit:
                pnl_pct = (entry - close) / entry
                return {"outcome": "win", "exit_price": close,
                        "pnl_pct": pnl_pct,
                        "pnl_amount": _calculate_pnl_amount(
                            pnl_pct, entry, suggested_shares, suggested_value,
                        )}
    # Use last available close as the exit if not hit
    last = future_closes[-1]
    if action == "buy":
        pnl = (last - entry) / entry
    else:
        pnl = (entry - last) / entry
    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
    return {"outcome": outcome, "exit_price": last,
            "pnl_pct": pnl,
            "pnl_amount": _calculate_pnl_amount(
                pnl, entry, suggested_shares, suggested_value,
            )}


def _calculate_pnl_amount(
    pnl_pct: float, entry: float, suggested_shares: int, suggested_value: float,
) -> float:
    if suggested_value > 0:
        return round(pnl_pct * suggested_value, 2)
    if suggested_shares > 0 and entry > 0:
        return round(pnl_pct * suggested_shares * entry, 2)
    return round(pnl_pct * 100, 2)


# --- Strategy Performance ---

def recalculate_performance(conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM strategy_performance WHERE data_mode = %s AND account_id = %s", (data_mode, account_id)
    )
    cursor.execute(
        "SELECT strategy_id, signal_group, COUNT(*) AS total, "
        "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses, "
        "SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END) AS pending, "
        "AVG(CASE WHEN outcome!='pending' THEN pnl_pct ELSE NULL END) AS avg_pnl, "
        "SUM(CASE WHEN outcome!='pending' THEN pnl_pct ELSE 0 END) AS total_pnl, "
        "AVG(score) AS avg_score "
        "FROM trade_outcomes WHERE signal_group = 't_trade' AND data_mode = %s AND account_id = %s "
        "GROUP BY strategy_id, signal_group",
        (data_mode, account_id),
    )
    rows = list(cursor.fetchall())
    for r in rows:
        settled = r["total"] - (r["pending"] or 0)
        win_rate = (r["wins"] or 0) / settled if settled > 0 else 0
        cursor.execute(
            "INSERT INTO strategy_performance "
            "(account_id, data_mode, strategy_id, signal_group, total_signals, wins, losses, pending, "
            "win_rate, avg_pnl_pct, total_pnl_pct, avg_score) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE total_signals=VALUES(total_signals), "
            "wins=VALUES(wins), losses=VALUES(losses), pending=VALUES(pending), "
            "win_rate=VALUES(win_rate), avg_pnl_pct=VALUES(avg_pnl_pct), "
            "total_pnl_pct=VALUES(total_pnl_pct), avg_score=VALUES(avg_score)",
            (account_id, data_mode, r["strategy_id"], r["signal_group"], r["total"],
             r["wins"] or 0, r["losses"] or 0, r["pending"] or 0,
             round(win_rate, 4), round(r["avg_pnl"] or 0, 4),
             round(r["total_pnl"] or 0, 4), round(r["avg_score"] or 0, 2)),
        )

    # Compute enhanced metrics for each inserted row
    for r in rows:
        sid = r["strategy_id"]
        sg = r["signal_group"]

        # avg holding days: average days from scan_date to settled_date for settled outcomes
        cursor.execute(
            "SELECT AVG(DATEDIFF(o.settled_date, r2.scan_date)) as avg_days "
            "FROM trade_outcomes o JOIN scan_reports r2 ON o.report_id = r2.id "
            "WHERE o.strategy_id = %s AND o.signal_group = %s "
            "AND o.outcome IN ('win','loss') AND o.settled_date IS NOT NULL "
            "AND o.data_mode = %s AND o.account_id = %s",
            (sid, sg, data_mode, account_id),
        )
        row_avg = cursor.fetchone()
        avg_days = float(row_avg["avg_days"] or 0)

        # best/worst trade pnl_pct
        cursor.execute(
            "SELECT MAX(pnl_pct) as best, MIN(pnl_pct) as worst "
            "FROM trade_outcomes WHERE strategy_id = %s AND signal_group = %s "
            "AND outcome IN ('win','loss') AND data_mode = %s AND account_id = %s",
            (sid, sg, data_mode, account_id),
        )
        extremes = cursor.fetchone()
        best_trade = float(extremes["best"] or 0)
        worst_trade = float(extremes["worst"] or 0)

        # weekly pnl (last 4 weeks)
        cursor.execute(
            "SELECT WEEK(o.settled_date, 1) as wk, "
            "SUM(COALESCE(o.pnl_pct, 0) * COALESCE(o.suggested_value, 0)) as wk_pnl "
            "FROM trade_outcomes o WHERE o.strategy_id = %s AND o.signal_group = %s "
            "AND o.outcome IN ('win','loss') AND o.settled_date IS NOT NULL "
            "AND o.data_mode = %s AND o.account_id = %s "
            "AND o.settled_date >= DATE_SUB(CURDATE(), INTERVAL 28 DAY) "
            "GROUP BY wk ORDER BY wk DESC",
            (sid, sg, data_mode, account_id),
        )
        weekly = [{"week": f"{row['wk']}", "pnl": float(row["wk_pnl"] or 0)}
                  for row in cursor.fetchall()]

        # monthly pnl (last 12 months)
        cursor.execute(
            "SELECT DATE_FORMAT(o.settled_date, '%%Y-%%m') as mo, "
            "SUM(COALESCE(o.pnl_pct, 0) * COALESCE(o.suggested_value, 0)) as mo_pnl "
            "FROM trade_outcomes o WHERE o.strategy_id = %s AND o.signal_group = %s "
            "AND o.outcome IN ('win','loss') AND o.settled_date IS NOT NULL "
            "AND o.data_mode = %s AND o.account_id = %s "
            "AND o.settled_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) "
            "GROUP BY mo ORDER BY mo DESC",
            (sid, sg, data_mode, account_id),
        )
        monthly = [{"month": row["mo"], "pnl": float(row["mo_pnl"] or 0)}
                   for row in cursor.fetchall()]

        # Note: sharpe_ratio and max_drawdown_pct are left NULL/0 for now
        # as they require time-series computation better done in Python

        # Update the inserted row with enhanced metrics
        cursor.execute(
            "UPDATE strategy_performance SET "
            "avg_holding_days = %s, best_trade_pct = %s, worst_trade_pct = %s, "
            "weekly_pnl_json = %s, monthly_pnl_json = %s "
            "WHERE strategy_id = %s AND signal_group = %s "
            "AND data_mode = %s AND account_id = %s",
            (avg_days, best_trade, worst_trade,
             json.dumps(weekly, ensure_ascii=False), json.dumps(monthly, ensure_ascii=False),
             sid, sg, data_mode, account_id),
        )


def get_strategy_performance(
    conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM strategy_performance "
        "WHERE data_mode = %s AND account_id = %s "
        "ORDER BY signal_group, strategy_id",
        (data_mode, account_id),
    )
    return list(cursor.fetchall())


def get_trade_outcomes(
    conn: pymysql.Connection, limit: int = 50, strategy_id: str | None = None,
    data_mode: str = "real", account_id: int = 1,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    if strategy_id:
        cursor.execute(
            "SELECT o.*, r.scan_date FROM trade_outcomes o "
            "JOIN scan_reports r ON o.report_id = r.id "
            "WHERE o.strategy_id = %s AND o.data_mode = %s AND o.account_id = %s "
            "ORDER BY r.scan_date DESC LIMIT %s",
            (strategy_id, data_mode, account_id, limit),
        )
    else:
        cursor.execute(
            "SELECT o.*, r.scan_date FROM trade_outcomes o "
            "JOIN scan_reports r ON o.report_id = r.id "
            "WHERE o.data_mode = %s AND o.account_id = %s "
            "ORDER BY r.scan_date DESC LIMIT %s",
            (data_mode, account_id, limit),
        )
    return list(cursor.fetchall())


def get_dashboard_stats(
    conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1,
) -> dict[str, Any]:
    """Return aggregated dashboard statistics."""
    cursor = conn.cursor()

    # Weekly win rate (current Mon-Sun)
    cursor.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins "
        "FROM trade_outcomes WHERE data_mode = %s AND account_id = %s "
        "AND outcome IN ('win','loss','breakeven') "
        "AND settled_date >= DATE_SUB(CURDATE(), INTERVAL (WEEKDAY(CURDATE())) DAY) "
        "AND settled_date < DATE_ADD(CURDATE(), INTERVAL 1 DAY)",
        (data_mode, account_id),
    )
    week = cursor.fetchone()
    weekly_total = week["total"] or 0
    weekly_wins = week["wins"] or 0
    weekly_win_rate = weekly_wins / weekly_total if weekly_total > 0 else 0.0

    # 7-day win rate
    cursor.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins "
        "FROM trade_outcomes WHERE data_mode = %s AND account_id = %s "
        "AND outcome IN ('win','loss','breakeven') "
        "AND settled_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
        (data_mode, account_id),
    )
    d7 = cursor.fetchone()
    d7_total = d7["total"] or 0
    d7_wins = d7["wins"] or 0
    d7_win_rate = d7_wins / d7_total if d7_total > 0 else 0.0

    # All-time summary
    cursor.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END) AS pending, "
        "AVG(CASE WHEN outcome IN ('win','loss','breakeven') THEN pnl_pct END) AS avg_pnl "
        "FROM trade_outcomes WHERE data_mode = %s AND account_id = %s",
        (data_mode, account_id),
    )
    all_time = cursor.fetchone()
    all_total = all_time["total"] or 0
    all_settled = all_total - (all_time["pending"] or 0)

    # Holdings
    cursor.execute(
        "SELECT COUNT(*) AS count FROM holdings WHERE data_mode = %s AND account_id = %s",
        (data_mode, account_id),
    )
    holdings = cursor.fetchone()

    # Latest scan
    cursor.execute(
        "SELECT executed_at, candidate_count, benchmark_symbol, market_regime "
        "FROM scan_reports WHERE data_mode = %s ORDER BY executed_at DESC LIMIT 1",
        (data_mode,),
    )
    latest_scan = cursor.fetchone()

    return {
        "weekly_win_rate": round(weekly_win_rate, 4),
        "weekly_total": weekly_total,
        "weekly_wins": weekly_wins,
        "d7_win_rate": round(d7_win_rate, 4),
        "d7_total": d7_total,
        "d7_wins": d7_wins,
        "all_total": all_total,
        "all_settled": all_settled,
        "all_avg_pnl": round(all_time["avg_pnl"] or 0, 4),
        "holdings_count": holdings["count"] or 0,
        "latest_scan": latest_scan,
    }


def get_recent_pnl(
    conn: pymysql.Connection, data_mode: str = "real", days: int = 5, account_id: int = 1,
) -> list[float]:
    """Return realized P&L amounts from the last N trading days, for circuit breaker."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT (COALESCE(pnl_pct, 0) * COALESCE(suggested_value, 0)) AS pnl_amount "
        "FROM trade_outcomes "
        "WHERE data_mode = %s AND account_id = %s "
        "AND outcome IN ('win', 'loss', 'breakeven') "
        "AND settled_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY) "
        "ORDER BY settled_date DESC",
        (data_mode, account_id, days),
    )
    return [float(row["pnl_amount"] or 0) for row in cursor.fetchall()]


# --- Backtest Persistence ---

def insert_backtest_run(
    conn: pymysql.Connection,
    start_date: date,
    end_date: date,
    data_source: str,
    initial_capital: float,
    final_equity: float,
    total_return_pct: float,
    max_drawdown_pct: float,
    total_trades: int,
    wins: int,
    losses: int,
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    profit_factor: float | None,
    config_snapshot: str = "",
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO backtest_runs (start_date, end_date, data_source, initial_capital, "
        "final_equity, total_return_pct, max_drawdown_pct, total_trades, wins, losses, "
        "win_rate, avg_win_pct, avg_loss_pct, profit_factor, config_snapshot) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (start_date, end_date, data_source, initial_capital, final_equity,
         total_return_pct, max_drawdown_pct, total_trades, wins, losses,
         win_rate, avg_win_pct, avg_loss_pct, profit_factor, config_snapshot),
    )
    return cursor.lastrowid or 0


def insert_backtest_trades(
    conn: pymysql.Connection, run_id: int, trades: list[dict],
) -> None:
    if not trades:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO backtest_trades "
        "(run_id, symbol, action, entry_date, exit_date, entry_price, exit_price, "
        "shares, pnl_amount, pnl_pct, strategy_id, exit_reason) "
        "VALUES (%(run_id)s, %(symbol)s, %(action)s, %(entry_date)s, %(exit_date)s, "
        "%(entry_price)s, %(exit_price)s, %(shares)s, %(pnl_amount)s, %(pnl_pct)s, "
        "%(strategy_id)s, %(exit_reason)s)",
        trades,
    )


def insert_backtest_signals(
    conn: pymysql.Connection, run_id: int, signals: list[dict],
) -> None:
    if not signals:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO backtest_signals "
        "(run_id, trading_day, strategy_id, signal_group, action, symbol, name, "
        "asset_type, sector, score, entry_price, stop_loss, take_profit, "
        "suggested_shares, suggested_value, executed, skip_reason, reasons, tags) "
        "VALUES (%(run_id)s, %(trading_day)s, %(strategy_id)s, %(signal_group)s, "
        "%(action)s, %(symbol)s, %(name)s, %(asset_type)s, %(sector)s, "
        "%(score)s, %(entry_price)s, %(stop_loss)s, %(take_profit)s, "
        "%(suggested_shares)s, %(suggested_value)s, %(executed)s, %(skip_reason)s, "
        "%(reasons)s, %(tags)s)",
        signals,
    )


def get_backtest_signals(
    conn: pymysql.Connection, run_id: int,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM backtest_signals WHERE run_id = %s ORDER BY trading_day, score DESC",
        (run_id,),
    )
    return list(cursor.fetchall())


def list_backtest_runs(
    conn: pymysql.Connection,
    limit: int = 50,
    year: int | None = None,
    month: int | None = None,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    conditions: list[str] = []
    params: list[Any] = []

    if year is not None:
        conditions.append("YEAR(start_date) = %s")
        params.append(year)
    if month is not None:
        conditions.append("MONTH(start_date) = %s")
        params.append(month)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM backtest_runs{where} ORDER BY executed_at DESC LIMIT %s"
    params.append(limit)
    cursor.execute(sql, tuple(params))
    return list(cursor.fetchall())


def get_backtest_years(conn: pymysql.Connection) -> list[int]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT YEAR(start_date) AS yr FROM backtest_runs "
        "WHERE start_date IS NOT NULL ORDER BY yr DESC"
    )
    return [row["yr"] for row in cursor.fetchall()]


def get_backtest_run(
    conn: pymysql.Connection, run_id: int,
) -> dict[str, Any] | None:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM backtest_runs WHERE id = %s", (run_id,))
    return cursor.fetchone()


def get_backtest_trades(
    conn: pymysql.Connection, run_id: int,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM backtest_trades WHERE run_id = %s ORDER BY exit_date, entry_date",
        (run_id,),
    )
    return list(cursor.fetchall())


# --- Accounts ---


def get_accounts(conn: pymysql.Connection) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts ORDER BY id")
    return list(cursor.fetchall())


def get_account(conn: pymysql.Connection, account_id: int) -> dict[str, Any] | None:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts WHERE id = %s", (account_id,))
    return cursor.fetchone()


def create_account(
    conn: pymysql.Connection,
    name: str,
    broker: str = "",
    account_type: str = "real",
    initial_capital: float = 0.0,
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO accounts (name, broker, type, initial_capital) "
        "VALUES (%s, %s, %s, %s)",
        (name, broker, account_type, initial_capital),
    )
    return cursor.lastrowid or 0


def ensure_default_account(conn: pymysql.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM accounts")
    if cursor.fetchone()["cnt"] == 0:
        cursor.execute(
            "INSERT INTO accounts (id, name, broker, type) VALUES (1, '默认账户', '', 'real')"
        )


def clear_backtest_history(conn: pymysql.Connection) -> dict[str, int]:
    cursor = conn.cursor()
    counts: dict[str, int] = {}
    tables = ("backtest_signals", "backtest_trades", "backtest_runs")

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
        counts[table] = int(cursor.fetchone()["cnt"] or 0)

    for table in tables:
        cursor.execute(f"DELETE FROM {table}")
        try:
            cursor.execute(f"ALTER TABLE {table} AUTO_INCREMENT = 1")
        except Exception:
            pass

    return counts


# --- Trade Images (OCR Screenshot Recording) ---


def insert_trade_image(
    conn: pymysql.Connection, account_id: int, file_path: str,
    file_hash: str, trade_date_hint=None,
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO trade_images (account_id, file_path, file_hash, trade_date_hint) "
        "VALUES (%s, %s, %s, %s)",
        (account_id, file_path, file_hash, trade_date_hint),
    )
    return cursor.lastrowid or 0


def update_trade_image_ocr(
    conn: pymysql.Connection, image_id: int,
    ocr_status: str, ocr_result_json: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE trade_images SET ocr_status = %s, ocr_result_json = %s WHERE id = %s",
        (ocr_status, ocr_result_json, image_id),
    )


def insert_actual_trades(
    conn: pymysql.Connection, trades: list[dict],
) -> None:
    if not trades:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO actual_trades "
        "(account_id, symbol, name, action, trade_date, trade_time, "
        "price, shares, amount, commission, image_id, ocr_confidence, "
        "ocr_raw_text, source) "
        "VALUES (%(account_id)s, %(symbol)s, %(name)s, %(action)s, "
        "%(trade_date)s, %(trade_time)s, %(price)s, %(shares)s, "
        "%(amount)s, %(commission)s, %(image_id)s, %(ocr_confidence)s, "
        "%(ocr_raw_text)s, %(source)s)",
        trades,
    )


def get_actual_trades(
    conn: pymysql.Connection, account_id: int,
    start_date=None, end_date=None,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    query = "SELECT * FROM actual_trades WHERE account_id = %s"
    params: list = [account_id]
    if start_date:
        query += " AND trade_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND trade_date <= %s"
        params.append(end_date)
    query += " ORDER BY trade_date DESC, trade_time DESC"
    cursor.execute(query, params)
    return list(cursor.fetchall())


def get_trade_images(
    conn: pymysql.Connection, account_id: int, limit: int = 50,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM trade_images WHERE account_id = %s "
        "ORDER BY uploaded_at DESC LIMIT %s",
        (account_id, limit),
    )
    return list(cursor.fetchall())
