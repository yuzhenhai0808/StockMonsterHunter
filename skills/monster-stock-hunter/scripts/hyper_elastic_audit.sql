-- 高弹性翻倍候选池 (High-Elasticity Lottery Audit)
--
-- 目的: 服务用户"想翻倍 / 想赚多 / 极端 / 最猛"等高赔率诉求,
-- 筛选**有翻倍基因 + 当前波动空间 + 流通可被拉动 + 临近催化窗口**的候选。
--
-- 这不是稳健选股,也不是降档处置。它直面"我就要赌一把高弹性"的诉求,
-- 但仍服从 R/R 硬门、ATR 止损纪律和单笔账户风险上限(由 mode 文件 + risk-rules 强制)。
--
-- 入选条件 (硬筛):
--   1. 翻倍基因: 过去 180 天内有至少 1 个交易日 ret_1d_pct >= 80%(放宽到 80% 是为不漏掉 +120% 区间票)
--   2. 当前 ATR14 >= 8% (波动空间;比常规 mode 5% 更激进)
--   3. 流通盘 < 50M shares (低流通才能被拉动;微盘 + 小盘)
--   4. 当前位置仍有空间: close < fifty_two_week_high * 0.85 (距 52 周高点至少 15%)
--      或 close_position_pct(20 日区间内位置) < 70%
--   5. 当前 close 在 $0.5 - $30 (低价才能放出大百分比波动)
--   6. 最近 20 日均成交额 >= $5M (有可交易性,不是死票)
--
-- 评级输出 (高弹性 A/B/C):
--   A 高: 翻倍基因 (180 天内有 +100% 单日) + ATR14 >= 12% + 流通 < 30M
--   B 中: 翻倍基因 (180 天内有 +50% 单日) + ATR14 >= 8% + 流通 < 50M
--   C 低: 仅 ATR14 >= 8% + 流通 < 50M,无翻倍基因
--
-- 由 references/modes/high-elasticity-lottery.md 引用; 仍需 Web 复核当前/盘前催化和红旗。

WITH d AS (
  SELECT max(price_date) AS day
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
),
ret_per_day AS (
  SELECT symbol, price_date, close,
         ((close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY price_date), 0)) - 1) * 100 AS ret_1d_pct
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date >= (SELECT day FROM d) - INTERVAL '180 days'
    AND price_date <= (SELECT day FROM d)
),
recent_180d AS (
  SELECT symbol,
         max(ret_1d_pct) AS max_1d_ret_180d,
         sum(CASE WHEN ret_1d_pct >= 100 THEN 1 ELSE 0 END) AS days_1d_above_100pct,
         sum(CASE WHEN ret_1d_pct >= 50  THEN 1 ELSE 0 END) AS days_1d_above_50pct
  FROM ret_per_day
  GROUP BY symbol
),
profile_latest AS (
  -- 同 symbol 取最新快照,避免历史多条 as_of_date 行造成笛卡尔
  SELECT DISTINCT ON (symbol)
         symbol, market_cap, shares_outstanding, float_shares,
         short_percent_of_float, fifty_two_week_high, fifty_two_week_low
  FROM market_data.us_equity_company_profile
  WHERE provider = 'yfinance'
  ORDER BY symbol, as_of_date DESC
),
atr_raw_calc AS (
  SELECT symbol, price_date, close,
         GREATEST(high - low,
                  ABS(high - LAG(close) OVER (PARTITION BY symbol ORDER BY price_date)),
                  ABS(low  - LAG(close) OVER (PARTITION BY symbol ORDER BY price_date))) AS tr
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date >= (SELECT day FROM d) - INTERVAL '40 days'
    AND price_date <= (SELECT day FROM d)
),
atr14_calc AS (
  SELECT symbol, price_date, close,
         avg(tr) OVER (PARTITION BY symbol ORDER BY price_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14_raw
  FROM atr_raw_calc
),
atr14_today AS (
  SELECT symbol, atr14_raw, close,
         (atr14_raw / NULLIF(close, 0)) * 100 AS atr14_pct
  FROM atr14_calc
  WHERE price_date = (SELECT day FROM d)
),
range_20d AS (
  SELECT symbol,
         max(high) AS high_20d,
         min(low)  AS low_20d,
         avg(close * volume) AS avg_dollar_volume_20d
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date >= (SELECT day FROM d) - INTERVAL '20 days'
    AND price_date <= (SELECT day FROM d)
  GROUP BY symbol
),
today_bar AS (
  SELECT symbol, open, high, low, close, volume,
         close * volume AS dollar_volume_today
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date = (SELECT day FROM d)
)
SELECT
  t.symbol,
  t.close,
  round(a.atr14_pct::numeric, 2) AS atr14_pct,
  round(COALESCE(r.max_1d_ret_180d, 0)::numeric, 1) AS max_1d_ret_180d_pct,
  COALESCE(r.days_1d_above_100pct, 0) AS days_above_100pct,
  cp.float_shares,
  round((cp.float_shares / 1000000.0)::numeric, 1) AS float_M,
  cp.short_percent_of_float,
  cp.market_cap,
  round((cp.market_cap / 1000000.0)::numeric, 0) AS market_cap_M,
  cp.fifty_two_week_high,
  round(((t.close / NULLIF(cp.fifty_two_week_high, 0)) * 100)::numeric, 1) AS pct_of_52wk_high,
  rg.high_20d,
  rg.low_20d,
  CASE WHEN rg.high_20d <> rg.low_20d
       THEN round((((t.close - rg.low_20d) / NULLIF(rg.high_20d - rg.low_20d, 0)) * 100)::numeric, 1)
       ELSE NULL END AS close_position_pct,
  round((rg.avg_dollar_volume_20d)::numeric, 0) AS avg_dollar_volume_20d,
  round((t.dollar_volume_today)::numeric, 0) AS dollar_volume_today,
  -- 高弹性等级
  CASE
    WHEN COALESCE(r.days_1d_above_100pct, 0) >= 1 AND a.atr14_pct >= 12
         AND cp.float_shares IS NOT NULL AND cp.float_shares < 30000000 THEN 'A'
    WHEN COALESCE(r.max_1d_ret_180d, 0) >= 50 AND a.atr14_pct >= 8
         AND cp.float_shares IS NOT NULL AND cp.float_shares < 50000000 THEN 'B'
    WHEN a.atr14_pct >= 8
         AND cp.float_shares IS NOT NULL AND cp.float_shares < 50000000 THEN 'C'
    ELSE NULL
  END AS elasticity_grade
FROM today_bar t
LEFT JOIN atr14_today a ON a.symbol = t.symbol
LEFT JOIN recent_180d r ON r.symbol = t.symbol
LEFT JOIN range_20d   rg ON rg.symbol = t.symbol
LEFT JOIN profile_latest cp ON cp.symbol = t.symbol
WHERE
  t.close BETWEEN 0.5 AND 30
  AND a.atr14_pct >= 8
  AND a.atr14_pct <= 80     -- 上限:ATR > 80% 多为刚暴涨暴跌完的退潮票,不可交易
  AND cp.float_shares IS NOT NULL
  AND cp.float_shares < 50000000
  AND rg.avg_dollar_volume_20d >= 5000000
  AND (
    cp.fifty_two_week_high IS NULL
    OR t.close < cp.fifty_two_week_high * 0.85
  )
  AND (
    rg.high_20d = rg.low_20d
    OR ((t.close - rg.low_20d) / NULLIF(rg.high_20d - rg.low_20d, 0)) < 0.70
  )
ORDER BY
  CASE
    WHEN COALESCE(r.days_1d_above_100pct, 0) >= 1 AND a.atr14_pct >= 12 THEN 1
    WHEN COALESCE(r.max_1d_ret_180d, 0) >= 50 AND a.atr14_pct >= 8 THEN 2
    ELSE 3
  END,
  a.atr14_pct DESC,
  COALESCE(r.max_1d_ret_180d, 0) DESC
LIMIT 50;
