from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from hashlib import md5
from pathlib import Path
from typing import Callable, Protocol
import csv
import json
import time as time_module

from .config import AkshareSettings
from .demo_data import DEMO_PROFILES, generate_demo_histories, generate_demo_intraday_histories
from .models import Bar, MinuteBar

CN_CODE = "\u4ee3\u7801"
CN_NAME = "\u540d\u79f0"
CN_DATE = "\u65e5\u671f"
CN_OPEN = "\u5f00\u76d8"
CN_HIGH = "\u6700\u9ad8"
CN_LOW = "\u6700\u4f4e"
CN_CLOSE = "\u6536\u76d8"
CN_VOLUME = "\u6210\u4ea4\u91cf"
CN_TIME = "\u65f6\u95f4"
CN_AVG_PRICE = "\u5747\u4ef7"


class MarketDataProvider(Protocol):
    def load_histories(
        self, symbols: list[str], as_of: date, lookback: int = 140
    ) -> dict[str, list[Bar]]:
        ...

    def load_intraday_histories(
        self, symbols: list[str], as_of: date, decision_time: time
    ) -> dict[str, list[MinuteBar]]:
        ...


class DemoMarketDataProvider:
    def load_histories(
        self, symbols: list[str], as_of: date, lookback: int = 140
    ) -> dict[str, list[Bar]]:
        return generate_demo_histories(symbols, as_of, sessions=lookback)

    def load_intraday_histories(
        self, symbols: list[str], as_of: date, decision_time: time
    ) -> dict[str, list[MinuteBar]]:
        return generate_demo_intraday_histories(symbols, as_of, decision_time)


class CsvMarketDataProvider:
    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)
        self._cache = self._load_csv()

    def load_histories(
        self, symbols: list[str], as_of: date, lookback: int = 140
    ) -> dict[str, list[Bar]]:
        histories: dict[str, list[Bar]] = {}
        for symbol in symbols:
            rows = [bar for bar in self._cache.get(symbol, []) if bar.date <= as_of]
            histories[symbol] = rows[-lookback:]
        return histories

    def load_intraday_histories(
        self, symbols: list[str], as_of: date, decision_time: time
    ) -> dict[str, list[MinuteBar]]:
        return {symbol: [] for symbol in symbols}

    def _load_csv(self) -> dict[str, list[Bar]]:
        grouped: dict[str, list[Bar]] = defaultdict(list)
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                grouped[row["symbol"]].append(
                    Bar(
                        symbol=row["symbol"],
                        name=row.get("name") or row["symbol"],
                        asset_type=row.get("asset_type") or "broad_etf",
                        sector=row.get("sector") or "unknown",
                        date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        is_st=_to_bool(row.get("is_st", "false")),
                        listing_days=int(row.get("listing_days") or 365),
                    )
                )
        for rows in grouped.values():
            rows.sort(key=lambda item: item.date)
        return dict(grouped)


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


class AkshareEtfDataProvider:
    provider_name = "akshare"

    def __init__(
        self,
        settings: AkshareSettings,
        symbol_metadata: dict[str, dict[str, str]] | None = None,
        client=None,
    ):
        self.settings = settings
        self.symbol_metadata = symbol_metadata or {}
        self.client = client or _import_akshare()
        self._name_cache: dict[str, str] = {}
        self._spot_lookup_failed = False
        self._cache_dir = Path(self.settings.cache_dir) / self.provider_name
        self._request_log_path = Path(self.settings.request_log_path)
        self._request_state_path = self._cache_dir / "_request_state.json"

    def load_histories(
        self, symbols: list[str], as_of: date, lookback: int = 140
    ) -> dict[str, list[Bar]]:
        start_date = (
            as_of - timedelta(days=max(lookback * 3, self.settings.history_buffer_days))
        ).strftime("%Y%m%d")
        end_date = as_of.strftime("%Y%m%d")

        if self.settings.use_spot_name_lookup and not self._spot_lookup_failed:
            self._ensure_name_cache(symbols)

        histories: dict[str, list[Bar]] = {}
        for symbol in symbols:
            rows = self._request_records(
                endpoint="daily_history",
                params={
                    "symbol": symbol,
                    "period": self.settings.period,
                    "start_date": start_date,
                    "end_date": end_date,
                    "adjust": self.settings.adjust,
                },
                ttl_seconds=self.settings.history_cache_ttl_seconds,
                fetcher=lambda symbol=symbol: self.client.fund_etf_hist_em(
                    symbol=symbol,
                    period=self.settings.period,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=self.settings.adjust,
                ),
            )
            histories[symbol] = self._frame_to_bars(symbol, rows, as_of)[-lookback:]
        return histories

    def load_intraday_histories(
        self, symbols: list[str], as_of: date, decision_time: time
    ) -> dict[str, list[MinuteBar]]:
        start_stamp = f"{as_of.isoformat()} 09:30:00"
        end_stamp = f"{as_of.isoformat()} {decision_time.strftime('%H:%M')}:59"
        intraday_histories: dict[str, list[MinuteBar]] = {}
        for symbol in symbols:
            try:
                rows = self._request_records(
                    endpoint="intraday_history",
                    params={
                        "symbol": symbol,
                        "period": self.settings.intraday_period,
                        "start_date": start_stamp,
                        "end_date": end_stamp,
                        "adjust": "",
                    },
                    ttl_seconds=self.settings.intraday_cache_ttl_seconds,
                    fetcher=lambda symbol=symbol: self.client.fund_etf_hist_min_em(
                        symbol=symbol,
                        period=self.settings.intraday_period,
                        adjust="",
                        start_date=start_stamp,
                        end_date=end_stamp,
                    ),
                )
            except Exception:
                intraday_histories[symbol] = []
                continue
            intraday_histories[symbol] = self._frame_to_minute_bars(
                symbol,
                rows,
                as_of,
                decision_time=decision_time,
            )
        return intraday_histories

    def _ensure_name_cache(self, symbols: list[str]) -> None:
        missing = [symbol for symbol in symbols if symbol not in self._name_cache]
        if not missing:
            return

        try:
            rows = self._request_records(
                endpoint="spot_lookup",
                params={"scope": "all"},
                ttl_seconds=self.settings.spot_cache_ttl_seconds,
                fetcher=self.client.fund_etf_spot_em,
            )
        except Exception:
            self._spot_lookup_failed = True
            return

        for row in rows:
            code = str(_first_present(row, CN_CODE, "symbol", default="")).zfill(6)
            name = str(_first_present(row, CN_NAME, "name", default="")).strip()
            if code and name:
                self._name_cache[code] = name

    def _request_records(
        self,
        endpoint: str,
        params: dict,
        ttl_seconds: int,
        fetcher: Callable[[], object],
    ) -> list[dict]:
        normalized_params = _normalize_for_json(params)
        cache_path = self._cache_path(endpoint, normalized_params)
        cached_rows = self._load_cached_rows(cache_path, ttl_seconds)
        if cached_rows is not None:
            self._log_request(
                endpoint=endpoint,
                params=normalized_params,
                cache_hit=True,
                row_count=len(cached_rows),
            )
            return cached_rows

        started = time_module.time()
        self._wait_for_request_slot()
        try:
            rows = _normalize_records(_frame_records(fetcher()))
        except Exception as exc:
            self._log_request(
                endpoint=endpoint,
                params=normalized_params,
                cache_hit=False,
                error=str(exc),
                elapsed_ms=round((time_module.time() - started) * 1000.0, 1),
            )
            raise

        self._write_cache(cache_path, endpoint, normalized_params, rows)
        self._log_request(
            endpoint=endpoint,
            params=normalized_params,
            cache_hit=False,
            row_count=len(rows),
            elapsed_ms=round((time_module.time() - started) * 1000.0, 1),
        )
        return rows

    def _cache_path(self, endpoint: str, params: dict) -> Path:
        payload = json.dumps(
            {"endpoint": endpoint, "params": params},
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = md5(payload.encode("utf-8")).hexdigest()
        symbol = str(params.get("symbol") or params.get("scope") or "all")
        return self._cache_dir / endpoint / f"{symbol}_{digest}.json"

    def _load_cached_rows(self, cache_path: Path, ttl_seconds: int) -> list[dict] | None:
        if ttl_seconds <= 0 or not cache_path.exists():
            return None
        age_seconds = time_module.time() - cache_path.stat().st_mtime
        if age_seconds > ttl_seconds:
            return None
        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                cache_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return list(payload.get("rows", []))

    def _write_cache(self, cache_path: Path, endpoint: str, params: dict, rows: list[dict]) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "endpoint": endpoint,
                    "params": params,
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "rows": rows,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    def _wait_for_request_slot(self) -> None:
        interval_seconds = max(0.0, float(self.settings.min_request_interval_seconds))
        if interval_seconds <= 0:
            return

        self._request_state_path.parent.mkdir(parents=True, exist_ok=True)
        last_request_ts = 0.0
        if self._request_state_path.exists():
            try:
                with self._request_state_path.open("r", encoding="utf-8") as handle:
                    state = json.load(handle)
                last_request_ts = float(state.get("last_request_ts") or 0.0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                last_request_ts = 0.0

        now_ts = time_module.time()
        wait_seconds = interval_seconds - max(0.0, now_ts - last_request_ts)
        if wait_seconds > 0:
            time_module.sleep(wait_seconds)

        with self._request_state_path.open("w", encoding="utf-8") as handle:
            json.dump({"last_request_ts": time_module.time()}, handle)

    def _log_request(
        self,
        endpoint: str,
        params: dict,
        cache_hit: bool,
        row_count: int | None = None,
        elapsed_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        self._request_log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "provider": self.provider_name,
            "endpoint": endpoint,
            "source": "cache" if cache_hit else "upstream",
            "cache_hit": cache_hit,
            "params": params,
        }
        if row_count is not None:
            entry["rows"] = row_count
        if elapsed_ms is not None:
            entry["elapsed_ms"] = elapsed_ms
        if error:
            entry["error"] = error

        with self._request_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _frame_to_bars(self, symbol: str, frame, as_of: date) -> list[Bar]:
        rows = _frame_records(frame)
        if not rows:
            return []

        metadata = self.symbol_metadata.get(symbol, {})
        profile = DEMO_PROFILES.get(symbol)
        name = metadata.get("name") or self._name_cache.get(symbol) or (profile.name if profile else symbol)
        asset_type = metadata.get("asset_type") or "etf"
        sector = metadata.get("sector") or "unknown"

        parsed_rows: list[tuple[date, dict]] = []
        for row in rows:
            row_date = _parse_date(_first_present(row, CN_DATE, "date"))
            if row_date is None or row_date > as_of:
                continue
            parsed_rows.append((row_date, row))

        if not parsed_rows:
            return []

        parsed_rows.sort(key=lambda item: item[0])
        listing_days = max(1, (as_of - parsed_rows[0][0]).days + 1)
        bars: list[Bar] = []
        for row_date, row in parsed_rows:
            bars.append(
                Bar(
                    symbol=symbol,
                    name=name,
                    asset_type=asset_type,
                    sector=sector,
                    date=row_date,
                    open=float(_first_present(row, CN_OPEN, "open")),
                    high=float(_first_present(row, CN_HIGH, "high")),
                    low=float(_first_present(row, CN_LOW, "low")),
                    close=float(_first_present(row, CN_CLOSE, "close")),
                    volume=float(_first_present(row, CN_VOLUME, "volume", default=0.0)),
                    is_st=False,
                    listing_days=listing_days,
                )
            )
        return bars

    def _frame_to_minute_bars(
        self,
        symbol: str,
        frame,
        as_of: date,
        decision_time: time | None = None,
    ) -> list[MinuteBar]:
        rows = _frame_records(frame)
        minute_bars: list[MinuteBar] = []
        for row in rows:
            timestamp = _parse_datetime(_first_present(row, CN_TIME, "time", "day"))
            if timestamp is None or timestamp.date() != as_of:
                continue
            if decision_time is not None and timestamp.time() > decision_time:
                continue
            minute_bars.append(
                MinuteBar(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=float(_first_present(row, CN_OPEN, "open")),
                    high=float(_first_present(row, CN_HIGH, "high")),
                    low=float(_first_present(row, CN_LOW, "low")),
                    close=float(_first_present(row, CN_CLOSE, "close")),
                    volume=float(_first_present(row, CN_VOLUME, "volume", default=0.0)),
                    avg_price=_to_float(_first_present(row, CN_AVG_PRICE, "avg_price")),
                )
            )
        minute_bars.sort(key=lambda item: item.timestamp)
        return minute_bars


class SinaEtfDataProvider(AkshareEtfDataProvider):
    provider_name = "sina"

    def load_histories(
        self, symbols: list[str], as_of: date, lookback: int = 140
    ) -> dict[str, list[Bar]]:
        histories: dict[str, list[Bar]] = {}
        for symbol in symbols:
            sina_symbol = _to_sina_symbol(symbol)
            rows = self._request_records(
                endpoint="daily_history",
                params={"symbol": sina_symbol},
                ttl_seconds=self.settings.history_cache_ttl_seconds,
                fetcher=lambda sina_symbol=sina_symbol: self.client.fund_etf_hist_sina(
                    symbol=sina_symbol
                ),
            )
            histories[symbol] = self._frame_to_bars(symbol, rows, as_of)[-lookback:]
        return histories

    def load_intraday_histories(
        self, symbols: list[str], as_of: date, decision_time: time
    ) -> dict[str, list[MinuteBar]]:
        intraday_histories: dict[str, list[MinuteBar]] = {}
        for symbol in symbols:
            sina_symbol = _to_sina_symbol(symbol)
            try:
                rows = self._request_records(
                    endpoint="intraday_history",
                    params={
                        "symbol": sina_symbol,
                        "period": self.settings.intraday_period,
                    },
                    ttl_seconds=self.settings.intraday_cache_ttl_seconds,
                    fetcher=lambda sina_symbol=sina_symbol: self.client.stock_zh_a_minute(
                        symbol=sina_symbol,
                        period=self.settings.intraday_period,
                        adjust="",
                    ),
                )
            except Exception:
                intraday_histories[symbol] = []
                continue

            intraday_histories[symbol] = self._frame_to_minute_bars(
                symbol,
                rows,
                as_of,
                decision_time=decision_time,
            )
        return intraday_histories

    def _ensure_name_cache(self, symbols: list[str]) -> None:
        return


class MultiSourceMarketDataProvider:
    def __init__(self, providers: list[MarketDataProvider]):
        self.providers = providers

    def load_histories(
        self, symbols: list[str], as_of: date, lookback: int = 140
    ) -> dict[str, list[Bar]]:
        unresolved = set(symbols)
        histories: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
        last_error: Exception | None = None

        for provider in self.providers:
            if not unresolved:
                break
            request_symbols = sorted(unresolved)
            try:
                batch = provider.load_histories(request_symbols, as_of, lookback=lookback)
            except Exception as exc:
                last_error = exc
                continue

            for symbol in request_symbols:
                rows = batch.get(symbol, [])
                if rows:
                    histories[symbol] = rows
                    unresolved.discard(symbol)

        if unresolved and len(unresolved) == len(symbols):
            if last_error is not None:
                raise last_error
            raise RuntimeError("All market data providers returned no historical data.")

        return histories

    def load_intraday_histories(
        self, symbols: list[str], as_of: date, decision_time: time
    ) -> dict[str, list[MinuteBar]]:
        unresolved = set(symbols)
        intraday_histories: dict[str, list[MinuteBar]] = {symbol: [] for symbol in symbols}

        for provider in self.providers:
            if not unresolved:
                break
            request_symbols = sorted(unresolved)
            try:
                batch = provider.load_intraday_histories(
                    request_symbols,
                    as_of,
                    decision_time,
                )
            except Exception:
                continue

            for symbol in request_symbols:
                rows = batch.get(symbol, [])
                if rows:
                    intraday_histories[symbol] = rows
                    unresolved.discard(symbol)

        return intraday_histories


def build_symbol_metadata(
    benchmark_symbol: str,
    broad_etfs: list[str],
    sector_etfs: list[str],
    extra_symbols: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    for symbol in {benchmark_symbol, *broad_etfs, *sector_etfs, *(extra_symbols or [])}:
        profile = DEMO_PROFILES.get(symbol)
        if symbol == benchmark_symbol or symbol in broad_etfs:
            asset_type = "broad_etf"
            sector = profile.sector if profile else "broad"
        elif symbol in sector_etfs:
            asset_type = "sector_etf"
            sector = profile.sector if profile else f"sector_{symbol}"
        else:
            asset_type = profile.asset_type if profile else "tracked_etf"
            sector = profile.sector if profile else "tracked"

        metadata[symbol] = {
            "name": profile.name if profile else symbol,
            "asset_type": asset_type,
            "sector": sector,
        }
    return metadata


def overlay_intraday_on_daily(
    histories: dict[str, list[Bar]],
    intraday_histories: dict[str, list[MinuteBar]],
) -> dict[str, list[Bar]]:
    merged: dict[str, list[Bar]] = {}
    for symbol, daily_bars in histories.items():
        intraday_bars = intraday_histories.get(symbol, [])
        if not intraday_bars:
            merged[symbol] = daily_bars
            continue

        template = daily_bars[-1] if daily_bars else None
        intraday_bar = _aggregate_intraday(symbol, intraday_bars, template)
        if intraday_bar is None:
            merged[symbol] = daily_bars
            continue

        updated = list(daily_bars)
        if updated and updated[-1].date == intraday_bar.date:
            updated[-1] = intraday_bar
        else:
            updated.append(intraday_bar)
        merged[symbol] = updated

    return merged


def _aggregate_intraday(
    symbol: str, minute_bars: list[MinuteBar], template: Bar | None
) -> Bar | None:
    if not minute_bars:
        return None

    first = minute_bars[0]
    last = minute_bars[-1]
    name = template.name if template else DEMO_PROFILES.get(symbol, DEMO_PROFILES["510300"]).name
    asset_type = template.asset_type if template else "etf"
    sector = template.sector if template else "unknown"
    listing_days = template.listing_days if template else 365
    is_st = template.is_st if template else False

    return Bar(
        symbol=symbol,
        name=name,
        asset_type=asset_type,
        sector=sector,
        date=last.timestamp.date(),
        open=round(first.open, 3),
        high=round(max(bar.high for bar in minute_bars), 3),
        low=round(min(bar.low for bar in minute_bars), 3),
        close=round(last.close, 3),
        volume=round(sum(bar.volume for bar in minute_bars), 2),
        is_st=is_st,
        listing_days=listing_days,
    )


def _import_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError(
            "AkShare is not installed. Run `pip install akshare`."
        ) from exc
    return ak


def _frame_records(frame) -> list[dict]:
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    if isinstance(frame, list):
        return frame
    raise TypeError(f"Unsupported frame type: {type(frame)!r}")


def _first_present(row: dict, *keys, default=None):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-")
    if " " in text:
        text = text.split(" ", 1)[0]
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip().replace("/", "-")
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _normalize_records(rows: list[dict]) -> list[dict]:
    return [
        {str(key): _normalize_for_json(value) for key, value in row.items()}
        for row in rows
    ]


def _normalize_for_json(value):
    if isinstance(value, dict):
        return {str(key): _normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(item) for item in value]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        try:
            return _normalize_for_json(value.item())
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _to_sina_symbol(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"
