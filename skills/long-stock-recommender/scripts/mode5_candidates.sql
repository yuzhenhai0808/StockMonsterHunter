-- 模式 5 财报跳空候选筛选
--
-- 用途: 找未来 3-14 天要公布财报,且符合硬性门槛的候选
--
-- 用法: 通过 query_market_data MCP 工具直接执行
--
-- 硬性门槛(全部必须满足,见 references/modes/earnings-gap.md 步骤 2):
--   市值 $5 亿 - $100 亿
--   20 日均成交额 > $1000 万
--   当前价距 20 日高 > 10% (close/high_20d < 0.90)
--   Sector ∈ {Tech, Communication, Industrials, Consumer Cyclical}
--   硬排除 Healthcare/Biotech
--   营收同比 > 10%
--   (RSI < 65 需另外用 symbol_snapshot MCP 确认,此 SQL 给初筛)

WITH earnings_window AS (
  SELECT symbol, earnings_date, eps_estimate, timing
  FROM market_data.us_equity_earnings
  WHERE earnings_date BETWEEN CURRENT_DATE + 3 AND CURRENT_DATE + 14
    AND eps_estimate IS NOT NULL
),
price_features AS (
  SELECT
    p.symbol, p.price_date, p.close, p.volume,
    MAX(p.high) OVER (PARTITION BY p.symbol ORDER BY p.price_date
                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
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
  e.symbol, e.earnings_date, e.timing,
  ROUND(l.close::numeric, 2) AS close,
  ROUND((100 * l.close / l.high_20d)::numeric, 1) AS pct_of_20d_high,
  ROUND((l.close * l.avg_vol_20d / 1e6)::numeric, 1) AS avg_dollar_vol_m,
  f.sector, f.industry,
  ROUND((f.market_cap / 1e9)::numeric, 2) AS market_cap_b,
  ROUND((100 * f.revenue_growth)::numeric, 1) AS rev_growth_pct,
  ROUND((100 * COALESCE(f.earnings_growth, 0))::numeric, 1) AS earnings_growth_pct,
  ROUND((100 * f.short_percent_of_float)::numeric, 1) AS short_pct,
  ROUND((100 * f.profit_margins)::numeric, 1) AS profit_margin_pct
FROM earnings_window e
JOIN latest l ON l.symbol = e.symbol
JOIN market_data.us_equity_company_profile f ON f.symbol = e.symbol
WHERE f.market_cap BETWEEN 500000000 AND 100000000000
  AND l.close * l.avg_vol_20d > 10000000
  AND l.close / l.high_20d < 0.95
  AND f.sector IN ('Technology','Communication Services','Industrials','Consumer Cyclical')
  AND f.revenue_growth > 0.15
ORDER BY f.revenue_growth DESC
LIMIT 25;
