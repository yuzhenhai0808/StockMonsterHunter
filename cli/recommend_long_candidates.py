#!/usr/bin/env python3
"""Recommend long-only momentum trade candidates by price bucket.

The screener favors liquid stocks that are already confirming upward momentum,
from high-beta small caps to larger companies that still have enough volatility
for a short-term trade.  It reuses the same PostgreSQL connection resolution as
the market-data MCP server and sync scripts.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_str = str(PROJECT_ROOT)
if sys.path[0] != project_root_str:
    sys.path.insert(0, project_root_str)

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")

from cli.fetch_fmp_openbb_to_postgres import open_db


DEFAULT_PRICE_BUCKETS = "1-10,10-100,100-500"
DEFAULT_MIN_ATR_PCT = 0.045


SCREEN_SQL = """
WITH p AS (
    SELECT
        symbol,
        price_date,
        open,
        high,
        low,
        close,
        volume,
        lag(close, 1) OVER (PARTITION BY symbol ORDER BY price_date) AS close_1d_ago,
        lag(close, 5) OVER (PARTITION BY symbol ORDER BY price_date) AS close_5d_ago,
        lag(close, 10) OVER (PARTITION BY symbol ORDER BY price_date) AS close_10d_ago,
        avg(close) OVER (
            PARTITION BY symbol ORDER BY price_date
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS ma10,
        avg(close) OVER (
            PARTITION BY symbol ORDER BY price_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS ma20,
        avg(volume) OVER (
            PARTITION BY symbol ORDER BY price_date
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS avg_vol_20d,
        max(high) OVER (
            PARTITION BY symbol ORDER BY price_date
            ROWS BETWEEN 20 PRECEDING AND CURRENT ROW
        ) AS high_20d,
        min(low) OVER (
            PARTITION BY symbol ORDER BY price_date
            ROWS BETWEEN 20 PRECEDING AND CURRENT ROW
        ) AS low_20d
    FROM market_data.us_equity_daily_prices
    WHERE provider = %(provider)s
),
latest AS (
    SELECT *
    FROM p
    WHERE price_date = (
        SELECT MAX(price_date)
        FROM market_data.us_equity_daily_prices
        WHERE provider = %(provider)s
    )
),
scored AS (
    SELECT
        *,
        ((close / NULLIF(close_1d_ago, 0)) - 1) AS ret_1d,
        ((close / NULLIF(close_5d_ago, 0)) - 1) AS ret_5d,
        ((close / NULLIF(close_10d_ago, 0)) - 1) AS ret_10d,
        ((high_20d / NULLIF(low_20d, 0)) - 1) AS range_20d,
        (volume / NULLIF(avg_vol_20d, 0)) AS volume_ratio,
        (close / NULLIF(high_20d, 0)) AS close_to_high_ratio,
        ((close - low) / NULLIF(high - low, 0)) AS close_position,
        (close * volume) AS dollar_volume
    FROM latest
)
SELECT
    symbol,
    price_date,
    open,
    high,
    low,
    close,
    volume,
    ma10,
    ma20,
    high_20d,
    low_20d,
    dollar_volume,
    ret_1d,
    ret_5d,
    ret_10d,
    range_20d,
    volume_ratio,
    close_to_high_ratio,
    close_position
FROM scored
WHERE close >= %(min_price)s
  AND close < %(max_price)s
  AND close_1d_ago BETWEEN 0.5 AND %(comparison_max_price)s
  AND close_5d_ago BETWEEN 0.5 AND %(comparison_max_price)s
  AND close_10d_ago BETWEEN 0.5 AND %(comparison_max_price)s
  AND volume > %(min_volume)s
  AND avg_vol_20d > %(min_avg_volume)s
  AND dollar_volume > %(min_dollar_volume)s
  AND close > ma10
  AND ma10 >= ma20
  AND ret_1d BETWEEN -0.08 AND 0.80
  AND ret_5d BETWEEN 0.05 AND 2.50
  AND ret_10d BETWEEN 0.08 AND 3.50
  AND range_20d BETWEEN 0.25 AND 6.00
  AND volume_ratio > 1.15
  AND close_to_high_ratio > 0.72
  AND close_position > %(min_close_position)s
ORDER BY
    (
        ret_5d * 1.8
        + ret_10d * 1.2
        + range_20d * 0.8
        + LEAST(volume_ratio, 6) * 0.18
        + close_to_high_ratio * 1.1
        + close_position * 0.7
    ) DESC
LIMIT %(candidate_limit)s;
"""


HISTORY_SQL = """
SELECT price_date, open, high, low, close, volume
FROM market_data.us_equity_daily_prices
WHERE provider = %s AND symbol = %s
ORDER BY price_date DESC
LIMIT %s;
"""


@dataclass
class Candidate:
    symbol: str
    price_date: date
    close: float
    volume: int
    dollar_volume: float
    ret_1d: float
    ret_5d: float
    ret_10d: float
    range_20d: float
    volume_ratio: float
    close_to_high_ratio: float
    close_position: float
    ma10: float
    ma20: float
    high_20d: float
    low_20d: float
    atr14: float
    score: float
    entry: float
    buy_low: float
    buy_high: float
    stop_loss: float
    take_profit: float
    risk_pct: float
    reward_pct: float
    rr: float


@dataclass(frozen=True)
class PriceBucket:
    label: str
    min_price: float
    max_price: float


def _float(value: Any) -> float:
    if value is None:
        return math.nan
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _round_price(value: float) -> float:
    if value < 2:
        return round(value, 3)
    if value < 20:
        return round(value, 2)
    return round(value, 2)


def _atr14(rows_desc: list[dict[str, Any]]) -> float:
    rows = list(reversed(rows_desc))
    true_ranges: list[float] = []
    prev_close: float | None = None
    for row in rows:
        high = _float(row["high"])
        low = _float(row["low"])
        close = _float(row["close"])
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
        prev_close = close
    if not true_ranges:
        return math.nan
    return sum(true_ranges[-14:]) / min(14, len(true_ranges))


def _score(row: dict[str, Any], atr14: float) -> float:
    close = _float(row["close"])
    atr_pct = atr14 / close if close else 0.0
    ret_5d = _float(row["ret_5d"])
    ret_10d = _float(row["ret_10d"])
    range_20d = _float(row["range_20d"])
    volume_ratio = _float(row["volume_ratio"])
    close_to_high = _float(row["close_to_high_ratio"])
    close_position = _float(row["close_position"])
    ret_1d = _float(row["ret_1d"])

    momentum = min(ret_5d, 0.75) * 1.6 + min(ret_10d, 1.1) * 1.0
    volatility = min(range_20d, 2.0) * 0.75 + min(atr_pct, 0.25) * 1.5
    volume = min(volume_ratio, 6.0) * 0.2
    quality = close_to_high * 1.2 + close_position * 0.9
    extension_penalty = max(ret_5d - 0.9, 0) * 1.2 + max(ret_1d - 0.35, 0) * 1.0
    weak_close_penalty = max(0.55 - close_position, 0) * 1.5
    return momentum + volatility + volume + quality - extension_penalty - weak_close_penalty


def _build_trade_plan(row: dict[str, Any], atr14: float) -> tuple[float, float, float, float, float, float, float]:
    close = _float(row["close"])
    low = _float(row["low"])
    ma10 = _float(row["ma10"])
    high_20d = _float(row["high_20d"])

    entry = close
    buy_low = close * 0.985
    buy_high = close * 1.015

    atr_stop = close - 1.15 * atr14
    trend_stop = ma10 * 0.97
    day_low_stop = low * 0.98
    raw_stop = max(atr_stop, trend_stop, day_low_stop)

    max_risk_stop = close * 0.82
    min_risk_stop = close * 0.94
    stop_loss = min(raw_stop, min_risk_stop)
    stop_loss = max(stop_loss, max_risk_stop)

    risk = max(close - stop_loss, close * 0.04)
    breakout_target = max(high_20d * 1.08, close + 1.8 * risk, close + 1.35 * atr14)
    take_profit = breakout_target

    risk_pct = risk / close
    reward_pct = (take_profit - close) / close
    rr = reward_pct / risk_pct if risk_pct else 0.0
    return (
        _round_price(entry),
        _round_price(buy_low),
        _round_price(buy_high),
        _round_price(stop_loss),
        _round_price(take_profit),
        risk_pct,
        reward_pct,
        rr,
    )


def _to_result(candidate: Candidate) -> dict[str, Any]:
    return {
        "symbol": candidate.symbol,
        "date": candidate.price_date.isoformat(),
        "close": _round_price(candidate.close),
        "entry": candidate.entry,
        "buy_zone": [_round_price(candidate.buy_low), _round_price(candidate.buy_high)],
        "stop_loss": candidate.stop_loss,
        "take_profit": candidate.take_profit,
        "risk_pct": round(candidate.risk_pct * 100, 2),
        "reward_pct": round(candidate.reward_pct * 100, 2),
        "risk_reward": round(candidate.rr, 2),
        "score": round(candidate.score, 4),
        "metrics": {
            "ret_1d_pct": round(candidate.ret_1d * 100, 2),
            "ret_5d_pct": round(candidate.ret_5d * 100, 2),
            "ret_10d_pct": round(candidate.ret_10d * 100, 2),
            "range_20d_pct": round(candidate.range_20d * 100, 2),
            "volume_ratio": round(candidate.volume_ratio, 2),
            "close_to_20d_high_pct": round(candidate.close_to_high_ratio * 100, 2),
            "close_position_pct": round(candidate.close_position * 100, 2),
            "atr14": _round_price(candidate.atr14),
            "dollar_volume": round(candidate.dollar_volume),
        },
    }


def _format_bucket_edge(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value).rstrip("0").rstrip(".")


def parse_price_buckets(raw: str) -> list[PriceBucket]:
    buckets: list[PriceBucket] = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "-" not in item:
            raise argparse.ArgumentTypeError(f"Invalid bucket {item!r}; expected MIN-MAX.")
        raw_min, raw_max = item.split("-", 1)
        try:
            min_price = float(raw_min)
            max_price = float(raw_max)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"Invalid bucket {item!r}; prices must be numbers.") from exc
        if min_price <= 0 or max_price <= min_price:
            raise argparse.ArgumentTypeError(f"Invalid bucket {item!r}; require 0 < MIN < MAX.")
        label = f"{_format_bucket_edge(min_price)}-{_format_bucket_edge(max_price)}"
        buckets.append(PriceBucket(label=label, min_price=min_price, max_price=max_price))
    if not buckets:
        raise argparse.ArgumentTypeError("At least one price bucket is required.")
    return buckets


def _default_min_atr_pct_for_bucket(bucket: PriceBucket) -> float:
    if bucket.min_price >= 100:
        return 0.02
    if bucket.min_price >= 10:
        return 0.035
    return DEFAULT_MIN_ATR_PCT


def _recommend_with_connection(args: argparse.Namespace, conn: Any) -> list[Candidate]:
    params = {
        "provider": args.provider,
        "min_price": args.min_price,
        "max_price": args.max_price,
        "comparison_max_price": max(args.max_price * 1.5, args.max_price + 25),
        "min_volume": args.min_volume,
        "min_avg_volume": args.min_avg_volume,
        "min_dollar_volume": args.min_dollar_volume,
        "min_close_position": args.min_close_position,
        "candidate_limit": args.candidate_limit,
    }

    recommendations: list[Candidate] = []
    with conn.cursor() as cur:
        cur.execute(SCREEN_SQL, params)
        rows = cur.fetchall()

        for row in rows:
            symbol = row["symbol"]
            cur.execute(HISTORY_SQL, (args.provider, symbol, args.history_days))
            history = cur.fetchall()
            atr14 = _atr14(history)
            if not math.isfinite(atr14) or atr14 <= 0:
                continue

            close = _float(row["close"])
            atr_pct = atr14 / close
            if atr_pct < args.min_atr_pct or atr_pct > args.max_atr_pct:
                continue

            entry, buy_low, buy_high, stop_loss, take_profit, risk_pct, reward_pct, rr = _build_trade_plan(row, atr14)
            if rr < args.min_rr:
                continue

            score = _score(row, atr14)
            recommendations.append(
                Candidate(
                    symbol=symbol,
                    price_date=row["price_date"],
                    close=close,
                    volume=int(row["volume"]),
                    dollar_volume=_float(row["dollar_volume"]),
                    ret_1d=_float(row["ret_1d"]),
                    ret_5d=_float(row["ret_5d"]),
                    ret_10d=_float(row["ret_10d"]),
                    range_20d=_float(row["range_20d"]),
                    volume_ratio=_float(row["volume_ratio"]),
                    close_to_high_ratio=_float(row["close_to_high_ratio"]),
                    close_position=_float(row["close_position"]),
                    ma10=_float(row["ma10"]),
                    ma20=_float(row["ma20"]),
                    high_20d=_float(row["high_20d"]),
                    low_20d=_float(row["low_20d"]),
                    atr14=atr14,
                    score=score,
                    entry=entry,
                    buy_low=buy_low,
                    buy_high=buy_high,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_pct=risk_pct,
                    reward_pct=reward_pct,
                    rr=rr,
                )
            )

    recommendations.sort(key=lambda item: item.score, reverse=True)
    return recommendations[: max(1, min(args.limit, 2))]


def recommend(args: argparse.Namespace) -> list[Candidate]:
    with open_db(args.db_host, args.db_port, args.db_name, args.db_user, args.db_password) as conn:
        return _recommend_with_connection(args, conn)


def recommend_by_bucket(args: argparse.Namespace) -> dict[str, Any]:
    buckets = args.price_buckets
    per_bucket_limit = max(1, min(args.per_bucket_limit or args.limit, 2))
    result: dict[str, Any] = {
        "provider": args.provider,
        "price_buckets": [],
    }
    with open_db(args.db_host, args.db_port, args.db_name, args.db_user, args.db_password) as conn:
        for bucket in buckets:
            bucket_args = argparse.Namespace(**vars(args))
            bucket_args.min_price = bucket.min_price
            bucket_args.max_price = bucket.max_price
            bucket_args.limit = per_bucket_limit
            if math.isclose(args.min_atr_pct, DEFAULT_MIN_ATR_PCT):
                bucket_args.min_atr_pct = _default_min_atr_pct_for_bucket(bucket)
            picks = _recommend_with_connection(bucket_args, conn)
            result["price_buckets"].append(
                {
                    "bucket": bucket.label,
                    "min_price": bucket.min_price,
                    "max_price": bucket.max_price,
                    "limit": per_bucket_limit,
                    "recommendations": [_to_result(item) for item in picks],
                }
            )
    return result


@dataclass
class ScreenParams:
    """Parameters accepted by the screener, mirroring the CLI argparse defaults.

    Any caller (MCP tools, notebooks, tests) can construct this instead of going
    through argparse, then call :func:`run_recommendation_bucketed` or
    :func:`run_recommendation_flat`.
    """

    provider: str = "yfinance"
    limit: int = 2
    per_bucket_limit: int | None = None
    min_price: float = 1.0
    max_price: float = 500.0
    min_volume: int = 1_000_000
    min_avg_volume: int = 150_000
    min_dollar_volume: float = 3_000_000
    min_close_position: float = 0.65
    min_atr_pct: float = DEFAULT_MIN_ATR_PCT
    max_atr_pct: float = 0.28
    min_rr: float = 1.6
    candidate_limit: int = 80
    history_days: int = 80
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None


def _params_to_namespace(
    params: ScreenParams,
    *,
    price_buckets: list[PriceBucket] | None = None,
) -> argparse.Namespace:
    ns = argparse.Namespace(**params.__dict__)
    if price_buckets is not None:
        ns.price_buckets = price_buckets
    return ns


def run_recommendation_bucketed(
    params: ScreenParams,
    buckets: list[PriceBucket],
) -> dict[str, Any]:
    """Run the screener for each price bucket and return the rich JSON payload.

    This is the programmatic equivalent of
    ``python cli/recommend_long_candidates.py`` without ``--flat``. Output
    schema matches the CLI exactly.
    """
    return recommend_by_bucket(_params_to_namespace(params, price_buckets=buckets))


def run_recommendation_flat(params: ScreenParams) -> list[dict[str, Any]]:
    """Run the screener in flat mode (single price range, no bucketing)."""
    ns = _params_to_namespace(params)
    picks = recommend(ns)
    return [_to_result(item) for item in picks]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recommend long-only momentum trades by price bucket."
    )
    parser.add_argument("--provider", default="yfinance")
    parser.add_argument("--limit", type=int, default=2, help="Maximum recommendations in flat mode; capped at 2.")
    parser.add_argument("--per-bucket-limit", type=int, default=None, help="Maximum recommendations per price bucket; capped at 2.")
    parser.add_argument(
        "--price-buckets",
        type=parse_price_buckets,
        default=parse_price_buckets(DEFAULT_PRICE_BUCKETS),
        help="Comma-separated price buckets, e.g. 1-10,10-100,100-500.",
    )
    parser.add_argument("--flat", action="store_true", help="Disable bucketed output and use --min-price/--max-price only.")
    parser.add_argument("--candidate-limit", type=int, default=80)
    parser.add_argument("--history-days", type=int, default=80)
    parser.add_argument("--min-price", type=float, default=1.0)
    parser.add_argument("--max-price", type=float, default=500.0)
    parser.add_argument("--min-volume", type=int, default=1_000_000)
    parser.add_argument("--min-avg-volume", type=int, default=150_000)
    parser.add_argument("--min-dollar-volume", type=float, default=3_000_000)
    parser.add_argument("--min-close-position", type=float, default=0.65)
    parser.add_argument("--min-atr-pct", type=float, default=DEFAULT_MIN_ATR_PCT)
    parser.add_argument("--max-atr-pct", type=float, default=0.28)
    parser.add_argument("--min-rr", type=float, default=1.6)
    parser.add_argument("--db-host", default=None)
    parser.add_argument("--db-port", type=int, default=None)
    parser.add_argument("--db-name", default=None)
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.flat:
        picks = recommend(args)
        payload: Any = [_to_result(item) for item in picks]
    else:
        payload = recommend_by_bucket(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
