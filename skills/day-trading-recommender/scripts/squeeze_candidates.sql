-- 轧空候选初筛
--
-- 用途: short-squeeze 模式下找高空头 + 低流通 + 近期启动中的股。
--        本 SQL 只做初筛,还要在 references/modes/short-squeeze.md 的 Step 2-4 做动能/催化/借券费率验证。
--
-- 用法: 通过 query_market_data MCP 工具执行。无参数。
--
-- 筛选条件:
--   空头占流通 >= 15%
--   流通股 <= 2000 万
--   近 5 日涨幅 >= 10%(已启动)
--   市值 $1 亿 - $50 亿(避开超微盘和大盘)
--   20 日均成交额 >= $5M
--   排除 Healthcare(临床数据驱动,不是真轧空)

WITH price_base AS (
  SELECT
    p.symbol, p.price_date, p.close, p.volume,
    LAG(p.close, 5) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_5d_ago,
    MAX(p.high) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
    AVG(p.volume) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol_20d,
    AVG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
    AVG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                       ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= CURRENT_DATE - INTERVAL '90 days'
),
latest AS (
  SELECT * FROM price_base
  WHERE price_date = (SELECT MAX(price_date) FROM price_base)
)
SELECT
  l.symbol,
  l.price_date,
  ROUND(l.close::numeric, 2) AS close,
  ROUND((100 * (l.close - l.close_5d_ago) / NULLIF(l.close_5d_ago, 0))::numeric, 1) AS ret_5d_pct,
  ROUND((100 * l.close / NULLIF(l.high_20d, 0))::numeric, 1) AS pct_of_20d_high,
  ROUND((l.volume::numeric / NULLIF(l.avg_vol_20d, 0)), 2) AS vol_ratio,
  ROUND((l.close * l.avg_vol_20d / 1e6)::numeric, 1) AS avg_dollar_vol_m,
  f.sector,
  f.industry,
  ROUND((f.market_cap / 1e9)::numeric, 2) AS market_cap_b,
  ROUND((f.float_shares / 1e6)::numeric, 1) AS float_m,
  ROUND((100 * f.short_percent_of_float)::numeric, 1) AS short_pct_of_float,
  ROUND(f.short_ratio::numeric, 2) AS days_to_cover,
  CASE
    WHEN l.close > l.ma20 AND l.ma20 > l.ma50 THEN 'bullish_stack'
    WHEN l.close > l.ma20 THEN 'above_ma20'
    ELSE 'weak'
  END AS trend
FROM latest l
JOIN market_data.us_equity_company_profile f ON f.symbol = l.symbol
WHERE f.short_percent_of_float >= 0.15
  AND f.float_shares <= 20000000
  AND (l.close - l.close_5d_ago) / NULLIF(l.close_5d_ago, 0) >= 0.10
  AND f.market_cap BETWEEN 100000000 AND 5000000000
  AND l.close * l.avg_vol_20d >= 5000000
  AND f.sector NOT IN ('Healthcare')
ORDER BY f.short_percent_of_float DESC,
         (l.close - l.close_5d_ago) / NULLIF(l.close_5d_ago, 0) DESC
LIMIT 20;
