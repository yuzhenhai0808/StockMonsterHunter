-- monster-stock-hunter: reclaim_bounce_audit.sql
--
-- 用途: 强票急跌后的二次修复/反包初筛,寻找前期强势、当前回撤、
--   但仍有反抽空间的候选。实时 VWAP/reclaim 必须再用 Web/盘口确认。
--
-- 适用模式: 二次修复/反包猎手、默认妖股雷达的二次点火池、复盘。
-- 执行方式: 通过 TradingAgents MCP 的 query_market_data 执行本 SQL。
-- 输出字段: symbol, price_date, close, ret_5d_pct, pullback_from_5d_high_pct,
--   volume_ratio_20d, dollar_volume_m, close_vs_prev_pct, reclaim_watch。

WITH base AS (
  SELECT
    p.symbol,
    p.price_date,
    p.high,
    p.low,
    p.close,
    p.volume,
    LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close,
    LAG(p.close, 5) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_5d_ago,
    MAX(p.high) OVER (
      PARTITION BY p.symbol ORDER BY p.price_date
      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
    ) AS high_5d,
    AVG(p.volume) OVER (
      PARTITION BY p.symbol ORDER BY p.price_date
      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS avg_volume_20d
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= CURRENT_DATE - INTERVAL '80 days'
),
latest AS (
  SELECT *
  FROM base
  WHERE price_date = (SELECT MAX(price_date) FROM base)
    AND prev_close IS NOT NULL
    AND close_5d_ago IS NOT NULL
),
features AS (
  SELECT
    symbol,
    price_date,
    close,
    volume,
    close * volume AS dollar_volume,
    volume::numeric / NULLIF(avg_volume_20d, 0) AS volume_ratio_20d,
    100 * (close - close_5d_ago) / NULLIF(close_5d_ago, 0) AS ret_5d_pct,
    100 * (close - prev_close) / NULLIF(prev_close, 0) AS close_vs_prev_pct,
    100 * (high_5d - close) / NULLIF(high_5d, 0) AS pullback_from_5d_high_pct,
    100 * (close - low) / NULLIF(close, 0) AS bounce_from_day_low_pct
  FROM latest
)
SELECT
  symbol,
  price_date,
  ROUND(close::numeric, 2) AS close,
  ROUND(ret_5d_pct::numeric, 2) AS ret_5d_pct,
  ROUND(pullback_from_5d_high_pct::numeric, 2) AS pullback_from_5d_high_pct,
  ROUND(volume_ratio_20d::numeric, 2) AS volume_ratio_20d,
  ROUND((dollar_volume / 1000000)::numeric, 2) AS dollar_volume_m,
  ROUND(close_vs_prev_pct::numeric, 2) AS close_vs_prev_pct,
  ROUND(bounce_from_day_low_pct::numeric, 2) AS bounce_from_day_low_pct,
  CASE
    WHEN close_vs_prev_pct >= 0 THEN '已收复昨收,查VWAP'
    WHEN bounce_from_day_low_pct >= 8 THEN '低点反抽,待reclaim'
    ELSE '仅观察'
  END AS reclaim_watch
FROM features
WHERE ret_5d_pct >= 20
  AND pullback_from_5d_high_pct BETWEEN 5 AND 35
  AND dollar_volume >= 10000000
  AND volume_ratio_20d >= 1.5
ORDER BY volume_ratio_20d DESC, dollar_volume DESC
LIMIT 50;
