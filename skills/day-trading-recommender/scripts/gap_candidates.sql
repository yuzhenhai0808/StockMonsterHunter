-- 盘前 gap 候选初筛
--
-- 用途: 在 premarket-gap 模式下,从本地数据库拿昨日有异常量能/接近高点的股票,
--        再用 WebSearch 补盘前 gap 信息。本 SQL 只做本地初筛。
--
-- 用法: 通过 query_market_data MCP 工具执行。无参数。
--
-- 筛选条件:
--   市值 >= $5 亿(排除微盘,微盘走 micro_momentum.sql)
--   20 日均成交额 >= $5M(日内流动性硬线)
--   昨日收盘接近 20 日高 (close/high_20d >= 0.95)
--   昨日量比 >= 1.5x(已有放量迹象)
--   排除 Healthcare/Biotech(二元风险)

WITH price_features AS (
  SELECT
    p.symbol, p.price_date, p.close, p.volume,
    MAX(p.high) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
    MIN(p.low) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                     ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS low_20d,
    AVG(p.volume) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol_20d
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= CURRENT_DATE - INTERVAL '60 days'
),
latest AS (
  SELECT * FROM price_features
  WHERE price_date = (SELECT MAX(price_date) FROM price_features)
)
SELECT
  l.symbol,
  l.price_date,
  ROUND(l.close::numeric, 2) AS close,
  ROUND((100 * l.close / l.high_20d)::numeric, 1) AS pct_of_20d_high,
  ROUND((l.volume::numeric / NULLIF(l.avg_vol_20d, 0)), 2) AS vol_ratio,
  ROUND((l.close * l.avg_vol_20d / 1e6)::numeric, 1) AS avg_dollar_vol_m,
  f.sector,
  f.industry,
  ROUND((f.market_cap / 1e9)::numeric, 2) AS market_cap_b,
  ROUND((100 * COALESCE(f.short_percent_of_float, 0))::numeric, 1) AS short_pct,
  ROUND((f.float_shares / 1e6)::numeric, 1) AS float_m,
  f.fifty_two_week_high,
  ROUND((l.close / NULLIF(f.fifty_two_week_high, 0) * 100)::numeric, 1) AS pct_of_52w_high
FROM latest l
JOIN market_data.us_equity_company_profile f ON f.symbol = l.symbol
WHERE f.market_cap >= 500000000
  AND l.close * l.avg_vol_20d >= 5000000
  AND l.close / NULLIF(l.high_20d, 0) >= 0.95
  AND l.volume::numeric / NULLIF(l.avg_vol_20d, 0) >= 1.5
  AND f.sector NOT IN ('Healthcare')
  AND l.close >= 3
ORDER BY (l.volume::numeric / NULLIF(l.avg_vol_20d, 0)) DESC,
         (100 * l.close / l.high_20d) DESC
LIMIT 25;
