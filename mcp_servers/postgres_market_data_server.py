#!/usr/bin/env python3
"""MCP server for local TradingAgents PostgreSQL market data.

The connection settings intentionally reuse the sync script's ``open_db()``
resolver, so explicit environment variables and the project defaults behave the
same way as ``cli/sync_us_universe_to_postgres.py``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")

from mcp.server.fastmcp import FastMCP

from cli.fetch_fmp_openbb_to_postgres import open_db
from cli.recommend_long_candidates import (
    DEFAULT_PRICE_BUCKETS,
    ScreenParams,
    parse_price_buckets,
    run_recommendation_bucketed,
)


mcp = FastMCP("tradingagents-market-data")

_FORBIDDEN_SQL = re.compile(
    r"\b("
    r"alter|analyze|call|comment|copy|create|delete|drop|execute|grant|"
    r"insert|merge|refresh|reindex|replace|revoke|truncate|update|vacuum"
    r")\b",
    re.IGNORECASE,
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default, indent=2)


def _rows_to_json(rows: list[dict[str, Any]], *, limit: int | None = None) -> str:
    selected = rows if limit is None else rows[:limit]
    return _to_json(selected)


def _validate_readonly_sql(query: str) -> str:
    sql = query.strip().rstrip(";")
    if not sql:
        raise ValueError("SQL query is empty.")
    if ";" in sql:
        raise ValueError("Only one SQL statement is allowed.")
    if _FORBIDDEN_SQL.search(sql):
        raise ValueError("Only read-only SELECT/WITH/EXPLAIN queries are allowed.")
    if not re.match(r"^(select|with|explain)\b", sql, flags=re.IGNORECASE):
        raise ValueError("Query must start with SELECT, WITH, or EXPLAIN.")
    return sql


def _split_symbols(symbols: str, *, max_count: int = 50) -> list[str]:
    normalized = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    if not normalized:
        raise ValueError("Provide at least one symbol.")
    if len(normalized) > max_count:
        raise ValueError(f"Too many symbols (max {max_count}).")
    return normalized


def _float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


@mcp.tool()
def db_health() -> str:
    """Return database connection info and row freshness by provider.

    Providers with sparse coverage (<100 symbols) are flagged
    ``usable_for_screening=false`` so LLMs do not accidentally screen against
    them. Earnings calendar and fundamentals coverage are summarised too, so
    tools that depend on them (``earnings_dates``, ``value_candidates``) can
    be gated.
    """
    sql = """
    SELECT
        current_database() AS database,
        current_user AS user_name,
        inet_server_addr()::text AS server_addr,
        inet_server_port() AS server_port;
    """
    freshness_sql = """
    SELECT
        provider,
        MAX(price_date) AS latest_date,
        COUNT(*) AS rows,
        COUNT(DISTINCT symbol) AS symbols
    FROM market_data.us_equity_daily_prices
    GROUP BY provider
    ORDER BY latest_date DESC, provider;
    """
    earnings_sql = """
    SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT symbol) AS symbols,
        MAX(fetched_at) AS last_sync,
        COUNT(*) FILTER (WHERE earnings_date >= CURRENT_DATE) AS upcoming_rows
    FROM market_data.us_equity_earnings;
    """
    fundamentals_sql = """
    SELECT
        (SELECT COUNT(DISTINCT symbol) FROM market_data.us_equity_company_profile
            WHERE as_of_date >= CURRENT_DATE - 3) AS profile_symbols,
        (SELECT MAX(fetched_at) FROM market_data.us_equity_company_profile) AS profile_last_sync,
        (SELECT COUNT(DISTINCT symbol) FROM market_data.us_equity_quarterly_financials) AS quarterly_symbols,
        (SELECT MAX(fetched_at) FROM market_data.us_equity_quarterly_financials) AS quarterly_last_sync;
    """
    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            info = cur.fetchone()
            cur.execute(freshness_sql)
            freshness = cur.fetchall()
            cur.execute(earnings_sql)
            earnings = cur.fetchone()
            cur.execute(fundamentals_sql)
            fundamentals = cur.fetchone()
    for row in freshness:
        row["usable_for_screening"] = (row.get("symbols") or 0) >= 100
    if fundamentals is not None:
        fundamentals["usable_for_value_screening"] = (
            (fundamentals.get("profile_symbols") or 0) >= 1000
        )
    return _to_json(
        {
            "connection": info,
            "freshness": freshness,
            "earnings_calendar": earnings,
            "fundamentals_coverage": fundamentals,
        }
    )


@mcp.tool()
def list_market_tables() -> str:
    """List tables in the market_data schema."""
    sql = """
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema = 'market_data'
    ORDER BY table_name;
    """
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return _rows_to_json(rows)


@mcp.tool()
def describe_table(table_name: str) -> str:
    """Describe columns for a market_data table."""
    sql = """
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'market_data' AND table_name = %s
    ORDER BY ordinal_position;
    """
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(sql, (table_name,))
        rows = cur.fetchall()
    return _rows_to_json(rows)


@mcp.tool()
def latest_prices(symbols: str, provider: str = "yfinance") -> str:
    """Return latest OHLCV rows plus prev_close, 1d/5d change, 20d volume ratio.

    For each requested symbol returns the most recent daily bar together with
    ``change_pct`` (vs prev close), ``change_5d_pct`` and ``volume_ratio_20d``
    so a simple quote check never needs follow-up SQL.
    """
    normalized = _split_symbols(symbols, max_count=50)

    sql = """
    WITH history AS (
        SELECT
            symbol,
            price_date,
            open, high, low, close, volume, provider,
            LAG(close, 1) OVER (PARTITION BY symbol ORDER BY price_date) AS prev_close,
            LAG(close, 5) OVER (PARTITION BY symbol ORDER BY price_date) AS close_5d_ago,
            AVG(volume) OVER (
                PARTITION BY symbol ORDER BY price_date
                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ) AS avg_volume_20d,
            ROW_NUMBER() OVER (
                PARTITION BY symbol, provider ORDER BY price_date DESC
            ) AS rn
        FROM market_data.us_equity_daily_prices
        WHERE provider = %s AND symbol = ANY(%s)
    )
    SELECT
        symbol, price_date, open, high, low, close, volume, provider,
        prev_close, close_5d_ago, avg_volume_20d
    FROM history
    WHERE rn = 1
    ORDER BY symbol;
    """
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(sql, (provider, normalized))
        rows = cur.fetchall()

    enriched: list[dict[str, Any]] = []
    for row in rows:
        close = _float(row.get("close"))
        prev = _float(row.get("prev_close"))
        close_5d = _float(row.get("close_5d_ago"))
        avg_vol = _float(row.get("avg_volume_20d"))
        vol = _float(row.get("volume"))

        change_pct = None
        if close is not None and prev not in (None, 0):
            change_pct = round((close / prev - 1) * 100, 4)
        change_5d_pct = None
        if close is not None and close_5d not in (None, 0):
            change_5d_pct = round((close / close_5d - 1) * 100, 4)
        volume_ratio_20d = None
        if vol is not None and avg_vol not in (None, 0):
            volume_ratio_20d = round(vol / avg_vol, 4)

        enriched.append(
            {
                "symbol": row.get("symbol"),
                "price_date": row.get("price_date"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
                "provider": row.get("provider"),
                "prev_close": row.get("prev_close"),
                "change_pct": change_pct,
                "change_5d_pct": change_5d_pct,
                "volume_ratio_20d": volume_ratio_20d,
            }
        )
    return _to_json(enriched)


@mcp.tool()
def query_market_data(query: str, max_rows: int = 200) -> str:
    """Execute one read-only SQL query and return rows as JSON."""
    sql = _validate_readonly_sql(query)
    row_limit = max(1, min(max_rows, 1000))
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchmany(row_limit)
    return _rows_to_json(rows)


_MONSTER_WEEKLY_BACKTEST_SQL = """
WITH settings AS (
    SELECT
        %(lookback_trading_days)s::int AS lookback_trading_days,
        %(min_ret_pct)s::numeric AS min_ret_pct,
        %(min_volume)s::bigint AS min_volume,
        %(min_close)s::numeric AS min_close,
        %(provider)s::text AS provider
),
trading_days AS (
    SELECT DISTINCT price_date
    FROM market_data.us_equity_daily_prices
    WHERE provider = (SELECT provider FROM settings)
    ORDER BY price_date DESC
    LIMIT (SELECT lookback_trading_days FROM settings)
),
daily AS (
    SELECT
        p.symbol,
        p.price_date,
        p.open,
        p.high,
        p.low,
        p.close,
        p.volume,
        p.close * p.volume AS dollar_volume,
        LAG(p.close, 1) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close,
        LAG(p.close, 2) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_2d,
        LAG(p.close, 5) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_5d,
        LAG(p.volume, 1) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_volume,
        AVG(p.volume) OVER (
            PARTITION BY p.symbol ORDER BY p.price_date
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS avg_volume_20_prev,
        AVG(p.close * p.volume) OVER (
            PARTITION BY p.symbol ORDER BY p.price_date
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS avg_dollar_20_prev
    FROM market_data.us_equity_daily_prices p
    WHERE p.provider = (SELECT provider FROM settings)
      AND p.price_date >= (SELECT min(price_date) FROM trading_days) - INTERVAL '40 days'
),
movers AS (
    SELECT
        d.*,
        100 * (d.close - d.prev_close) / NULLIF(d.prev_close, 0) AS ret_1d_pct,
        100 * (d.open - d.prev_close) / NULLIF(d.prev_close, 0) AS gap_open_pct,
        100 * (d.high - d.prev_close) / NULLIF(d.prev_close, 0) AS max_intraday_from_prev_pct,
        100 * (d.prev_close - d.close_2d) / NULLIF(d.close_2d, 0) AS prior_1d_pct,
        100 * (d.prev_close - d.close_5d) / NULLIF(d.close_5d, 0) AS prior_4d_pct,
        d.volume::numeric / NULLIF(d.avg_volume_20_prev, 0) AS volume_ratio_vs_prev20,
        d.dollar_volume / 1000000 AS dollar_volume_m,
        d.avg_dollar_20_prev / 1000000 AS avg_dollar_20_prev_m,
        d.prev_close * d.prev_volume / 1000000 AS prev_dollar_volume_m
    FROM daily d
    WHERE d.price_date IN (SELECT price_date FROM trading_days)
      AND d.prev_close IS NOT NULL
      AND d.close >= (SELECT min_close FROM settings)
      AND d.volume >= (SELECT min_volume FROM settings)
),
profile_latest AS (
    SELECT DISTINCT ON (symbol)
        symbol,
        sector,
        industry,
        market_cap,
        float_shares,
        short_percent_of_float,
        fifty_two_week_high
    FROM market_data.us_equity_company_profile
    WHERE provider = (SELECT provider FROM settings)
    ORDER BY symbol, as_of_date DESC
),
scored AS (
    SELECT
        m.*,
        cp.sector,
        cp.industry,
        cp.market_cap,
        cp.float_shares,
        cp.short_percent_of_float,
        cp.fifty_two_week_high,
        (
            CASE WHEN COALESCE(m.prev_dollar_volume_m, 0) < 5 THEN 1 ELSE 0 END +
            CASE WHEN m.volume_ratio_vs_prev20 >= 5
                   OR m.dollar_volume_m >= COALESCE(m.avg_dollar_20_prev_m, 0) * 5
                 THEN 1 ELSE 0 END +
            CASE WHEN m.gap_open_pct BETWEEN 3 AND 30 THEN 1 ELSE 0 END +
            CASE WHEN cp.float_shares IS NOT NULL AND cp.float_shares < 30000000 THEN 1 ELSE 0 END +
            CASE WHEN m.prior_1d_pct < 0 OR m.prior_4d_pct < 0 THEN 1 ELSE 0 END +
            CASE WHEN m.max_intraday_from_prev_pct >= 40 THEN 1 ELSE 0 END
        ) AS visible_quant_signal_count
    FROM movers m
    LEFT JOIN profile_latest cp ON cp.symbol = m.symbol
    WHERE m.ret_1d_pct >= (SELECT min_ret_pct FROM settings)
)
SELECT
    symbol,
    price_date,
    ROUND(prev_close::numeric, 4) AS prev_close,
    ROUND(open::numeric, 4) AS open,
    ROUND(high::numeric, 4) AS high,
    ROUND(low::numeric, 4) AS low,
    ROUND(close::numeric, 4) AS close,
    ROUND(ret_1d_pct::numeric, 2) AS ret_1d_pct,
    ROUND(gap_open_pct::numeric, 2) AS gap_open_pct,
    ROUND(max_intraday_from_prev_pct::numeric, 2) AS max_intraday_from_prev_pct,
    ROUND(prior_1d_pct::numeric, 2) AS prior_1d_pct,
    ROUND(prior_4d_pct::numeric, 2) AS prior_4d_pct,
    volume,
    ROUND(volume_ratio_vs_prev20::numeric, 2) AS volume_ratio_vs_prev20,
    ROUND(dollar_volume_m::numeric, 2) AS dollar_volume_m,
    ROUND(avg_dollar_20_prev_m::numeric, 2) AS avg_dollar_20_prev_m,
    ROUND(prev_dollar_volume_m::numeric, 2) AS prev_dollar_volume_m,
    ROUND((market_cap / 1000000.0)::numeric, 0) AS market_cap_m,
    ROUND((float_shares / 1000000.0)::numeric, 1) AS float_m,
    short_percent_of_float,
    sector,
    industry,
    visible_quant_signal_count,
    CASE
        WHEN gap_open_pct > 40 THEN '盘前/开盘已飞:只能接棒或审计'
        WHEN gap_open_pct BETWEEN 3 AND 30 AND visible_quant_signal_count >= 4 THEN '可提前:盘前点火候选'
        WHEN prior_1d_pct < 0 AND volume_ratio_vs_prev20 >= 5 THEN '可提前:弱势反转爆量候选'
        WHEN prior_4d_pct >= 40 THEN '接棒:前期已强'
        ELSE '需Web催化确认或不可提前'
    END AS replay_bucket
FROM scored
ORDER BY price_date, ret_1d_pct DESC
LIMIT %(row_limit)s;
"""


@mcp.tool()
def monster_weekly_backtest(
    lookback_trading_days: int = 5,
    min_ret_pct: float = 40.0,
    provider: str = "yfinance",
    min_volume: int = 1_000_000,
    min_close: float = 0.20,
    max_rows: int = 200,
) -> str:
    """Review recent monster stocks and their pre-ignition traces.

    Screens the last ``lookback_trading_days`` complete daily bars for stocks
    whose 1-day return is at least ``min_ret_pct``. Returns prior-day weakness,
    open gap, volume ratio, dollar volume, four-day lead-in move, float/market
    cap context, and a replay bucket describing whether the move looked
    discoverable before it became a leaderboard result.

    This is the MCP-backed version of
    ``skills/monster-stock-hunter/scripts/monster_weekly_backtest.sql``.
    """
    days = max(1, min(int(lookback_trading_days), 20))
    row_limit = max(1, min(max_rows, 500))
    params = {
        "lookback_trading_days": days,
        "min_ret_pct": float(min_ret_pct),
        "provider": provider,
        "min_volume": int(min_volume),
        "min_close": float(min_close),
        "row_limit": row_limit,
    }
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(_MONSTER_WEEKLY_BACKTEST_SQL, params)
        rows = cur.fetchall()
    return _to_json({"params": params, "results": rows})


_IGNITION_CANDIDATE_REPLAY_SQL = """
WITH settings AS (
    SELECT
        %(target_date)s::date AS target_date,
        %(min_close)s::numeric AS min_close,
        %(min_avg_volume_20d)s::bigint AS min_avg_volume_20d,
        %(min_avg_dollar_20d)s::numeric AS min_avg_dollar_20d,
        %(min_prediction_score)s::int AS min_prediction_score,
        %(provider)s::text AS provider
),
asof_day AS (
    SELECT max(price_date) AS asof_date
    FROM market_data.us_equity_daily_prices
    WHERE provider = (SELECT provider FROM settings)
      AND price_date < (SELECT target_date FROM settings)
),
history AS (
    SELECT
        p.symbol,
        p.price_date,
        p.open,
        p.high,
        p.low,
        p.close,
        p.volume,
        LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close
    FROM market_data.us_equity_daily_prices p
    WHERE p.provider = (SELECT provider FROM settings)
      AND p.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '220 days'
      AND p.price_date <= (SELECT asof_date FROM asof_day)
),
features AS (
    SELECT
        h.symbol,
        max(h.price_date) AS asof_date,
        (ARRAY_AGG(h.open ORDER BY h.price_date DESC))[1] AS asof_open,
        (ARRAY_AGG(h.high ORDER BY h.price_date DESC))[1] AS asof_high,
        (ARRAY_AGG(h.low ORDER BY h.price_date DESC))[1] AS asof_low,
        (ARRAY_AGG(h.close ORDER BY h.price_date DESC))[1] AS asof_close,
        (ARRAY_AGG(h.volume ORDER BY h.price_date DESC))[1] AS asof_volume,
        (ARRAY_AGG(h.prev_close ORDER BY h.price_date DESC))[1] AS prev_close,
        AVG(h.volume) FILTER (
            WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days'
        ) AS avg_volume_20d_proxy,
        AVG(h.close * h.volume) FILTER (
            WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days'
        ) AS avg_dollar_20d_proxy,
        MAX(h.high) FILTER (
            WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '20 days'
        ) AS high_20d,
        MIN(h.low) FILTER (
            WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '20 days'
        ) AS low_20d,
        MAX(100 * (h.close - h.prev_close) / NULLIF(h.prev_close, 0)) FILTER (
            WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '180 days'
        ) AS max_1d_ret_180d_pct,
        SUM(
            CASE WHEN 100 * (h.close - h.prev_close) / NULLIF(h.prev_close, 0) >= 50
                 THEN 1 ELSE 0 END
        ) FILTER (
            WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '180 days'
        ) AS days_above_50pct
    FROM history h
    GROUP BY h.symbol
),
profile_latest AS (
    SELECT DISTINCT ON (symbol)
        symbol,
        sector,
        industry,
        market_cap,
        float_shares,
        short_percent_of_float,
        fifty_two_week_high
    FROM market_data.us_equity_company_profile
    WHERE provider = (SELECT provider FROM settings)
      AND as_of_date <= (SELECT asof_date FROM asof_day)
    ORDER BY symbol, as_of_date DESC
),
target_day AS (
    SELECT
        p.symbol,
        p.open AS target_open,
        p.high AS target_high,
        p.low AS target_low,
        p.close AS target_close,
        p.volume AS target_volume
    FROM market_data.us_equity_daily_prices p
    WHERE p.provider = (SELECT provider FROM settings)
      AND p.price_date = (SELECT target_date FROM settings)
),
scored AS (
    SELECT
        f.*,
        cp.sector,
        cp.industry,
        cp.market_cap,
        cp.float_shares,
        cp.short_percent_of_float,
        cp.fifty_two_week_high,
        t.target_open,
        t.target_high,
        t.target_low,
        t.target_close,
        t.target_volume,
        100 * (f.asof_close - f.prev_close) / NULLIF(f.prev_close, 0) AS asof_ret_1d_pct,
        100 * (f.high_20d - f.asof_close) / NULLIF(f.high_20d, 0) AS pullback_from_20d_high_pct,
        100 * (t.target_high - f.asof_close) / NULLIF(f.asof_close, 0) AS next_day_max_from_asof_pct,
        100 * (t.target_close - f.asof_close) / NULLIF(f.asof_close, 0) AS next_day_close_from_asof_pct,
        (
            CASE WHEN f.avg_dollar_20d_proxy < 5000000 THEN 1 ELSE 0 END +
            CASE WHEN f.asof_volume >= f.avg_volume_20d_proxy * 3 THEN 1 ELSE 0 END +
            CASE WHEN 100 * (f.asof_close - f.prev_close) / NULLIF(f.prev_close, 0)
                      BETWEEN 0 AND 15 THEN 1 ELSE 0 END +
            CASE WHEN cp.float_shares IS NOT NULL AND cp.float_shares < 30000000 THEN 1 ELSE 0 END +
            CASE WHEN COALESCE(f.max_1d_ret_180d_pct, 0) >= 50 THEN 1 ELSE 0 END +
            CASE WHEN f.asof_close BETWEEN (SELECT min_close FROM settings) AND 30 THEN 1 ELSE 0 END +
            CASE WHEN 100 * (f.high_20d - f.asof_close) / NULLIF(f.high_20d, 0)
                      BETWEEN 5 AND 35 THEN 1 ELSE 0 END
        ) AS replay_prediction_score
    FROM features f
    LEFT JOIN profile_latest cp ON cp.symbol = f.symbol
    LEFT JOIN target_day t ON t.symbol = f.symbol
    WHERE f.asof_close >= (SELECT min_close FROM settings)
      AND f.avg_volume_20d_proxy >= (SELECT min_avg_volume_20d FROM settings)
      AND f.avg_dollar_20d_proxy >= (SELECT min_avg_dollar_20d FROM settings)
),
candidates AS (
    SELECT *
    FROM scored
    WHERE replay_prediction_score >= (SELECT min_prediction_score FROM settings)
      AND (
        asof_ret_1d_pct BETWEEN -10 AND 15
        OR pullback_from_20d_high_pct BETWEEN 5 AND 35
        OR COALESCE(max_1d_ret_180d_pct, 0) >= 50
      )
)
SELECT
    (SELECT target_date FROM settings) AS target_date,
    symbol,
    asof_date,
    ROUND(asof_close::numeric, 4) AS asof_close,
    ROUND(asof_ret_1d_pct::numeric, 2) AS asof_ret_1d_pct,
    ROUND((avg_dollar_20d_proxy / 1000000)::numeric, 2) AS avg_dollar_20d_m,
    ROUND((asof_close * asof_volume / 1000000)::numeric, 2) AS asof_dollar_volume_m,
    ROUND(max_1d_ret_180d_pct::numeric, 2) AS max_1d_ret_180d_pct,
    days_above_50pct,
    ROUND((market_cap / 1000000.0)::numeric, 0) AS market_cap_m,
    ROUND((float_shares / 1000000.0)::numeric, 1) AS float_m,
    short_percent_of_float,
    ROUND(pullback_from_20d_high_pct::numeric, 2) AS pullback_from_20d_high_pct,
    replay_prediction_score,
    CASE
        WHEN replay_prediction_score >= 6
          AND asof_ret_1d_pct BETWEEN 0 AND 15
          AND asof_close * asof_volume >= avg_dollar_20d_proxy * 1.5
          THEN '纸面雷达:优先盘前Web升级'
        WHEN replay_prediction_score >= 6
          AND asof_close * asof_volume < avg_dollar_20d_proxy * 0.5
          THEN '静态壳风险:无当前量能则剔除'
        WHEN replay_prediction_score = 5
          AND (
            CASE WHEN float_shares IS NOT NULL AND float_shares < 30000000 THEN 1 ELSE 0 END +
            CASE WHEN avg_dollar_20d_proxy < 5000000 THEN 1 ELSE 0 END +
            CASE WHEN asof_ret_1d_pct < 0 THEN 1 ELSE 0 END +
            CASE WHEN COALESCE(max_1d_ret_180d_pct, 0) >= 50 THEN 1 ELSE 0 END
          ) >= 2
          THEN 'Web升级观察:查movers/gap/48h催化/当前成交额'
        WHEN replay_prediction_score >= 6
          THEN '纸面雷达:待触发'
        ELSE '观察'
    END AS replay_gate_action,
    CASE
        WHEN asof_ret_1d_pct BETWEEN 0 AND 15 THEN '0-15点火前'
        WHEN asof_ret_1d_pct > 15 AND asof_ret_1d_pct <= 40 THEN '15-40接棒'
        WHEN asof_ret_1d_pct > 40 THEN '40+审计'
        ELSE '弱势反转观察'
    END AS replay_layer,
    ROUND(target_open::numeric, 4) AS target_open,
    ROUND(target_high::numeric, 4) AS target_high,
    ROUND(target_low::numeric, 4) AS target_low,
    ROUND(target_close::numeric, 4) AS target_close,
    ROUND(next_day_max_from_asof_pct::numeric, 2) AS next_day_max_from_asof_pct,
    ROUND(next_day_close_from_asof_pct::numeric, 2) AS next_day_close_from_asof_pct,
    CASE
        WHEN next_day_max_from_asof_pct >= 40 THEN '妖股命中候选'
        WHEN next_day_max_from_asof_pct >= 20 THEN '强命中候选'
        WHEN next_day_max_from_asof_pct >= 10 THEN '快打命中候选'
        WHEN target_high IS NULL THEN '无目标日数据'
        ELSE '未命中或误报'
    END AS replay_result
FROM candidates
ORDER BY replay_prediction_score DESC, next_day_max_from_asof_pct DESC NULLS LAST
LIMIT %(row_limit)s;
"""


@mcp.tool()
def ignition_candidate_replay(
    target_date: str,
    provider: str = "yfinance",
    min_close: float = 0.20,
    min_avg_volume_20d: int = 500_000,
    min_avg_dollar_20d: float = 250_000,
    min_prediction_score: int = 4,
    max_rows: int = 100,
) -> str:
    """Replay prediction candidates using only data before ``target_date``.

    ``target_date`` is a YYYY-MM-DD session to validate. The query builds the
    candidate list from the prior complete trading day, then joins the target
    day OHLCV only for outcome measurement. This is designed for S8
    monster-stock prediction validation and avoids future leakage. The default
    ``min_prediction_score`` is 4 so replay can expose borderline misses; score
    5 candidates should be escalated through current Web/quote checks before
    they are classified as misses.

    This is the MCP-backed version of
    ``skills/monster-stock-hunter/scripts/ignition_candidate_replay.sql``.
    """
    try:
        parsed_target = date.fromisoformat(target_date)
    except ValueError as exc:
        raise ValueError("target_date must be YYYY-MM-DD") from exc
    score = max(1, min(int(min_prediction_score), 10))
    row_limit = max(1, min(max_rows, 500))
    params = {
        "target_date": parsed_target,
        "provider": provider,
        "min_close": float(min_close),
        "min_avg_volume_20d": int(min_avg_volume_20d),
        "min_avg_dollar_20d": float(min_avg_dollar_20d),
        "min_prediction_score": score,
        "row_limit": row_limit,
    }
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(_IGNITION_CANDIDATE_REPLAY_SQL, params)
        rows = cur.fetchall()
    return _to_json({"params": params, "results": rows})


_ROLLING_TRADING_DAYS_SQL = """
SELECT DISTINCT price_date
FROM market_data.us_equity_daily_prices
WHERE provider = %(provider)s
ORDER BY price_date DESC
LIMIT %(lookback_days)s;
"""


_ROLLING_MONSTER_COUNTS_SQL = """
WITH daily AS (
    SELECT
        p.symbol,
        p.price_date,
        p.close,
        p.volume,
        LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close
    FROM market_data.us_equity_daily_prices p
    WHERE p.provider = %(provider)s
      AND p.price_date >= %(start_date)s::date - INTERVAL '5 days'
      AND p.price_date <= %(end_date)s::date
)
SELECT
    price_date,
    COUNT(*) FILTER (
        WHERE close >= %(min_close)s
          AND volume >= %(min_volume)s
          AND 100 * (close - prev_close) / NULLIF(prev_close, 0) >= %(monster_threshold)s
    ) AS monster_count
FROM daily
WHERE price_date = ANY(%(dates)s)
GROUP BY price_date
ORDER BY price_date;
"""


_ROLLING_MONSTER_SYMBOLS_SQL = """
WITH daily AS (
    SELECT
        p.symbol,
        p.price_date,
        p.open,
        p.high,
        p.low,
        p.close,
        p.volume,
        LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close
    FROM market_data.us_equity_daily_prices p
    WHERE p.provider = %(provider)s
      AND p.price_date >= %(start_date)s::date - INTERVAL '5 days'
      AND p.price_date <= %(end_date)s::date
)
SELECT
    symbol,
    price_date,
    ROUND(open::numeric, 4) AS open,
    ROUND(high::numeric, 4) AS high,
    ROUND(low::numeric, 4) AS low,
    ROUND(close::numeric, 4) AS close,
    ROUND((100 * (close - prev_close) / NULLIF(prev_close, 0))::numeric, 2) AS ret_1d_pct,
    ROUND((100 * (high - prev_close) / NULLIF(prev_close, 0))::numeric, 2) AS max_from_prev_pct
FROM daily
WHERE price_date = ANY(%(dates)s)
  AND close >= %(min_close)s
  AND volume >= %(min_volume)s
  AND 100 * (close - prev_close) / NULLIF(prev_close, 0) >= %(monster_threshold)s
ORDER BY price_date, ret_1d_pct DESC;
"""


@mcp.tool()
def rolling_ignition_walkforward(
    provider: str = "yfinance",
    train_days: int = 5,
    validate_days: int = 2,
    lookback_days: int = 12,
    monster_threshold: float = 40.0,
    min_close: float = 0.20,
    min_volume: int = 1_000_000,
    min_avg_volume_20d: int = 500_000,
    min_avg_dollar_20d: float = 250_000,
    max_detail_rows: int = 300,
) -> str:
    """Run rolling walk-forward validation for monster-stock prediction rules.

    Each fold uses ``train_days`` complete sessions only to count and describe
    the monster-stock environment, then validates the frozen two-layer replay
    rule on the following ``validate_days`` sessions. Candidate generation for
    each validation date uses only data before that date; target-day OHLCV is
    joined only to measure outcomes.

    Layers:
    - ``主攻层``: replay score >= 6 and not static-shell risk.
    - ``观察层``: replay score = 5 with low-base/low-float/weak-reversal/
      prior-monster traits, requiring Web upgrade in live use.
    """
    t_days = max(3, min(int(train_days), 10))
    v_days = max(1, min(int(validate_days), 5))
    l_days = max(t_days + v_days, min(int(lookback_days), 60))
    detail_limit = max(1, min(int(max_detail_rows), 1000))

    params = {
        "provider": provider,
        "train_days": t_days,
        "validate_days": v_days,
        "lookback_days": l_days,
        "monster_threshold": float(monster_threshold),
        "min_close": float(min_close),
        "min_volume": int(min_volume),
        "min_avg_volume_20d": int(min_avg_volume_20d),
        "min_avg_dollar_20d": float(min_avg_dollar_20d),
        "max_detail_rows": detail_limit,
    }

    with open_db() as conn, conn.cursor() as cur:
        cur.execute(
            _ROLLING_TRADING_DAYS_SQL,
            {"provider": provider, "lookback_days": l_days},
        )
        trading_days = [row["price_date"] for row in cur.fetchall()]
        trading_days = sorted(trading_days)

        if len(trading_days) < t_days + v_days:
            return _to_json(
                {
                    "params": params,
                    "error": "not enough trading days for requested train/validate window",
                    "available_trading_days": trading_days,
                }
            )

        fold_count = len(trading_days) - t_days - v_days + 1
        summaries: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        rule_stats: dict[str, dict[str, int]] = {
            "主攻层: score>=6 且非静态壳风险": {"support": 0, "fail": 0},
            "观察层: score=5 且低基数/低流通/弱反转/翻倍基因>=2": {"support": 0, "fail": 0},
            "静态壳风险: score>=6 但量能低于均值50%": {"support": 0, "fail": 0},
        }

        for fold_index in range(fold_count):
            train_dates = trading_days[fold_index : fold_index + t_days]
            validate_dates = trading_days[
                fold_index + t_days : fold_index + t_days + v_days
            ]

            cur.execute(
                _ROLLING_MONSTER_COUNTS_SQL,
                {
                    "provider": provider,
                    "dates": train_dates,
                    "start_date": train_dates[0],
                    "end_date": train_dates[-1],
                    "min_close": float(min_close),
                    "min_volume": int(min_volume),
                    "monster_threshold": float(monster_threshold),
                },
            )
            train_monster_count = sum(int(row["monster_count"] or 0) for row in cur.fetchall())

            cur.execute(
                _ROLLING_MONSTER_SYMBOLS_SQL,
                {
                    "provider": provider,
                    "dates": validate_dates,
                    "start_date": validate_dates[0],
                    "end_date": validate_dates[-1],
                    "min_close": float(min_close),
                    "min_volume": int(min_volume),
                    "monster_threshold": float(monster_threshold),
                },
            )
            validation_monsters = cur.fetchall()
            monster_keys = {
                (row["price_date"], row["symbol"]) for row in validation_monsters
            }

            fold_candidates: list[dict[str, Any]] = []
            for target in validate_dates:
                replay_params = {
                    "target_date": target,
                    "provider": provider,
                    "min_close": float(min_close),
                    "min_avg_volume_20d": int(min_avg_volume_20d),
                    "min_avg_dollar_20d": float(min_avg_dollar_20d),
                    "min_prediction_score": 5,
                    "row_limit": 500,
                }
                cur.execute(_IGNITION_CANDIDATE_REPLAY_SQL, replay_params)
                for row in cur.fetchall():
                    score = int(row["replay_prediction_score"] or 0)
                    gate_action = row.get("replay_gate_action") or ""
                    if score >= 6 and not gate_action.startswith("静态壳风险"):
                        layer = "主攻层"
                    elif score == 5 and gate_action.startswith("Web升级观察"):
                        layer = "观察层"
                    elif gate_action.startswith("静态壳风险"):
                        layer = "静态壳风险"
                    else:
                        continue

                    max_return = _num(row.get("next_day_max_from_asof_pct"))
                    if max_return >= float(monster_threshold):
                        result = "妖股命中"
                    elif max_return >= 20:
                        result = "强命中"
                    elif max_return >= 10:
                        result = "快打命中"
                    elif layer == "静态壳风险":
                        result = "风险标记正确"
                    else:
                        result = "误报"

                    candidate = {
                        "fold": fold_index + 1,
                        "target_date": row["target_date"],
                        "symbol": row["symbol"],
                        "layer": layer,
                        "asof_price": row.get("asof_close"),
                        "score": score,
                        "gate_action": gate_action,
                        "target_open": row.get("target_open"),
                        "target_high": row.get("target_high"),
                        "target_close": row.get("target_close"),
                        "max_return_pct": row.get("next_day_max_from_asof_pct"),
                        "result": result,
                        "reason": row.get("replay_result"),
                    }
                    fold_candidates.append(candidate)

            candidate_keys = {
                (row["target_date"], row["symbol"])
                for row in fold_candidates
                if row["layer"] in {"主攻层", "观察层"}
            }
            missed_monsters = monster_keys - candidate_keys

            main_candidates = [row for row in fold_candidates if row["layer"] == "主攻层"]
            observation_candidates = [
                row for row in fold_candidates if row["layer"] == "观察层"
            ]
            static_filtered = [
                row for row in fold_candidates if row["layer"] == "静态壳风险"
            ]
            main_hits = [
                row for row in main_candidates
                if _num(row.get("max_return_pct")) >= 20
            ]
            observation_hits = [
                row for row in observation_candidates
                if _num(row.get("max_return_pct")) >= 20
            ]
            main_misreports = [
                row for row in main_candidates
                if _num(row.get("max_return_pct")) < 10
            ]

            main_error_limit = max(3, len(main_hits) * 3)
            if main_hits and len(main_misreports) <= main_error_limit:
                rule_stats["主攻层: score>=6 且非静态壳风险"]["support"] += 1
            elif main_candidates:
                rule_stats["主攻层: score>=6 且非静态壳风险"]["fail"] += 1
            if observation_hits:
                rule_stats["观察层: score=5 且低基数/低流通/弱反转/翻倍基因>=2"]["support"] += 1
            elif observation_candidates:
                rule_stats["观察层: score=5 且低基数/低流通/弱反转/翻倍基因>=2"]["fail"] += 1
            if static_filtered and all(_num(row.get("max_return_pct")) < 10 for row in static_filtered):
                rule_stats["静态壳风险: score>=6 但量能低于均值50%"]["support"] += 1
            elif static_filtered:
                rule_stats["静态壳风险: score>=6 但量能低于均值50%"]["fail"] += 1

            if main_hits and len(main_misreports) <= max(3, len(main_hits) * 3):
                rule_conclusion = "主攻层可保留;观察层用于防漏"
            elif observation_hits and missed_monsters:
                rule_conclusion = "主攻偏弱;观察层有防漏价值"
            elif len(main_misreports) > max(3, len(main_hits) * 3):
                rule_conclusion = "主攻误报偏多;需强化Web升级"
            else:
                rule_conclusion = "样本不足或未触发;仅保留为待观察假设"

            summaries.append(
                {
                    "fold": fold_index + 1,
                    "train_dates": [d.isoformat() for d in train_dates],
                    "validate_dates": [d.isoformat() for d in validate_dates],
                    "train_monster_count": train_monster_count,
                    "validation_monster_count": len(validation_monsters),
                    "main_candidate_count": len(main_candidates),
                    "main_hit_count": len(main_hits),
                    "observation_candidate_count": len(observation_candidates),
                    "observation_hit_count": len(observation_hits),
                    "missed_monster_count": len(missed_monsters),
                    "main_misreport_count": len(main_misreports),
                    "rule_conclusion": rule_conclusion,
                }
            )
            details.extend(fold_candidates)
            for row in validation_monsters:
                if (row["price_date"], row["symbol"]) in missed_monsters:
                    details.append(
                        {
                            "fold": fold_index + 1,
                            "target_date": row["price_date"],
                            "symbol": row["symbol"],
                            "layer": "漏网审计",
                            "asof_price": None,
                            "score": None,
                            "gate_action": "未进入主攻/观察候选",
                            "target_open": row.get("open"),
                            "target_high": row.get("high"),
                            "target_close": row.get("close"),
                            "max_return_pct": row.get("max_from_prev_pct"),
                            "result": "漏网",
                            "reason": f"验证日收盘涨幅 {row.get('ret_1d_pct')}%",
                        }
                    )

    rule_table = []
    for rule, stats in rule_stats.items():
        support = stats["support"]
        fail = stats["fail"]
        if rule.startswith("静态壳风险") and fail > support:
            decision = "只作风险标签,不作硬过滤"
        elif support >= max(2, fail):
            decision = "写入skill"
        elif support > 0:
            decision = "保留为条件规则"
        else:
            decision = "不写入结论"
        rule_table.append(
            {
                "rule": rule,
                "support_folds": support,
                "failed_folds": fail,
                "applicable_when": "仅限本地日线回放;实时使用仍需Web/quote升级",
                "not_applicable_when": "缺少当前成交额、bid/ask、48h催化或触发红旗",
                "decision": decision,
            }
        )

    return _to_json(
        {
            "params": params,
            "trading_days": [d.isoformat() for d in trading_days],
            "summary": summaries,
            "details": details[:detail_limit],
            "detail_rows_returned": min(len(details), detail_limit),
            "detail_rows_total": len(details),
            "rule_table": rule_table,
        }
    )


@mcp.tool()
def long_momentum_candidates(
    price_buckets: str = DEFAULT_PRICE_BUCKETS,
    limit_per_bucket: int = 2,
    provider: str = "yfinance",
    min_volume: int = 1_000_000,
    min_avg_volume: int = 150_000,
    min_dollar_volume: float = 3_000_000,
    min_rr: float = 1.6,
    max_atr_pct: float = 0.28,
) -> str:
    """Screen long-momentum candidates by price bucket, with full trade plan.

    Equivalent to ``python cli/recommend_long_candidates.py --limit N``:
    returns one JSON object per price bucket containing entry, buy_zone,
    stop_loss, take_profit, risk/reward and momentum metrics for each pick.

    ``price_buckets`` is comma-separated ``MIN-MAX`` pairs, default
    ``1-10,10-100,100-500``. ``limit_per_bucket`` is capped at 2 to match the
    skill workflow. Per-bucket ATR floors are applied automatically.
    """
    buckets = parse_price_buckets(price_buckets)
    per_bucket = max(1, min(limit_per_bucket, 2))
    params = ScreenParams(
        provider=provider,
        limit=per_bucket,
        per_bucket_limit=per_bucket,
        min_volume=min_volume,
        min_avg_volume=min_avg_volume,
        min_dollar_volume=min_dollar_volume,
        min_rr=min_rr,
        max_atr_pct=max_atr_pct,
    )
    payload = run_recommendation_bucketed(params, buckets)
    return _to_json(payload)


_SNAPSHOT_SQL = """
WITH p AS (
    SELECT
        symbol,
        price_date,
        open, high, low, close, volume,
        LAG(close, 1)  OVER w AS prev_close,
        LAG(close, 5)  OVER w AS close_5d_ago,
        LAG(close, 10) OVER w AS close_10d_ago,
        LAG(close, 20) OVER w AS close_20d_ago,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)  AS ma10,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
        AVG(volume) OVER (PARTITION BY symbol ORDER BY price_date
                          ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS avg_vol_20d,
        MAX(high) OVER (PARTITION BY symbol ORDER BY price_date
                        ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS high_20d,
        MIN(low)  OVER (PARTITION BY symbol ORDER BY price_date
                        ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS low_20d
    FROM market_data.us_equity_daily_prices
    WHERE provider = %(provider)s AND symbol = ANY(%(symbols)s)
    WINDOW w AS (PARTITION BY symbol ORDER BY price_date)
)
SELECT *
FROM p
WHERE price_date = (
    SELECT MAX(price_date)
    FROM market_data.us_equity_daily_prices
    WHERE provider = %(provider)s
)
ORDER BY symbol;
"""

_HISTORY_SQL = """
SELECT price_date, open, high, low, close, volume
FROM market_data.us_equity_daily_prices
WHERE provider = %s AND symbol = %s
ORDER BY price_date DESC
LIMIT %s;
"""


def _atr14(rows_desc: list[dict[str, Any]]) -> float | None:
    rows = list(reversed(rows_desc))
    true_ranges: list[float] = []
    prev_close: float | None = None
    for row in rows:
        high = _float(row.get("high"))
        low = _float(row.get("low"))
        close = _float(row.get("close"))
        if high is None or low is None or close is None:
            prev_close = close
            continue
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
        prev_close = close
    if not true_ranges:
        return None
    return sum(true_ranges[-14:]) / min(14, len(true_ranges))


def _rsi14(rows_desc: list[dict[str, Any]]) -> float | None:
    closes = [_float(r.get("close")) for r in reversed(rows_desc)]
    closes = [c for c in closes if c is not None]
    if len(closes) < 15:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    period = 14
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _pct(numer: float | None, denom: float | None, digits: int = 4) -> float | None:
    if numer is None or denom in (None, 0):
        return None
    return round((numer / denom - 1) * 100, digits)


def _num(value: Any, default: float = 0.0) -> float:
    parsed = _float(value)
    return default if parsed is None else parsed


@mcp.tool()
def symbol_snapshot(
    symbols: str,
    lookback_days: int = 60,
    provider: str = "yfinance",
    include_benchmark: bool = True,
) -> str:
    """Full single-ticker snapshot: OHLCV + MAs + ATR14 + RSI14 + relative strength.

    Returns one object per symbol with enough context for the LLM to judge
    trend, volatility, volume expansion and position vs 20-day range without
    issuing follow-up SQL. ``include_benchmark=True`` adds 20-day excess
    return vs SPY (falls back to null if SPY is missing from the database).

    Limits: up to 20 symbols; lookback_days clipped to [30, 250].
    """
    normalized = _split_symbols(symbols, max_count=20)
    history_days = max(30, min(lookback_days, 250))

    with open_db() as conn, conn.cursor() as cur:
        cur.execute(_SNAPSHOT_SQL, {"provider": provider, "symbols": normalized})
        latest_rows = {row["symbol"]: row for row in cur.fetchall()}

        histories: dict[str, list[dict[str, Any]]] = {}
        for symbol in normalized:
            cur.execute(_HISTORY_SQL, (provider, symbol, history_days))
            histories[symbol] = cur.fetchall()

        spy_ret_20d = None
        if include_benchmark:
            cur.execute(_SNAPSHOT_SQL, {"provider": provider, "symbols": ["SPY"]})
            spy_latest = cur.fetchone()
            if spy_latest:
                spy_ret_20d = _pct(
                    _float(spy_latest.get("close")),
                    _float(spy_latest.get("close_20d_ago")),
                )

    snapshots: list[dict[str, Any]] = []
    for symbol in normalized:
        row = latest_rows.get(symbol)
        history = histories.get(symbol) or []
        if row is None:
            snapshots.append({"symbol": symbol, "error": "no data"})
            continue

        close = _float(row.get("close"))
        high = _float(row.get("high"))
        low = _float(row.get("low"))
        high_20d = _float(row.get("high_20d"))
        volume = _float(row.get("volume"))
        avg_vol_20d = _float(row.get("avg_vol_20d"))
        atr14 = _atr14(history)
        rsi14 = _rsi14(history)

        ret_20d_pct = _pct(close, _float(row.get("close_20d_ago")))
        excess_pct = None
        if ret_20d_pct is not None and spy_ret_20d is not None:
            excess_pct = round(ret_20d_pct - spy_ret_20d, 4)

        snapshots.append(
            {
                "symbol": symbol,
                "date": row.get("price_date"),
                "price": {
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                    "prev_close": row.get("prev_close"),
                },
                "returns": {
                    "ret_1d_pct": _pct(close, _float(row.get("prev_close"))),
                    "ret_5d_pct": _pct(close, _float(row.get("close_5d_ago"))),
                    "ret_10d_pct": _pct(close, _float(row.get("close_10d_ago"))),
                    "ret_20d_pct": ret_20d_pct,
                },
                "moving_averages": {
                    "ma10": row.get("ma10"),
                    "ma20": row.get("ma20"),
                    "ma50": row.get("ma50"),
                },
                "volatility": {
                    "atr14": round(atr14, 4) if atr14 is not None else None,
                    "atr14_pct": round(atr14 / close * 100, 4)
                    if (atr14 is not None and close)
                    else None,
                },
                "momentum": {"rsi14": rsi14},
                "volume": {
                    "avg_vol_20d": row.get("avg_vol_20d"),
                    "volume_ratio": round(volume / avg_vol_20d, 4)
                    if (volume is not None and avg_vol_20d not in (None, 0))
                    else None,
                    "dollar_volume": round(close * volume)
                    if (close is not None and volume is not None)
                    else None,
                },
                "range": {
                    "high_20d": row.get("high_20d"),
                    "low_20d": row.get("low_20d"),
                    "close_to_20d_high_pct": round(close / high_20d * 100, 2)
                    if (close is not None and high_20d not in (None, 0))
                    else None,
                    "close_position_pct": round((close - low) / (high - low) * 100, 2)
                    if (
                        close is not None
                        and high is not None
                        and low is not None
                        and high != low
                    )
                    else None,
                },
                "relative_strength": (
                    {
                        "spy_ret_20d_pct": spy_ret_20d,
                        "excess_ret_20d_pct": excess_pct,
                    }
                    if include_benchmark
                    else None
                ),
            }
        )

    return _to_json(snapshots)


@mcp.tool()
def earnings_dates(
    symbols: str,
    within_days: int = 30,
    provider: str = "yfinance",
) -> str:
    """Return the next earnings date within ``within_days`` days per symbol.

    ``within_days <= 0`` also includes past events within the same window
    (using ``abs(within_days)`` as the horizon). Each row carries
    ``days_until`` and a ``recommendation`` field that encodes the skill's
    rule of thumb: ``<=7`` = exclude, ``8-14`` = downgrade, ``>14`` = ok.

    Symbols whose calendar was never synced are returned separately in
    ``symbols_without_data``. Use ``db_health`` → ``earnings_calendar`` to
    check overall coverage.
    """
    normalized = _split_symbols(symbols, max_count=50)
    include_past = within_days <= 0
    horizon = abs(within_days) if within_days != 0 else 30

    if include_past:
        date_filter = (
            "earnings_date BETWEEN CURRENT_DATE - %(horizon)s "
            "AND CURRENT_DATE + %(horizon)s"
        )
    else:
        date_filter = (
            "earnings_date BETWEEN CURRENT_DATE AND CURRENT_DATE + %(horizon)s"
        )

    sql = f"""
    WITH ranked AS (
        SELECT
            symbol,
            earnings_date,
            eps_estimate,
            eps_reported,
            eps_surprise_pct,
            timing,
            is_estimated,
            provider,
            fetched_at,
            (earnings_date - CURRENT_DATE) AS days_until,
            ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY earnings_date ASC
            ) AS rn
        FROM market_data.us_equity_earnings
        WHERE provider = %(provider)s
          AND symbol = ANY(%(symbols)s)
          AND {date_filter}
    )
    SELECT
        symbol,
        earnings_date,
        eps_estimate,
        eps_reported,
        eps_surprise_pct,
        timing,
        is_estimated,
        provider,
        days_until,
        fetched_at
    FROM ranked
    WHERE rn = 1
    ORDER BY days_until ASC NULLS LAST, symbol;
    """
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {"provider": provider, "symbols": normalized, "horizon": horizon},
        )
        rows = cur.fetchall()

    for row in rows:
        days_until = row.get("days_until")
        if days_until is None:
            continue
        if days_until <= 7:
            row["recommendation"] = "exclude"
        elif days_until <= 14:
            row["recommendation"] = "downgrade"
        else:
            row["recommendation"] = "ok"

    known_symbols = {row["symbol"] for row in rows}
    missing = [s for s in normalized if s not in known_symbols]
    return _to_json(
        {
            "horizon_days": horizon,
            "include_past": include_past,
            "results": rows,
            "symbols_without_data": missing,
        }
    )


# ---------------------------------------------------------------------------
# Left-side (mean-reversion) screeners
# ---------------------------------------------------------------------------


def _parse_buckets(raw: str) -> list[tuple[str, float, float]]:
    """Parse ``MIN-MAX,MIN-MAX`` into ``[(label, min, max), ...]``."""
    buckets: list[tuple[str, float, float]] = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item or "-" not in item:
            continue
        raw_min, raw_max = item.split("-", 1)
        try:
            min_v = float(raw_min)
            max_v = float(raw_max)
        except ValueError:
            continue
        if min_v <= 0 or max_v <= min_v:
            continue
        buckets.append((item, min_v, max_v))
    if not buckets:
        raise ValueError(f"No valid buckets parsed from {raw!r}")
    return buckets


_OVERSOLD_SQL = """
WITH base AS (
    SELECT
        symbol,
        price_date,
        open, high, low, close, volume,
        LAG(close, 1)  OVER w AS prev_close,
        LAG(close, 3)  OVER w AS close_3d_ago,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma200,
        MIN(low) OVER (PARTITION BY symbol ORDER BY price_date
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS low_20d,
        MIN(low) OVER (PARTITION BY symbol ORDER BY price_date
                       ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,
        AVG(volume) OVER (PARTITION BY symbol ORDER BY price_date
                          ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS avg_vol_20d
    FROM market_data.us_equity_daily_prices
    WHERE provider = %(provider)s
    WINDOW w AS (PARTITION BY symbol ORDER BY price_date)
),
deltas AS (
    SELECT
        symbol, price_date, open, high, low, close, volume,
        prev_close, close_3d_ago, ma20, ma50, ma200,
        low_20d, low_52w, avg_vol_20d,
        close - prev_close AS change,
        GREATEST(close - prev_close, 0) AS gain,
        GREATEST(prev_close - close, 0) AS loss
    FROM base
),
rsi AS (
    SELECT
        symbol, price_date,
        open, high, low, close, volume,
        prev_close, close_3d_ago, ma20, ma50, ma200,
        low_20d, low_52w, avg_vol_20d,
        AVG(gain) OVER (PARTITION BY symbol ORDER BY price_date
                        ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain_14,
        AVG(loss) OVER (PARTITION BY symbol ORDER BY price_date
                        ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss_14
    FROM deltas
),
latest AS (
    SELECT *
    FROM rsi
    WHERE price_date = (SELECT MAX(price_date)
                        FROM market_data.us_equity_daily_prices
                        WHERE provider = %(provider)s)
)
SELECT
    symbol,
    price_date,
    close,
    open, high, low, volume,
    ma20, ma50, ma200, low_20d, low_52w, avg_vol_20d,
    CASE WHEN avg_loss_14 = 0 THEN 100
         ELSE 100 - 100 / (1 + avg_gain_14 / NULLIF(avg_loss_14, 0))
    END AS rsi14,
    (volume / NULLIF(avg_vol_20d, 0)) AS volume_ratio,
    (close / NULLIF(close_3d_ago, 0) - 1) AS ret_3d,
    (close / NULLIF(low_20d, 0) - 1) AS dist_low_20d
FROM latest
WHERE close BETWEEN %(min_price)s AND %(max_price)s
  AND close_3d_ago IS NOT NULL
  AND ma200 IS NOT NULL
  AND avg_vol_20d > %(min_avg_volume)s
  AND close >= ma200
  AND (close / NULLIF(low_20d, 0) - 1) <= 0.05
  AND (close / NULLIF(close_3d_ago, 0) - 1) <= -0.05
  AND (volume / NULLIF(avg_vol_20d, 0)) <= 1.5
  AND (CASE WHEN avg_loss_14 = 0 THEN 100
            ELSE 100 - 100 / (1 + avg_gain_14 / NULLIF(avg_loss_14, 0)) END) < 30
  AND close > low_52w * 1.03
ORDER BY
    (CASE WHEN avg_loss_14 = 0 THEN 100
          ELSE 100 - 100 / (1 + avg_gain_14 / NULLIF(avg_loss_14, 0)) END) ASC,
    (close / NULLIF(low_20d, 0) - 1) ASC
LIMIT %(row_limit)s;
"""


@mcp.tool()
def oversold_bounce_candidates(
    price_buckets: str = "1-10,10-100,100-500",
    limit_per_bucket: int = 2,
    provider: str = "yfinance",
    min_avg_volume: int = 300_000,
) -> str:
    """Mean-reversion / oversold-bounce screener (pure OHLCV).

    Picks stocks trading within 5% of their 20-day low, with RSI14 < 30,
    3-day loss >= 5%, but still above their 200-day MA (long trend intact)
    and above their 52-week low * 1.03 (not free-falling). Volume must not
    be expanding (panic selling excluded).

    Trade plan: entry = close, stop = low_20d * 0.97, target = ma20, min RR
    of 1.2 (reversion trades tolerate a lower RR than momentum trades).
    """
    buckets = _parse_buckets(price_buckets)
    per_bucket = max(1, min(limit_per_bucket, 5))
    response: dict[str, Any] = {"provider": provider, "price_buckets": []}
    with open_db() as conn, conn.cursor() as cur:
        for label, min_v, max_v in buckets:
            cur.execute(
                _OVERSOLD_SQL,
                {
                    "provider": provider,
                    "min_price": min_v,
                    "max_price": max_v,
                    "min_avg_volume": min_avg_volume,
                    "row_limit": per_bucket * 3,
                },
            )
            rows = cur.fetchall()
            picks: list[dict[str, Any]] = []
            for row in rows:
                close = _float(row.get("close"))
                low_20d = _float(row.get("low_20d"))
                ma20 = _float(row.get("ma20"))
                if close is None or low_20d is None or ma20 is None:
                    continue
                stop = round(low_20d * 0.97, 4)
                target = round(ma20, 4)
                if stop >= close or target <= close:
                    continue
                risk = close - stop
                reward = target - close
                rr = reward / risk if risk > 0 else 0
                if rr < 1.2:
                    continue
                picks.append(
                    {
                        "symbol": row.get("symbol"),
                        "date": row.get("price_date"),
                        "close": close,
                        "entry": close,
                        "buy_zone": [round(close * 0.99, 4), round(close * 1.01, 4)],
                        "stop_loss": stop,
                        "take_profit": target,
                        "risk_pct": round(risk / close * 100, 2),
                        "reward_pct": round(reward / close * 100, 2),
                        "risk_reward": round(rr, 2),
                        "metrics": {
                            "rsi14": round(_float(row.get("rsi14")) or 0, 2),
                            "ret_3d_pct": round((_float(row.get("ret_3d")) or 0) * 100, 2),
                            "dist_20d_low_pct": round(
                                (_float(row.get("dist_low_20d")) or 0) * 100, 2
                            ),
                            "volume_ratio": round(_float(row.get("volume_ratio")) or 0, 2),
                            "ma20": row.get("ma20"),
                            "ma50": row.get("ma50"),
                            "ma200": row.get("ma200"),
                            "low_20d": row.get("low_20d"),
                            "low_52w": row.get("low_52w"),
                        },
                    }
                )
                if len(picks) >= per_bucket:
                    break
            response["price_buckets"].append(
                {
                    "bucket": label,
                    "min_price": min_v,
                    "max_price": max_v,
                    "limit": per_bucket,
                    "recommendations": picks,
                }
            )
    return _to_json(response)


_PULLBACK_SQL = """
WITH base AS (
    SELECT
        symbol, price_date,
        open, high, low, close, volume,
        LAG(close, 5)  OVER w AS close_5d_ago,
        LAG(close, 60) OVER w AS close_60d_ago,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
        AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                         ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
        MAX(high) OVER (PARTITION BY symbol ORDER BY price_date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
        AVG(volume) OVER (PARTITION BY symbol ORDER BY price_date
                          ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS avg_vol_20d
    FROM market_data.us_equity_daily_prices
    WHERE provider = %(provider)s
    WINDOW w AS (PARTITION BY symbol ORDER BY price_date)
),
latest AS (
    SELECT *
    FROM base
    WHERE price_date = (SELECT MAX(price_date)
                        FROM market_data.us_equity_daily_prices
                        WHERE provider = %(provider)s)
)
SELECT
    symbol, price_date, close, volume,
    ma20, ma50, high_20d, avg_vol_20d,
    (close / NULLIF(close_5d_ago, 0) - 1)  AS ret_5d,
    (close / NULLIF(close_60d_ago, 0) - 1) AS ret_60d,
    (close / NULLIF(ma20, 0) - 1)          AS dist_ma20,
    (volume / NULLIF(avg_vol_20d, 0))      AS volume_ratio
FROM latest
WHERE close BETWEEN %(min_price)s AND %(max_price)s
  AND close_60d_ago IS NOT NULL AND close_5d_ago IS NOT NULL
  AND avg_vol_20d > %(min_avg_volume)s
  AND (close / NULLIF(close_60d_ago, 0) - 1) > 0.20
  AND (close / NULLIF(close_5d_ago, 0) - 1) BETWEEN -0.15 AND -0.03
  AND close > ma50
  AND ABS(close / NULLIF(ma20, 0) - 1) <= 0.03
  AND (volume / NULLIF(avg_vol_20d, 0)) BETWEEN 0.6 AND 1.3
ORDER BY (close / NULLIF(close_60d_ago, 0) - 1) DESC
LIMIT %(row_limit)s;
"""


@mcp.tool()
def pullback_candidates(
    price_buckets: str = "10-100,100-500",
    limit_per_bucket: int = 2,
    provider: str = "yfinance",
    min_avg_volume: int = 500_000,
) -> str:
    """Short-term pullback inside a medium-term uptrend (pure OHLCV).

    Strong stocks (60-day return > 20%) that are retracing 3–15% over the
    last 5 days and touching their MA20 from above, with close still above
    MA50 (trend intact) and volume NOT expanding (i.e. controlled pullback
    rather than distribution). Small-cap (1-10) is skipped by default
    because noise dominates the signal there.

    Trade plan: entry = close, stop = tighter of (MA20 - 1%) or (MA50 - 3%),
    target = 20-day high * 1.05, min RR of 1.2 (pullbacks earn a
    structural retest rather than a fresh breakout, so the acceptable RR is
    lower than for momentum trades).
    """
    buckets = _parse_buckets(price_buckets)
    per_bucket = max(1, min(limit_per_bucket, 5))
    response: dict[str, Any] = {"provider": provider, "price_buckets": []}
    with open_db() as conn, conn.cursor() as cur:
        for label, min_v, max_v in buckets:
            cur.execute(
                _PULLBACK_SQL,
                {
                    "provider": provider,
                    "min_price": min_v,
                    "max_price": max_v,
                    "min_avg_volume": min_avg_volume,
                    "row_limit": per_bucket * 3,
                },
            )
            rows = cur.fetchall()
            picks: list[dict[str, Any]] = []
            for row in rows:
                close = _float(row.get("close"))
                ma20 = _float(row.get("ma20"))
                ma50 = _float(row.get("ma50"))
                high_20d = _float(row.get("high_20d"))
                if close is None or ma50 is None or high_20d is None:
                    continue
                candidate_stops = [ma50 * 0.97]
                if ma20 is not None:
                    candidate_stops.append(ma20 * 0.99)
                raw_stop = max(candidate_stops)
                # Clip to [close*0.92, close*0.98] so the stop is neither too
                # tight (whipsaw) nor too wide (poor RR). Mirrors the floor
                # used in long_momentum_candidates.
                stop = round(max(min(raw_stop, close * 0.98), close * 0.92), 4)
                target = round(high_20d * 1.05, 4)
                if stop >= close or target <= close:
                    continue
                risk = close - stop
                reward = target - close
                rr = reward / risk if risk > 0 else 0
                if rr < 1.2:
                    continue
                picks.append(
                    {
                        "symbol": row.get("symbol"),
                        "date": row.get("price_date"),
                        "close": close,
                        "entry": close,
                        "buy_zone": [round(close * 0.99, 4), round(close * 1.01, 4)],
                        "stop_loss": stop,
                        "take_profit": target,
                        "risk_pct": round(risk / close * 100, 2),
                        "reward_pct": round(reward / close * 100, 2),
                        "risk_reward": round(rr, 2),
                        "metrics": {
                            "ret_5d_pct": round((_float(row.get("ret_5d")) or 0) * 100, 2),
                            "ret_60d_pct": round((_float(row.get("ret_60d")) or 0) * 100, 2),
                            "dist_ma20_pct": round(
                                (_float(row.get("dist_ma20")) or 0) * 100, 2
                            ),
                            "volume_ratio": round(_float(row.get("volume_ratio")) or 0, 2),
                            "ma20": row.get("ma20"),
                            "ma50": row.get("ma50"),
                            "high_20d": row.get("high_20d"),
                        },
                    }
                )
                if len(picks) >= per_bucket:
                    break
            response["price_buckets"].append(
                {
                    "bucket": label,
                    "min_price": min_v,
                    "max_price": max_v,
                    "limit": per_bucket,
                    "recommendations": picks,
                }
            )
    return _to_json(response)


# ---------------------------------------------------------------------------
# Fundamentals-driven screeners
# ---------------------------------------------------------------------------

_VALUE_SQL = """
WITH latest_price AS (
    SELECT symbol, close, price_date,
           AVG(close) OVER (PARTITION BY symbol ORDER BY price_date
                            ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma200,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY price_date DESC) AS rn
    FROM market_data.us_equity_daily_prices
    WHERE provider = %(provider)s
),
latest_profile AS (
    SELECT DISTINCT ON (symbol) *
    FROM market_data.us_equity_company_profile
    ORDER BY symbol, as_of_date DESC
)
SELECT
    p.symbol,
    p.sector,
    p.industry,
    p.market_cap,
    lp.close,
    lp.ma200,
    p.trailing_pe,
    p.forward_pe,
    p.price_to_book,
    p.price_to_sales_ttm,
    p.return_on_equity,
    p.debt_to_equity,
    p.profit_margins,
    p.operating_margins,
    p.gross_margins,
    p.revenue_growth,
    p.earnings_growth,
    p.free_cashflow_ttm,
    p.operating_cashflow_ttm,
    p.total_debt,
    p.total_cash,
    p.dividend_yield,
    p.fifty_two_week_high,
    p.fifty_two_week_low,
    (lp.close / NULLIF(p.fifty_two_week_low, 0) - 1) AS dist_52w_low,
    (lp.close / NULLIF(p.fifty_two_week_high, 0))    AS pct_of_52w_high,
    (lp.close / NULLIF(lp.ma200, 0))                 AS price_to_ma200,
    CASE WHEN p.market_cap > 0 AND p.free_cashflow_ttm IS NOT NULL
         THEN p.free_cashflow_ttm / p.market_cap
         ELSE NULL
    END AS fcf_yield
FROM latest_profile p
JOIN latest_price lp ON lp.symbol = p.symbol AND lp.rn = 1
WHERE p.market_cap BETWEEN %(min_cap)s AND %(max_cap)s
  AND p.profit_margins > 0
  AND p.operating_margins > 0
  AND p.operating_cashflow_ttm > 0
  AND (%(require_fcf)s = FALSE OR p.free_cashflow_ttm > 0)
  AND p.debt_to_equity IS NOT NULL AND p.debt_to_equity < 1.5
  AND p.trailing_pe > 0 AND p.trailing_pe < 30
  AND p.price_to_book > 0 AND p.price_to_book < 5
  AND p.return_on_equity > 0.08
  AND p.fifty_two_week_low IS NOT NULL AND p.fifty_two_week_high IS NOT NULL
  AND (lp.close / NULLIF(p.fifty_two_week_low, 0) - 1) <= 0.30
  AND (lp.close / NULLIF(lp.ma200, 0)) BETWEEN 0.85 AND 1.10
ORDER BY (
    -p.trailing_pe * 0.04
    + p.return_on_equity * 2.0
    - p.price_to_book * 0.3
    + COALESCE(CASE WHEN p.market_cap > 0 THEN p.free_cashflow_ttm / p.market_cap END, 0) * 3.0
    + COALESCE(p.revenue_growth, 0) * 1.5
) DESC
LIMIT %(row_limit)s;
"""


@mcp.tool()
def value_candidates(
    market_cap_buckets: str = "0.3-2,2-10,10-200",
    limit_per_bucket: int = 3,
    provider: str = "yfinance",
    require_positive_cashflow: bool = True,
) -> str:
    """Value screener: profitable, cheap, near-trough companies.

    Buckets are in BILLIONS USD. Defaults target small-cap ($0.3–2B),
    mid-cap ($2–10B), and large-cap ($10–200B). Filters: positive profit /
    operating / operating-cash-flow, D/E < 1.5, trailing P/E in (0, 30),
    P/B in (0, 5), ROE > 8%, within 30% of 52-week low, price/MA200 in
    [0.85, 1.10] (i.e. near the 200-day anchor, not in free fall).

    Requires the fundamentals sync to have populated
    ``us_equity_company_profile``. Check ``db_health.fundamentals_coverage``
    first — if ``profile_symbols`` is low this tool will return empty
    results.

    No trade plan; value trades don't use short-term stops. The ``metrics``
    block summarizes the quality/cheapness signals so the caller can
    decide a scaling-in band.
    """
    buckets = _parse_buckets(market_cap_buckets)
    per_bucket = max(1, min(limit_per_bucket, 10))
    response: dict[str, Any] = {
        "provider": provider,
        "market_cap_buckets": [],
        "unit": "billion USD",
    }
    with open_db() as conn, conn.cursor() as cur:
        for label, min_b, max_b in buckets:
            cur.execute(
                _VALUE_SQL,
                {
                    "provider": provider,
                    "min_cap": min_b * 1e9,
                    "max_cap": max_b * 1e9,
                    "require_fcf": require_positive_cashflow,
                    "row_limit": per_bucket,
                },
            )
            rows = cur.fetchall()
            picks: list[dict[str, Any]] = []
            for row in rows:
                close = _float(row.get("close"))
                picks.append(
                    {
                        "symbol": row.get("symbol"),
                        "sector": row.get("sector"),
                        "industry": row.get("industry"),
                        "market_cap_b": round((_float(row.get("market_cap")) or 0) / 1e9, 3),
                        "close": close,
                        "suggested_scale_in_band": [
                            round(close * 0.95, 4) if close else None,
                            round(close * 1.02, 4) if close else None,
                        ],
                        "valuation": {
                            "trailing_pe": row.get("trailing_pe"),
                            "forward_pe": row.get("forward_pe"),
                            "price_to_book": row.get("price_to_book"),
                            "price_to_sales_ttm": row.get("price_to_sales_ttm"),
                            "fcf_yield_pct": round(
                                (_float(row.get("fcf_yield")) or 0) * 100, 2
                            ),
                        },
                        "quality": {
                            "return_on_equity": row.get("return_on_equity"),
                            "profit_margins": row.get("profit_margins"),
                            "operating_margins": row.get("operating_margins"),
                            "gross_margins": row.get("gross_margins"),
                            "debt_to_equity": row.get("debt_to_equity"),
                            "revenue_growth": row.get("revenue_growth"),
                            "earnings_growth": row.get("earnings_growth"),
                        },
                        "position": {
                            "pct_of_52w_high": round(
                                (_float(row.get("pct_of_52w_high")) or 0) * 100, 2
                            ),
                            "dist_52w_low_pct": round(
                                (_float(row.get("dist_52w_low")) or 0) * 100, 2
                            ),
                            "price_to_ma200": round(
                                _float(row.get("price_to_ma200")) or 0, 4
                            ),
                            "fifty_two_week_high": row.get("fifty_two_week_high"),
                            "fifty_two_week_low": row.get("fifty_two_week_low"),
                        },
                    }
                )
            response["market_cap_buckets"].append(
                {
                    "bucket": label,
                    "min_b": min_b,
                    "max_b": max_b,
                    "limit": per_bucket,
                    "recommendations": picks,
                }
            )
    return _to_json(response)


_COMPANY_PROFILE_SQL = """
SELECT *
FROM market_data.us_equity_company_profile
WHERE symbol = %(symbol)s
ORDER BY as_of_date DESC
LIMIT 1;
"""

_COMPANY_QFIN_SQL = """
SELECT *
FROM market_data.us_equity_quarterly_financials
WHERE symbol = %(symbol)s
ORDER BY report_date DESC
LIMIT 5;
"""

_SECTOR_MEDIAN_SQL = """
WITH latest_profile AS (
    SELECT DISTINCT ON (symbol) *
    FROM market_data.us_equity_company_profile
    ORDER BY symbol, as_of_date DESC
)
SELECT
    sector,
    COUNT(*) AS peer_count,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY trailing_pe)
        FILTER (WHERE trailing_pe > 0 AND trailing_pe < 500) AS median_trailing_pe,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_to_book)
        FILTER (WHERE price_to_book > 0 AND price_to_book < 50) AS median_price_to_book,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY return_on_equity)
        FILTER (WHERE return_on_equity IS NOT NULL) AS median_roe,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY profit_margins)
        FILTER (WHERE profit_margins IS NOT NULL) AS median_profit_margin
FROM latest_profile
WHERE sector = %(sector)s
GROUP BY sector;
"""


@mcp.tool()
def company_fundamentals(symbol: str) -> str:
    """Deep-dive profile + last 4–5 quarters + sector medians for one ticker.

    Useful right after ``value_candidates`` surfaces a name — the LLM can see
    the trend direction of revenue / operating income / free cash flow and
    compare trailing P/E and P/B to the sector's median. Returns
    ``{"symbol": ..., "error": "no fundamentals data"}`` if the profile has
    not been synced.
    """
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    with open_db() as conn, conn.cursor() as cur:
        cur.execute(_COMPANY_PROFILE_SQL, {"symbol": sym})
        profile = cur.fetchone()
        if profile is None:
            return _to_json({"symbol": sym, "error": "no fundamentals data"})
        cur.execute(_COMPANY_QFIN_SQL, {"symbol": sym})
        quarterly = cur.fetchall()
        sector_median = None
        if profile.get("sector"):
            cur.execute(_SECTOR_MEDIAN_SQL, {"sector": profile["sector"]})
            sector_median = cur.fetchone()
    return _to_json(
        {
            "symbol": sym,
            "profile": profile,
            "quarterly_financials": quarterly,
            "sector_median": sector_median,
        }
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the TradingAgents market-data MCP server."
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.getenv("MARKET_DATA_MCP_TRANSPORT", "stdio"),
        help=(
            "MCP transport. Use stdio for one client process; use sse or "
            "streamable-http for a shared long-running service."
        ),
    )
    parser.add_argument(
        "--host",
        default=os.getenv("FASTMCP_HOST", "127.0.0.1"),
        help="Host for sse/streamable-http transports.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("FASTMCP_PORT", "18080")),
        help="Port for sse/streamable-http transports.",
    )
    parser.add_argument(
        "--mount-path",
        default=os.getenv("FASTMCP_MOUNT_PATH", "/"),
        help="Optional mount path for SSE transport.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport, mount_path=args.mount_path)
