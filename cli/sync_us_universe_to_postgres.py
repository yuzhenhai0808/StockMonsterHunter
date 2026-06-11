#!/usr/bin/env python3
"""Refresh a US equity universe table, then batch-sync daily bars to PostgreSQL.

This script is intentionally separate from ``fetch_fmp_openbb_to_postgres.py`` so
the existing project flow remains unchanged.

Typical usage:

1. Refresh the stock universe from SEC and sync all active symbols:
    python cli/sync_us_universe_to_postgres.py \
      --refresh-universe \
      --use-db-universe \
      --provider yfinance

2. Refresh only the universe table:
    python cli/sync_us_universe_to_postgres.py \
      --refresh-universe \
      --refresh-only

3. Sync prices for the symbols already stored in PostgreSQL:
    python cli/sync_us_universe_to_postgres.py \
      --use-db-universe \
      --provider yfinance
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

try:
    import psycopg
except ModuleNotFoundError:
    print(
        "Missing dependency: psycopg. Install it with: pip install psycopg[binary]",
        file=sys.stderr,
    )
    raise SystemExit(1)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_str = str(PROJECT_ROOT)
if sys.path[0] != project_root_str:
    sys.path.insert(0, project_root_str)

from cli.fetch_fmp_openbb_to_postgres import (
    DEFAULT_DB_HOST,
    DEFAULT_DB_NAME,
    DEFAULT_DB_PASSWORD,
    DEFAULT_DB_PORT,
    DEFAULT_DB_USER,
    DEFAULT_PROVIDER,
    DEFAULT_REQUEST_BUDGET,
    DEFAULT_SLEEP_SECONDS,
    fetch_symbol_history,
    normalize_price_rows,
    normalize_symbols,
    open_db,
    parse_iso_date,
    record_run,
    resolve_fetch_start,
    write_price_rows,
)

import yfinance as yf


_TAG = "[UniverseSync]"
DEFAULT_UNIVERSE_SOURCE = "sec"
MIN_SYNC_START_DATE = date(2026, 1, 1)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "TradingAgents/1.0 support@example.com"


CREATE_UNIVERSE_SQL = """
CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS market_data.us_equity_symbols (
    symbol TEXT PRIMARY KEY,
    security_name TEXT,
    exchange TEXT,
    asset_type TEXT NOT NULL DEFAULT 'equity',
    universe_source TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_us_equity_symbols_active_source
ON market_data.us_equity_symbols (is_active, universe_source, symbol);

CREATE TABLE IF NOT EXISTS market_data.us_equity_sync_checkpoints (
    job_name TEXT PRIMARY KEY,
    universe_source TEXT NOT NULL,
    provider TEXT NOT NULL,
    next_symbol TEXT,
    total_symbols INTEGER,
    last_completed_symbol TEXT,
    last_status TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
"""


UPSERT_SYMBOL_SQL = """
INSERT INTO market_data.us_equity_symbols (
    symbol,
    security_name,
    exchange,
    asset_type,
    universe_source,
    is_active,
    first_seen_at,
    last_seen_at,
    updated_at
)
VALUES (
    %(symbol)s,
    %(security_name)s,
    %(exchange)s,
    %(asset_type)s,
    %(universe_source)s,
    TRUE,
    NOW(),
    NOW(),
    NOW()
)
ON CONFLICT (symbol) DO UPDATE
SET
    security_name = EXCLUDED.security_name,
    exchange = COALESCE(EXCLUDED.exchange, market_data.us_equity_symbols.exchange),
    asset_type = EXCLUDED.asset_type,
    universe_source = EXCLUDED.universe_source,
    is_active = TRUE,
    last_seen_at = NOW(),
    updated_at = NOW();
"""


DEACTIVATE_MISSING_SQL = """
UPDATE market_data.us_equity_symbols
SET is_active = FALSE,
    updated_at = NOW()
WHERE universe_source = %s
  AND symbol <> ALL(%s);
"""


@dataclass
class UniverseSyncConfig:
    provider: str
    start_date: date
    end_date: date
    explicit_symbols: list[str]
    symbol_prefixes: list[str]
    use_db_universe: bool
    refresh_universe: bool
    refresh_only: bool
    universe_source: str
    universe_limit: int | None
    deactivate_missing: bool
    request_budget: int
    concurrency: int
    sleep_seconds: float
    batch_download: bool
    include_today: bool
    exclude_stale_failures: bool
    stale_days: int
    failure_lookback_days: int
    min_failure_count: int
    single_batch: bool
    max_retry_rounds: int
    dry_run: bool
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None

    def open_connection(self) -> psycopg.Connection:
        return open_db(
            self.db_host,
            self.db_port,
            self.db_name,
            self.db_user,
            self.db_password,
        )


def ensure_schema(conn: psycopg.Connection) -> None:
    from cli.fetch_fmp_openbb_to_postgres import ensure_schema as ensure_price_schema

    ensure_price_schema(conn)
    with conn.cursor() as cur:
        cur.execute(CREATE_UNIVERSE_SQL)
    conn.commit()


def fetch_sec_universe(limit: int | None = None) -> list[dict]:
    request = urllib.request.Request(
        SEC_TICKERS_URL,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json",
        },
    )
    print(f"{_TAG} Fetching US equity universe from SEC ...", flush=True)
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    rows: list[dict] = []
    for entry in payload.values():
        symbol = str(entry.get("ticker", "")).strip().upper()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "security_name": str(entry.get("title", "")).strip() or None,
                "exchange": None,
                "asset_type": "equity",
                "universe_source": "sec",
            }
        )

    rows.sort(key=lambda item: item["symbol"])
    if limit is not None:
        rows = rows[:limit]

    print(f"{_TAG} SEC universe rows fetched: {len(rows)}", flush=True)
    return rows


def fetch_universe_rows(source: str, limit: int | None = None) -> list[dict]:
    if source == "sec":
        return fetch_sec_universe(limit=limit)
    raise SystemExit(f"Unsupported universe source: {source}")


def upsert_universe_rows(
    conn: psycopg.Connection,
    rows: Iterable[dict],
    *,
    source: str,
    deactivate_missing: bool = False,
) -> int:
    buffered = list(rows)
    if not buffered:
        return 0

    with conn.cursor() as cur:
        cur.executemany(UPSERT_SYMBOL_SQL, buffered)
        if deactivate_missing:
            symbols = [row["symbol"] for row in buffered]
            cur.execute(DEACTIVATE_MISSING_SQL, (source, symbols))
    conn.commit()
    return len(buffered)


def load_symbols_from_db(
    conn: psycopg.Connection,
    *,
    active_only: bool = True,
    limit: int | None = None,
) -> list[str]:
    sql = """
    SELECT symbol
    FROM market_data.us_equity_symbols
    WHERE (%s = FALSE OR is_active = TRUE)
    ORDER BY symbol
    """
    params: list[object] = [active_only]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    symbols = [row["symbol"] for row in rows]
    print(f"{_TAG} Loaded {len(symbols)} symbols from DB universe", flush=True)
    return symbols


def filter_stale_failure_symbols(
    conn: psycopg.Connection,
    symbols: list[str],
    *,
    provider: str,
    stale_days: int,
    failure_lookback_days: int,
    min_failure_count: int,
) -> list[str]:
    if not symbols:
        return symbols

    sql = """
    WITH latest AS (
        SELECT symbol, MAX(price_date) AS latest_price_date
        FROM market_data.us_equity_daily_prices
        WHERE provider = %s
          AND symbol = ANY(%s)
        GROUP BY symbol
    ),
    failures AS (
        SELECT symbol, COUNT(*) AS failure_count
        FROM market_data.sync_runs
        WHERE provider = %s
          AND symbol = ANY(%s)
          AND created_at >= NOW() - (%s * INTERVAL '1 day')
          AND (
              status IN ('failed', 'failed_no_rows')
              OR LOWER(COALESCE(error_message, '')) LIKE '%%possibly delisted%%'
              OR LOWER(COALESCE(error_message, '')) LIKE '%%no timezone found%%'
              OR LOWER(COALESCE(error_message, '')) LIKE '%%symbol may be delisted%%'
          )
        GROUP BY symbol
    )
    SELECT f.symbol
    FROM failures f
    LEFT JOIN latest l ON l.symbol = f.symbol
    WHERE f.failure_count >= %s
      AND (
          l.latest_price_date IS NULL
          OR l.latest_price_date < CURRENT_DATE - %s
      );
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                provider,
                symbols,
                provider,
                symbols,
                failure_lookback_days,
                min_failure_count,
                stale_days,
            ),
        )
        stale_symbols = {row["symbol"] for row in cur.fetchall()}
    if not stale_symbols:
        return symbols

    filtered = [symbol for symbol in symbols if symbol not in stale_symbols]
    print(
        f"{_TAG} Excluded {len(stale_symbols)} stale/no-data symbol(s) "
        f"from Yahoo sync; {len(filtered)}/{len(symbols)} remain.",
        flush=True,
    )
    return filtered


def checkpoint_job_name(config: UniverseSyncConfig) -> str | None:
    if config.explicit_symbols:
        return None
    shard = ""
    if config.symbol_prefixes:
        shard_label = "-".join(prefix.replace("-", "") for prefix in config.symbol_prefixes)
        shard = f":prefix:{shard_label}"
    return f"us_equity:{config.universe_source}:{config.provider}:active{shard}"


def load_checkpoint(
    conn: psycopg.Connection,
    job_name: str,
) -> dict | None:
    sql = """
    SELECT job_name, next_symbol, total_symbols, last_completed_symbol, last_status
    FROM market_data.us_equity_sync_checkpoints
    WHERE job_name = %s;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (job_name,))
        return cur.fetchone()


def save_checkpoint(
    conn: psycopg.Connection,
    *,
    job_name: str,
    universe_source: str,
    provider: str,
    next_symbol: str | None,
    total_symbols: int,
    last_completed_symbol: str | None,
    last_status: str | None,
) -> None:
    sql = """
    INSERT INTO market_data.us_equity_sync_checkpoints (
        job_name,
        universe_source,
        provider,
        next_symbol,
        total_symbols,
        last_completed_symbol,
        last_status,
        updated_at,
        completed_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NULL)
    ON CONFLICT (job_name) DO UPDATE
    SET
        universe_source = EXCLUDED.universe_source,
        provider = EXCLUDED.provider,
        next_symbol = EXCLUDED.next_symbol,
        total_symbols = EXCLUDED.total_symbols,
        last_completed_symbol = EXCLUDED.last_completed_symbol,
        last_status = EXCLUDED.last_status,
        updated_at = NOW(),
        completed_at = NULL;
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                job_name,
                universe_source,
                provider,
                next_symbol,
                total_symbols,
                last_completed_symbol,
                last_status,
            ),
        )
    conn.commit()


def mark_checkpoint_complete(conn: psycopg.Connection, job_name: str) -> None:
    sql = """
    UPDATE market_data.us_equity_sync_checkpoints
    SET next_symbol = NULL,
        updated_at = NOW(),
        completed_at = NOW()
    WHERE job_name = %s;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (job_name,))
    conn.commit()


def rotate_symbols_from_checkpoint(
    symbols: list[str],
    next_symbol: str | None,
) -> list[str]:
    """Resume from the checkpoint through the end of the universe list.

    This intentionally does not wrap back to the beginning.  A sync run should
    process one linear pass only; after it reaches the end, the checkpoint is
    marked complete and a future run can start from the beginning again.
    """
    if not next_symbol:
        return symbols

    try:
        start_idx = symbols.index(next_symbol)
    except ValueError:
        print(
            f"{_TAG} Checkpoint symbol {next_symbol!r} not found in current universe; restarting from the beginning.",
            flush=True,
        )
        return symbols

    if start_idx == 0:
        return symbols

    return symbols[start_idx:]


def parse_sync_date(raw_value: str, arg_name: str) -> date:
    value = raw_value.strip().lower()
    if value in {"today", "current", "current-date", "now"}:
        return date.today()
    return parse_iso_date(raw_value, arg_name)


def expand_symbol_prefix(value: str) -> list[str]:
    if value in {"0-9", "DIGIT", "DIGITS", "NUMBER", "NUMBERS"}:
        return ["0-9"]
    if "-" not in value:
        return [value]

    start, end = (part.strip() for part in value.split("-", 1))
    if len(start) != 1 or len(end) != 1:
        return [value]
    if start.isalpha() and end.isalpha() and start <= end:
        return [chr(code) for code in range(ord(start), ord(end) + 1)]
    return [value]


def normalize_symbol_prefixes(raw_value: str) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    for item in raw_value.split(","):
        value = item.strip().upper()
        if not value:
            continue
        for expanded in expand_symbol_prefix(value):
            if expanded not in seen:
                seen.add(expanded)
                prefixes.append(expanded)
    return prefixes


def symbol_matches_prefixes(symbol: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    upper_symbol = symbol.upper()
    for prefix in prefixes:
        if prefix == "0-9":
            if upper_symbol[:1].isdigit():
                return True
        elif upper_symbol.startswith(prefix):
            return True
    return False


def filter_symbols_by_prefixes(symbols: list[str], prefixes: list[str]) -> list[str]:
    if not prefixes:
        return symbols

    filtered = [
        symbol
        for symbol in symbols
        if symbol_matches_prefixes(symbol, prefixes)
    ]
    print(
        f"{_TAG} Prefix filter {prefixes}: {len(filtered)}/{len(symbols)} symbols selected",
        flush=True,
    )
    return filtered


def get_latest_price_dates(
    conn: psycopg.Connection,
    symbols: list[str],
    provider: str,
) -> dict[str, date]:
    if not symbols:
        return {}

    sql = """
    SELECT symbol, MAX(price_date) AS max_date
    FROM market_data.us_equity_daily_prices
    WHERE provider = %s AND symbol = ANY(%s)
    GROUP BY symbol;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (provider, symbols))
        rows = cur.fetchall()
    return {row["symbol"]: row["max_date"] for row in rows if row["max_date"]}


def get_provider_trade_dates(
    conn: psycopg.Connection,
    provider: str,
    start_date: date,
    end_date: date,
) -> list[date]:
    sql = """
    SELECT DISTINCT price_date
    FROM market_data.us_equity_daily_prices
    WHERE provider = %s
      AND price_date BETWEEN %s AND %s
    ORDER BY price_date;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (provider, start_date, end_date))
        rows = cur.fetchall()
    return [row["price_date"] for row in rows]


def get_existing_price_dates_by_symbol(
    conn: psycopg.Connection,
    symbols: list[str],
    provider: str,
    start_date: date,
    end_date: date,
) -> dict[str, set[date]]:
    if not symbols:
        return {}

    sql = """
    SELECT symbol, price_date
    FROM market_data.us_equity_daily_prices
    WHERE provider = %s
      AND symbol = ANY(%s)
      AND price_date BETWEEN %s AND %s;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (provider, symbols, start_date, end_date))
        rows = cur.fetchall()

    existing: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        existing[row["symbol"]].add(row["price_date"])
    return existing


def resolve_fetch_start_from_coverage(
    *,
    requested_start: date,
    requested_end: date,
    latest_date: date | None,
    expected_trade_dates: list[date],
    existing_dates: set[date],
) -> tuple[date, str]:
    """Return the first date that still needs a provider fetch.

    ``expected_trade_dates`` comes from the local provider calendar already in
    PostgreSQL, so weekends and market holidays are naturally ignored.
    """
    missing_dates = [
        trade_date
        for trade_date in expected_trade_dates
        if trade_date not in existing_dates
    ]
    if missing_dates:
        return max(requested_start, min(missing_dates)), "missing_db_dates"
    if latest_date is None:
        return requested_start, "no_existing_rows"
    if latest_date >= requested_end:
        return requested_end + timedelta(days=1), "covered"
    return max(requested_start, latest_date + timedelta(days=1)), "extend_latest"


def sync_symbol_with_latest(
    conn: psycopg.Connection,
    symbol: str,
    provider: str,
    backfill_start: date,
    end_date: date,
    *,
    latest_date: date | None = None,
) -> tuple[int, str, str]:
    fetch_start = resolve_fetch_start(backfill_start, latest_date)
    print(
        f"{_TAG} sync_symbol({symbol}): provider={provider}, "
        f"latest_in_db={latest_date}, fetch_range=[{fetch_start} ~ {end_date}]",
        flush=True,
    )

    today = date.today()

    if fetch_start > end_date:
        print(f"{_TAG} {symbol}: already up to date — skipping", flush=True)
        record_run(
            conn,
            symbol=symbol,
            provider=provider,
            requested_start_date=fetch_start,
            requested_end_date=end_date,
            rows_written=0,
            status="skipped_up_to_date",
        )
        return 0, "skipped_up_to_date", ""

    if fetch_start > today:
        print(
            f"{_TAG} {symbol}: DB has data through {latest_date} — "
            f"requested range starts after today's date ({today}), treating as fresh",
            flush=True,
        )
        record_run(
            conn,
            symbol=symbol,
            provider=provider,
            requested_start_date=fetch_start,
            requested_end_date=end_date,
            rows_written=0,
            status="skipped_up_to_date",
        )
        return 0, "skipped_up_to_date", ""

    # Allow fetching through the current calendar date.  yfinance may return
    # today's in-progress or freshly completed daily bar depending on market
    # timing; if it is unavailable, a later run will fill it incrementally.
    effective_end = min(end_date, today)

    try:
        print(
            f"{_TAG} {symbol}: fetching from OpenBB ({provider}) "
            f"[{fetch_start} ~ {effective_end}] ...",
            flush=True,
        )
        df = fetch_symbol_history(symbol, fetch_start, effective_end, provider=provider)
        raw_count = 0 if df is None else len(df)
        print(f"{_TAG} {symbol}: OpenBB returned {raw_count} raw rows", flush=True)
        rows = normalize_price_rows(symbol, df, provider=provider)
        rows_written = write_price_rows(conn, rows)
        if rows_written > 0:
            print(f"{_TAG} {symbol}: wrote {rows_written} rows to database", flush=True)
            status = "success"
        else:
            print(
                f"{_TAG} {symbol}: no rows returned for [{fetch_start} ~ {effective_end}]; "
                "leaving as a soft failure so it can be retried later",
                flush=True,
            )
            status = "failed_no_rows"
        record_run(
            conn,
            symbol=symbol,
            provider=provider,
            requested_start_date=fetch_start,
            requested_end_date=effective_end,
            rows_written=rows_written,
            status=status,
        )
        if rows_written == 0:
            return 0, "failed", "provider returned 0 rows"
        return rows_written, "success", ""
    except Exception as exc:
        conn.rollback()
        err_detail = f"{type(exc).__name__}: {exc}"
        print(f"{_TAG} ✗ {symbol}: sync failed — {err_detail}", flush=True)
        record_run(
            conn,
            symbol=symbol,
            provider=provider,
            requested_start_date=fetch_start,
            requested_end_date=effective_end,
            rows_written=0,
            status="failed",
            error_message=str(exc)[:1000],
        )
        return 0, "failed", err_detail


def fetch_symbol_rows_worker(
    symbol: str,
    provider: str,
    fetch_start: date,
    effective_end: date,
) -> tuple[str, date, date, int, list[dict], str]:
    """Fetch and normalize one symbol's rows without touching PostgreSQL."""
    try:
        df = fetch_symbol_history(symbol, fetch_start, effective_end, provider=provider)
        raw_count = 0 if df is None else len(df)
        rows = normalize_price_rows(symbol, df, provider=provider) if raw_count else []
        return symbol, fetch_start, effective_end, raw_count, rows, ""
    except Exception as exc:
        return symbol, fetch_start, effective_end, 0, [], f"{type(exc).__name__}: {exc}"


def _normalise_yfinance_columns(df):
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


def _extract_yfinance_symbol_frame(df, symbol: str):
    if df is None or df.empty:
        return None
    if not (hasattr(df.columns, "nlevels") and df.columns.nlevels > 1):
        return df

    target = symbol.upper()
    for level in range(df.columns.nlevels):
        values = {str(value).upper() for value in df.columns.get_level_values(level)}
        if target in values:
            return df.xs(symbol, level=level, axis=1, drop_level=True)
    return None


def fetch_yfinance_batch_rows_worker(
    jobs: list[tuple[str, date, date]],
    provider: str,
) -> list[tuple[str, date, date, int, list[dict], str]]:
    """Fetch one same-range group with a single yfinance multi-ticker request."""
    symbols = [symbol for symbol, _, _ in jobs]
    fetch_start = jobs[0][1]
    effective_end = jobs[0][2]
    try:
        df = yf.download(
            symbols if len(symbols) > 1 else symbols[0],
            start=fetch_start.isoformat(),
            end=(effective_end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            actions=False,
            timeout=20,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        err_detail = f"{type(exc).__name__}: {exc}"
        return [
            (symbol, start, end, 0, [], err_detail)
            for symbol, start, end in jobs
        ]

    results: list[tuple[str, date, date, int, list[dict], str]] = []
    for symbol, start, end in jobs:
        try:
            symbol_df = _extract_yfinance_symbol_frame(df, symbol)
            if symbol_df is None or symbol_df.empty:
                results.append((symbol, start, end, 0, [], ""))
                continue
            symbol_df = symbol_df.dropna(how="all")
            if symbol_df.empty:
                results.append((symbol, start, end, 0, [], ""))
                continue
            normalized_df = _normalise_yfinance_columns(symbol_df)
            rows = normalize_price_rows(symbol, normalized_df, provider=provider)
            results.append((symbol, start, end, len(normalized_df), rows, ""))
        except Exception as exc:
            results.append(
                (symbol, start, end, 0, [], f"{type(exc).__name__}: {exc}")
            )
    return results


def _record_price_fetch_result(
    config: UniverseSyncConfig,
    conn: psycopg.Connection,
    latest_dates: dict[str, date],
    result: tuple[str, date, date, int, list[dict], str],
) -> tuple[int, str | None]:
    symbol, fetch_start, effective_end, raw_count, rows, err_detail = result
    if not err_detail:
        print(
            f"{_TAG} {symbol}: yfinance returned {raw_count} raw rows",
            flush=True,
        )
        try:
            rows_written = write_price_rows(conn, rows)
            record_run(
                conn,
                symbol=symbol,
                provider=config.provider,
                requested_start_date=fetch_start,
                requested_end_date=effective_end,
                rows_written=rows_written,
                status="success",
            )
        except Exception as exc:
            conn.rollback()
            err_detail = f"{type(exc).__name__}: {exc}"
            record_run(
                conn,
                symbol=symbol,
                provider=config.provider,
                requested_start_date=fetch_start,
                requested_end_date=effective_end,
                rows_written=0,
                status="failed",
                error_message=str(exc)[:1000],
            )
            print(f"[ERR] {symbol}: sync failed - {err_detail}", file=sys.stderr)
            return 0, symbol

        latest_dates[symbol] = min(config.end_date, date.today())
        if rows_written > 0:
            print(f"[OK] {symbol}: wrote {rows_written} rows.", flush=True)
        else:
            print(
                f"[WARN] {symbol}: wrote 0 rows with no explicit exception; "
                "this often indicates no new bar or an upstream yfinance soft-failure.",
                flush=True,
            )
        return rows_written, None

    record_run(
        conn,
        symbol=symbol,
        provider=config.provider,
        requested_start_date=fetch_start,
        requested_end_date=effective_end,
        rows_written=0,
        status="failed",
        error_message=err_detail[:1000],
    )
    print(f"[ERR] {symbol}: sync failed - {err_detail}", file=sys.stderr)
    return 0, symbol


def sync_batch_concurrently(
    config: UniverseSyncConfig,
    conn: psycopg.Connection,
    batch_symbols: list[str],
    latest_dates: dict[str, date],
) -> tuple[int, int, list[str]]:
    """Sync one batch concurrently.

    Returns ``(requests_used, rows_written, failed_symbols)``.
    """
    requests_used = 0
    total_rows = 0
    failed_symbols: list[str] = []
    today = date.today()
    worker_count = min(config.concurrency, len(batch_symbols))
    fetch_jobs: list[tuple[str, date, date]] = []
    effective_end = min(config.end_date, today)
    trade_dates = get_provider_trade_dates(
        conn,
        config.provider,
        config.start_date,
        effective_end,
    )
    existing_dates = get_existing_price_dates_by_symbol(
        conn,
        batch_symbols,
        config.provider,
        config.start_date,
        effective_end,
    )

    print(
        f"{_TAG} Running concurrent sync for {len(batch_symbols)} symbols "
        f"with {worker_count} workers.",
        flush=True,
    )
    if trade_dates:
        print(
            f"{_TAG} Local provider calendar has {len(trade_dates)} date(s) "
            f"for [{config.start_date} ~ {effective_end}].",
            flush=True,
        )
    else:
        print(
            f"{_TAG} Local provider calendar has no dates for "
            f"[{config.start_date} ~ {effective_end}]; Yahoo fetch is required.",
            flush=True,
        )

    for symbol in batch_symbols:
        latest_date = latest_dates.get(symbol)
        fetch_start, coverage_status = resolve_fetch_start_from_coverage(
            requested_start=config.start_date,
            requested_end=effective_end,
            latest_date=latest_date,
            expected_trade_dates=trade_dates,
            existing_dates=existing_dates.get(symbol, set()),
        )

        if fetch_start > effective_end:
            print(
                f"[SKIP] {symbol}: database already covers "
                f"[{config.start_date} ~ {effective_end}] ({coverage_status}).",
                flush=True,
            )
            record_run(
                conn,
                symbol=symbol,
                provider=config.provider,
                requested_start_date=fetch_start,
                requested_end_date=effective_end,
                rows_written=0,
                status="skipped_up_to_date",
            )
            continue

        if fetch_start > today:
            print(
                f"[SKIP] {symbol}: requested range starts after today's date ({today}).",
                flush=True,
            )
            record_run(
                conn,
                symbol=symbol,
                provider=config.provider,
                requested_start_date=fetch_start,
                requested_end_date=config.end_date,
                rows_written=0,
                status="skipped_up_to_date",
            )
            continue

        if coverage_status == "missing_db_dates":
            print(
                f"{_TAG} {symbol}: DB range has gaps; fetching from {fetch_start} "
                f"through {effective_end}.",
                flush=True,
            )
        fetch_jobs.append((symbol, fetch_start, effective_end))

    if not fetch_jobs:
        return 0, 0, []

    if config.batch_download and config.provider.lower() == "yfinance":
        grouped_jobs: dict[tuple[date, date], list[tuple[str, date, date]]] = defaultdict(list)
        for job in fetch_jobs:
            _, fetch_start, effective_end = job
            grouped_jobs[(fetch_start, effective_end)].append(job)
        print(
            f"{_TAG} Dispatching {len(grouped_jobs)} yfinance batch request(s) "
            f"covering {len(fetch_jobs)} symbols.",
            flush=True,
        )
        for jobs in grouped_jobs.values():
            requests_used += 1
            for result in fetch_yfinance_batch_rows_worker(jobs, config.provider):
                rows_written, failed_symbol = _record_price_fetch_result(
                    config,
                    conn,
                    latest_dates,
                    result,
                )
                total_rows += rows_written
                if failed_symbol is not None:
                    failed_symbols.append(failed_symbol)
            if config.sleep_seconds:
                time.sleep(config.sleep_seconds)
        return requests_used, total_rows, failed_symbols

    worker_count = min(config.concurrency, len(fetch_jobs))
    print(
        f"{_TAG} Dispatching {len(fetch_jobs)} yfinance request(s) "
        f"with {worker_count} workers.",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                fetch_symbol_rows_worker,
                symbol,
                config.provider,
                fetch_start,
                effective_end,
            ): symbol
            for symbol, fetch_start, effective_end in fetch_jobs
        }

        for future in as_completed(futures):
            requests_used += 1
            rows_written, failed_symbol = _record_price_fetch_result(
                config,
                conn,
                latest_dates,
                future.result(),
            )
            total_rows += rows_written
            if failed_symbol is not None:
                failed_symbols.append(failed_symbol)

            if config.sleep_seconds:
                time.sleep(config.sleep_seconds)

    return requests_used, total_rows, failed_symbols


def parse_args() -> UniverseSyncConfig:
    parser = argparse.ArgumentParser(
        description="Refresh a US equity universe table and batch-sync daily bars."
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"OpenBB data provider. Default: {DEFAULT_PROVIDER}.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated tickers. If omitted, use DB universe flags.",
    )
    parser.add_argument(
        "--symbol-prefixes",
        default="",
        help=(
            "Optional comma-separated symbol prefixes to shard syncing, "
            "for example A,B,C or 0-9. Applies after symbols are resolved."
        ),
    )
    parser.add_argument(
        "--use-db-universe",
        action="store_true",
        help="Sync all active symbols from market_data.us_equity_symbols.",
    )
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="Refresh market_data.us_equity_symbols before price sync.",
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Refresh the universe table only, without syncing prices.",
    )
    parser.add_argument(
        "--universe-source",
        default=DEFAULT_UNIVERSE_SOURCE,
        help=f"Universe source. Default: {DEFAULT_UNIVERSE_SOURCE}.",
    )
    parser.add_argument(
        "--universe-limit",
        type=int,
        default=None,
        help="Limit the number of universe rows refreshed or synced.",
    )
    parser.add_argument(
        "--deactivate-missing",
        action="store_true",
        help="Mark DB symbols missing from the latest universe snapshot as inactive.",
    )
    parser.add_argument(
        "--start-date",
        default=MIN_SYNC_START_DATE.isoformat(),
        help=f"Initial backfill start date in YYYY-MM-DD. Default: {MIN_SYNC_START_DATE.isoformat()}.",
    )
    parser.add_argument(
        "--end-date",
        default="today",
        help="Sync end date in YYYY-MM-DD, or 'today'. Default: today.",
    )
    parser.add_argument(
        "--include-today",
        action="store_true",
        help="Include today's still-forming daily bar. Default skips today and syncs completed bars only.",
    )
    parser.add_argument(
        "--no-exclude-stale-failures",
        dest="exclude_stale_failures",
        action="store_false",
        help="Do not exclude symbols with repeated recent no-data/delisted failures.",
    )
    parser.set_defaults(exclude_stale_failures=True)
    parser.add_argument(
        "--stale-days",
        type=int,
        default=14,
        help="Latest price older than this many days is stale for failure exclusion. Default: 14.",
    )
    parser.add_argument(
        "--failure-lookback-days",
        type=int,
        default=30,
        help="Look back this many days in sync_runs for failure exclusion. Default: 30.",
    )
    parser.add_argument(
        "--min-failure-count",
        type=int,
        default=2,
        help="Minimum recent failures before excluding a stale symbol. Default: 2.",
    )
    parser.add_argument(
        "--request-budget",
        type=int,
        default=DEFAULT_REQUEST_BUDGET,
        help=f"Max API requests per batch before auto-continuing. Default: {DEFAULT_REQUEST_BUDGET}.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "Number of symbols to sync in parallel within each batch. "
            "Use 4-12 for yfinance; default preserves serial behavior."
        ),
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Sleep between symbols to avoid bursty request patterns. Default: {DEFAULT_SLEEP_SECONDS}s.",
    )
    parser.add_argument(
        "--batch-download",
        dest="batch_download",
        action="store_true",
        default=True,
        help="Use one yfinance multi-ticker download per same-range batch (default on).",
    )
    parser.add_argument(
        "--no-batch-download",
        dest="batch_download",
        action="store_false",
        help="Fall back to one yfinance request per symbol.",
    )
    parser.add_argument(
        "--single-batch",
        action="store_true",
        help="Process only one batch of up to --request-budget symbols, then stop.",
    )
    parser.add_argument(
        "--max-retry-rounds",
        type=int,
        default=2,
        help="Retry failed symbols after the main pass. Default: 2 retry rounds.",
    )
    parser.add_argument(
        "--db-host",
        default=None,
        help=f"PostgreSQL host. Default: $PGHOST or {DEFAULT_DB_HOST}",
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=None,
        help=f"PostgreSQL port. Default: $PGPORT or {DEFAULT_DB_PORT}",
    )
    parser.add_argument(
        "--db-name",
        default=None,
        help=f"PostgreSQL database name. Default: $PGDATABASE or {DEFAULT_DB_NAME}",
    )
    parser.add_argument(
        "--db-user",
        default=None,
        help=f"PostgreSQL user. Default: $PGUSER or {DEFAULT_DB_USER}",
    )
    parser.add_argument(
        "--db-password",
        default=None,
        help=f"PostgreSQL password. Default: $PGPASSWORD or {DEFAULT_DB_PASSWORD}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be synced without writing prices to PostgreSQL.",
    )

    args = parser.parse_args()

    explicit_symbols = normalize_symbols(args.symbols)
    symbol_prefixes = normalize_symbol_prefixes(args.symbol_prefixes)
    start_date = parse_sync_date(args.start_date, "--start-date")
    end_date = parse_sync_date(args.end_date, "--end-date")
    if not args.include_today:
        completed_bar_date = date.today() - timedelta(days=1)
        if end_date > completed_bar_date:
            print(
                f"{_TAG} --include-today not set; clamping --end-date "
                f"from {end_date} to completed daily bar date {completed_bar_date}.",
                flush=True,
            )
            end_date = completed_bar_date
    if start_date < MIN_SYNC_START_DATE:
        print(
            f"{_TAG} --start-date {start_date} is earlier than the allowed floor; "
            f"clamping to {MIN_SYNC_START_DATE}.",
            flush=True,
        )
        start_date = MIN_SYNC_START_DATE
    if start_date > end_date:
        raise SystemExit("--start-date cannot be after --end-date.")

    if not explicit_symbols and not args.use_db_universe and not args.refresh_universe:
        raise SystemExit(
            "Provide --symbols, or use --use-db-universe, or run --refresh-universe."
        )

    if args.refresh_only and not args.refresh_universe:
        raise SystemExit("--refresh-only requires --refresh-universe.")

    return UniverseSyncConfig(
        provider=args.provider,
        start_date=start_date,
        end_date=end_date,
        explicit_symbols=explicit_symbols,
        symbol_prefixes=symbol_prefixes,
        use_db_universe=args.use_db_universe,
        refresh_universe=args.refresh_universe,
        refresh_only=args.refresh_only,
        universe_source=args.universe_source,
        universe_limit=args.universe_limit,
        deactivate_missing=args.deactivate_missing,
        request_budget=max(args.request_budget, 1),
        concurrency=max(args.concurrency, 1),
        sleep_seconds=max(args.sleep_seconds, 0.0),
        batch_download=args.batch_download,
        include_today=args.include_today,
        exclude_stale_failures=args.exclude_stale_failures,
        stale_days=max(args.stale_days, 1),
        failure_lookback_days=max(args.failure_lookback_days, 1),
        min_failure_count=max(args.min_failure_count, 1),
        single_batch=args.single_batch,
        max_retry_rounds=max(args.max_retry_rounds, 0),
        dry_run=args.dry_run,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
    )


def resolve_symbols_to_sync(
    conn: psycopg.Connection,
    config: UniverseSyncConfig,
) -> list[str]:
    if config.explicit_symbols:
        symbols = config.explicit_symbols
    else:
        symbols = load_symbols_from_db(
            conn,
            active_only=True,
            limit=config.universe_limit,
        )
        if config.exclude_stale_failures:
            symbols = filter_stale_failure_symbols(
                conn,
                symbols,
                provider=config.provider,
                stale_days=config.stale_days,
                failure_lookback_days=config.failure_lookback_days,
                min_failure_count=config.min_failure_count,
            )
    return filter_symbols_by_prefixes(symbols, config.symbol_prefixes)


def main() -> int:
    config = parse_args()

    try:
        with config.open_connection() as conn:
            ensure_schema(conn)

            if config.refresh_universe:
                universe_rows = fetch_universe_rows(
                    config.universe_source,
                    limit=config.universe_limit,
                )
                refreshed = upsert_universe_rows(
                    conn,
                    universe_rows,
                    source=config.universe_source,
                    deactivate_missing=config.deactivate_missing,
                )
                print(
                    f"{_TAG} Universe refresh complete: {refreshed} rows upserted",
                    flush=True,
                )
                if config.refresh_only:
                    print(f"{_TAG} Refresh-only mode complete.", flush=True)
                    return 0

            symbols = resolve_symbols_to_sync(conn, config)
            if not symbols:
                print(f"{_TAG} No symbols resolved for syncing.", flush=True)
                return 0

            checkpoint_name = checkpoint_job_name(config)
            checkpoint = None
            if checkpoint_name and not config.dry_run:
                checkpoint = load_checkpoint(conn, checkpoint_name)
                if checkpoint and checkpoint.get("next_symbol"):
                    print(
                        f"{_TAG} Resuming from checkpoint at symbol {checkpoint['next_symbol']}.",
                        flush=True,
                    )

            ordered_symbols = rotate_symbols_from_checkpoint(
                symbols,
                checkpoint["next_symbol"] if checkpoint else None,
            )
            latest_dates = get_latest_price_dates(conn, symbols, config.provider)
            total_rows = 0
            requests_used = 0
            failed_symbols: list[str] = []
            stopped_early = False

            batches = [
                ordered_symbols[idx: idx + config.request_budget]
                for idx in range(0, len(ordered_symbols), config.request_budget)
            ]

            for batch_index, batch_symbols in enumerate(batches, start=1):
                if config.single_batch and batch_index > 1:
                    stopped_early = True
                    next_symbol = batch_symbols[0]
                    if checkpoint_name and not config.dry_run:
                        save_checkpoint(
                            conn,
                            job_name=checkpoint_name,
                            universe_source=config.universe_source,
                            provider=config.provider,
                            next_symbol=next_symbol,
                            total_symbols=len(symbols),
                            last_completed_symbol=None,
                            last_status="paused_single_batch",
                        )
                    print(
                        f"{_TAG} Single-batch mode reached the next batch at {next_symbol}; stopping.",
                        flush=True,
                    )
                    break

                if len(batches) > 1:
                    print(
                        f"{_TAG} Starting batch {batch_index}/{len(batches)} ({len(batch_symbols)} symbols).",
                        flush=True,
                    )

                if config.dry_run:
                    for symbol in batch_symbols:
                        latest_date = latest_dates.get(symbol)
                        fetch_start = resolve_fetch_start(config.start_date, latest_date)
                        mode = "DRY RUN" if fetch_start <= config.end_date else "SKIP"
                        print(f"[{mode}] {symbol}: {fetch_start} -> {config.end_date}")
                    continue

                if config.batch_download or config.concurrency > 1:
                    batch_requests, batch_rows, batch_failures = sync_batch_concurrently(
                        config,
                        conn,
                        batch_symbols,
                        latest_dates,
                    )
                    requests_used += batch_requests
                    total_rows += batch_rows
                    failed_symbols.extend(batch_failures)

                    if checkpoint_name:
                        next_index = batch_index * config.request_budget
                        next_symbol = (
                            ordered_symbols[next_index]
                            if next_index < len(ordered_symbols)
                            else None
                        )
                        save_checkpoint(
                            conn,
                            job_name=checkpoint_name,
                            universe_source=config.universe_source,
                            provider=config.provider,
                            next_symbol=next_symbol,
                            total_symbols=len(symbols),
                            last_completed_symbol=batch_symbols[-1] if batch_symbols else None,
                            last_status=(
                                "batch_completed_with_errors"
                                if batch_failures
                                else "batch_complete"
                            ),
                        )
                    continue

                for index_in_batch, symbol in enumerate(batch_symbols):
                    global_index = (batch_index - 1) * config.request_budget + index_in_batch
                    next_symbol = (
                        ordered_symbols[global_index + 1]
                        if global_index + 1 < len(ordered_symbols)
                        else None
                    )

                    latest_date = latest_dates.get(symbol)

                    rows_written, status, err_detail = sync_symbol_with_latest(
                        conn,
                        symbol,
                        config.provider,
                        config.start_date,
                        config.end_date,
                        latest_date=latest_date,
                    )

                    if status == "success":
                        requests_used += 1
                        total_rows += rows_written
                        latest_dates[symbol] = min(config.end_date, date.today())
                        print(f"[OK] {symbol}: wrote {rows_written} rows.", flush=True)
                    elif status == "skipped_up_to_date":
                        print(f"[SKIP] {symbol}: database is already up to date.", flush=True)
                    else:
                        requests_used += 1
                        failed_symbols.append(symbol)
                        print(f"[ERR] {symbol}: sync failed — {err_detail}", file=sys.stderr)

                    if checkpoint_name:
                        save_checkpoint(
                            conn,
                            job_name=checkpoint_name,
                            universe_source=config.universe_source,
                            provider=config.provider,
                            next_symbol=next_symbol,
                            total_symbols=len(symbols),
                            last_completed_symbol=symbol,
                            last_status=status,
                        )

                    if config.sleep_seconds:
                        time.sleep(config.sleep_seconds)

            if failed_symbols and not config.dry_run:
                pending_failures = failed_symbols
                for retry_round in range(1, config.max_retry_rounds + 1):
                    if not pending_failures:
                        break

                    print(
                        f"{_TAG} Retry round {retry_round}/{config.max_retry_rounds} for {len(pending_failures)} failed symbols.",
                        flush=True,
                    )
                    current_round = pending_failures
                    pending_failures = []

                    if config.batch_download or config.concurrency > 1:
                        retry_requests, retry_rows, retry_failures = sync_batch_concurrently(
                            config,
                            conn,
                            current_round,
                            latest_dates,
                        )
                        requests_used += retry_requests
                        total_rows += retry_rows
                        pending_failures.extend(retry_failures)
                    else:
                        for symbol in current_round:
                            latest_date = latest_dates.get(symbol)
                            rows_written, status, err_detail = sync_symbol_with_latest(
                                conn,
                                symbol,
                                config.provider,
                                config.start_date,
                                config.end_date,
                                latest_date=latest_date,
                            )

                            if status == "success":
                                requests_used += 1
                                total_rows += rows_written
                                latest_dates[symbol] = min(config.end_date, date.today())
                                print(f"[RETRY-OK] {symbol}: wrote {rows_written} rows.", flush=True)
                            elif status == "skipped_up_to_date":
                                print(f"[RETRY-SKIP] {symbol}: database is already up to date.", flush=True)
                            else:
                                requests_used += 1
                                pending_failures.append(symbol)
                                print(f"[RETRY-ERR] {symbol}: sync failed — {err_detail}", file=sys.stderr)

                            if config.sleep_seconds:
                                time.sleep(config.sleep_seconds)

                failed_symbols = pending_failures

            if (
                checkpoint_name
                and not config.dry_run
                and not stopped_early
                and not failed_symbols
            ):
                mark_checkpoint_complete(conn, checkpoint_name)

            print(
                f"{_TAG} Finished. Symbols in universe: {len(symbols)}. "
                f"Requests used: {requests_used}. Rows written: {total_rows}. "
                f"Remaining failures: {len(failed_symbols)}.",
                flush=True,
            )
    except psycopg.Error as exc:
        print(f"{_TAG} Database error: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"{_TAG} Universe fetch error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
