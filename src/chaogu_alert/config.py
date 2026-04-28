from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
import os
import tomllib


@dataclass(slots=True)
class AppSettings:
    reports_dir: str = "reports"
    data_source: str = "demo"
    data_sources: list[str] = field(default_factory=list)
    scan_journal_path: str = ""


@dataclass(slots=True)
class EmailSettings:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    use_ssl: bool = True
    subject_prefix: str = "[A-share Alert]"


@dataclass(slots=True)
class AkshareSettings:
    adjust: str = "qfq"
    period: str = "daily"
    history_buffer_days: int = 420
    use_spot_name_lookup: bool = True
    intraday_period: str = "1"
    cache_dir: str = "cache/akshare"
    request_log_path: str = "logs/upstream_requests.jsonl"
    min_request_interval_seconds: float = 1.2
    spot_cache_ttl_seconds: int = 3600
    history_cache_ttl_seconds: int = 43200
    intraday_cache_ttl_seconds: int = 600


@dataclass(slots=True)
class ScheduleSettings:
    timezone: str = "Asia/Shanghai"
    market_close_time: str = "14:35"
    enforce_after_close: bool = True
    skip_non_trading_day: bool = True
    logs_dir: str = "logs"
    task_name: str = "ChaoguAlertDaily"


@dataclass(slots=True)
class RiskSettings:
    capital: float = 100000.0
    cash_buffer_pct: float = 0.15
    max_positions: int = 4
    max_position_pct: float = 0.25
    max_sector_position_pct: float = 0.30
    risk_per_trade_pct: float = 0.01
    lot_size: int = 100
    min_score: float = 68.0
    etf_commission: float = 5.0
    stock_commission: float = 5.0
    stock_commission_rate: float = 0.00025
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0
    trailing_stop_enabled: bool = True
    circuit_breaker_enabled: bool = True
    circuit_breaker_weekly_pct: float = 0.06
    circuit_breaker_daily_pct: float = 0.03


@dataclass(slots=True)
class UniverseSettings:
    benchmark_symbol: str = "510300"
    min_listing_days: int = 120
    broad_etfs: list[str] = field(
        default_factory=lambda: ["510300", "510500", "515080"]
    )
    sector_etfs: list[str] = field(
        default_factory=lambda: ["512880", "516160", "512660", "512010"]
    )
    groups: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class HoldingSettings:
    symbol: str
    shares: int
    cost_basis: float = 0.0
    max_t_trade_pct: float = 0.20
    min_t_trade_pct: float = 0.10


@dataclass(slots=True)
class PortfolioSettings:
    available_cash: float = 0.0
    holdings: list[HoldingSettings] = field(default_factory=list)


@dataclass(slots=True)
class AccountSettings:
    id: int = 1
    name: str = "默认账户"
    broker: str = ""
    type: str = "real"
    initial_capital: float = 0.0
    portfolio: PortfolioSettings = field(default_factory=PortfolioSettings)


@dataclass(slots=True)
class EtfTrendSettings:
    enabled: bool = True
    ma_fast: int = 20
    ma_slow: int = 60
    min_return_20: float = 0.02
    min_volume_ratio: float = 0.95
    stop_loss_pct: float = 0.05


@dataclass(slots=True)
class EtfRotationSettings:
    enabled: bool = True
    lookback: int = 20
    top_n: int = 2
    min_return_20: float = 0.04
    benchmark_filter_period: int = 60
    stop_loss_pct: float = 0.06


@dataclass(slots=True)
class TTradeSettings:
    enabled: bool = True
    min_intraday_pullback_pct: float = 0.008
    min_intraday_rebound_pct: float = 0.008
    min_distance_from_vwap_pct: float = 0.003
    max_daily_range_pct: float = 0.06
    require_trend_ma: int = 20
    add_on_market_regime: str = "risk_on"


@dataclass(slots=True)
class MysqlSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "claude"


@dataclass(slots=True)
class StrategySettings:
    etf_trend: EtfTrendSettings = field(default_factory=EtfTrendSettings)
    etf_rotation: EtfRotationSettings = field(default_factory=EtfRotationSettings)
    t_trade: TTradeSettings = field(default_factory=TTradeSettings)


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

    # Backward compat: if no [[accounts]] configured, fall back to [portfolio]
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

    default_portfolio = _build_default_portfolio(account_list, portfolio, holdings)

    return AppConfig(
        app=AppSettings(**raw.get("app", {})),
        akshare=AkshareSettings(**raw.get("akshare", {})),
        schedule=ScheduleSettings(**raw.get("schedule", {})),
        email=EmailSettings(**raw.get("email", {})),
        risk=RiskSettings(**raw.get("risk", {})),
        universe=UniverseSettings(**raw.get("universe", {})),
        portfolio=default_portfolio,
        strategy=StrategySettings(
            etf_trend=EtfTrendSettings(**strategy.get("etf_trend", {})),
            etf_rotation=EtfRotationSettings(**strategy.get("etf_rotation", {})),
            t_trade=TTradeSettings(**strategy.get("t_trade", {})),
        ),
        mysql=MysqlSettings(**raw.get("mysql", {})),
        accounts=account_list,
    )


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return value


def get_account_settings(
    config: AppConfig, account_id: int | None = None,
) -> AccountSettings | None:
    if not config.accounts:
        return None
    if account_id is not None:
        for account in config.accounts:
            if account.id == account_id:
                return account
    for account in config.accounts:
        if account.id == 1:
            return account
    return config.accounts[0]


def get_effective_account_id(config: AppConfig, account_id: int | None = None) -> int:
    account = get_account_settings(config, account_id)
    if account is not None:
        return account.id
    return account_id or 1


def get_effective_portfolio(
    config: AppConfig, account_id: int | None = None,
) -> PortfolioSettings:
    account = get_account_settings(config, account_id)
    if account is not None:
        return account.portfolio
    return config.portfolio


def with_account(config: AppConfig, account_id: int | None = None) -> AppConfig:
    portfolio = get_effective_portfolio(config, account_id)
    if config.portfolio is portfolio:
        return config
    return replace(
        config,
        portfolio=PortfolioSettings(
            available_cash=portfolio.available_cash,
            holdings=list(portfolio.holdings),
        ),
    )


def _build_default_portfolio(
    accounts: list[AccountSettings],
    portfolio_raw: dict[str, Any],
    holdings: list[HoldingSettings],
) -> PortfolioSettings:
    default_account = next((account for account in accounts if account.id == 1), None)
    if default_account is None and accounts:
        default_account = accounts[0]
    if default_account is not None:
        return PortfolioSettings(
            available_cash=default_account.portfolio.available_cash,
            holdings=list(default_account.portfolio.holdings),
        )
    return PortfolioSettings(
        available_cash=float(portfolio_raw.get("available_cash", 0.0)),
        holdings=holdings,
    )
