from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flask import Flask, jsonify, make_response, redirect, render_template, request, session, url_for

from chaogu_alert.comparison import match_strategy_to_actual
from chaogu_alert.config import (
    HoldingSettings,
    get_effective_account_id,
    load_config,
    with_account,
)
from chaogu_alert.data import (
    AkshareEtfDataProvider,
    DemoMarketDataProvider,
    MultiSourceMarketDataProvider,
    SinaEtfDataProvider,
    build_symbol_metadata,
)
from chaogu_alert.db import (
    clear_backtest_history,
    confirm_execution,
    confirm_plan_execution,
    connection_context,
    create_account,
    create_holding,
    delete_holding,
    ensure_tables,
    get_actual_trades,
    get_accounts,
    get_all_tracked_symbols,
    get_backtest_run,
    get_backtest_signals,
    get_backtest_years,
    get_backtest_trades,
    get_dashboard_stats,
    get_recent_pnl,
    get_scan_report,
    get_strategy_performance,
    get_symbol_prices,
    get_trade_images,
    get_trade_outcomes,
    insert_actual_trades,
    insert_trade_image,
    list_backtest_runs,
    list_holdings,
    list_holdings_with_prices,
    list_scan_reports,
    recalculate_performance,
    settle_outcomes,
    update_holding,
    update_trade_image_ocr,
)
from chaogu_alert.engine import ScannerEngine
from chaogu_alert.mysql_persistence import MySqlScanPersistence
from chaogu_alert.persistence import NullScanPersistence
from chaogu_alert.universe import UniverseResolver

import json
import threading

_logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder=str(ROOT / "templates"))
app.secret_key = "chaogu-alert-account-secret-key-change-in-production"

_scan_status: dict[str, str | None] = {}
_scan_lock = threading.Lock()

REGIME_LABELS = {
    "risk_on": "偏强",
    "risk_off": "偏弱",
    "unknown": "未知",
}

STRATEGY_LABELS = {
    "etf_trend": "趋势突破",
    "etf_rotation": "轮动强势",
    "etf_t_trade": "持仓做T",
}

ACTION_LABELS = {
    "buy": "买入",
    "sell": "卖出",
}

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads" / "trades"


def regime_label(value: str) -> str:
    return REGIME_LABELS.get(value, value)


def strategy_label(value: str) -> str:
    return STRATEGY_LABELS.get(value, value)


def action_label(value: str) -> str:
    return ACTION_LABELS.get(value, value)


MODE_LABELS = {"real": "真实数据", "demo": "Demo 模拟"}

app.add_template_global(regime_label)
app.add_template_global(strategy_label)
app.add_template_global(action_label)
app.add_template_global(lambda m: MODE_LABELS.get(m, m), "mode_label")

SYMBOL_SORT_FIELDS = {
    "symbol": "symbol",
    "name": "name",
    "scan_date": "scan_date",
    "previous_close": "previous_close",
    "open": "open",
    "latest_price": "latest_price",
    "pct_change": "pct_change",
    "change_amount": "change_amount",
}


def build_public_quote_url(symbol: str) -> str:
    if not symbol:
        return "#"
    exchange_prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"https://quote.eastmoney.com/{exchange_prefix}{symbol}.html"


def sort_symbol_rows(
    rows: list[dict],
    sort_key: str,
    order: str,
) -> tuple[list[dict], str, str]:
    effective_sort = SYMBOL_SORT_FIELDS.get(sort_key, "symbol")
    effective_order = "desc" if order == "desc" else "asc"
    rows = list(rows)

    with_value = [
        row for row in rows
        if row.get(effective_sort) not in (None, "")
    ]
    without_value = [
        row for row in rows
        if row.get(effective_sort) in (None, "")
    ]

    if effective_sort in {"symbol", "name"}:
        with_value.sort(key=lambda row: str(row.get(effective_sort, "")).lower())
    else:
        with_value.sort(key=lambda row: row.get(effective_sort))

    if effective_order == "desc":
        with_value.reverse()

    return with_value + without_value, effective_sort, effective_order


def _config_account_rows(config) -> list[dict]:
    rows = [
        {
            "id": acct.id,
            "name": acct.name,
            "broker": acct.broker,
            "type": acct.type,
            "initial_capital": acct.initial_capital,
        }
        for acct in (config.accounts or [])
    ]
    if rows:
        return rows
    return [{
        "id": 1,
        "name": "默认账户",
        "broker": "",
        "type": "real",
        "initial_capital": 0.0,
    }]


def get_available_accounts(config) -> list[dict]:
    if config.mysql.enabled:
        try:
            with connection_context(config.mysql) as conn:
                rows = get_accounts(conn)
            if rows:
                return rows
        except Exception:
            _logger.exception("failed to load accounts from mysql")
    return _config_account_rows(config)


def resolve_active_account_id(config) -> int:
    accounts = get_available_accounts(config)
    requested = int(session.get("account_id", 1) or 1)
    if any(int(acct["id"]) == requested for acct in accounts):
        return requested

    fallback = next((int(acct["id"]) for acct in accounts if int(acct["id"]) == 1), None)
    if fallback is None:
        fallback = int(accounts[0]["id"]) if accounts else 1
    session["account_id"] = fallback
    return fallback


app.add_template_global(build_public_quote_url, "public_quote_url")


@app.template_filter("from_json")
def from_json_filter(value):
    if not value:
        return None
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


@app.context_processor
def inject_accounts():
    try:
        config = get_config()
        accounts = get_available_accounts(config)
        return dict(
            accounts=accounts,
            current_account_id=resolve_active_account_id(config),
        )
    except Exception:
        return dict(accounts=[], current_account_id=1)


def get_data_mode() -> str:
    return request.cookies.get("data_mode", "real")


@app.before_request
def load_account():
    if "account_id" not in session:
        session["account_id"] = 1


def get_account_id() -> int:
    return session.get("account_id", 1)


def get_account_name() -> str:
    config = get_config()
    current_account_id = get_effective_account_id(config, get_account_id())
    for acct in config.accounts:
        if acct.id == current_account_id:
            return acct.name
    return "默认账户"


def get_config():
    config_path = app.config.get("CONFIG_PATH", ROOT / "config.toml")
    return load_config(config_path)


def get_active_config():
    config = get_config()
    account_id = get_effective_account_id(config, get_account_id())
    return with_account(config, account_id), account_id


@app.route("/mode/<mode>")
def switch_mode(mode: str):
    if mode not in ("demo", "real"):
        mode = "real"
    resp = make_response(redirect(request.referrer or url_for("scan_list")))
    resp.set_cookie("data_mode", mode, max_age=60 * 60 * 24 * 365)
    return resp


@app.route("/account/<int:account_id>")
def switch_account(account_id: int):
    config = get_config()
    valid = any(acct.id == account_id for acct in config.accounts) or account_id == 1
    if valid:
        session["account_id"] = account_id
    return redirect(request.referrer or "/")


@app.route("/")
def scan_list():
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("scan_list.html", scans=[], stats=None, data_mode=data_mode, error="MySQL 未启用，请在 config.toml 中配置 [mysql] 段")
    try:
        with connection_context(config.mysql) as conn:
            scans = list_scan_reports(conn, data_mode=data_mode)
            stats = get_dashboard_stats(conn, data_mode=data_mode, account_id=account_id)
    except Exception as exc:
        return render_template("scan_list.html", scans=[], stats=None, data_mode=data_mode, error=f"数据库连接失败：{exc}")
    return render_template("scan_list.html", scans=scans, stats=stats, data_mode=data_mode)


@app.route("/scan/<int:report_id>")
def scan_detail(report_id: int):
    config = get_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("scan_detail.html", report=None, data_mode=data_mode, error="MySQL 未启用")
    try:
        with connection_context(config.mysql) as conn:
            report = get_scan_report(conn, report_id, data_mode=data_mode)
    except Exception as exc:
        return render_template("scan_detail.html", report=None, data_mode=data_mode, error=f"数据库连接失败：{exc}")
    if not report:
        return render_template("scan_detail.html", report=None, data_mode=data_mode, error="未找到该扫描记录")
    return render_template("scan_detail.html", report=report, data_mode=data_mode)


@app.route("/scan/plan/<int:plan_id>/confirm", methods=["POST"])
def plan_confirm(plan_id: int):
    """Mark a trade plan as executed or skipped."""
    config = get_config()
    data_mode = get_data_mode()
    action = request.form.get("action", "execute")
    fill_price = request.form.get("fill_price", "").strip()
    try:
        with connection_context(config.mysql) as conn:
            confirm_plan_execution(
                conn, plan_id,
                executed=(action == "execute"),
                actual_fill_price=float(fill_price) if fill_price else None,
                data_mode=data_mode,
            )
    except Exception as exc:
        _logger.exception("confirm plan failed")
    referrer = request.referrer or url_for("scan_list")
    return redirect(referrer)


@app.route("/scan/run", methods=["POST"])
def scan_run():
    config, account_id = get_active_config()
    data_source = request.form.get("data_source", "demo")
    data_mode = "demo" if data_source == "demo" else "real"
    task_id = f"{data_mode}:{account_id}"

    with _scan_lock:
        if _scan_status.get(task_id) == "running":
            with connection_context(config.mysql) as conn:
                scans = list_scan_reports(conn, data_mode=data_mode)
                stats = get_dashboard_stats(conn, data_mode=data_mode, account_id=account_id)
            return render_template("scan_list.html", scans=scans, stats=stats, data_mode=data_mode,
                                   message="扫描正在进行中，请稍后刷新查看结果", message_type="info")

        _scan_status[task_id] = "running"

    def _run_scan():
        try:
            positions: list[dict] = []
            weekly_pnl: list[float] = []
            daily_pnl: list[float] = []
            active_config = config
            if data_mode == "real":
                mysql_holdings = _resolve_web_holdings(
                    config, data_mode=data_mode, account_id=account_id,
                )
                if mysql_holdings is not None:
                    active_config = replace(
                        config,
                        portfolio=replace(config.portfolio, holdings=mysql_holdings),
                    )
            if config.mysql.enabled:
                with connection_context(config.mysql) as conn:
                    positions = list_holdings(conn, data_mode=data_mode, account_id=account_id)
                    weekly_pnl = get_recent_pnl(
                        conn, data_mode=data_mode, days=5, account_id=account_id,
                    )
                    daily_pnl = get_recent_pnl(
                        conn, data_mode=data_mode, days=1, account_id=account_id,
                    )
            provider = _build_web_provider(active_config, data_source)
            persistence = MySqlScanPersistence(config.mysql) if config.mysql.enabled else NullScanPersistence()
            engine = ScannerEngine(
                active_config, provider, persistence=persistence, data_mode=data_mode,
            )
            as_of = date.today()
            report = engine.scan(
                as_of,
                positions=positions,
                weekly_pnl=weekly_pnl,
                daily_pnl=daily_pnl,
                account_id=account_id,
            )
            open_count = len([p for p in report.trade_plans if p.signal_group == "open"])
            t_count = len([p for p in report.trade_plans if p.signal_group == "t_trade"])
            _logger.info("scan complete: %s open=%s t=%s", as_of, open_count, t_count)
        except Exception:
            _logger.exception("background scan failed")
        finally:
            with _scan_lock:
                _scan_status[task_id] = "done"

    threading.Thread(target=_run_scan, daemon=True).start()

    return render_template(
        "scan_running.html", data_mode=data_mode, task_id=task_id,
        message="扫描已提交，正在后台执行...", message_type="info",
    )


@app.route("/scan/status/<task_id>")
def scan_status(task_id: str):
    """Poll endpoint for async scan completion."""
    with _scan_lock:
        status = _scan_status.get(task_id, "unknown")
    return jsonify({"status": status})


def _resolve_web_holdings(config, data_mode: str = "real", account_id: int = 1):
    if not config.mysql.enabled:
        return None
    try:
        with connection_context(config.mysql) as conn:
            rows = list_holdings(conn, data_mode=data_mode, account_id=account_id)
        if not rows:
            return None
        return [HoldingSettings(
            symbol=r["symbol"], shares=int(r["shares"]),
            cost_basis=float(r["cost_basis"]),
            min_t_trade_pct=float(r["min_t_trade_pct"]),
            max_t_trade_pct=float(r["max_t_trade_pct"]),
        ) for r in rows]
    except Exception:
        return None


def _build_web_provider(config, data_source: str = "demo"):
    if data_source == "demo":
        return DemoMarketDataProvider()
    if data_source not in {"auto", "akshare", "sina"}:
        return DemoMarketDataProvider()

    universe = UniverseResolver(config)
    metadata = build_symbol_metadata(
        config.universe.benchmark_symbol,
        config.universe.broad_etfs,
        config.universe.sector_etfs,
        extra_symbols=list(universe.all_scan_symbols()),
    )
    sources = config.app.data_sources if data_source == "auto" else [data_source]
    providers = []
    for src in sources:
        if src == "akshare":
            providers.append(AkshareEtfDataProvider(config.akshare, symbol_metadata=metadata))
        elif src == "sina":
            providers.append(SinaEtfDataProvider(config.akshare, symbol_metadata=metadata))
    if len(providers) == 1:
        return providers[0]
    return MultiSourceMarketDataProvider(providers)


@app.route("/holdings", methods=["GET"])
def holdings_list():
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("holdings.html", holdings=[], data_mode=data_mode, error="MySQL 未启用")
    try:
        with connection_context(config.mysql) as conn:
            holdings = list_holdings_with_prices(
                conn, data_mode=data_mode, account_id=account_id,
            )
    except Exception as exc:
        return render_template("holdings.html", holdings=[], data_mode=data_mode, error=f"数据库连接失败：{exc}")
    return render_template("holdings.html", holdings=holdings, data_mode=data_mode)


@app.route("/holdings/new", methods=["POST"])
def holdings_new():
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    try:
        with connection_context(config.mysql) as conn:
            create_holding(
                conn,
                account_id=account_id,
                symbol=request.form.get("symbol", "").strip(),
                name=request.form.get("name", "").strip(),
                shares=int(request.form.get("shares", 0)),
                cost_basis=float(request.form.get("cost_basis", 0)),
                min_t_trade_pct=float(request.form.get("min_t_trade_pct", 0.10)),
                max_t_trade_pct=float(request.form.get("max_t_trade_pct", 0.20)),
                data_mode=data_mode,
            )
    except Exception as exc:
        return render_template("holdings.html", holdings=[], data_mode=data_mode, error=f"保存失败：{exc}")
    return redirect(url_for("holdings_list"))


@app.route("/holdings/<int:holding_id>/edit", methods=["POST"])
def holdings_edit(holding_id: int):
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    try:
        with connection_context(config.mysql) as conn:
            update_holding(
                conn,
                account_id=account_id,
                holding_id=holding_id,
                symbol=request.form.get("symbol", "").strip(),
                name=request.form.get("name", "").strip(),
                shares=int(request.form.get("shares", 0)),
                cost_basis=float(request.form.get("cost_basis", 0)),
                min_t_trade_pct=float(request.form.get("min_t_trade_pct", 0.10)),
                max_t_trade_pct=float(request.form.get("max_t_trade_pct", 0.20)),
                data_mode=data_mode,
            )
    except Exception as exc:
        return render_template("holdings.html", holdings=[], data_mode=data_mode, error=f"保存失败：{exc}")
    return redirect(url_for("holdings_list"))


@app.route("/holdings/<int:holding_id>/delete", methods=["POST"])
def holdings_delete(holding_id: int):
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    try:
        with connection_context(config.mysql) as conn:
            delete_holding(conn, holding_id, data_mode=data_mode, account_id=account_id)
    except Exception:
        pass
    return redirect(url_for("holdings_list"))


@app.route("/symbols")
def symbols_list():
    """List all tracked symbols with price history."""
    config = get_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("symbols.html", symbols=[], data_mode=data_mode, error="MySQL 未启用")
    try:
        with connection_context(config.mysql) as conn:
            symbols = get_all_tracked_symbols(conn, data_mode=data_mode)
    except Exception as exc:
        return render_template("symbols.html", symbols=[], data_mode=data_mode, error=f"查询失败：{exc}")
    return render_template("symbols.html", symbols=symbols, data_mode=data_mode)


@app.route("/symbol/<symbol>")
def symbol_history(symbol: str):
    """Price history and chart for a single symbol."""
    config = get_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("symbol_history.html", symbol=symbol, name="", prices=[], data_mode=data_mode, error="MySQL 未启用")
    try:
        with connection_context(config.mysql) as conn:
            prices = get_symbol_prices(conn, symbol, data_mode=data_mode)
    except Exception as exc:
        return render_template("symbol_history.html", symbol=symbol, name="", prices=[], data_mode=data_mode, error=f"查询失败：{exc}")
    name = prices[0].get("name", "") if prices else ""
    return render_template("symbol_history.html", symbol=symbol, name=name, prices=prices, data_mode=data_mode)


@app.route("/performance")
def performance():
    """Strategy win-rate dashboard."""
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("performance.html", performances=[], outcomes=[], data_mode=data_mode, error="MySQL 未启用")
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
            outcomes = get_trade_outcomes(
                conn, limit=30, data_mode=data_mode, account_id=account_id,
            )
    except Exception as exc:
        return render_template("performance.html", performances=[], outcomes=[], data_mode=data_mode, error=f"查询失败：{exc}")
    return render_template("performance.html", performances=perf, outcomes=outcomes, data_mode=data_mode)


@app.route("/performance/settle", methods=["POST"])
def performance_settle():
    """Manually trigger outcome settlement."""
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    try:
        with connection_context(config.mysql) as conn:
            n = settle_outcomes(conn, data_mode=data_mode, account_id=account_id)
            recalculate_performance(conn, data_mode=data_mode, account_id=account_id)
        message = f"结算完成：{n} 条记录已更新"
        message_type = "success"
    except Exception as exc:
        message = f"结算失败：{exc}"
        message_type = "error"
    with connection_context(config.mysql) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM strategy_performance "
            "WHERE data_mode = %s AND account_id = %s "
            "ORDER BY strategy_id, signal_group",
            (data_mode, account_id),
        )
        perf = list(cursor.fetchall())
        outcomes = get_trade_outcomes(
            conn, limit=30, data_mode=data_mode, account_id=account_id,
        )
    return render_template("performance.html", performances=perf, outcomes=outcomes,
                           data_mode=data_mode, message=message, message_type=message_type)


@app.route("/optimize")
def optimize():
    """Strategy optimization suggestions based on historical performance."""
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("optimize.html", suggestions=[], data_mode=data_mode, error="MySQL 未启用")

    try:
        with connection_context(config.mysql) as conn:
            perf = get_strategy_performance(
                conn, data_mode=data_mode, account_id=account_id,
            )
            outcomes = get_trade_outcomes(
                conn, limit=200, data_mode=data_mode, account_id=account_id,
            )
    except Exception as exc:
        return render_template("optimize.html", suggestions=[], data_mode=data_mode, error=f"查询失败：{exc}")

    suggestions = _generate_optimization_suggestions(config, perf, outcomes)
    return render_template("optimize.html", suggestions=suggestions, performances=perf, data_mode=data_mode)


def _generate_optimization_suggestions(config, perf, outcomes):
    suggestions = []

    # Build per-strategy outcome analysis
    for p in perf:
        strategy_outs = [o for o in outcomes if o["strategy_id"] == p["strategy_id"]]
        settled = [o for o in strategy_outs if o["outcome"] != "pending"]
        if len(settled) < 5:
            continue

        win_rate = p["win_rate"] or 0
        avg_score = float(p["avg_score"] or 0)
        avg_pnl = float(p["avg_pnl_pct"] or 0)

        # Score calibration check: are high scores predictive?
        wins_scores = [float(o["score"]) for o in settled if o["outcome"] == "win"]
        loss_scores = [float(o["score"]) for o in settled if o["outcome"] == "loss"]
        win_avg = sum(wins_scores) / len(wins_scores) if wins_scores else 0
        loss_avg = sum(loss_scores) / len(loss_scores) if loss_scores else 0
        score_gap = win_avg - loss_avg

        items = []

        # 1. Score threshold calibration
        if p["strategy_id"] == "etf_t_trade":
            current_min = config.strategy.t_trade.min_intraday_pullback_pct
            if score_gap > 3:
                items.append({
                    "param": "min_score 阈值",
                    "current": f"当前 {getattr(config.risk, 'min_score', 68)}",
                    "suggestion": f"评分区分度良好 (盈利均分{win_avg:.1f} vs 亏损均分{loss_avg:.1f})，可小幅提升最小评分到 {min(75, int(win_avg - 2))} 以过滤低质信号",
                    "impact": "预计提高胜率但减少信号数",
                })
            elif score_gap < 1 and win_rate < 0.5:
                items.append({
                    "param": "min_score 阈值",
                    "current": f"当前 {getattr(config.risk, 'min_score', 68)}",
                    "suggestion": f"评分区分度不足 (差值{score_gap:.1f})，建议降低最小评分到 {max(55, int(win_avg - 5))} 以增加样本，同时观察后续表现",
                    "impact": "可能增加信号数但需警惕质量下降",
                })

        # 2. T-trade parameter tuning
        if p["strategy_id"] == "etf_t_trade":
            t_cfg = config.strategy.t_trade

            # Pullback threshold
            if avg_pnl < 0 and t_cfg.min_intraday_pullback_pct < 0.015:
                items.append({
                    "param": "min_intraday_pullback_pct (最小日内回撤)",
                    "current": f"当前 {t_cfg.min_intraday_pullback_pct:.3f} ({t_cfg.min_intraday_pullback_pct*100:.1f}%)",
                    "suggestion": f"胜率偏低({win_rate*100:.0f}%)且平均盈亏为负({avg_pnl*100:.2f}%)，建议将回撤阈值从 {t_cfg.min_intraday_pullback_pct*100:.1f}% 提高到 {min(t_cfg.min_intraday_pullback_pct*100+0.5, 2.0):.1f}%，等待更深的回撤再入场",
                    "impact": "减少信号数但提高单笔质量",
                })
            elif avg_pnl > 0.01 and t_cfg.min_intraday_pullback_pct > 0.005:
                items.append({
                    "param": "min_intraday_pullback_pct (最小日内回撤)",
                    "current": f"当前 {t_cfg.min_intraday_pullback_pct:.3f} ({t_cfg.min_intraday_pullback_pct*100:.1f}%)",
                    "suggestion": f"胜率良好({win_rate*100:.0f}%)，可考虑将回撤阈值从 {t_cfg.min_intraday_pullback_pct*100:.1f}% 微降到 {max(t_cfg.min_intraday_pullback_pct*100-0.2, 0.5):.1f}%，捕捉更多机会",
                    "impact": "可能增加信号数，需跟踪胜率变化",
                })

            # Max daily range
            if win_rate > 0.6 and t_cfg.max_daily_range_pct < 0.08:
                items.append({
                    "param": "max_daily_range_pct (最大日内振幅)",
                    "current": f"当前 {t_cfg.max_daily_range_pct:.3f} ({t_cfg.max_daily_range_pct*100:.1f}%)",
                    "suggestion": f"胜率较高({win_rate*100:.0f}%)，可放宽振幅限制从 {t_cfg.max_daily_range_pct*100:.1f}% 到 8.0%，扩大标的覆盖",
                    "impact": "可能增加高波动标的的信号",
                })

        # 3. Stop loss / take profit ratio
        if p["strategy_id"] == "etf_t_trade":
            wins_list = [o for o in settled if o["outcome"] == "win"]
            if wins_list:
                avg_win_pnl = sum(float(o["pnl_pct"] or 0) for o in wins_list) / len(wins_list)
                loss_list = [o for o in settled if o["outcome"] == "loss"]
                avg_loss_pnl = sum(abs(float(o["pnl_pct"] or 0)) for o in loss_list) if loss_list else 0

                if avg_loss_pnl > avg_win_pnl * 2 and loss_list:
                    items.append({
                        "param": "止损/止盈比例",
                        "current": f"平均盈利 {avg_win_pnl*100:.2f}% / 平均亏损 {avg_loss_pnl*100:.2f}%",
                        "suggestion": f"平均亏损远大于平均盈利，盈亏比偏低 ({avg_win_pnl/avg_loss_pnl:.1f}:1)，建议收紧止损位或放宽止盈目标，使盈亏比 >= 2:1",
                        "impact": "提高盈亏比，直接影响期望收益",
                    })

        if items:
            suggestions.append({
                "strategy_id": p["strategy_id"],
                "signal_group": p["signal_group"],
                "win_rate": win_rate,
                "total": p["total_signals"],
                "avg_pnl": avg_pnl,
                "score_gap": score_gap,
                "items": items,
            })

    return suggestions


# ---- Trade Recording Routes ----

@app.route("/trades")
def trades_list():
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    if not config.mysql.enabled:
        return render_template("trades.html", trades=[], images=[], data_mode=data_mode, error="MySQL 未启用")
    try:
        with connection_context(config.mysql) as conn:
            images = get_trade_images(conn, account_id, limit=30)
            trades = get_actual_trades(conn, account_id)
    except Exception as exc:
        return render_template("trades.html", trades=[], images=[], data_mode=data_mode, error=f"查询失败：{exc}")
    return render_template("trades.html", trades=trades, images=images, data_mode=data_mode)


@app.route("/trades/upload", methods=["POST"])
def trades_upload():
    config, account_id = get_active_config()
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "empty filename"}), 400

    content = file.read()
    file_hash = hashlib.md5(content).hexdigest()

    try:
        with connection_context(config.mysql) as conn:
            # Check duplicate
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM trade_images WHERE file_hash = %s", (file_hash,)
            )
            if cursor.fetchone():
                return jsonify({"error": "该截图已上传过"}), 409

            # Save file to disk
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

    # Run OCR (outside the transaction)
    trades = []
    ocr_status = "failed"
    ocr_json_str = ""
    try:
        from chaogu_alert.ocr import extract_trades
        trades = extract_trades(str(save_path))
        ocr_status = "done" if trades else "failed"
        ocr_json_str = json.dumps(trades, ensure_ascii=False, default=str)
        with connection_context(config.mysql) as conn:
            update_trade_image_ocr(conn, image_id, ocr_status, ocr_json_str)
            conn.commit()
    except ImportError:
        ocr_status = "failed"
        ocr_json_str = json.dumps({"error": "PaddleOCR not installed"}, ensure_ascii=False)
    except Exception as exc:
        ocr_status = "failed"
        ocr_json_str = json.dumps({"error": str(exc)}, ensure_ascii=False)

    return jsonify({
        "image_id": image_id,
        "ocr_status": ocr_status,
        "trades": trades,
    })


@app.route("/trades/confirm", methods=["POST"])
def trades_confirm():
    config, account_id = get_active_config()
    data = request.get_json()
    if not data or "trades" not in data:
        return jsonify({"error": "no trades data"}), 400

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
                    "amount": t.get("amount", round(float(t["price"]) * int(t["shares"]), 2)),
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


# ---- Comparison Routes ----

@app.route("/compare")
def compare_page():
    config, account_id = get_active_config()
    data_mode = get_data_mode()
    error = None
    result = None

    if not config.mysql.enabled:
        return render_template("compare.html", result=None, data_mode=data_mode, error="MySQL 未启用")

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
            # Determine date range from data
            all_dates = []
            for o in outcomes:
                d = o.get("settled_date") or o.get("scan_date")
                if d:
                    if isinstance(d, date):
                        all_dates.append(d)
                    else:
                        try:
                            all_dates.append(date.fromisoformat(str(d)[:10]))
                        except (ValueError, TypeError):
                            pass
            for t in actuals:
                d = t.get("trade_date")
                if d:
                    if isinstance(d, date):
                        all_dates.append(d)
                    else:
                        try:
                            all_dates.append(date.fromisoformat(str(d)[:10]))
                        except (ValueError, TypeError):
                            pass

            start = min(all_dates) if all_dates else date.today()
            end = max(all_dates) if all_dates else date.today()

            result = match_strategy_to_actual(outcomes, actuals, start, end)

    except Exception as exc:
        error = f"对比分析失败：{exc}"

    return render_template("compare.html", result=result, error=error, data_mode=data_mode)


# ---- Backtest History Routes ----


@app.route("/backtest")
def backtest_list():
    config = get_config()
    data_mode = get_data_mode()
    year_str = request.args.get("year", "").strip()
    month_str = request.args.get("month", "").strip()
    year = int(year_str) if year_str else None
    month = int(month_str) if month_str else None

    runs = []
    available_years = []
    try:
        with connection_context(config.mysql) as conn:
            available_years = get_backtest_years(conn)
            runs = list_backtest_runs(conn, limit=50, year=year, month=month)
    except Exception as exc:
        return render_template(
            "backtest_list.html", runs=[], data_mode=data_mode,
            error=f"查询失败：{exc}",
            current_year=year, current_month=month,
            available_years=[],
        )

    return render_template(
        "backtest_list.html",
        runs=runs,
        data_mode=data_mode,
        current_year=year,
        current_month=month,
        available_years=available_years,
    )


@app.route("/backtest/<int:run_id>")
def backtest_detail(run_id: int):
    config = get_config()
    data_mode = get_data_mode()
    try:
        with connection_context(config.mysql) as conn:
            run = get_backtest_run(conn, run_id)
            if not run:
                return render_template("backtest_detail.html", run=None, trades=[], data_mode=data_mode, error="回测记录不存在")
            trades = get_backtest_trades(conn, run_id)
            signals = get_backtest_signals(conn, run_id)
    except Exception as exc:
        return render_template("backtest_detail.html", run=None, trades=[], signals=[], data_mode=data_mode, error=f"查询失败：{exc}")

    return render_template("backtest_detail.html", run=run, trades=trades, signals=signals, data_mode=data_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chaogu-Alert Web Dashboard")
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    app.config["CONFIG_PATH"] = args.config
    config = load_config(args.config)
    if config.mysql.enabled:
        try:
            ensure_tables(config.mysql)
            print(f"MySQL tables ready @ {config.mysql.host}:{config.mysql.port}/{config.mysql.database}")
        except Exception as exc:
            print(f"WARNING: MySQL connection failed: {exc}")
            print("Web dashboard will start but database features will be unavailable.")

    print(f"Starting Chaogu-Alert Dashboard on http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
