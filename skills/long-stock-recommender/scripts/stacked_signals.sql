-- 叠加信号筛选(模式 6)
--
-- 用途: 找同时满足"动量强 + 财报窗口 + 历史财报正反应 + 无资本事件红旗"的股票
--       这是组合 A(追涨 + 财报跳空 + 历史正反应)的数据库层近似
--
-- 用法: 通过 query_market_data MCP 工具直接执行
--       注意: 这个 SQL 给出初筛,最终候选必须再调 long_momentum_candidates MCP
--            和 earnings_reaction_check.sql 双重验证

WITH earnings_window AS (
  -- 未来 3-14 天有财报
  SELECT symbol, earnings_date, eps_estimate, timing
  FROM market_data.us_equity_earnings
  WHERE earnings_date BETWEEN CURRENT_DATE + 3 AND CURRENT_DATE + 14
    AND eps_estimate IS NOT NULL
),
price_features AS (
  SELECT
    p.symbol, p.price_date, p.close, p.volume,
    LAG(p.close, 20) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_20d,
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
),
-- 动量信号:20 日涨幅 > 10%,距 20 日高 < 10% (接近突破)
momentum_candidates AS (
  SELECT symbol, close, close_20d, high_20d, avg_vol_20d,
         ROUND((close / close_20d - 1)::numeric * 100, 1) AS ret_20d_pct,
         ROUND((close / high_20d)::numeric * 100, 1) AS pct_of_20d_high
  FROM latest
  WHERE close / close_20d > 1.10
    AND close / high_20d > 0.85
    AND close * avg_vol_20d > 10000000  -- 流动性
),
-- 历史财报反应统计
earnings_history AS (
  SELECT symbol,
         COUNT(*) AS total_reports,
         COUNT(*) FILTER (WHERE next_day_pct > 0) AS positive_reactions,
         ROUND(AVG(next_day_pct)::numeric, 2) AS avg_reaction_pct
  FROM (
    SELECT e.symbol, e.earnings_date,
           p0.close AS er_close,
           (SELECT p1.close
            FROM market_data.us_equity_daily_prices p1
            WHERE p1.symbol = e.symbol AND p1.provider='yfinance'
              AND p1.price_date = (
                SELECT MIN(price_date) FROM market_data.us_equity_daily_prices
                WHERE symbol = e.symbol AND provider='yfinance'
                  AND price_date > e.earnings_date
              )
           ) AS next_close,
           100 * ((SELECT p1.close
                   FROM market_data.us_equity_daily_prices p1
                   WHERE p1.symbol = e.symbol AND p1.provider='yfinance'
                     AND p1.price_date = (
                       SELECT MIN(price_date) FROM market_data.us_equity_daily_prices
                       WHERE symbol = e.symbol AND provider='yfinance'
                         AND price_date > e.earnings_date
                     )
                  ) / p0.close - 1) AS next_day_pct,
           ROW_NUMBER() OVER (PARTITION BY e.symbol ORDER BY e.earnings_date DESC) AS rn
    FROM market_data.us_equity_earnings e
    JOIN market_data.us_equity_daily_prices p0
      ON p0.symbol = e.symbol AND p0.provider='yfinance' AND p0.price_date = e.earnings_date
    WHERE e.earnings_date < CURRENT_DATE
      AND e.earnings_date >= '2024-01-01'
  ) hist
  WHERE rn <= 4
  GROUP BY symbol
  HAVING COUNT(*) >= 3  -- 至少有 3 次财报数据
)
SELECT
  ew.symbol,
  ew.earnings_date,
  ew.timing,
  m.close,
  m.ret_20d_pct,
  m.pct_of_20d_high,
  f.sector,
  ROUND((f.market_cap / 1e9)::numeric, 2) AS market_cap_b,
  ROUND((100 * f.revenue_growth)::numeric, 1) AS rev_growth_pct,
  eh.total_reports,
  eh.positive_reactions,
  ROUND((100.0 * eh.positive_reactions / eh.total_reports)::numeric, 0) AS win_rate_pct,
  eh.avg_reaction_pct
FROM earnings_window ew
JOIN momentum_candidates m ON m.symbol = ew.symbol
JOIN market_data.us_equity_company_profile f ON f.symbol = ew.symbol
JOIN earnings_history eh ON eh.symbol = ew.symbol
WHERE eh.positive_reactions::float / eh.total_reports >= 0.5  -- 胜率 ≥ 50%
  AND f.market_cap BETWEEN 500000000 AND 100000000000
  AND f.sector IN ('Technology','Communication Services','Industrials','Consumer Cyclical')
  AND f.revenue_growth > 0.10
ORDER BY eh.positive_reactions DESC, m.ret_20d_pct DESC
LIMIT 15;
