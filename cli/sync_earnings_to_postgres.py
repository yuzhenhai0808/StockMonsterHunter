#!/usr/bin/env python3
"""Sync upcoming and recent earnings dates from yfinance into PostgreSQL.

Reads symbols from ``market_data.us_equity_symbols`` (or ``--symbols``),
calls ``yfinance.Ticker(sym).get_earnings_dates(limit=4)``, and upserts
into ``market_data.us_equity_earnings``. Reuses the same DB connection
helper and sync_runs audit log as the OHLCV sync scripts.

Typical daily invocation (runs after OHLCV sync, current week only):

    python cli/sync_earnings_to_postgres.py --use-db-universe --calendar-week

Sample / dry-run:

    python cli/sync_earnings_to_postgres.py --symbols POET,NVTS,ARM,AMD
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

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


_TAG = "[EarningsSync]"

PROVIDER = "yfinance"
RUN_PROVIDER = "yfinance_earnings"

DEFAULT_SLEEP_SECONDS = 0.35
DEFAULT_CONCURRENCY = 3
DEFAULT_LIMIT = 4
DEFAULT_REQUEST_BUDGET = 0  # 0 = unlimited
DEFAULT_SKIP_RECENT_HOURS = 12

_NO_EARNINGS_DATA_MARKERS = (
    "earnings date",
    "no earnings dates found",
    "possibly delisted",
    "no timezone found",
)


UPSERT_SQL = """
INSERT INTO market_data.us_equity_earnings (
    symbol,
    earnings_date,
    eps_estimate,
    eps_reported,
    eps_surprise_pct,
    timing,
    is_estimated,
    provider
)
VALUES (
    %(symbol)s,
    %(earnings_date)s,
    %(eps_estimate)s,
    %(eps_reported)s,
    %(eps_surprise_pct)s,
    %(timing)s,
    %(is_estimated)s,
    %(provider)s
)
ON CONFLICT (symbol, earnings_date, provider) DO UPDATE
SET
    eps_estimate = EXCLUDED.eps_estimate,
    eps_reported = EXCLUDED.eps_reported,
    eps_surprise_pct = EXCLUDED.eps_surprise_pct,
    timing = EXCLUDED.timing,
    is_estimated = EXCLUDED.is_estimated,
    fetched_at = NOW();
"""


DELETE_PAST_ESTIMATES_SQL = """
DELETE FROM market_data.us_equity_earnings
WHERE symbol = %s
  AND provider = %s
  AND earnings_date >= CURRENT_DATE - 30
  AND fetched_at < NOW() - INTERVAL '12 hours';
"""


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


def _extract_timing(ts: Any) -> str | None:
    if ts is None:
        return None
    try:
        hour = ts.hour
    except AttributeError:
        return None
    if hour == 0:
        return None
    if hour < 12:
        return "bmo"
    return "amc"


def _extract_date(ts: Any) -> date | None:
    if ts is None:
        return None
    try:
        return ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
    except Exception:
        return None


def _is_no_earnings_data_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _NO_EARNINGS_DATA_MARKERS)


def _calendar_get(calendar: Any, key: str) -> Any:
    if isinstance(calendar, dict):
        return calendar.get(key)
    try:
        return calendar.get(key)
    except Exception:
        return None


def fetch_symbol_earnings(
    symbol: str,
    limit: int = DEFAULT_LIMIT,
    *,
    window_start: date | None = None,
    window_end: date | None = None,
) -> list[dict[str, Any]]:
    """Return a normalized list of earnings records for one symbol.

    Combines two yfinance sources:
    - ``Ticker.get_earnings_dates(limit=...)`` — historical prints only
    - ``Ticker.calendar`` — the single next upcoming earnings date (estimate only)

    Empty list on any fetch error or missing data (caller logs the context).
    """
    ticker = yf.Ticker(symbol)
    try:
        df = ticker.get_earnings_dates(limit=limit)
    except Exception as exc:  # yfinance is flaky — never let one symbol kill the batch
        if _is_no_earnings_data_error(exc):
            return []
        raise RuntimeError(f"yfinance fetch failed: {exc}") from exc

    today = date.today()
    records: list[dict[str, Any]] = []
    seen_dates: set[date] = set()

    if df is not None and not df.empty:
        for ts, row in df.iterrows():
            earnings_date = _extract_date(ts)
            if earnings_date is None or earnings_date in seen_dates:
                continue
            if window_start is not None and earnings_date < window_start:
                continue
            if window_end is not None and earnings_date > window_end:
                continue
            seen_dates.add(earnings_date)
            eps_estimate = _finite_float(row.get("EPS Estimate"))
            eps_reported = _finite_float(row.get("Reported EPS"))
            eps_surprise = _finite_float(row.get("Surprise(%)"))
            timing = _extract_timing(ts)
            is_estimated = eps_reported is None or earnings_date > today
            records.append(
                {
                    "symbol": symbol.upper(),
                    "earnings_date": earnings_date,
                    "eps_estimate": eps_estimate,
                    "eps_reported": eps_reported,
                    "eps_surprise_pct": eps_surprise,
                    "timing": timing,
                    "is_estimated": is_estimated,
                    "provider": PROVIDER,
                }
            )

    # Ticker.calendar exposes the next upcoming earnings date;
    # get_earnings_dates only returns historical prints. Merge as an estimate.
    try:
        calendar = ticker.calendar
    except Exception:
        calendar = None
    if calendar is not None:
        dates = _calendar_get(calendar, "Earnings Date") or []
        if not isinstance(dates, (list, tuple)):
            dates = [dates]
        for raw_date in dates:
            earnings_date = _extract_date(raw_date)
            if earnings_date is None or earnings_date < today:
                continue
            if window_start is not None and earnings_date < window_start:
                continue
            if window_end is not None and earnings_date > window_end:
                continue
            if earnings_date in seen_dates:
                continue
            seen_dates.add(earnings_date)
            records.append(
                {
                    "symbol": symbol.upper(),
                    "earnings_date": earnings_date,
                    "eps_estimate": _finite_float(_calendar_get(calendar, "Earnings Average")),
                    "eps_reported": None,
                    "eps_surprise_pct": None,
                    "timing": None,
                    "is_estimated": True,
                    "provider": PROVIDER,
                }
            )

    return records


@dataclass
class EarningsFetchResult:
    symbol: str
    records: list[dict[str, Any]]
    error: str | None = None


def load_universe_from_db(conn: Any) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol FROM market_data.us_equity_symbols
            WHERE is_active = TRUE
            ORDER BY symbol;
            """
        )
        return [row["symbol"] for row in cur.fetchall()]


def current_week_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def relative_window_bounds(
    *,
    lookback_days: int,
    lookahead_days: int,
    today: date | None = None,
) -> tuple[date, date]:
    today = today or date.today()
    return today - timedelta(days=lookback_days), today + timedelta(days=lookahead_days)


def parse_window_date(raw_value: str, arg_name: str) -> date | None:
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise SystemExit(f"{arg_name} must be YYYY-MM-DD, got: {raw_value}") from exc


def load_symbols_for_earnings_window(
    conn: Any,
    *,
    start_date: date,
    end_date: date,
) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT e.symbol
            FROM market_data.us_equity_earnings e
            JOIN market_data.us_equity_symbols s
              ON s.symbol = e.symbol
            WHERE s.is_active = TRUE
              AND e.provider = %s
              AND e.earnings_date BETWEEN %s AND %s
            ORDER BY e.symbol;
            """,
            (PROVIDER, start_date, end_date),
        )
        return [row["symbol"] for row in cur.fetchall()]


def filter_recently_fetched_window_symbols(
    conn: Any,
    symbols: list[str],
    *,
    start_date: date,
    end_date: date,
    skip_recent_hours: int,
) -> list[str]:
    if not symbols or skip_recent_hours <= 0:
        return symbols

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT symbol
            FROM market_data.us_equity_earnings
            WHERE provider = %s
              AND symbol = ANY(%s)
              AND earnings_date BETWEEN %s AND %s
              AND fetched_at >= NOW() - (%s * INTERVAL '1 hour');
            """,
            (PROVIDER, symbols, start_date, end_date, skip_recent_hours),
        )
        fresh_symbols = {row["symbol"] for row in cur.fetchall()}
    if not fresh_symbols:
        return symbols

    filtered = [symbol for symbol in symbols if symbol not in fresh_symbols]
    print(
        f"{_TAG} skipped {len(fresh_symbols)} symbol(s) fetched within "
        f"{skip_recent_hours}h for {start_date}..{end_date}; "
        f"{len(filtered)}/{len(symbols)} remain.",
        flush=True,
    )
    return filtered


def record_run(
    conn: Any,
    symbol: str,
    rows_written: int,
    status: str,
    error_message: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            INSERT_RUN_SQL,
            (
                RUN_PROVIDER,
                symbol,
                None,
                None,
                rows_written,
                status,
                error_message[:1000] if error_message else None,
            ),
        )


def sync_one_symbol(
    conn: Any,
    symbol: str,
    *,
    limit: int,
    window_start: date | None = None,
    window_end: date | None = None,
) -> int:
    records = fetch_symbol_earnings(
        symbol,
        limit=limit,
        window_start=window_start,
        window_end=window_end,
    )
    if not records:
        record_run(conn, symbol, 0, "skipped_no_data")
        return 0
    with conn.cursor() as cur:
        cur.execute(DELETE_PAST_ESTIMATES_SQL, (symbol.upper(), PROVIDER))
        for record in records:
            cur.execute(UPSERT_SQL, record)
    record_run(conn, symbol, len(records), "success")
    return len(records)


def fetch_earnings_worker(
    symbol: str,
    *,
    limit: int,
    sleep_seconds: float,
    window_start: date | None = None,
    window_end: date | None = None,
    initial_delay: float = 0.0,
) -> EarningsFetchResult:
    delay = max(initial_delay, 0.0) + max(sleep_seconds, 0.0)
    if delay > 0:
        time.sleep(delay)
    try:
        records = fetch_symbol_earnings(
            symbol,
            limit=limit,
            window_start=window_start,
            window_end=window_end,
        )
        return EarningsFetchResult(symbol=symbol, records=records)
    except Exception as exc:
        return EarningsFetchResult(
            symbol=symbol,
            records=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def persist_earnings_records(conn: Any, symbol: str, records: list[dict[str, Any]]) -> int:
    if not records:
        record_run(conn, symbol, 0, "skipped_no_data")
        return 0
    with conn.cursor() as cur:
        cur.execute(DELETE_PAST_ESTIMATES_SQL, (symbol.upper(), PROVIDER))
        for record in records:
            cur.execute(UPSERT_SQL, record)
    record_run(conn, symbol, len(records), "success")
    return len(records)


def sync_symbols(
    symbols: Iterable[str],
    *,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: int = DEFAULT_LIMIT,
    request_budget: int = DEFAULT_REQUEST_BUDGET,
    window_start: date | None = None,
    window_end: date | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    stats = {"symbols": 0, "rows_written": 0, "success": 0, "failed": 0, "skipped": 0}
    symbol_list = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if request_budget:
        symbol_list = symbol_list[:request_budget]
    stats["symbols"] = len(symbol_list)

    with open_db() as conn:
        ensure_schema(conn)
        conn.commit()
        if dry_run:
            for symbol in symbol_list:
                print(f"{_TAG} [dry-run] would fetch {symbol}", flush=True)
                record_run(conn, symbol, 0, "dry_run")
            conn.commit()
            return stats

        worker_count = max(1, min(concurrency, 8, len(symbol_list)))
        print(
            f"{_TAG} fetching {len(symbol_list)} symbol(s) with {worker_count} worker(s).",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(
                    fetch_earnings_worker,
                    symbol,
                    limit=limit,
                    sleep_seconds=sleep_seconds,
                    window_start=window_start,
                    window_end=window_end,
                    initial_delay=(index % worker_count) * max(sleep_seconds, 0.0) / worker_count,
                ): symbol
                for index, symbol in enumerate(symbol_list)
            }
            for future in as_completed(futures):
                result = future.result()
                if result.error:
                    stats["failed"] += 1
                    print(f"{_TAG} {result.symbol}: FAILED - {result.error}", flush=True)
                    try:
                        record_run(conn, result.symbol, 0, "failed", result.error)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    continue

                try:
                    written = persist_earnings_records(conn, result.symbol, result.records)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    stats["failed"] += 1
                    error_text = f"{type(exc).__name__}: {exc}"
                    print(f"{_TAG} {result.symbol}: FAILED - {error_text}", flush=True)
                    try:
                        record_run(conn, result.symbol, 0, "failed", error_text)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    continue

                stats["rows_written"] += written
                if written:
                    stats["success"] += 1
                    print(f"{_TAG} {result.symbol}: wrote {written} rows", flush=True)
                else:
                    stats["skipped"] += 1
                    print(f"{_TAG} {result.symbol}: no data", flush=True)

    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync upcoming/recent earnings dates from yfinance."
    )
    parser.add_argument("--symbols", help="Comma-separated tickers (overrides --use-db-universe).")
    parser.add_argument(
        "--use-db-universe",
        action="store_true",
        help="Read active symbols from market_data.us_equity_symbols.",
    )
    parser.add_argument(
        "--calendar-week",
        action="store_true",
        help=(
            "When used with --use-db-universe, only refresh symbols already in "
            "the earnings table for the current Monday-Sunday week."
        ),
    )
    parser.add_argument(
        "--calendar-window",
        action="store_true",
        help=(
            "When used with --use-db-universe, only refresh symbols already in "
            "the earnings table for a rolling date window."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Rolling earnings window lookback when --calendar-window is used. Default: 7.",
    )
    parser.add_argument(
        "--lookahead-days",
        type=int,
        default=7,
        help="Rolling earnings window lookahead when --calendar-window is used. Default: 7.",
    )
    parser.add_argument(
        "--window-start-date",
        default="",
        help="Explicit earnings window start date in YYYY-MM-DD. Overrides --lookback-days.",
    )
    parser.add_argument(
        "--window-end-date",
        default="",
        help="Explicit earnings window end date in YYYY-MM-DD. Overrides --lookahead-days.",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Earnings rows to pull per symbol.")
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Number of earnings fetch workers. Default: {DEFAULT_CONCURRENCY}.",
    )
    parser.add_argument(
        "--request-budget",
        type=int,
        default=DEFAULT_REQUEST_BUDGET,
        help="Max yfinance calls this run (0 = unlimited).",
    )
    parser.add_argument(
        "--skip-recent-hours",
        type=int,
        default=DEFAULT_SKIP_RECENT_HOURS,
        help=(
            "Skip symbols with earnings rows in the selected window fetched within "
            f"this many hours. Default: {DEFAULT_SKIP_RECENT_HOURS}; 0 disables."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    explicit_window_start = parse_window_date(args.window_start_date, "--window-start-date")
    explicit_window_end = parse_window_date(args.window_end_date, "--window-end-date")
    selected_window: tuple[date, date] | None = None

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.use_db_universe:
        with open_db() as conn:
            ensure_schema(conn)
            conn.commit()
            if args.calendar_week and args.calendar_window:
                print(
                    f"{_TAG} use only one of --calendar-week or --calendar-window.",
                    file=sys.stderr,
                )
                return 2
            if args.calendar_window:
                if explicit_window_start is not None or explicit_window_end is not None:
                    today = date.today()
                    window_start = explicit_window_start or today
                    window_end = explicit_window_end or today
                else:
                    window_start, window_end = relative_window_bounds(
                        lookback_days=max(args.lookback_days, 0),
                        lookahead_days=max(args.lookahead_days, 0),
                    )
                if window_start > window_end:
                    print(
                        f"{_TAG} earnings window start cannot be after end: "
                        f"{window_start} > {window_end}.",
                        file=sys.stderr,
                    )
                    return 2
                selected_window = (window_start, window_end)
                symbols = load_symbols_for_earnings_window(
                    conn,
                    start_date=window_start,
                    end_date=window_end,
                )
                symbols = filter_recently_fetched_window_symbols(
                    conn,
                    symbols,
                    start_date=window_start,
                    end_date=window_end,
                    skip_recent_hours=max(args.skip_recent_hours, 0),
                )
                print(
                    f"{_TAG} loaded {len(symbols)} symbols from earnings rolling window "
                    f"{window_start}..{window_end}.",
                    flush=True,
                )
            elif args.calendar_week:
                week_start, week_end = current_week_bounds()
                symbols = load_symbols_for_earnings_window(
                    conn,
                    start_date=week_start,
                    end_date=week_end,
                )
                print(
                    f"{_TAG} loaded {len(symbols)} symbols from earnings window "
                    f"{week_start}..{week_end}.",
                    flush=True,
                )
            else:
                symbols = load_universe_from_db(conn)
                print(f"{_TAG} loaded {len(symbols)} symbols from DB universe.", flush=True)
    else:
        print(
            f"{_TAG} provide --symbols or --use-db-universe.",
            file=sys.stderr,
        )
        return 2

    if not symbols:
        print(f"{_TAG} no symbols to sync.", flush=True)
        return 0

    started = datetime.now(timezone.utc)
    stats = sync_symbols(
        symbols,
        sleep_seconds=args.sleep_seconds,
        concurrency=args.concurrency,
        limit=args.limit,
        request_budget=args.request_budget,
        window_start=selected_window[0] if selected_window else explicit_window_start,
        window_end=selected_window[1] if selected_window else explicit_window_end,
        dry_run=args.dry_run,
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(
        f"{_TAG} done in {elapsed:.1f}s: "
        f"symbols={stats['symbols']} success={stats['success']} "
        f"skipped={stats['skipped']} failed={stats['failed']} "
        f"rows_written={stats['rows_written']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
