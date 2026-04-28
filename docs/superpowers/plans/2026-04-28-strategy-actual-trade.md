# Strategy Performance & Trade Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build multi-account architecture, strategy performance tracking with daily auto-settlement, OCR-based trade recording from 招商证券 WeChat screenshots, and a strategy-vs-actual comparison page.

**Architecture:** Phase 1 adds `accounts` table and `account_id` FK to holdings/strategy_performance/trade_outcomes, with config migration from single `[portfolio]` to `[[accounts]]`. Phase 2A enhances `strategy_performance` with Sharpe/drawdown/weekly-monthly-PnL columns and runs daily settlement after each scan. Phase 2B creates `actual_trades`/`trade_images` tables, a PaddleOCR pipeline in `ocr.py`, and upload/confirm APIs. Phase 3 builds a matching engine in `comparison.py` and a `/compare` page showing PnL overlay, deviation breakdown, and behavioral defect analysis.

**Tech Stack:** Python 3.11+, Flask, MySQL (pymysql), PaddleOCR, existing chaogu_alert codebase

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/chaogu_alert/config.py` | Modify | Add `AccountSettings`, migrate `AppConfig.portfolio` → `accounts` |
| `src/chaogu_alert/db.py` | Modify | `accounts`/`actual_trades`/`trade_images` schema, enhance settle/performance functions with account_id, add insert/get functions |
| `src/chaogu_alert/mysql_persistence.py` | Modify | Pass account_id through persistence flow |
| `src/chaogu_alert/ocr.py` | Create | PaddleOCR pipeline: detect → cluster → extract → validate |
| `src/chaogu_alert/comparison.py` | Create | Match strategy signals to actual trades, classify deviations |
| `web.py` | Modify | Account switcher, `/trades` routes, `/compare` route, filter by account_id |
| `config.example.toml` | Modify | New `[[accounts]]` section replacing `[portfolio]` |
| `config.toml` | Modify | Real config migration |
| `templates/trades.html` | Create | Trade list + upload form + OCR review |
| `templates/compare.html` | Create | Strategy vs actual comparison page |
| `templates/performance.html` | Modify | Enhanced with new metrics columns and charts |
| `templates/base.html` | Modify | Add account switcher to nav, add new nav links |
| `static/style.css` | Modify | New styles for trades/compare pages |
| `pyproject.toml` | Modify | Optional `ocr` dependency group |

---

### Task 1: Accounts table + schema migration

**Files:**
- Modify: `src/chaogu_alert/db.py` (schema SQL + ensure_tables)
- Modify: `src/chaogu_alert/config.py` (AccountSettings dataclass)

- [ ] **Step 1: Add AccountSettings dataclass to config.py**

Add after the existing `PortfolioSettings` class (line 102):

```python
@dataclass(slots=True)
class AccountSettings:
    id: int = 1
    name: str = "默认账户"
    broker: str = ""
    type: str = "real"
    initial_capital: float = 0.0
    portfolio: PortfolioSettings = field(default_factory=PortfolioSettings)
```

- [ ] **Step 2: Update AppConfig to include accounts**

In `AppConfig` (line 153), add `accounts` field:

```python
@dataclass(slots=True)
class AppConfig:
    app: AppSettings = field(default_factory=AppSettings)
    akshare: AkshareSettings = field(default_factory=AkshareSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    email: EmailSettings = field(default_factory=EmailSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    portfolio: PortfolioSettings = field(default_factory=PortfolioSettings)
    accounts: list[AccountSettings] = field(default_factory=list)
    strategy: StrategySettings = field(default_factory=StrategySettings)
    mysql: MysqlSettings = field(default_factory=MysqlSettings)
```

- [ ] **Step 3: Update load_config to parse [[accounts]]**

In `load_config()` (line 166), add accounts parsing before the AppConfig constructor:

```python
def load_config(path: str | Path) -> AppConfig:
    raw = _resolve_env_placeholders(_load_toml(Path(path)))
    strategy = raw.get("strategy", {})
    portfolio = raw.get("portfolio", {})
    holdings = [
        HoldingSettings(**holding)
        for holding in portfolio.get("holdings", [])
    ]

    accounts_raw = raw.get("accounts", [])
    account_list: list[AccountSettings] = []
    if accounts_raw:
        for acct in accounts_raw:
            acct_portfolio = acct.get("portfolio", {})
            acct_holdings = [
                HoldingSettings(**h)
                for h in acct_portfolio.get("holdings", [])
            ]
            account_list.append(AccountSettings(
                id=acct.get("id", 1),
                name=acct.get("name", "默认账户"),
                broker=acct.get("broker", ""),
                type=acct.get("type", "real"),
                initial_capital=float(acct.get("initial_capital", 0)),
                portfolio=PortfolioSettings(
                    available_cash=float(acct_portfolio.get("available_cash", 0)),
                    holdings=acct_holdings,
                ),
            ))

    return AppConfig(
        # ... existing fields ...
        accounts=account_list,
    )
```

- [ ] **Step 4: Add accounts table and migration SQL to db.py**

In `_SCHEMA_SQL`, add before the backtest_runs CREATE TABLE (after line 80):

```sql
CREATE TABLE IF NOT EXISTS accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    broker VARCHAR(30) NOT NULL DEFAULT '',
    type VARCHAR(10) NOT NULL DEFAULT 'real',
    initial_capital DECIMAL(14,2) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 5: Add migration SQL for existing tables**

Add a `_MIGRATION_SQL` constant or extend `ensure_tables` to run:

```python
_ACCOUNT_MIGRATION_SQL = """
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS account_id INT NOT NULL DEFAULT 1 AFTER id;
ALTER TABLE holdings ADD INDEX IF NOT EXISTS idx_holdings_account (account_id);

ALTER TABLE strategy_performance ADD COLUMN IF NOT EXISTS account_id INT NOT NULL DEFAULT 1 AFTER id;
ALTER TABLE strategy_performance DROP INDEX IF EXISTS uk_mode_strategy_group;
ALTER TABLE strategy_performance ADD UNIQUE INDEX IF NOT EXISTS uk_account_strategy_group (account_id, strategy_id, signal_group);

ALTER TABLE trade_outcomes ADD COLUMN IF NOT EXISTS account_id INT NOT NULL DEFAULT 1 AFTER id;
ALTER TABLE trade_outcomes ADD INDEX IF NOT EXISTS idx_outcomes_account (account_id);
"""
```

Note: MySQL doesn't support `ADD COLUMN IF NOT EXISTS` or `DROP INDEX IF EXISTS`. Use the `ensure_tables` pattern of catching duplicate column errors:

```python
def _safe_migrate(conn, sql_statements: list[str]):
    cursor = conn.cursor()
    for stmt in sql_statements:
        try:
            cursor.execute(stmt)
        except pymysql.err.OperationalError as e:
            code = e.args[0] if e.args else 0
            if code not in (1060, 1061, 1091):  # duplicate column/key, missing key
                raise
```

- [ ] **Step 6: Add helper functions for accounts in db.py**

```python
def get_accounts(conn: pymysql.Connection) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts ORDER BY id")
    return list(cursor.fetchall())

def get_account(conn: pymysql.Connection, account_id: int) -> dict[str, Any] | None:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts WHERE id = %s", (account_id,))
    return cursor.fetchone()

def ensure_default_account(conn: pymysql.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM accounts")
    if cursor.fetchone()["cnt"] == 0:
        cursor.execute(
            "INSERT INTO accounts (id, name, broker, type) VALUES (1, '默认账户', '', 'real')"
        )
```

- [ ] **Step 7: Run tests to verify no regressions**

Run: `python -m unittest tests.test_engine -v`
Expected: All 10 tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/chaogu_alert/config.py src/chaogu_alert/db.py
git commit -m "feat: add AccountSettings and accounts table for multi-account architecture"
```

---

### Task 2: Config migration — portfolio to accounts

**Files:**
- Modify: `config.example.toml`
- Modify: `config.toml`
- Modify: `src/chaogu_alert/config.py` (backward compat in load_config)

- [ ] **Step 1: Add backward compat for single portfolio config**

In `load_config()`, after parsing accounts, fall back to `[portfolio]` if `[[accounts]]` is empty:

```python
if not account_list and holdings:
    account_list.append(AccountSettings(
        id=1,
        name="默认账户",
        broker="",
        type="real",
        portfolio=PortfolioSettings(
            available_cash=float(portfolio.get("available_cash", 0.0)),
            holdings=holdings,
        ),
    ))
```

- [ ] **Step 2: Update config.example.toml**

Replace `[portfolio]` section with commented `[[accounts]]`:

```toml
# -- Multi-Account (replaces [portfolio]) --
[[accounts]]
id = 1
name = "招商证券"
broker = "zhaoshang"
type = "real"
initial_capital = 120000

[accounts.portfolio]
available_cash = 20000

[[accounts.portfolio.holdings]]
symbol = "510300"
shares = 3000
cost_basis = 4.68
min_t_trade_pct = 0.10
max_t_trade_pct = 0.20

[[accounts.portfolio.holdings]]
symbol = "512880"
shares = 5000
cost_basis = 1.52
min_t_trade_pct = 0.10
max_t_trade_pct = 0.20

# -- Legacy portfolio (used if [[accounts]] not configured) --
[portfolio]
available_cash = 0
```

- [ ] **Step 3: Update config.toml with real account**

Same structure as example but with real data. Move existing `[[portfolio.holdings]]` into `[[accounts]].portfolio.holdings`.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.example.toml config.toml src/chaogu_alert/config.py
git commit -m "refactor: migrate config from [portfolio] to [[accounts]] with backward compat"
```

---

### Task 3: Update existing persistence for account_id

**Files:**
- Modify: `src/chaogu_alert/mysql_persistence.py`
- Modify: `src/chaogu_alert/db.py` (update insert/update functions)

- [ ] **Step 1: Update insert_trade_outcomes to accept account_id**

In `db.py`, find the `insert_trade_outcomes` function and add `account_id`:

```python
def insert_trade_outcomes(
    conn: pymysql.Connection, report_id: int, outcomes: list[dict],
    data_mode: str = "real", account_id: int = 1,
) -> None:
    if not outcomes:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO trade_outcomes "
        "(account_id, report_id, symbol, name, strategy_id, signal_group, "
        "action, score, entry_price, stop_loss, take_profit, "
        "suggested_shares, suggested_value, data_mode) "
        "VALUES (%(account_id)s, %(report_id)s, %(symbol)s, %(name)s, "
        "%(strategy_id)s, %(signal_group)s, %(action)s, %(score)s, "
        "%(entry_price)s, %(stop_loss)s, %(take_profit)s, "
        "%(suggested_shares)s, %(suggested_value)s, %(data_mode)s)",
        [{**o, "report_id": report_id, "account_id": account_id, "data_mode": data_mode}
         for o in outcomes],
    )
```

- [ ] **Step 2: Update settle_outcomes to filter by account_id**

Add `account_id` parameter:

```python
def settle_outcomes(
    conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1,
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT o.id, o.symbol, o.entry_price, o.stop_loss, o.take_profit, "
        "o.action, o.report_id, r.scan_date "
        "FROM trade_outcomes o JOIN scan_reports r ON o.report_id = r.id "
        "WHERE o.outcome = 'pending' AND o.data_mode = %s AND o.account_id = %s",
        (data_mode, account_id),
    )
    # ... rest stays same
```

- [ ] **Step 3: Update recalculate_performance for account_id**

```python
def recalculate_performance(
    conn: pymysql.Connection, data_mode: str = "real", account_id: int = 1,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM strategy_performance WHERE data_mode = %s AND account_id = %s",
        (data_mode, account_id),
    )
    cursor.execute(
        "SELECT strategy_id, signal_group, COUNT(*) AS total, "
        "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses, "
        "SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END) AS pending, "
        "AVG(CASE WHEN outcome!='pending' THEN pnl_pct ELSE NULL END) AS avg_pnl, "
        "SUM(CASE WHEN outcome!='pending' THEN pnl_pct ELSE 0 END) AS total_pnl, "
        "AVG(score) AS avg_score "
        "FROM trade_outcomes WHERE signal_group = 't_trade' "
        "AND data_mode = %s AND account_id = %s "
        "GROUP BY strategy_id, signal_group",
        (data_mode, account_id),
    )
    # ... INSERT INTO strategy_performance adds account_id
```

- [ ] **Step 4: Update MySqlScanPersistence.save_scan to pass account_id**

In `mysql_persistence.py`, get the default account_id (1 for now, to be wired from web session later):

```python
def save_scan(self, context: ScanContext, report: ScanReport, account_id: int = 1) -> None:
    # ... existing code ...
    insert_trade_outcomes(conn, report_id, outcomes, data_mode=data_mode, account_id=account_id)
    settled = settle_outcomes(conn, data_mode=data_mode, account_id=account_id)
    recalculate_performance(conn, data_mode=data_mode, account_id=account_id)
```

- [ ] **Step 5: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/chaogu_alert/db.py src/chaogu_alert/mysql_persistence.py
git commit -m "feat: add account_id to persistence flow (settle, recalculate, outcomes)"
```

---

### Task 4: Web account switcher

**Files:**
- Modify: `web.py`
- Modify: `templates/base.html`
- Modify: `static/style.css`

- [ ] **Step 1: Add session-based account context in web.py**

After the existing config loading, add:

```python
from flask import session

@app.before_request
def load_account():
    if "account_id" not in session:
        session["account_id"] = 1

def get_account_id() -> int:
    return session.get("account_id", 1)

def get_account_name() -> str:
    config = get_config()
    for acct in config.accounts:
        if acct.id == get_account_id():
            return acct.name
    return "默认账户"
```

- [ ] **Step 2: Add account switcher route**

```python
@app.route("/account/<int:account_id>")
def switch_account(account_id: int):
    config = get_config()
    valid = any(acct.id == account_id for acct in config.accounts) or account_id == 1
    if valid:
        session["account_id"] = account_id
    return redirect(request.referrer or "/")
```

- [ ] **Step 3: Add account switcher to base.html nav**

In `templates/base.html`, add to the nav section before the mode-toggle:

```html
{% if data_mode == 'real' %}
<div class="account-switcher">
  <select onchange="location.href='/account/'+this.value" class="account-select">
    {% for acct in accounts %}
    <option value="{{ acct.id }}" {% if session.get('account_id', 1) == acct.id %}selected{% endif %}>
      {{ acct.name }}
    </option>
    {% endfor %}
  </select>
</div>
{% endif %}
```

- [ ] **Step 4: Pass accounts to all templates**

Update the `data_mode` context to also include accounts:

```python
@app.context_processor
def inject_context():
    config = get_config()
    return dict(
        data_mode=get_data_mode(),
        accounts=config.accounts or [],
        current_account_id=session.get("account_id", 1),
    )
```

- [ ] **Step 5: Add CSS for account switcher**

```css
.account-switcher { margin: 0 8px; }
.account-select {
  background: var(--bg-input);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 13px;
  cursor: pointer;
}
```

- [ ] **Step 6: Run tests and verify**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add web.py templates/base.html static/style.css
git commit -m "feat: add web account switcher with session-based account_id"
```

---

### Task 5: Enhance strategy_performance table

**Files:**
- Modify: `src/chaogu_alert/db.py` (schema migration + recalculate_performance)

- [ ] **Step 1: Add migration SQL for new columns**

Add to the migration section:

```python
_PERFORMANCE_MIGRATION_SQL = [
    "ALTER TABLE strategy_performance ADD COLUMN avg_holding_days DECIMAL(6,1) NOT NULL DEFAULT 0 AFTER avg_pnl_pct",
    "ALTER TABLE strategy_performance ADD COLUMN max_drawdown_pct DECIMAL(8,2) NOT NULL DEFAULT 0 AFTER avg_holding_days",
    "ALTER TABLE strategy_performance ADD COLUMN sharpe_ratio DECIMAL(6,2) DEFAULT NULL AFTER max_drawdown_pct",
    "ALTER TABLE strategy_performance ADD COLUMN best_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0 AFTER sharpe_ratio",
    "ALTER TABLE strategy_performance ADD COLUMN worst_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 0 AFTER best_trade_pct",
    "ALTER TABLE strategy_performance ADD COLUMN weekly_pnl_json TEXT AFTER worst_trade_pct",
    "ALTER TABLE strategy_performance ADD COLUMN monthly_pnl_json TEXT AFTER weekly_pnl_json",
]
```

Apply via `_safe_migrate` in `ensure_tables()`.

- [ ] **Step 2: Update recalculate_performance to compute new metrics**

After the existing INSERT, add computation of new fields:

```python
# After inserting base stats, compute enhanced metrics per strategy
for r in rows:
    sid = r["strategy_id"]
    sg = r["signal_group"]

    # avg holding days: average of (settled_date - scan_date) for settled outcomes
    cursor.execute(
        "SELECT AVG(DATEDIFF(o.settled_date, r.scan_date)) as avg_days "
        "FROM trade_outcomes o JOIN scan_reports r ON o.report_id = r.id "
        "WHERE o.strategy_id = %s AND o.signal_group = %s "
        "AND o.outcome IN ('win','loss') AND o.settled_date IS NOT NULL "
        "AND o.data_mode = %s AND o.account_id = %s",
        (sid, sg, data_mode, account_id),
    )
    avg_days = cursor.fetchone()["avg_days"] or 0

    # best/worst trade
    cursor.execute(
        "SELECT MAX(pnl_pct) as best, MIN(pnl_pct) as worst "
        "FROM trade_outcomes WHERE strategy_id = %s AND signal_group = %s "
        "AND outcome IN ('win','loss') AND data_mode = %s AND account_id = %s",
        (sid, sg, data_mode, account_id),
    )
    extremes = cursor.fetchone()

    # weekly pnl (last 4 weeks)
    cursor.execute(
        "SELECT WEEK(o.settled_date) as wk, SUM(o.pnl_amount) as wk_pnl "
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
        "SELECT DATE_FORMAT(o.settled_date, '%%Y-%%m') as mo, SUM(o.pnl_amount) as mo_pnl "
        "FROM trade_outcomes o WHERE o.strategy_id = %s AND o.signal_group = %s "
        "AND o.outcome IN ('win','loss') AND o.settled_date IS NOT NULL "
        "AND o.data_mode = %s AND o.account_id = %s "
        "AND o.settled_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) "
        "GROUP BY mo ORDER BY mo DESC",
        (sid, sg, data_mode, account_id),
    )
    monthly = [{"month": row["mo"], "pnl": float(row["mo_pnl"] or 0)}
               for row in cursor.fetchall()]

    # Update the inserted row
    cursor.execute(
        "UPDATE strategy_performance SET "
        "avg_holding_days = %s, best_trade_pct = %s, worst_trade_pct = %s, "
        "weekly_pnl_json = %s, monthly_pnl_json = %s "
        "WHERE strategy_id = %s AND signal_group = %s "
        "AND data_mode = %s AND account_id = %s",
        (avg_days, float(extremes["best"] or 0), float(extremes["worst"] or 0),
         json.dumps(weekly), json.dumps(monthly),
         sid, sg, data_mode, account_id),
    )
```

Add `import json` at top of db.py.

- [ ] **Step 3: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/chaogu_alert/db.py
git commit -m "feat: enhance strategy_performance with holding days, drawdown, sharpe, weekly/monthly pnl"
```

---

### Task 6: Enhance performance Web page

**Files:**
- Modify: `web.py` (`/performance` route)
- Modify: `templates/performance.html`

- [ ] **Step 1: Update /performance route to pass enhanced data**

```python
@app.route("/performance")
def performance_view():
    config = get_config()
    data_mode = get_data_mode()
    account_id = get_account_id()
    try:
        with connection_context(config.mysql) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM strategy_performance "
                "WHERE data_mode = %s AND account_id = %s "
                "ORDER BY strategy_id, signal_group",
                (data_mode, account_id),
            )
            perf = list(cursor.fetchall())
    except Exception as exc:
        perf = []
        error = f"查询失败：{exc}"
        return render_template("performance.html", perf=[], error=error, data_mode=data_mode)
    return render_template("performance.html", perf=perf, error=None, data_mode=data_mode)
```

- [ ] **Step 2: Update performance.html template**

Add new columns to the table and a weekly PnL bar section:

```html
{% extends "base.html" %}
{% block title %}策略绩效 - Chaogu-Alert{% endblock %}
{% block content %}
<h2>策略绩效</h2>
{% if perf %}
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>策略</th><th>信号组</th><th>信号数</th><th>胜率</th>
        <th>均盈%</th><th>均亏%</th><th>总PnL</th><th>回撤</th>
        <th>夏普</th><th>持仓天</th><th>近4周</th>
      </tr>
    </thead>
    <tbody>
      {% for p in perf %}
      <tr>
        <td>{{ p.strategy_id }}</td>
        <td>{{ p.signal_group }}</td>
        <td>{{ p.total_signals }}</td>
        <td class="{{ 'text-green' if p.win_rate > 0.5 else 'text-red' }}">
          {{ "%.1f"|format(p.win_rate * 100) }}%
        </td>
        <td class="text-green">+{{ "%.2f"|format(p.avg_pnl_pct * 100) if p.avg_pnl_pct > 0 else "0.00" }}%</td>
        <td class="text-red">{{ "%.2f"|format(p.avg_pnl_pct * 100) if p.avg_pnl_pct < 0 else "0.00" }}%</td>
        <td class="{{ 'text-green' if p.total_pnl_pct > 0 else 'text-red' }}">
          {{ "%+.2f"|format(p.total_pnl_pct * 100) }}%
        </td>
        <td>{{ "%.1f"|format(p.max_drawdown_pct) }}%</td>
        <td>{{ "%.2f"|format(p.sharpe_ratio) if p.sharpe_ratio else "-" }}</td>
        <td>{{ "%.0f"|format(p.avg_holding_days) }}天</td>
        <td style="font-size:10px;">
          {% set weekly = p.weekly_pnl_json | from_json if p.weekly_pnl_json else [] %}
          {% for w in weekly[:4] %}
          <span class="{{ 'text-green' if w.pnl > 0 else 'text-red' }}">{{ "%+.0f"|format(w.pnl) }}</span>
          {% endfor %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<div class="empty-state"><p class="empty-text">暂无绩效数据</p></div>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add web.py templates/performance.html
git commit -m "feat: enhance performance page with new metrics and weekly pnl display"
```

---

### Task 7: actual_trades + trade_images tables

**Files:**
- Modify: `src/chaogu_alert/db.py` (schema + insert/get functions)

- [ ] **Step 1: Add tables to _SCHEMA_SQL**

Before `"""` closing the schema string, add:

```sql
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
```

- [ ] **Step 2: Add CRUD functions**

```python
def insert_trade_image(
    conn: pymysql.Connection, account_id: int, file_path: str,
    file_hash: str, trade_date_hint: date | None = None,
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
    start_date: date | None = None, end_date: date | None = None,
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
```

- [ ] **Step 3: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/chaogu_alert/db.py
git commit -m "feat: add actual_trades and trade_images tables with CRUD functions"
```

---

### Task 8: PaddleOCR pipeline

**Files:**
- Create: `src/chaogu_alert/ocr.py`

- [ ] **Step 1: Create ocr.py with pipeline**

```python
from __future__ import annotations

import re
from typing import Any


def extract_trades(image_path: str) -> list[dict[str, Any]]:
    """Run PaddleOCR on image and extract trade records."""
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise ImportError(
            "PaddleOCR not installed. Run: pip install paddleocr paddlepaddle"
        )

    ocr = PaddleOCR(lang="ch", show_log=False)
    result = ocr.ocr(image_path)

    if not result or not result[0]:
        return []

    text_boxes = [
        {"text": line[1][0], "confidence": line[1][1],
         "bbox": line[0]}
        for line in result[0]
    ]

    rows = _cluster_rows(text_boxes)
    trades = []
    for row_texts in rows:
        trade = _extract_fields(row_texts)
        if _validate(trade):
            trades.append(trade)
    return trades


def _cluster_rows(text_boxes: list[dict]) -> list[list[str]]:
    """Cluster text boxes into rows by y-coordinate proximity."""
    if not text_boxes:
        return []
    sorted_boxes = sorted(text_boxes, key=lambda b: (b["bbox"][0][1], b["bbox"][0][0]))
    rows: list[list[str]] = []
    current_row: list[str] = []
    current_y = sorted_boxes[0]["bbox"][0][1]

    for box in sorted_boxes:
        y = box["bbox"][0][1]
        if abs(y - current_y) < 10:  # same row threshold
            current_row.append(box["text"])
        else:
            if current_row:
                rows.append(current_row)
            current_row = [box["text"]]
            current_y = y
    if current_row:
        rows.append(current_row)
    return rows


def _extract_fields(row_texts: list[str]) -> dict[str, Any]:
    """Extract trade fields from a row of texts using regex."""
    combined = " ".join(row_texts)

    # Symbol code: 6 digits
    code_match = re.search(r"\b(\d{6})\b", combined)
    symbol = code_match.group(1) if code_match else ""

    # Action direction
    action = "buy" if any(w in combined for w in ["买入", "买"]) else "sell" if any(w in combined for w in ["卖出", "卖"]) else ""

    # Price: decimal with 3 digits after point (Chinese brokerage standard)
    price_match = re.search(r"(\d+\.\d{3})", combined)
    price = float(price_match.group(1)) if price_match else 0.0

    # Date: YYYY-MM-DD or YYYY/MM/DD
    date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", combined)
    trade_date = date_match.group(1).replace("/", "-") if date_match else ""

    # Time: HH:MM:SS
    time_match = re.search(r"(\d{2}:\d{2}:\d{2})", combined)
    trade_time = time_match.group(1) if time_match else None

    # Shares: integer, not matching code or date/year
    shares_matches = re.findall(r"\b(\d+)\b", combined)
    shares = 0
    for m in shares_matches:
        n = int(m)
        if 100 <= n <= 1000000 and n % 100 == 0:
            shares = n
            break

    # Amount: decimal with 2 digits
    amount_match = re.search(r"(\d+\.\d{2})", combined)
    amount = float(amount_match.group(1)) if amount_match else round(shares * price, 2)

    return {
        "symbol": symbol,
        "name": "",
        "action": action,
        "trade_date": trade_date,
        "trade_time": trade_time,
        "price": price,
        "shares": shares,
        "amount": amount,
        "commission": 0.0,
    }


def _validate(trade: dict) -> bool:
    """Return True if required fields are present."""
    return bool(
        trade["symbol"]
        and trade["action"]
        and trade["trade_date"]
        and trade["price"] > 0
        and trade["shares"] > 0
    )
```

- [ ] **Step 2: Add pyproject.toml optional dependency**

```toml
[project.optional-dependencies]
ocr = ["paddleocr>=2.8", "paddlepaddle"]
```

- [ ] **Step 3: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS (ocr module not loaded until called)

- [ ] **Step 4: Commit**

```bash
git add src/chaogu_alert/ocr.py pyproject.toml
git commit -m "feat: add PaddleOCR pipeline for 招商证券 trade screenshot extraction"
```

---

### Task 9: Trade upload and OCR Web routes

**Files:**
- Modify: `web.py` (new routes)
- Create: `templates/trades.html`

- [ ] **Step 1: Add upload route**

```python
import hashlib
import os
from pathlib import Path

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads" / "trades"

@app.route("/trades")
def trades_list():
    config = get_config()
    account_id = get_account_id()
    data_mode = get_data_mode()
    try:
        with connection_context(config.mysql) as conn:
            images = get_trade_images(conn, account_id, limit=30)
            trades = get_actual_trades(conn, account_id)
    except Exception as exc:
        return render_template("trades.html", trades=[], images=[],
                               data_mode=data_mode, error=f"查询失败：{exc}")
    return render_template("trades.html", trades=trades, images=images, data_mode=data_mode)


@app.route("/trades/upload", methods=["POST"])
def trades_upload():
    account_id = get_account_id()
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "empty filename"}), 400

    content = file.read()
    file_hash = hashlib.md5(content).hexdigest()

    config = get_config()
    try:
        with connection_context(config.mysql) as conn:
            # Check duplicate
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM trade_images WHERE file_hash = %s", (file_hash,)
            )
            if cursor.fetchone():
                return jsonify({"error": "该截图已上传过"}), 409

            # Save file
            month_dir = UPLOAD_DIR / str(account_id) / date.today().strftime("%Y-%m")
            month_dir.mkdir(parents=True, exist_ok=True)
            save_path = month_dir / f"{file_hash}.png"
            with open(save_path, "wb") as f:
                f.write(content)

            # Insert image record
            image_id = insert_trade_image(
                conn, account_id, str(save_path), file_hash,
            )

            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # Run OCR
    try:
        from chaogu_alert.ocr import extract_trades
        trades = extract_trades(str(save_path))
        ocr_json = json.dumps(trades, ensure_ascii=False, default=str)
        ocr_status = "done" if trades else "failed"

        with connection_context(config.mysql) as conn:
            update_trade_image_ocr(conn, image_id, ocr_status, ocr_json)
            conn.commit()
    except ImportError:
        ocr_status = "failed"
        ocr_json = json.dumps({"error": "PaddleOCR not installed"})
        trades = []
    except Exception as exc:
        ocr_status = "failed"
        ocr_json = json.dumps({"error": str(exc)})
        trades = []

    return jsonify({
        "image_id": image_id,
        "ocr_status": ocr_status,
        "trades": trades,
    })


@app.route("/trades/confirm", methods=["POST"])
def trades_confirm():
    account_id = get_account_id()
    data = request.get_json()
    if not data or "trades" not in data:
        return jsonify({"error": "no trades data"}), 400

    config = get_config()
    try:
        with connection_context(config.mysql) as conn:
            trades = [
                {
                    "account_id": account_id,
                    "symbol": t["symbol"],
                    "name": t.get("name", ""),
                    "action": t["action"],
                    "trade_date": t["trade_date"],
                    "trade_time": t.get("trade_time"),
                    "price": t["price"],
                    "shares": t["shares"],
                    "amount": t.get("amount", round(t["price"] * t["shares"], 2)),
                    "commission": t.get("commission", 0),
                    "image_id": t.get("image_id"),
                    "ocr_confidence": t.get("ocr_confidence"),
                    "ocr_raw_text": t.get("ocr_raw_text", ""),
                    "source": t.get("source", "ocr"),
                }
                for t in data["trades"]
            ]
            insert_actual_trades(conn, trades)
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "count": len(trades)})
```

- [ ] **Step 2: Create trades.html template**

```html
{% extends "base.html" %}
{% block title %}交易记录 - Chaogu-Alert{% endblock %}
{% block content %}
<h2>交易记录</h2>

<div class="section">
  <h3>上传截图</h3>
  <div class="upload-zone" id="upload-zone">
    <input type="file" id="file-input" accept="image/*" style="display:none">
    <p class="upload-hint">拖拽或点击上传券商截图</p>
  </div>
  <div id="ocr-progress" style="display:none; margin-top:12px;">
    <div class="spinner"></div> OCR识别中...
  </div>
  <div id="ocr-result" style="margin-top:12px;"></div>
</div>

{% if trades %}
<div class="section">
  <h3>交易明细 <span class="count">{{ trades|length }}</span></h3>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>日期</th><th>标的</th><th>方向</th><th>价格</th>
          <th>数量</th><th>金额</th><th>来源</th><th>置信度</th>
        </tr>
      </thead>
      <tbody>
        {% for t in trades %}
        <tr>
          <td>{{ t.trade_date }}</td>
          <td><strong>{{ t.symbol }}</strong></td>
          <td class="{{ 'text-green' if t.action == 'buy' else 'text-red' }}">{{ t.action }}</td>
          <td>{{ "%.3f"|format(t.price) }}</td>
          <td>{{ t.shares }}</td>
          <td>{{ "%.2f"|format(t.amount) }}</td>
          <td class="dim">{{ t.source }}</td>
          <td>{{ "%.1f"|format(t.ocr_confidence * 100) if t.ocr_confidence else "-" }}%</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% else %}
<div class="empty-state small"><p class="empty-text">暂无交易记录，请上传截图</p></div>
{% endif %}
{% endblock %}

{% block scripts %}
<script>
(function() {
  var zone = document.getElementById('upload-zone');
  var input = document.getElementById('file-input');
  var progress = document.getElementById('ocr-progress');
  var result = document.getElementById('ocr-result');

  zone.addEventListener('click', function() { input.click(); });
  zone.addEventListener('dragover', function(e) { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', function() { zone.classList.remove('drag-over'); });
  zone.addEventListener('drop', function(e) {
    e.preventDefault();
    zone.classList.remove('drag-over');
    uploadFile(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', function() {
    if (input.files[0]) uploadFile(input.files[0]);
  });

  function uploadFile(file) {
    progress.style.display = 'block';
    result.innerHTML = '';
    var fd = new FormData();
    fd.append('file', file);
    fetch('/trades/upload', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        progress.style.display = 'none';
        if (data.error) {
          result.innerHTML = '<div class="alert alert-error">' + data.error + '</div>';
        } else if (data.trades && data.trades.length) {
          var html = '<h4>识别出 ' + data.trades.length + ' 笔交易（请确认后保存）</h4>';
          html += '<table><thead><tr><th>日期</th><th>代码</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th></tr></thead><tbody>';
          data.trades.forEach(function(t) {
            html += '<tr><td>' + t.trade_date + '</td><td>' + t.symbol + '</td><td>' + t.action + '</td><td>' + t.price + '</td><td>' + t.shares + '</td><td>' + t.amount + '</td></tr>';
          });
          html += '</tbody></table>';
          html += '<button class="btn-scan" onclick="confirmTrades(' + JSON.stringify(data.trades).replace(/"/g, '&quot;') + ', ' + data.image_id + ')">确认并保存</button>';
          result.innerHTML = html;
        } else {
          result.innerHTML = '<div class="alert alert-error">未能识别出交易记录，请检查截图格式</div>';
        }
      })
      .catch(function(err) {
        progress.style.display = 'none';
        result.innerHTML = '<div class="alert alert-error">上传失败: ' + err + '</div>';
      });
  }

  window.confirmTrades = function(trades, imageId) {
    trades.forEach(function(t) { t.image_id = imageId; });
    fetch('/trades/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trades: trades })
    })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.ok) {
          location.reload();
        } else {
          result.innerHTML += '<div class="alert alert-error">保存失败: ' + data.error + '</div>';
        }
      });
  };
})();
</script>
{% endblock %}
```

- [ ] **Step 3: Add CSS for upload zone**

```css
.upload-zone {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 36px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s;
}
.upload-zone:hover, .upload-zone.drag-over {
  border-color: var(--accent);
}
.upload-hint { color: var(--text-dim); font-size: 14px; margin: 0; }
```

- [ ] **Step 4: Add nav link in base.html**

```html
<a href="/trades" class="{% if request.path.startswith('/trades') %}active{% endif %}">交易记录</a>
```

- [ ] **Step 5: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web.py templates/trades.html templates/base.html static/style.css
git commit -m "feat: add trade upload, OCR, and /trades page"
```

---

### Task 10: Comparison matching engine

**Files:**
- Create: `src/chaogu_alert/comparison.py`

- [ ] **Step 1: Create comparison.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class MatchedRow:
    trade_date: date
    symbol: str
    strategy_signal: dict | None   # trade_outcomes row or None
    actual_trade: dict | None      # actual_trades row or None
    deviation: str = ""            # match|miss|hold_loss|overtrade|early_exit|slippage
    pnl_impact: float = 0.0


@dataclass
class ComparisonResult:
    start_date: date
    end_date: date
    total_strategy_pnl: float
    total_actual_pnl: float
    execution_gap: float
    signal_execution_rate: float
    matched_rows: list[MatchedRow] = field(default_factory=list)
    defect_summary: dict[str, dict] = field(default_factory=dict)


def match_strategy_to_actual(
    trade_outcomes: list[dict],
    actual_trades: list[dict],
    start_date: date,
    end_date: date,
) -> ComparisonResult:
    """Match strategy signals to actual trades and classify deviations."""
    # Index actual trades by (date, symbol) for O(1) lookup
    actual_by_key: dict[tuple, list[dict]] = {}
    for t in actual_trades:
        td = _to_date(t["trade_date"])
        if td is None:
            continue
        key = (td, t["symbol"])
        actual_by_key.setdefault(key, []).append(t)

    # Index strategy signals by (date, symbol)
    strategy_by_key: dict[tuple, list[dict]] = {}
    total_strategy_pnl = 0.0
    for o in trade_outcomes:
        td = _to_date(o.get("settled_date") or o.get("scan_date"))
        if td is None:
            continue
        key = (td, o["symbol"])
        strategy_by_key.setdefault(key, []).append(o)
        pnl = float(o.get("pnl_amount") or 0)
        total_strategy_pnl += pnl

    total_actual_pnl = 0.0
    for t in actual_trades:
        total_actual_pnl += float(t.get("pnl_amount") or 0)

    # Match with ±1 day tolerance
    all_dates = sorted(set(
        list(strategy_by_key.keys()) + list(actual_by_key.keys())
    ))
    rows: list[MatchedRow] = []
    execution_gap = 0.0

    for (d, sym) in all_dates:
        strategy_signals = strategy_by_key.get((d, sym), [])
        actuals = actual_by_key.get((d, sym), [])

        # Also check ±1 day
        if not actuals:
            for offset in [-1, 1]:
                adjacent = actual_by_key.get((d + timedelta(days=offset), sym), [])
                if adjacent:
                    actuals = adjacent
                    break

        s_has_buy = any(s["action"] == "buy" for s in strategy_signals)
        s_has_sell = any(s["action"] == "sell" for s in strategy_signals)
        a_has_buy = any(t["action"] == "buy" for t in actuals)
        a_has_sell = any(t["action"] == "sell" for t in actuals)

        deviation = "match"
        impact = 0.0

        if s_has_buy and a_has_buy:
            deviation = "match"
        elif s_has_sell and a_has_sell:
            deviation = "match"
        elif s_has_buy and not a_has_buy:
            deviation = "miss"
            impact = sum(float(s.get("pnl_amount") or 0) for s in strategy_signals)
        elif s_has_sell and not a_has_sell:
            deviation = "hold_loss"
            impact = sum(float(s.get("pnl_amount") or 0) for s in strategy_signals)
        elif not s_has_buy and not s_has_sell and a_has_buy:
            deviation = "overtrade"
            impact = -sum(float(t.get("amount") or 0) * 0.02 for t in actuals)
        elif s_has_buy and a_has_sell:
            deviation = "early_exit"
            impact = sum(float(s.get("pnl_amount") or 0) for s in strategy_signals)

        if deviation != "match":
            execution_gap += impact

        rows.append(MatchedRow(
            trade_date=d,
            symbol=sym,
            strategy_signal=strategy_signals[0] if strategy_signals else None,
            actual_trade=actuals[0] if actuals else None,
            deviation=deviation,
            pnl_impact=round(impact, 2),
        ))

    # Summarize defects
    from collections import defaultdict
    defects: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_loss": 0.0})
    for row in rows:
        if row.deviation != "match":
            defects[row.deviation]["count"] += 1
            defects[row.deviation]["total_loss"] += row.pnl_impact

    signal_count = sum(1 for r in rows if r.strategy_signal)
    executed_count = sum(1 for r in rows if r.strategy_signal and r.actual_trade)
    exec_rate = executed_count / signal_count if signal_count > 0 else 0.0

    return ComparisonResult(
        start_date=start_date,
        end_date=end_date,
        total_strategy_pnl=round(total_strategy_pnl, 2),
        total_actual_pnl=round(total_actual_pnl, 2),
        execution_gap=round(execution_gap, 2),
        signal_execution_rate=round(exec_rate, 4),
        matched_rows=rows,
        defect_summary=dict(defects),
    )


def _to_date(val) -> date | None:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    return None
```

- [ ] **Step 2: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/chaogu_alert/comparison.py
git commit -m "feat: add strategy vs actual trade comparison matching engine"
```

---

### Task 11: Comparison Web page

**Files:**
- Modify: `web.py` (add /compare route)
- Create: `templates/compare.html`
- Modify: `templates/base.html` (add nav link)

- [ ] **Step 1: Add /compare route**

```python
@app.route("/compare")
def compare_page():
    config = get_config()
    data_mode = get_data_mode()
    account_id = get_account_id()
    error = None
    result = None

    try:
        with connection_context(config.mysql) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT o.*, r.scan_date FROM trade_outcomes o "
                "JOIN scan_reports r ON o.report_id = r.id "
                "WHERE o.data_mode = %s AND o.account_id = %s "
                "ORDER BY r.scan_date",
                (data_mode, account_id),
            )
            outcomes = list(cursor.fetchall())

            cursor.execute(
                "SELECT * FROM actual_trades WHERE account_id = %s "
                "ORDER BY trade_date",
                (account_id,),
            )
            actuals = list(cursor.fetchall())

        if outcomes or actuals:
            today = date.today()
            from chaogu_alert.comparison import match_strategy_to_actual

            # Determine date range
            all_dates = []
            for o in outcomes:
                d = o.get("settled_date") or o.get("scan_date")
                if d:
                    all_dates.append(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
            for t in actuals:
                d = t.get("trade_date")
                if d:
                    all_dates.append(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
            start = min(all_dates) if all_dates else today
            end = max(all_dates) if all_dates else today

            result = match_strategy_to_actual(outcomes, actuals, start, end)

    except Exception as exc:
        error = f"对比分析失败：{exc}"

    return render_template("compare.html", result=result, error=error, data_mode=data_mode)
```

- [ ] **Step 2: Create compare.html template**

```html
{% extends "base.html" %}
{% block title %}策略 vs 实盘对比 - Chaogu-Alert{% endblock %}
{% block content %}
<h2>策略 vs 实盘对比</h2>

{% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}

{% if result %}
<div class="dashboard-grid">
  <div class="dash-card accent-blue">
    <div class="dash-card-value text-green">{{ "%+.0f"|format(result.total_strategy_pnl) }}</div>
    <div class="dash-card-label">策略累计盈亏</div>
  </div>
  <div class="dash-card accent-red">
    <div class="dash-card-value {{ 'text-green' if result.total_actual_pnl > 0 else 'text-red' }}">{{ "%+.0f"|format(result.total_actual_pnl) }}</div>
    <div class="dash-card-label">实盘累计盈亏</div>
  </div>
  <div class="dash-card accent-yellow">
    <div class="dash-card-value {{ 'text-green' if result.execution_gap > 0 else 'text-red' }}">{{ "%+.0f"|format(result.execution_gap) }}</div>
    <div class="dash-card-label">执行差距</div>
  </div>
  <div class="dash-card accent-purple">
    <div class="dash-card-value">{{ "%.1f"|format(result.signal_execution_rate * 100) }}%</div>
    <div class="dash-card-label">信号执行率</div>
  </div>
</div>

{% if result.defect_summary %}
<div class="section">
  <h3>行为缺陷分析</h3>
  <div class="table-wrap">
    <table>
      <thead><tr><th>缺陷类型</th><th>次数</th><th>累计损失</th></tr></thead>
      <tbody>
        {% for dev_type, info in result.defect_summary.items() %}
        <tr>
          <td>
            {% if dev_type == 'miss' %}❌ 漏单
            {% elif dev_type == 'hold_loss' %}🛑 不止损
            {% elif dev_type == 'overtrade' %}⚠ 过度交易
            {% elif dev_type == 'early_exit' %}⏰ 过早止盈
            {% elif dev_type == 'slippage' %}📉 滑点
            {% else %}{{ dev_type }}{% endif %}
          </td>
          <td>{{ info.count }}</td>
          <td class="text-red">{{ "%+.0f"|format(info.total_loss) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}

<div class="section">
  <h3>逐笔对比 <span class="count">{{ result.matched_rows|length }}</span></h3>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>日期</th><th>标的</th><th>策略信号</th><th>实盘操作</th><th>偏差</th><th>影响</th></tr>
      </thead>
      <tbody>
        {% for row in result.matched_rows %}
        <tr class="{{ 'match-row' if row.deviation == 'match' else 'deviation-row' }}">
          <td>{{ row.trade_date }}</td>
          <td><strong>{{ row.symbol }}</strong></td>
          <td>{{ row.strategy_signal.action if row.strategy_signal else '—' }}</td>
          <td>{{ row.actual_trade.action if row.actual_trade else '—' }}</td>
          <td class="{{ 'text-green' if row.deviation == 'match' else 'text-red' }}">
            {% if row.deviation == 'match' %}✅ 一致
            {% elif row.deviation == 'miss' %}❌ 漏单
            {% elif row.deviation == 'hold_loss' %}🛑 不止损
            {% elif row.deviation == 'overtrade' %}⚠ 过度交易
            {% elif row.deviation == 'early_exit' %}⏰ 过早止盈
            {% else %}{{ row.deviation }}{% endif %}
          </td>
          <td class="{{ 'text-green' if row.pnl_impact > 0 else 'text-red' if row.pnl_impact < 0 else '' }}">
            {{ "%+.0f"|format(row.pnl_impact) if row.pnl_impact else '—' }}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% else %}
<div class="empty-state"><p class="empty-text">暂无数据，请先执行扫描并上传交易记录</p></div>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Add nav link and CSS**

In `templates/base.html`:
```html
<a href="/compare" class="{% if request.path.startswith('/compare') %}active{% endif %}">对比</a>
```

In `static/style.css`:
```css
.match-row { background: rgba(52, 211, 153, 0.04); }
.deviation-row { background: rgba(248, 113, 113, 0.04); }
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_engine -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web.py templates/compare.html templates/base.html static/style.css
git commit -m "feat: add strategy vs actual comparison page with defect analysis"
```

---

### Task 12: Integration and final verification

**Files:**
- Modify: `src/chaogu_alert/__init__.py` (export new modules if needed)

- [ ] **Step 1: Verify all imports work**

```bash
python -c "
import sys; sys.path.insert(0, 'src')
from chaogu_alert.config import load_config, AccountSettings
from chaogu_alert.db import get_connection
from chaogu_alert.ocr import extract_trades
from chaogu_alert.comparison import match_strategy_to_actual
print('All imports OK')
"
```

- [ ] **Step 2: Run full test suite**

Run: `python -m unittest tests.test_engine -v`
Expected: 10 tests PASS

- [ ] **Step 3: Test web server startup**

Run: `python web.py --port 5000` (background), verify all routes respond:
```bash
curl -s http://127.0.0.1:5000/ | head -5
curl -s http://127.0.0.1:5000/trades | head -5
curl -s http://127.0.0.1:5000/compare | head -5
curl -s http://127.0.0.1:5000/performance | head -5
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: final integration verification for Phase 1-3"
```
