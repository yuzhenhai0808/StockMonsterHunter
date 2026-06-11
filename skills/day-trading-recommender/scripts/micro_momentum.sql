-- 微盘爆拉候选初筛
--
-- 用途: micro-pump 模式下找市值小、近期涨幅大、仍有流动性的候选。
--        本 SQL 的输出必须结合 red-flags.md 的微盘 Pump 评分表做二次筛选。
--
-- 用法: 通过 query_market_data MCP 工具执行。无参数。
--
-- 筛选条件:
--   市值 $1000 万 - $5 亿(微盘区间)
--   近 3 日涨幅 > 50% 或近 5 日涨幅 > 80%
--   20 日均成交量 >= 10 万股(保证能出)
--   昨日收盘 >= $1(排除粉单)
--   昨日收盘 < 前一交易日(今天可能已经回调,适合入场) OR
--   仍在上涨中(取决于用户偏好)

WITH price_base AS (
  SELECT
    p.symbol, p.price_date, p.close, p.volume,
    LAG(p.close, 3) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_3d_ago,
    LAG(p.close, 5) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_5d_ago,
    LAG(p.close, 1) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close,
    AVG(p.volume) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol_20d
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= CURRENT_DATE - INTERVAL '40 days'
),
latest AS (
  SELECT * FROM price_base
  WHERE price_date = (SELECT MAX(price_date) FROM price_base)
)
SELECT
  l.symbol,
  l.price_date,
  ROUND(l.close::numeric, 2) AS close,
  ROUND((100 * (l.close - l.close_3d_ago) / NULLIF(l.close_3d_ago, 0))::numeric, 1) AS ret_3d_pct,
  ROUND((100 * (l.close - l.close_5d_ago) / NULLIF(l.close_5d_ago, 0))::numeric, 1) AS ret_5d_pct,
  ROUND((l.volume::numeric / NULLIF(l.avg_vol_20d, 0)), 2) AS vol_ratio,
  ROUND(l.avg_vol_20d::numeric, 0) AS avg_vol_20d,
  f.sector,
  f.industry,
  ROUND((f.market_cap / 1e6)::numeric, 1) AS market_cap_m,
  ROUND((f.float_shares / 1e6)::numeric, 2) AS float_m,
  ROUND((100 * COALESCE(f.short_percent_of_float, 0))::numeric, 1) AS short_pct,
  f.fifty_two_week_high,
  ROUND((f.fifty_two_week_high / NULLIF(l.close, 0))::numeric, 1) AS highs_ratio,
  ROUND((f.total_revenue_ttm / 1e6)::numeric, 2) AS revenue_ttm_m
FROM latest l
JOIN market_data.us_equity_company_profile f ON f.symbol = l.symbol
WHERE f.market_cap BETWEEN 10000000 AND 500000000
  AND l.close >= 1
  AND l.avg_vol_20d >= 100000
  AND (
    (l.close - l.close_3d_ago) / NULLIF(l.close_3d_ago, 0) > 0.50
    OR (l.close - l.close_5d_ago) / NULLIF(l.close_5d_ago, 0) > 0.80
  )
ORDER BY (l.close - l.close_3d_ago) / NULLIF(l.close_3d_ago, 0) DESC
LIMIT 25;
