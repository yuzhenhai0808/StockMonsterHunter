-- monster-stock-hunter: leaderboard_audit.sql
--
-- 用途: 最近完整日线的全市场防漏审计,合并三类池:
--   1) 涨幅榜
--   2) 低价高成交妖股
--   3) 高成交额强动量
--
-- 适用模式: 默认双主线妖股雷达、榜首龙头、准妖股挑战者、复盘防漏。
-- 执行方式: 通过 TradingAgents MCP 的 query_market_data 执行本 SQL。
-- 输出字段: pool, pool_rank, symbol, price_date, close, ret_1d_pct,
--   volume, dollar_volume_m, volume_ratio_20d, day_range_pct。

WITH base AS (
  SELECT
    p.symbol,
    p.price_date,
    p.open,
    p.high,
    p.low,
    p.close,
    p.volume,
    LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close,
    AVG(p.volume) OVER (
      PARTITION BY p.symbol ORDER BY p.price_date
      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS avg_volume_20d
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= CURRENT_DATE - INTERVAL '60 days'
),
latest AS (
  SELECT *
  FROM base
  WHERE price_date = (SELECT MAX(price_date) FROM base)
    AND prev_close IS NOT NULL
),
features AS (
  SELECT
    symbol,
    price_date,
    close,
    volume,
    close * volume AS dollar_volume,
    volume::numeric / NULLIF(avg_volume_20d, 0) AS volume_ratio_20d,
    100 * (close - prev_close) / NULLIF(prev_close, 0) AS ret_1d_pct,
    100 * (high - low) / NULLIF(close, 0) AS day_range_pct
  FROM latest
),
ranked AS (
  SELECT '涨幅榜' AS pool, *, ROW_NUMBER() OVER (ORDER BY ret_1d_pct DESC) AS pool_rank
  FROM features
  WHERE close >= 0.5
    AND volume >= 1000000

  UNION ALL

  SELECT '低价高成交妖股' AS pool, *, ROW_NUMBER() OVER (ORDER BY ret_1d_pct DESC, dollar_volume DESC) AS pool_rank
  FROM features
  WHERE close BETWEEN 0.5 AND 20
    AND volume >= 1000000
    AND dollar_volume >= 10000000
    AND ret_1d_pct >= 20

  UNION ALL

  SELECT '高成交额强动量' AS pool, *, ROW_NUMBER() OVER (ORDER BY dollar_volume DESC, ret_1d_pct DESC) AS pool_rank
  FROM features
  WHERE close >= 1
    AND volume >= 5000000
    AND dollar_volume >= 50000000
    AND ret_1d_pct > 0
)
SELECT
  pool,
  pool_rank,
  symbol,
  price_date,
  ROUND(close::numeric, 2) AS close,
  ROUND(ret_1d_pct::numeric, 2) AS ret_1d_pct,
  volume,
  ROUND((dollar_volume / 1000000)::numeric, 2) AS dollar_volume_m,
  ROUND(volume_ratio_20d::numeric, 2) AS volume_ratio_20d,
  ROUND(day_range_pct::numeric, 2) AS day_range_pct
FROM ranked
WHERE pool_rank <= CASE
  WHEN pool = '涨幅榜' THEN 30
  WHEN pool = '低价高成交妖股' THEN 50
  ELSE 40
END
ORDER BY pool, pool_rank;
