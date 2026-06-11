#!/usr/bin/env python3
"""Incrementally sync US equity daily bars from OpenBB into PostgreSQL.

- Daily bars only (`interval=1d`)
- One API request per symbol
- Incremental sync based on the latest stored trading date
- Optional per-run request budget and sleep interval
- Supports multiple data providers via --provider (default: yfinance)

Example:
    python3 fetch_fmp_openbb_to_postgres.py \
      --symbols AAPL,MSFT,NVDA,GOOG,SPY,QQQ \
      --start-date 2021-01-01
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

import yfinance as yf

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    print(
        "Missing dependency: psycopg. Install it with: pip install psycopg[binary]",
        file=sys.stderr,
    )
    raise SystemExit(1)

# openbb is heavy and only needed when actually fetching from the API.
# Lazy-import it inside fetch_symbol_history() so that DB-only operations
# (freshness check, schema init) work even without openbb installed.
obb = None  # sentinel; replaced on first use


# ---------------------------------------------------------------------------
# 默认配置（集中管理，方便日后查阅和修改）
# ---------------------------------------------------------------------------
DEFAULT_PROVIDER = "yfinance"          # 可选: "fmp", "yfinance", "polygon" 等
FMP_API_KEY_ENV = "B6RayH2hIljFZz2NasOcdmeaUouQRbWK" # FMP key 环境变量名（备忘）
DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = 5432
DEFAULT_DB_NAME = "appdb"
DEFAULT_DB_USER = "admin"
DEFAULT_DB_PASSWORD = "yzh1234"
DEFAULT_REQUEST_BUDGET = 40
DEFAULT_SLEEP_SECONDS = 0.35
DEFAULT_BACKFILL_YEARS = 3


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

CREATE_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS market_data.us_equity_daily_prices (
    symbol TEXT NOT NULL,
    price_date DATE NOT NULL,
    open NUMERIC(28, 10),
    high NUMERIC(28, 10),
    low NUMERIC(28, 10),
    close NUMERIC(28, 10) NOT NULL,
    volume BIGINT,
    vwap NUMERIC(28, 10),
    provider TEXT NOT NULL DEFAULT 'fmp',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, price_date, provider)
);

CREATE INDEX IF NOT EXISTS idx_us_equity_daily_prices_symbol_date
ON market_data.us_equity_daily_prices (symbol, price_date DESC);

CREATE TABLE IF NOT EXISTS market_data.sync_runs (
    run_id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    requested_start_date DATE,
    requested_end_date DATE,
    rows_written INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_data.us_equity_earnings (
    symbol TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    eps_estimate NUMERIC(14, 4),
    eps_reported NUMERIC(14, 4),
    eps_surprise_pct NUMERIC(14, 4),
    timing TEXT,
    is_estimated BOOLEAN NOT NULL DEFAULT TRUE,
    provider TEXT NOT NULL DEFAULT 'yfinance',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, earnings_date, provider)
);

CREATE INDEX IF NOT EXISTS idx_us_equity_earnings_symbol_date
ON market_data.us_equity_earnings (symbol, earnings_date DESC);

CREATE INDEX IF NOT EXISTS idx_us_equity_earnings_upcoming
ON market_data.us_equity_earnings (earnings_date)
WHERE is_estimated = TRUE;

CREATE TABLE IF NOT EXISTS market_data.us_equity_company_profile (
    symbol TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    sector TEXT,
    industry TEXT,
    market_cap NUMERIC(24, 2),
    shares_outstanding BIGINT,
    float_shares BIGINT,
    short_percent_of_float NUMERIC(10, 6),
    short_ratio NUMERIC(10, 4),
    trailing_pe NUMERIC(14, 4),
    forward_pe NUMERIC(14, 4),
    price_to_book NUMERIC(14, 4),
    peg_ratio NUMERIC(14, 4),
    price_to_sales_ttm NUMERIC(14, 4),
    enterprise_value NUMERIC(24, 2),
    book_value NUMERIC(14, 4),
    trailing_eps NUMERIC(14, 4),
    forward_eps NUMERIC(14, 4),
    profit_margins NUMERIC(10, 6),
    operating_margins NUMERIC(10, 6),
    gross_margins NUMERIC(10, 6),
    return_on_equity NUMERIC(10, 6),
    debt_to_equity NUMERIC(14, 4),
    revenue_growth NUMERIC(10, 6),
    earnings_growth NUMERIC(10, 6),
    total_revenue_ttm NUMERIC(24, 2),
    total_debt NUMERIC(24, 2),
    total_cash NUMERIC(24, 2),
    operating_cashflow_ttm NUMERIC(24, 2),
    free_cashflow_ttm NUMERIC(24, 2),
    dividend_yield NUMERIC(10, 6),
    beta NUMERIC(10, 4),
    fifty_two_week_high NUMERIC(14, 4),
    fifty_two_week_low NUMERIC(14, 4),
    provider TEXT NOT NULL DEFAULT 'yfinance',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, as_of_date, provider)
);

CREATE INDEX IF NOT EXISTS idx_profile_symbol_date
ON market_data.us_equity_company_profile (symbol, as_of_date DESC);

CREATE INDEX IF NOT EXISTS idx_profile_sector
ON market_data.us_equity_company_profile (sector, market_cap DESC);

CREATE TABLE IF NOT EXISTS market_data.us_equity_quarterly_financials (
    symbol TEXT NOT NULL,
    report_date DATE NOT NULL,
    total_revenue NUMERIC(24, 2),
    gross_profit NUMERIC(24, 2),
    operating_income NUMERIC(24, 2),
    ebitda NUMERIC(24, 2),
    ebit NUMERIC(24, 2),
    net_income NUMERIC(24, 2),
    basic_eps NUMERIC(14, 4),
    diluted_eps NUMERIC(14, 4),
    total_assets NUMERIC(24, 2),
    total_debt NUMERIC(24, 2),
    total_cash NUMERIC(24, 2),
    stockholders_equity NUMERIC(24, 2),
    operating_cash_flow NUMERIC(24, 2),
    free_cash_flow NUMERIC(24, 2),
    provider TEXT NOT NULL DEFAULT 'yfinance',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, report_date, provider)
);

CREATE INDEX IF NOT EXISTS idx_qfin_symbol_date
ON market_data.us_equity_quarterly_financials (symbol, report_date DESC);
"""


UPSERT_SQL = """
INSERT INTO market_data.us_equity_daily_prices (
    symbol,
    price_date,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    provider
)
VALUES (
    %(symbol)s,
    %(price_date)s,
    %(open)s,
    %(high)s,
    %(low)s,
    %(close)s,
    %(volume)s,
    %(vwap)s,
    %(provider)s
)
ON CONFLICT (symbol, price_date, provider) DO UPDATE
SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    vwap = EXCLUDED.vwap,
    fetched_at = NOW();
"""


INSERT_RUN_SQL = """
INSERT INTO market_data.sync_runs (
    provider,
    symbol,
    requested_start_date,
    requested_end_date,
    rows_written,
    status,
    error_message
)
VALUES (%s, %s, %s, %s, %s, %s, %s);
"""


# ---------------------------------------------------------------------------
# Database connection — single source of truth for parameter resolution
# ---------------------------------------------------------------------------

_TAG = "[SyncScript]"


def open_db(
    host: str | None = None,
    port: int | None = None,
    dbname: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> psycopg.Connection:
    """Open a PG connection.  Explicit params > env vars > built-in defaults."""
    _host = host or os.getenv("PGHOST", DEFAULT_DB_HOST)
    _port = port or int(os.getenv("PGPORT", str(DEFAULT_DB_PORT)))
    _dbname = dbname or os.getenv("PGDATABASE", DEFAULT_DB_NAME)
    _user = user or os.getenv("PGUSER", DEFAULT_DB_USER)
    _password = password or os.getenv("PGPASSWORD", DEFAULT_DB_PASSWORD)
    print(
        f"{_TAG} Opening DB connection: {_user}@{_host}:{_port}/{_dbname}",
        file=sys.stderr,
        flush=True,
    )
    return psycopg.connect(
        host=_host,
        port=_port,
        dbname=_dbname,
        user=_user,
        password=_password,
        row_factory=dict_row,
    )


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_SCHEMA_SQL)
        cur.execute(
            """
            SELECT column_name, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'market_data'
              AND table_name = 'us_equity_daily_prices'
              AND column_name = ANY(%s);
            """,
            (["open", "high", "low", "close", "vwap"],),
        )
        numeric_columns = {
            row["column_name"]: (row["numeric_precision"], row["numeric_scale"])
            for row in cur.fetchall()
        }
        needs_numeric_widen = any(
            numeric_columns.get(column) != (28, 10)
            for column in ["open", "high", "low", "close", "vwap"]
        )
        if needs_numeric_widen:
            cur.execute(
                """
            ALTER TABLE market_data.us_equity_daily_prices
            ALTER COLUMN open TYPE NUMERIC(28, 10),
            ALTER COLUMN high TYPE NUMERIC(28, 10),
            ALTER COLUMN low TYPE NUMERIC(28, 10),
            ALTER COLUMN close TYPE NUMERIC(28, 10),
            ALTER COLUMN vwap TYPE NUMERIC(28, 10);
                """
            )
    conn.commit()


# ---------------------------------------------------------------------------
# CLI config dataclass
# ---------------------------------------------------------------------------

@dataclass
class SyncConfig:
    provider: str
    start_date: date
    end_date: date
    symbols: list[str]
    request_budget: int
    sleep_seconds: float
    dry_run: bool
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None

    def open_connection(self) -> psycopg.Connection:
        return open_db(self.db_host, self.db_port, self.db_name,
                       self.db_user, self.db_password)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_symbols(raw_symbols: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in raw_symbols.split(","):
        symbol = item.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)
    return ordered


def parse_iso_date(raw_value: str, arg_name: str) -> date:
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise SystemExit(f"{arg_name} must be YYYY-MM-DD, got: {raw_value}") from exc


def get_latest_price_date(
    conn: psycopg.Connection,
    symbol: str,
    provider: str | None = DEFAULT_PROVIDER,
) -> date | None:
    if provider is None:
        sql = """
        SELECT MAX(price_date) AS max_date
        FROM market_data.us_equity_daily_prices
        WHERE symbol = %s;
        """
        params = (symbol,)
    else:
        sql = """
        SELECT MAX(price_date) AS max_date
        FROM market_data.us_equity_daily_prices
        WHERE symbol = %s AND provider = %s;
        """
        params = (symbol, provider)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row["max_date"] if row and row["max_date"] else None


def get_latest_price_dates_by_provider(
    conn: psycopg.Connection,
    symbol: str,
) -> dict[str, date]:
    sql = """
    SELECT provider, MAX(price_date) AS max_date
    FROM market_data.us_equity_daily_prices
    WHERE symbol = %s
    GROUP BY provider
    ORDER BY provider;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (symbol,))
        rows = cur.fetchall()
    return {
        row["provider"]: row["max_date"]
        for row in rows
        if row and row["max_date"]
    }


def resolve_fetch_start(
    backfill_start: date,
    latest_date: date | None,
) -> date:
    if latest_date is None:
        return backfill_start
    return max(backfill_start, latest_date + timedelta(days=1))


def _select_sync_anchor(
    *,
    backfill_start: date,
    latest_for_provider: date | None,
    latest_any_provider: date | None,
) -> tuple[date, str]:
    if latest_any_provider is not None:
        anchor = latest_any_provider
        source = "any_provider"
        if latest_for_provider == latest_any_provider:
            source = "provider_match"
        return resolve_fetch_start(backfill_start, anchor), source
    if latest_for_provider is not None:
        return resolve_fetch_start(backfill_start, latest_for_provider), "provider_match"
    return backfill_start, "backfill"


# ---------------------------------------------------------------------------
# OpenBB fetch + row normalization + PG write
# ---------------------------------------------------------------------------

def fetch_symbol_history(
    symbol: str, start_date: date, end_date: date, provider: str = DEFAULT_PROVIDER,
):
    if provider.lower() == "yfinance":
        # Use yfinance directly to avoid OpenBB provider metadata issues.
        df = yf.download(
            symbol,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            actions=False,
            timeout=10,
        )
        if df is None or df.empty:
            return df

        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = [
                col[0].lower() if isinstance(col, tuple) else str(col).lower()
                for col in df.columns
            ]
        else:
            df.columns = [str(col).lower() for col in df.columns]

        df.index.name = "date"
        return df.reset_index().rename(
            columns={
                "Date": "date",
                "Datetime": "date",
                "adj close": "adj_close",
            }
        )

    global obb
    if obb is None:
        try:
            from openbb import obb as _obb
            obb = _obb
        except ModuleNotFoundError:
            raise RuntimeError(
                "openbb is required to fetch new market data. "
                "Install it with: pip install openbb"
            )
    return (
        obb.equity.price.historical(
            symbol=symbol,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            interval="1d",
            provider=provider,
        )
        .to_df()
        .reset_index()
    )


def to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if value != value:
        return None
    return Decimal(str(value))


def normalize_price_rows(symbol: str, df, provider: str = DEFAULT_PROVIDER) -> list[dict]:
    records: list[dict] = []
    for row in df.to_dict(orient="records"):
        raw_date = row.get("date")
        if raw_date is None:
            continue

        if hasattr(raw_date, "date"):
            price_date = raw_date.date()
        elif isinstance(raw_date, date):
            price_date = raw_date
        else:
            price_date = date.fromisoformat(str(raw_date)[:10])

        volume = row.get("volume")
        records.append(
            {
                "symbol": symbol,
                "price_date": price_date,
                "open": to_decimal(row.get("open")),
                "high": to_decimal(row.get("high")),
                "low": to_decimal(row.get("low")),
                "close": to_decimal(row.get("close")),
                "volume": int(volume) if volume not in (None, "") else None,
                "vwap": to_decimal(row.get("vwap")),
                "provider": provider,
            }
        )

    records.sort(key=lambda item: item["price_date"])
    return records


def write_price_rows(conn: psycopg.Connection, rows: Iterable[dict]) -> int:
    buffered = list(rows)
    if not buffered:
        return 0

    try:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, buffered)
        conn.commit()
        return len(buffered)
    except Exception:
        conn.rollback()
        raise


def record_run(
    conn: psycopg.Connection,
    symbol: str,
    provider: str,
    requested_start_date: date | None,
    requested_end_date: date | None,
    rows_written: int,
    status: str,
    error_message: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            INSERT_RUN_SQL,
            (
                provider,
                symbol,
                requested_start_date,
                requested_end_date,
                rows_written,
                status,
                error_message,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Core: sync a single symbol  (shared by CLI main & programmatic API)
# ---------------------------------------------------------------------------

def sync_symbol(
    conn: psycopg.Connection,
    symbol: str,
    provider: str,
    backfill_start: date,
    end_date: date,
    *,
    latest_for_provider: date | None = None,
    latest_any_provider: date | None = None,
    status_ok: str = "success",
    status_skip: str = "skipped_up_to_date",
    status_err: str = "failed",
) -> tuple[int, str, str]:
    """Check PG freshness for one symbol; fetch + write when stale.

    Returns ``(rows_written, status_label, error_detail)``.
    *error_detail* is an empty string on success/skip.
    """
    if latest_for_provider is None:
        latest_for_provider = get_latest_price_date(conn, symbol, provider=provider)
    if latest_any_provider is None:
        latest_any_provider = get_latest_price_date(conn, symbol, provider=None)

    fetch_start, sync_anchor = _select_sync_anchor(
        backfill_start=backfill_start,
        latest_for_provider=latest_for_provider,
        latest_any_provider=latest_any_provider,
    )

    print(
        f"{_TAG} sync_symbol({symbol}): provider={provider}, "
        f"latest_for_provider={latest_for_provider}, "
        f"latest_any_provider={latest_any_provider}, "
        f"sync_anchor={sync_anchor}, "
        f"fetch_range=[{fetch_start} ~ {end_date}]",
        flush=True,
    )

    if latest_for_provider is None and latest_any_provider is not None:
        print(
            f"{_TAG} {symbol}: provider '{provider}' has no rows, "
            f"but PostgreSQL already has data through {latest_any_provider} "
            f"from another provider. Continuing with incremental sync only.",
            flush=True,
        )
    elif (
        latest_for_provider is not None
        and latest_any_provider is not None
        and latest_for_provider < latest_any_provider
    ):
        print(
            f"{_TAG} {symbol}: provider '{provider}' trails the DB-wide latest "
            f"date ({latest_for_provider} < {latest_any_provider}). "
            f"Using the DB-wide latest date as the incremental sync anchor.",
            flush=True,
        )

    today = date.today()

    if fetch_start > end_date:
        print(
            f"{_TAG} {symbol}: already up to date — skipping",
            flush=True,
        )
        record_run(
            conn, symbol=symbol, provider=provider,
            requested_start_date=fetch_start, requested_end_date=end_date,
            rows_written=0, status=status_skip,
        )
        return 0, status_skip, ""

    # yfinance (and most providers) only serve completed (EOD) daily bars.
    # Trying to fetch today's data before market close causes errors like
    # "startDate > endDate".  If fetch_start is today, the DB is already
    # as fresh as it can be — skip the API call.
    if fetch_start >= today:
        print(
            f"{_TAG} {symbol}: DB has data through "
            f"{latest_any_provider or latest_for_provider} — "
            f"today's ({today}) EOD bar is not yet available, treating as fresh",
            flush=True,
        )
        record_run(
            conn, symbol=symbol, provider=provider,
            requested_start_date=fetch_start, requested_end_date=end_date,
            rows_written=0, status=status_skip,
        )
        return 0, status_skip, ""

    # Cap end_date at yesterday to avoid requesting today's incomplete bar
    effective_end = min(end_date, today - timedelta(days=1))

    try:
        print(
            f"{_TAG} {symbol}: fetching from OpenBB ({provider}) "
            f"[{fetch_start} ~ {effective_end}] ...",
            flush=True,
        )
        df = fetch_symbol_history(symbol, fetch_start, effective_end, provider=provider)
        print(
            f"{_TAG} {symbol}: OpenBB returned {len(df)} raw rows",
            flush=True,
        )
        rows = normalize_price_rows(symbol, df, provider=provider)
        rows_written = write_price_rows(conn, rows)
        print(
            f"{_TAG} ✓ {symbol}: wrote {rows_written} rows to database",
            flush=True,
        )
        record_run(
            conn, symbol=symbol, provider=provider,
            requested_start_date=fetch_start, requested_end_date=effective_end,
            rows_written=rows_written, status=status_ok,
        )
        return rows_written, status_ok, ""
    except Exception as exc:
        conn.rollback()
        err_detail = f"{type(exc).__name__}: {exc}"
        print(
            f"{_TAG} ✗ {symbol}: sync failed — {err_detail}",
            flush=True,
        )
        record_run(
            conn, symbol=symbol, provider=provider,
            requested_start_date=fetch_start, requested_end_date=effective_end,
            rows_written=0, status=status_err,
            error_message=str(exc)[:1000],
        )
        return 0, status_err, err_detail


# ---------------------------------------------------------------------------
# Programmatic API (called by Market Analyst before analysis)
# ---------------------------------------------------------------------------

def ensure_data_fresh(
    symbol: str,
    trade_date: str | date,
    *,
    provider: str = DEFAULT_PROVIDER,
    backfill_years: int = DEFAULT_BACKFILL_YEARS,
    db_host: str | None = None,
    db_port: int | None = None,
    db_name: str | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
) -> str:
    """Check whether PostgreSQL has up-to-date price data for *symbol* and
    sync automatically when it does not.

    Returns a short status string describing the outcome.
    """
    if isinstance(trade_date, str):
        trade_date = date.fromisoformat(trade_date)

    symbol = symbol.upper()
    backfill_start = trade_date - timedelta(days=365 * backfill_years)

    print(
        f"{_TAG} ensure_data_fresh({symbol}): trade_date={trade_date}, "
        f"provider={provider}, backfill_start={backfill_start}",
        flush=True,
    )

    with open_db(db_host, db_port, db_name, db_user, db_password) as conn:
        ensure_schema(conn)
        latest_for_provider = get_latest_price_date(conn, symbol, provider=provider)
        latest_any_provider = get_latest_price_date(conn, symbol, provider=None)
        latest_by_provider = get_latest_price_dates_by_provider(conn, symbol)

        print(
            f"{_TAG} {symbol}: DB freshness snapshot -> "
            f"latest_for_provider={latest_for_provider}, "
            f"latest_any_provider={latest_any_provider}, "
            f"per_provider={latest_by_provider or '{}'}",
            flush=True,
        )

        rows_written, status, err_detail = sync_symbol(
            conn, symbol, provider, backfill_start, trade_date,
            latest_for_provider=latest_for_provider,
            latest_any_provider=latest_any_provider,
            status_ok="auto_sync_success",
            status_skip="fresh",
            status_err="auto_sync_failed",
        )

    if status == "fresh":
        msg = (
            f"[FRESH] {symbol}: data is up to date "
            f"(trade_date={trade_date}, latest_any_provider={latest_any_provider})"
        )
    elif status == "auto_sync_success":
        msg = (
            f"[AUTO-SYNC OK] {symbol}: wrote {rows_written} rows "
            f"(from {latest_any_provider + timedelta(days=1) if latest_any_provider else backfill_start})"
        )
    else:
        msg = (
            f"[AUTO-SYNC ERR] {symbol}: sync failed\n"
            f"  Reason: {err_detail or 'unknown'}\n"
            f"  Troubleshooting:\n"
            f"    1. Check network connectivity to OpenBB / {provider}\n"
            f"    2. Verify API key is configured (provider={provider})\n"
            f"    3. Try manual sync: python cli/fetch_fmp_openbb_to_postgres.py "
            f"--symbols {symbol} --provider {provider}"
        )

    print(f"{_TAG} {msg}", flush=True)
    return msg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> SyncConfig:
    parser = argparse.ArgumentParser(
        description="Sync daily US equity bars from OpenBB to PostgreSQL."
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"OpenBB data provider. Default: {DEFAULT_PROVIDER}. "
        "Alternatives: fmp (requires paid plan for most symbols).",
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated tickers, e.g. AAPL,MSFT,NVDA,SPY,QQQ",
    )
    parser.add_argument(
        "--start-date",
        default=(date.today() - timedelta(days=365 * DEFAULT_BACKFILL_YEARS)).isoformat(),
        help=f"Initial backfill start date in YYYY-MM-DD. Default: {DEFAULT_BACKFILL_YEARS} years ago.",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Sync end date in YYYY-MM-DD. Default: today.",
    )
    parser.add_argument(
        "--request-budget",
        type=int,
        default=DEFAULT_REQUEST_BUDGET,
        help=f"Max API requests for this run. Default: {DEFAULT_REQUEST_BUDGET}.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Sleep between symbols to avoid bursty request patterns. Default: {DEFAULT_SLEEP_SECONDS}s.",
    )
    parser.add_argument(
        "--db-host", default=None,
        help=f"PostgreSQL host. Default: $PGHOST or {DEFAULT_DB_HOST}",
    )
    parser.add_argument(
        "--db-port", type=int, default=None,
        help=f"PostgreSQL port. Default: $PGPORT or {DEFAULT_DB_PORT}",
    )
    parser.add_argument(
        "--db-name", default=None,
        help=f"PostgreSQL database name. Default: $PGDATABASE or {DEFAULT_DB_NAME}",
    )
    parser.add_argument(
        "--db-user", default=None,
        help=f"PostgreSQL user. Default: $PGUSER or {DEFAULT_DB_USER}",
    )
    parser.add_argument(
        "--db-password", default=None,
        help=f"PostgreSQL password. Default: $PGPASSWORD or {DEFAULT_DB_PASSWORD}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be synced without writing to PostgreSQL.",
    )

    args = parser.parse_args()

    symbols = normalize_symbols(args.symbols)
    if not symbols:
        raise SystemExit("No valid symbols were provided.")

    start_date = parse_iso_date(args.start_date, "--start-date")
    end_date = parse_iso_date(args.end_date, "--end-date")
    if start_date > end_date:
        raise SystemExit("--start-date cannot be after --end-date.")

    return SyncConfig(
        provider=args.provider,
        start_date=start_date,
        end_date=end_date,
        symbols=symbols,
        request_budget=max(args.request_budget, 1),
        sleep_seconds=max(args.sleep_seconds, 0.0),
        dry_run=args.dry_run,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
    )


def main() -> int:
    config = parse_args()

    try:
        with config.open_connection() as conn:
            ensure_schema(conn)

            requests_used = 0
            total_rows = 0

            for symbol in config.symbols:
                if requests_used >= config.request_budget:
                    print(
                        f"Request budget reached ({config.request_budget}). "
                        "Stopping this run."
                    )
                    break

                if config.dry_run:
                    latest = get_latest_price_date(conn, symbol, provider=config.provider)
                    fetch_start = resolve_fetch_start(config.start_date, latest)
                    mode = "DRY RUN" if fetch_start <= config.end_date else "SKIP"
                    print(f"[{mode}] {symbol}: {fetch_start} -> {config.end_date}")
                    record_run(
                        conn, symbol=symbol, provider=config.provider,
                        requested_start_date=fetch_start,
                        requested_end_date=config.end_date,
                        rows_written=0, status="dry_run",
                    )
                    requests_used += 1
                    continue

                rows_written, status, err_detail = sync_symbol(
                    conn, symbol, config.provider,
                    config.start_date, config.end_date,
                )

                if status == "skipped_up_to_date":
                    print(f"[SKIP] {symbol}: database is already up to date.")
                elif status == "success":
                    requests_used += 1
                    total_rows += rows_written
                    print(f"[OK] {symbol}: wrote {rows_written} rows.")
                else:
                    requests_used += 1
                    print(
                        f"[ERR] {symbol}: sync failed — {err_detail}",
                        file=sys.stderr,
                    )

                if config.sleep_seconds:
                    time.sleep(config.sleep_seconds)

            print(
                f"Finished. Requests used: {requests_used}. "
                f"Rows written: {total_rows}."
            )
    except psycopg.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
