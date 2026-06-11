-- 历史财报反应检查(模式 5 的一票否决门槛)
--
-- 用途: 查询 tickers 过去 4 次财报日 vs 次一交易日的收盘价变化
--        用于判断该股票是否属于"利好出尽型"(财报后总是跌)
--
-- 用法: 把 {tickers} 替换为 ('TICKER1','TICKER2',...) 后通过 query_market_data MCP 工具执行
--
-- 一票否决规则(见 references/modes/earnings-gap.md 步骤 0):
--   - 过去 4 次财报中 ≥3 次次日下跌 → 直接排除
--   - 即使 EPS 超预期仍下跌 ≥2 次 → 降级,标注"利好出尽型"

WITH recent_earnings AS (
  SELECT symbol, earnings_date, eps_surprise_pct,
         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY earnings_date DESC) AS rn
  FROM market_data.us_equity_earnings
  WHERE symbol IN {tickers}
    AND earnings_date < CURRENT_DATE
    AND earnings_date >= '2024-01-01'
),
earnings_price AS (
  SELECT e.symbol, e.earnings_date, e.eps_surprise_pct,
         p0.close AS er_day_close,
         (SELECT p1.close
          FROM market_data.us_equity_daily_prices p1
          WHERE p1.symbol = e.symbol AND p1.provider='yfinance'
            AND p1.price_date = (
              SELECT MIN(price_date) FROM market_data.us_equity_daily_prices
              WHERE symbol = e.symbol AND provider='yfinance'
                AND price_date > e.earnings_date
            )
         ) AS next_day_close
  FROM recent_earnings e
  JOIN market_data.us_equity_daily_prices p0
    ON p0.symbol = e.symbol AND p0.provider='yfinance' AND p0.price_date = e.earnings_date
  WHERE e.rn <= 4
)
SELECT symbol, earnings_date,
       ROUND(eps_surprise_pct::numeric, 2) AS eps_surprise_pct,
       ROUND(100 * (next_day_close / er_day_close - 1)::numeric, 2) AS next_day_pct
FROM earnings_price
ORDER BY symbol, earnings_date DESC;
