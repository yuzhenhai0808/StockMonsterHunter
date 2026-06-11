#!/usr/bin/env python3
"""Sync company profile + quarterly financials from yfinance into PostgreSQL.

Populates two tables so the value_candidates MCP tool has the fundamental
coverage it needs:

- ``market_data.us_equity_company_profile``   — snapshot (refreshed daily)
- ``market_data.us_equity_quarterly_financials`` — quarterly statements

Each symbol costs several yfinance HTTP calls. ThreadPoolExecutor is used to
run ``--concurrency`` workers in parallel, while an optional global
``--requests-per-minute`` budget keeps the aggregate request rate bounded.
The full universe is also resumable and can skip symbols refreshed recently,
which is a better fit for fundamentals that do not need daily full refreshes.

Typical runs:

    # sample
    python cli/sync_fundamentals_to_postgres.py --symbols AAPL,NVTS,TFX,SLNH

    # full universe (nightly)
    python cli/sync_fundamentals_to_postgres.py \
        --use-db-universe --concurrency 4 --skip-recent-hours 168
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")

import yfinance as yf

from cli.fetch_fmp_openbb_to_postgres import (
    INSERT_RUN_SQL,
    ensure_schema,
    open_db,
)


_TAG = "[FundamentalsSync]"

PROVIDER = "yfinance"
RUN_PROVIDER = "yfinance_fundamentals"

DEFAULT_SLEEP_SECONDS = 1.0
DEFAULT_CONCURRENCY = 2
DEFAULT_REQUEST_BUDGET = 0
DEFAULT_CALLS_PER_SYMBOL = 4
DEFAULT_REQUESTS_PER_MINUTE = 0.0
DEFAULT_SKIP_RECENT_HOURS = 0.0


PROFILE_UPSERT_SQL = """
INSERT INTO market_data.us_equity_company_profile (
    symbol, as_of_date, sector, industry,
    market_cap, shares_outstanding, float_shares,
    short_percent_of_float, short_ratio,
    trailing_pe, forward_pe, price_to_book, peg_ratio, price_to_sales_ttm,
    enterprise_value, book_value, trailing_eps, forward_eps,
    profit_margins, operating_margins, gross_margins, return_on_equity,
    debt_to_equity, revenue_growth, earnings_growth,
    total_revenue_ttm, total_debt, total_cash,
    operating_cashflow_ttm, free_cashflow_ttm,
    dividend_yield, beta, fifty_two_week_high, fifty_two_week_low,
    provider
)
VALUES (
    %(symbol)s, %(as_of_date)s, %(sector)s, %(industry)s,
    %(market_cap)s, %(shares_outstanding)s, %(float_shares)s,
    %(short_percent_of_float)s, %(short_ratio)s,
    %(trailing_pe)s, %(forward_pe)s, %(price_to_book)s, %(peg_ratio)s, %(price_to_sales_ttm)s,
    %(enterprise_value)s, %(book_value)s, %(trailing_eps)s, %(forward_eps)s,
    %(profit_margins)s, %(operating_margins)s, %(gross_margins)s, %(return_on_equity)s,
    %(debt_to_equity)s, %(revenue_growth)s, %(earnings_growth)s,
    %(total_revenue_ttm)s, %(total_debt)s, %(total_cash)s,
    %(operating_cashflow_ttm)s, %(free_cashflow_ttm)s,
    %(dividend_yield)s, %(beta)s, %(fifty_two_week_high)s, %(fifty_two_week_low)s,
    %(provider)s
)
ON CONFLICT (symbol, as_of_date, provider) DO UPDATE
SET
    sector = EXCLUDED.sector,
    industry = EXCLUDED.industry,
    market_cap = EXCLUDED.market_cap,
    shares_outstanding = EXCLUDED.shares_outstanding,
    float_shares = EXCLUDED.float_shares,
    short_percent_of_float = EXCLUDED.short_percent_of_float,
    short_ratio = EXCLUDED.short_ratio,
    trailing_pe = EXCLUDED.trailing_pe,
    forward_pe = EXCLUDED.forward_pe,
    price_to_book = EXCLUDED.price_to_book,
    peg_ratio = EXCLUDED.peg_ratio,
    price_to_sales_ttm = EXCLUDED.price_to_sales_ttm,
    enterprise_value = EXCLUDED.enterprise_value,
    book_value = EXCLUDED.book_value,
    trailing_eps = EXCLUDED.trailing_eps,
    forward_eps = EXCLUDED.forward_eps,
    profit_margins = EXCLUDED.profit_margins,
    operating_margins = EXCLUDED.operating_margins,
    gross_margins = EXCLUDED.gross_margins,
    return_on_equity = EXCLUDED.return_on_equity,
    debt_to_equity = EXCLUDED.debt_to_equity,
    revenue_growth = EXCLUDED.revenue_growth,
    earnings_growth = EXCLUDED.earnings_growth,
    total_revenue_ttm = EXCLUDED.total_revenue_ttm,
    total_debt = EXCLUDED.total_debt,
    total_cash = EXCLUDED.total_cash,
    operating_cashflow_ttm = EXCLUDED.operating_cashflow_ttm,
    free_cashflow_ttm = EXCLUDED.free_cashflow_ttm,
    dividend_yield = EXCLUDED.dividend_yield,
    beta = EXCLUDED.beta,
    fifty_two_week_high = EXCLUDED.fifty_two_week_high,
    fifty_two_week_low = EXCLUDED.fifty_two_week_low,
    fetched_at = NOW();
"""


QFIN_UPSERT_SQL = """
INSERT INTO market_data.us_equity_quarterly_financials (
    symbol, report_date,
    total_revenue, gross_profit, operating_income, ebitda, ebit, net_income,
    basic_eps, diluted_eps,
    total_assets, total_debt, total_cash, stockholders_equity,
    operating_cash_flow, free_cash_flow,
    provider
)
VALUES (
    %(symbol)s, %(report_date)s,
    %(total_revenue)s, %(gross_profit)s, %(operating_income)s, %(ebitda)s, %(ebit)s, %(net_income)s,
    %(basic_eps)s, %(diluted_eps)s,
    %(total_assets)s, %(total_debt)s, %(total_cash)s, %(stockholders_equity)s,
    %(operating_cash_flow)s, %(free_cash_flow)s,
    %(provider)s
)
ON CONFLICT (symbol, report_date, provider) DO UPDATE
SET
    total_revenue = EXCLUDED.total_revenue,
    gross_profit = EXCLUDED.gross_profit,
    operating_income = EXCLUDED.operating_income,
    ebitda = EXCLUDED.ebitda,
    ebit = EXCLUDED.ebit,
    net_income = EXCLUDED.net_income,
    basic_eps = EXCLUDED.basic_eps,
    diluted_eps = EXCLUDED.diluted_eps,
    total_assets = EXCLUDED.total_assets,
    total_debt = EXCLUDED.total_debt,
    total_cash = EXCLUDED.total_cash,
    stockholders_equity = EXCLUDED.stockholders_equity,
    operating_cash_flow = EXCLUDED.operating_cash_flow,
    free_cash_flow = EXCLUDED.free_cash_flow,
    fetched_at = NOW();
"""


# Candidate row-name mappings used to extract the values we care about from
# yfinance's irregular DataFrame indexes. The first match wins.
PROFILE_FIELDS = [
    ("sector", "sector"),
    ("industry", "industry"),
    ("market_cap", "marketCap"),
    ("shares_outstanding", "sharesOutstanding"),
    ("float_shares", "floatShares"),
    ("short_percent_of_float", "shortPercentOfFloat"),
    ("short_ratio", "shortRatio"),
    ("trailing_pe", "trailingPE"),
    ("forward_pe", "forwardPE"),
    ("price_to_book", "priceToBook"),
    ("peg_ratio", "pegRatio"),
    ("price_to_sales_ttm", "priceToSalesTrailing12Months"),
    ("enterprise_value", "enterpriseValue"),
    ("book_value", "bookValue"),
    ("trailing_eps", "trailingEps"),
    ("forward_eps", "forwardEps"),
    ("profit_margins", "profitMargins"),
    ("operating_margins", "operatingMargins"),
    ("gross_margins", "grossMargins"),
    ("return_on_equity", "returnOnEquity"),
    ("debt_to_equity", "debtToEquity"),
    ("revenue_growth", "revenueGrowth"),
    ("earnings_growth", "earningsGrowth"),
    ("total_revenue_ttm", "totalRevenue"),
    ("total_debt", "totalDebt"),
    ("total_cash", "totalCash"),
    ("operating_cashflow_ttm", "operatingCashflow"),
    ("free_cashflow_ttm", "freeCashflow"),
    ("dividend_yield", "dividendYield"),
    ("beta", "beta"),
    ("fifty_two_week_high", "fiftyTwoWeekHigh"),
    ("fifty_two_week_low", "fiftyTwoWeekLow"),
]


FINANCIALS_ROW_MAP = {
    "total_revenue": ["Total Revenue", "Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": [
        "Operating Income",
        "Total Operating Income As Reported",
        "Operating Income Loss",
    ],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT"],
    "net_income": [
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income From Continuing Operation Net Minority Interest",
        "Net Income Continuous Operations",
    ],
    "basic_eps": ["Basic EPS"],
    "diluted_eps": ["Diluted EPS"],
}


BALANCE_ROW_MAP = {
    "total_assets": ["Total Assets"],
    "total_debt": ["Total Debt", "Long Term Debt And Capital Lease Obligation"],
    "total_cash": [
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash Financial",
    ],
    "stockholders_equity": [
        "Stockholders Equity",
        "Common Stock Equity",
        "Total Equity Gross Minority Interest",
    ],
}


CASHFLOW_ROW_MAP = {
    "operating_cash_flow": [
        "Operating Cash Flow",
        "Cash Flow From Continuing Operating Activities",
    ],
    "free_cash_flow": ["Free Cash Flow"],
}


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _find_row_value(df: Any, candidates: Sequence[str], column: Any) -> float | None:
    """Return the first matching row's value for ``column``.

    yfinance uses inconsistent row labels; ``candidates`` is the priority list.
    Different statements may not share the same set of column dates, so we
    guard against a column that is missing from this particular frame.
    """
    if df is None or df.empty:
        return None
    if column not in df.columns:
        return None
    index = df.index
    for candidate in candidates:
        if candidate in index:
            return _finite_float(df.at[candidate, column])
    return None


def _extract_report_date(ts: Any) -> date | None:
    if ts is None:
        return None
    try:
        return ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
    except Exception:
        return None


def _build_profile_record(symbol: str, info: dict[str, Any], today: date) -> dict[str, Any] | None:
    if not info:
        return None
    record: dict[str, Any] = {
        "symbol": symbol.upper(),
        "as_of_date": today,
        "provider": PROVIDER,
    }
    for db_field, info_key in PROFILE_FIELDS:
        raw = info.get(info_key)
        if db_field in ("sector", "industry"):
            record[db_field] = _safe_str(raw)
        elif db_field in ("shares_outstanding", "float_shares"):
            value = _finite_float(raw)
            record[db_field] = int(value) if value is not None else None
        else:
            record[db_field] = _finite_float(raw)
    # Require at least some signal to be worth storing.
    if (
        record["market_cap"] is None
        and record["trailing_pe"] is None
        and record["total_revenue_ttm"] is None
        and record["sector"] is None
    ):
        return None
    return record


def _build_quarterly_records(
    symbol: str,
    financials: Any,
    balance: Any,
    cashflow: Any,
) -> list[dict[str, Any]]:
    columns: set[Any] = set()
    for df in (financials, balance, cashflow):
        if df is not None and not df.empty:
            columns.update(df.columns.tolist())
    if not columns:
        return []

    records: list[dict[str, Any]] = []
    for column in sorted(columns, reverse=True):
        report_date = _extract_report_date(column)
        if report_date is None:
            continue
        record: dict[str, Any] = {
            "symbol": symbol.upper(),
            "report_date": report_date,
            "provider": PROVIDER,
        }
        for field, candidates in FINANCIALS_ROW_MAP.items():
            record[field] = _find_row_value(financials, candidates, column)
        for field, candidates in BALANCE_ROW_MAP.items():
            record[field] = _find_row_value(balance, candidates, column)
        for field, candidates in CASHFLOW_ROW_MAP.items():
            record[field] = _find_row_value(cashflow, candidates, column)
        if all(
            record[field] is None
            for field in list(FINANCIALS_ROW_MAP)
            + list(BALANCE_ROW_MAP)
            + list(CASHFLOW_ROW_MAP)
        ):
            continue
        records.append(record)
    return records


@dataclass
class SymbolResult:
    symbol: str
    status: str
    profile_rows: int = 0
    quarterly_rows: int = 0
    error: str | None = None


class RequestPacer:
    """Thread-safe symbol-level pacing derived from an HTTP request budget.

    yfinance does not expose one neat request per symbol: ``Ticker.info`` and
    the financial statement properties fan out internally. We pace before each
    symbol by estimating how many upstream HTTP calls that symbol costs. This
    keeps concurrent workers useful without accidentally multiplying the global
    Yahoo request rate by ``--concurrency``.
    """

    def __init__(self, requests_per_minute: float, calls_per_symbol: int) -> None:
        self._lock = threading.Lock()
        self._next_at = time.monotonic()
        if requests_per_minute <= 0:
            self._interval = 0.0
        else:
            calls = max(1, calls_per_symbol)
            self._interval = 60.0 * calls / requests_per_minute

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
        if sleep_for > 0:
            time.sleep(sleep_for)


def fetch_symbol(symbol: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return ``(profile_record, quarterly_records)`` for one symbol.

    Errors bubble up; caller is responsible for logging and continuing.
    """
    ticker = yf.Ticker(symbol)
    info = {}
    try:
        info = ticker.info or {}
    except Exception as exc:  # noqa: BLE001 - yfinance is flaky
        raise RuntimeError(f"info fetch failed: {exc}") from exc

    today = date.today()
    profile = _build_profile_record(symbol, info, today)

    try:
        financials = ticker.quarterly_financials
    except Exception as exc:  # noqa: BLE001
        financials = None
    try:
        balance = ticker.quarterly_balance_sheet
    except Exception as exc:  # noqa: BLE001
        balance = None
    try:
        cashflow = ticker.quarterly_cashflow
    except Exception as exc:  # noqa: BLE001
        cashflow = None

    quarterly = _build_quarterly_records(symbol, financials, balance, cashflow)
    return profile, quarterly


_TRANSIENT_ERROR_MARKERS = (
    "tls",
    "ssl",
    "openssl",
    "curl",
    "connect error",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "max retries exceeded",
    "temporary failure",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "too many requests",
    "rate limited",
    "invalid crumb",
    "unauthorized",
)

_RATE_LIMIT_ERROR_MARKERS = (
    "too many requests",
    "rate limited",
    "invalid crumb",
    "unauthorized",
)


def _is_transient_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)


def _is_rate_limited_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_ERROR_MARKERS)


def fetch_symbol_with_retry(
    symbol: str,
    *,
    max_attempts: int = 3,
    initial_backoff: float = 1.0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Wrap ``fetch_symbol`` with exponential backoff for transient errors.

    ``curl_cffi`` (used by yfinance 0.2.40+) is prone to intermittent TLS
    handshake failures on macOS — a single retry almost always succeeds.
    Non-transient errors are raised immediately without retry.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_symbol(symbol)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == max_attempts or not _is_transient_error(exc):
                raise
            if _is_rate_limited_error(exc):
                backoff = max(15.0, initial_backoff * (5 ** (attempt - 1)))
                reason = "rate limit / crumb failure"
            else:
                backoff = initial_backoff * (2 ** (attempt - 1))
                reason = "transient transport failure"
            print(
                f"{_TAG} {symbol}: {reason} on attempt {attempt}/{max_attempts} "
                f"({type(exc).__name__}); retrying in {backoff:.1f}s",
                flush=True,
            )
            time.sleep(backoff)
    # Unreachable: loop either returns or raises.
    assert last_exc is not None
    raise last_exc


_db_lock = threading.Lock()


def _persist_symbol(
    symbol: str,
    profile: dict[str, Any] | None,
    quarterly: list[dict[str, Any]],
) -> SymbolResult:
    with _db_lock, open_db() as conn, conn.cursor() as cur:
        profile_rows = 0
        quarterly_rows = 0
        if profile is not None:
            cur.execute(PROFILE_UPSERT_SQL, profile)
            profile_rows = 1
        for record in quarterly:
            cur.execute(QFIN_UPSERT_SQL, record)
            quarterly_rows += 1
        status = "success" if (profile_rows or quarterly_rows) else "skipped_no_data"
        cur.execute(
            INSERT_RUN_SQL,
            (
                RUN_PROVIDER,
                symbol.upper(),
                None,
                None,
                profile_rows + quarterly_rows,
                status,
                None,
            ),
        )
        conn.commit()
    return SymbolResult(
        symbol=symbol.upper(),
        status=status,
        profile_rows=profile_rows,
        quarterly_rows=quarterly_rows,
    )


def _record_failure(symbol: str, error: str) -> None:
    with _db_lock, open_db() as conn, conn.cursor() as cur:
        cur.execute(
            INSERT_RUN_SQL,
            (RUN_PROVIDER, symbol.upper(), None, None, 0, "failed", error[:1000]),
        )
        conn.commit()


def sync_one(symbol: str, sleep_seconds: float, pacer: RequestPacer | None = None) -> SymbolResult:
    symbol = symbol.strip().upper()
    if not symbol:
        return SymbolResult(symbol=symbol, status="skipped_empty")
    try:
        if pacer is not None:
            pacer.wait()
        profile, quarterly = fetch_symbol_with_retry(symbol)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        print(f"{_TAG} {symbol}: FAILED — {error}", flush=True)
        try:
            _record_failure(symbol, error)
        except Exception:
            pass
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        return SymbolResult(symbol=symbol, status="failed", error=error)
    try:
        result = _persist_symbol(symbol, profile, quarterly)
    except Exception as exc:  # noqa: BLE001
        error = f"persist failed: {type(exc).__name__}: {exc}"
        print(f"{_TAG} {symbol}: FAILED — {error}", flush=True)
        try:
            _record_failure(symbol, error)
        except Exception:
            pass
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        return SymbolResult(symbol=symbol, status="failed", error=error)

    if result.status == "success":
        print(
            f"{_TAG} {symbol}: profile={result.profile_rows} quarterly={result.quarterly_rows}",
            flush=True,
        )
    else:
        print(f"{_TAG} {symbol}: no usable data", flush=True)
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    return result


def load_universe_from_db() -> list[str]:
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol FROM market_data.us_equity_symbols
            WHERE is_active = TRUE
            ORDER BY symbol;
            """
        )
        return [row["symbol"] for row in cur.fetchall()]


def load_already_synced_today() -> set[str]:
    """Return symbols that already have a profile row for today.

    Used by ``--skip-if-synced-today`` (default on) so a crashed / killed run
    can simply be restarted with the same command — already-processed symbols
    are skipped automatically.
    """
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT symbol
            FROM market_data.us_equity_company_profile
            WHERE as_of_date = CURRENT_DATE
              AND provider = %s
            """,
            (PROVIDER,),
        )
        return {row["symbol"] for row in cur.fetchall()}


def load_recently_synced(hours: float) -> set[str]:
    """Return symbols with a profile fetched within the recent-hour window."""
    if hours <= 0:
        return set()
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT symbol
            FROM market_data.us_equity_company_profile
            WHERE provider = %s
              AND fetched_at >= NOW() - (%s * INTERVAL '1 hour')
            """,
            (PROVIDER, hours),
        )
        return {row["symbol"] for row in cur.fetchall()}


def _expand_prefix_range(spec: str) -> set[str]:
    """Expand ``A-F`` / ``A`` / digit forms into a set of starting characters.

    Accepts letters A-Z (case-insensitive) and digits 0-9. A single token is
    treated as a 1-character prefix. Multiple tokens are OR'd.
    """
    spec = spec.strip().upper()
    if not spec:
        return set()
    if "-" in spec:
        start, end = spec.split("-", 1)
        start = start.strip()
        end = end.strip()
        if len(start) != 1 or len(end) != 1:
            raise ValueError(f"Invalid prefix range {spec!r}; expected single chars like A-F")
        if start > end:
            raise ValueError(f"Invalid prefix range {spec!r}; start > end")
        return {chr(c) for c in range(ord(start), ord(end) + 1)}
    if len(spec) != 1:
        raise ValueError(f"Invalid prefix {spec!r}; expected a single char or A-F form")
    return {spec}


def parse_symbol_prefixes(raw: str) -> set[str] | None:
    """Parse ``--symbol-prefixes A-F,G-M`` into a set of allowed first chars."""
    raw = (raw or "").strip()
    if not raw:
        return None
    result: set[str] = set()
    for chunk in raw.split(","):
        result.update(_expand_prefix_range(chunk))
    return result or None


def _filter_symbols(
    symbols: list[str],
    *,
    prefixes: set[str] | None,
    skip_already_synced: bool,
    skip_recent_hours: float,
) -> tuple[list[str], int, int, int]:
    """Apply prefix and freshness filters.

    Returns ``(filtered, dropped_prefix, dropped_synced_today, dropped_recent)``.
    """
    dropped_prefix = 0
    dropped_synced = 0
    dropped_recent = 0
    if prefixes:
        before = len(symbols)
        symbols = [s for s in symbols if s and s[0].upper() in prefixes]
        dropped_prefix = before - len(symbols)
    if skip_already_synced:
        done = load_already_synced_today()
        before = len(symbols)
        symbols = [s for s in symbols if s.upper() not in done]
        dropped_synced = before - len(symbols)
    if skip_recent_hours > 0:
        recent = load_recently_synced(skip_recent_hours)
        before = len(symbols)
        symbols = [s for s in symbols if s.upper() not in recent]
        dropped_recent = before - len(symbols)
    return symbols, dropped_prefix, dropped_synced, dropped_recent


def sync_symbols(
    symbols: Iterable[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    request_budget: int = DEFAULT_REQUEST_BUDGET,
    requests_per_minute: float = DEFAULT_REQUESTS_PER_MINUTE,
    calls_per_symbol: int = DEFAULT_CALLS_PER_SYMBOL,
    dry_run: bool = False,
) -> dict[str, int]:
    stats = {"symbols": 0, "success": 0, "failed": 0, "skipped": 0}
    symbol_list = [s.strip().upper() for s in symbols if s.strip()]
    if request_budget:
        symbol_list = symbol_list[:request_budget]
    stats["symbols"] = len(symbol_list)
    if not symbol_list:
        return stats
    if dry_run:
        for symbol in symbol_list:
            print(f"{_TAG} [dry-run] would fetch {symbol}", flush=True)
        stats["skipped"] = len(symbol_list)
        return stats

    pacer = RequestPacer(requests_per_minute, calls_per_symbol)
    if pacer.enabled:
        estimated_seconds = len(symbol_list) * 60.0 * max(1, calls_per_symbol) / requests_per_minute
        print(
            f"{_TAG} pacing enabled: {requests_per_minute:g} requests/min, "
            f"~{calls_per_symbol} calls/symbol, estimated floor {estimated_seconds/60:.1f} min",
            flush=True,
        )

    max_workers = max(1, min(concurrency, 12))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(sync_one, s, sleep_seconds, pacer): s for s in symbol_list}
        for future in as_completed(futures):
            result = future.result()
            if result.status == "success":
                stats["success"] += 1
            elif result.status == "failed":
                stats["failed"] += 1
            else:
                stats["skipped"] += 1
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync company profile + quarterly financials from yfinance."
    )
    parser.add_argument("--symbols", help="Comma-separated tickers.")
    parser.add_argument("--use-db-universe", action="store_true")
    parser.add_argument(
        "--symbol-prefixes",
        default=None,
        help="Only sync symbols whose first char is in the given set. "
        "Example: 'A-F,G-M' or 'A,B' or 'A-D'. Useful for parallel shards.",
    )
    parser.add_argument(
        "--skip-if-synced-today",
        dest="skip_if_synced_today",
        action="store_true",
        default=True,
        help="Skip symbols that already have a profile row for today (default on). "
        "Makes restarts effectively resumable.",
    )
    parser.add_argument(
        "--no-skip-if-synced-today",
        dest="skip_if_synced_today",
        action="store_false",
        help="Do a full re-sync even if today's profile rows already exist.",
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--request-budget", type=int, default=DEFAULT_REQUEST_BUDGET)
    parser.add_argument(
        "--requests-per-minute",
        type=float,
        default=DEFAULT_REQUESTS_PER_MINUTE,
        help="Optional global Yahoo/yfinance request budget. 0 disables pacing.",
    )
    parser.add_argument(
        "--calls-per-symbol",
        type=int,
        default=DEFAULT_CALLS_PER_SYMBOL,
        help="Estimated yfinance HTTP calls per symbol for pacing calculations.",
    )
    parser.add_argument(
        "--skip-recent-hours",
        type=float,
        default=DEFAULT_SKIP_RECENT_HOURS,
        help="Skip symbols whose profile was fetched in the last N hours. "
        "Use 168 for a weekly fundamentals refresh cadence.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with open_db() as conn:
        ensure_schema(conn)
        conn.commit()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.use_db_universe:
        symbols = load_universe_from_db()
        print(f"{_TAG} loaded {len(symbols)} symbols from DB universe.", flush=True)
    else:
        print(f"{_TAG} provide --symbols or --use-db-universe.", file=sys.stderr)
        return 2

    prefixes = parse_symbol_prefixes(args.symbol_prefixes)
    symbols, dropped_prefix, dropped_synced, dropped_recent = _filter_symbols(
        symbols,
        prefixes=prefixes,
        skip_already_synced=args.skip_if_synced_today,
        skip_recent_hours=args.skip_recent_hours,
    )
    if prefixes:
        print(
            f"{_TAG} prefix filter {sorted(prefixes)!r} dropped {dropped_prefix} symbols",
            flush=True,
        )
    if args.skip_if_synced_today and dropped_synced:
        print(
            f"{_TAG} skipped {dropped_synced} symbols already synced today",
            flush=True,
        )
    if args.skip_recent_hours > 0 and dropped_recent:
        print(
            f"{_TAG} skipped {dropped_recent} symbols fetched in the last "
            f"{args.skip_recent_hours:g} hours",
            flush=True,
        )

    if not symbols:
        print(f"{_TAG} no symbols to sync.", flush=True)
        return 0

    started = datetime.now(timezone.utc)
    stats = sync_symbols(
        symbols,
        concurrency=args.concurrency,
        sleep_seconds=args.sleep_seconds,
        request_budget=args.request_budget,
        requests_per_minute=args.requests_per_minute,
        calls_per_symbol=args.calls_per_symbol,
        dry_run=args.dry_run,
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(
        f"{_TAG} done in {elapsed:.1f}s: "
        f"symbols={stats['symbols']} success={stats['success']} "
        f"skipped={stats['skipped']} failed={stats['failed']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
