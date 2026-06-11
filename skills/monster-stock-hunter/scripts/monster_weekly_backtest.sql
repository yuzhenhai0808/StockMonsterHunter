-- monster-stock-hunter: monster_weekly_backtest.sql
--
-- Purpose: review recent monster stocks (default last 5 complete trading days)
-- with ret_1d >= 40%, then expose the pre-ignition traces that could have
-- been visible before/during the move.
--
-- Read-only. Execute through TradingAgents MCP query_market_data.
-- Tune settings.lookback_trading_days / settings.min_ret_pct if needed.

WITH settings AS (
  SELECT
    5::int AS lookback_trading_days,
    40::numeric AS min_ret_pct,
    1000000::bigint AS min_volume,
    0.20::numeric AS min_close
),
trading_days AS (
  SELECT DISTINCT price_date
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
  ORDER BY price_date DESC
  LIMIT (SELECT lookback_trading_days FROM settings)
),
daily AS (
  SELECT
    p.symbol,
    p.price_date,
    p.open,
    p.high,
    p.low,
    p.close,
    p.volume,
    p.close * p.volume AS dollar_volume,
    LAG(p.close, 1) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close,
    LAG(p.close, 2) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_2d,
    LAG(p.close, 5) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS close_5d,
    LAG(p.volume, 1) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_volume,
    AVG(p.volume) OVER (
      PARTITION BY p.symbol ORDER BY p.price_date
      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
    ) AS avg_volume_20_prev,
    AVG(p.close * p.volume) OVER (
      PARTITION BY p.symbol ORDER BY p.price_date
      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
    ) AS avg_dollar_20_prev
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= (
      SELECT min(price_date) FROM trading_days
    ) - INTERVAL '40 days'
),
movers AS (
  SELECT
    d.*,
    100 * (d.close - d.prev_close) / NULLIF(d.prev_close, 0) AS ret_1d_pct,
    100 * (d.open - d.prev_close) / NULLIF(d.prev_close, 0) AS gap_open_pct,
    100 * (d.high - d.prev_close) / NULLIF(d.prev_close, 0) AS max_intraday_from_prev_pct,
    100 * (d.prev_close - d.close_2d) / NULLIF(d.close_2d, 0) AS prior_1d_pct,
    100 * (d.prev_close - d.close_5d) / NULLIF(d.close_5d, 0) AS prior_4d_pct,
    d.volume::numeric / NULLIF(d.avg_volume_20_prev, 0) AS volume_ratio_vs_prev20,
    d.dollar_volume / 1000000 AS dollar_volume_m,
    d.avg_dollar_20_prev / 1000000 AS avg_dollar_20_prev_m,
    d.prev_close * d.prev_volume / 1000000 AS prev_dollar_volume_m
  FROM daily d
  WHERE d.price_date IN (SELECT price_date FROM trading_days)
    AND d.prev_close IS NOT NULL
    AND d.close >= (SELECT min_close FROM settings)
    AND d.volume >= (SELECT min_volume FROM settings)
),
profile_latest AS (
  SELECT DISTINCT ON (symbol)
    symbol,
    sector,
    industry,
    market_cap,
    float_shares,
    short_percent_of_float,
    fifty_two_week_high
  FROM market_data.us_equity_company_profile
  WHERE provider = 'yfinance'
  ORDER BY symbol, as_of_date DESC
),
scored AS (
  SELECT
    m.*,
    cp.sector,
    cp.industry,
    cp.market_cap,
    cp.float_shares,
    cp.short_percent_of_float,
    cp.fifty_two_week_high,
    (
      CASE WHEN COALESCE(m.prev_dollar_volume_m, 0) < 5 THEN 1 ELSE 0 END +
      CASE WHEN m.volume_ratio_vs_prev20 >= 5 OR m.dollar_volume_m >= COALESCE(m.avg_dollar_20_prev_m, 0) * 5 THEN 1 ELSE 0 END +
      CASE WHEN m.gap_open_pct BETWEEN 3 AND 30 THEN 1 ELSE 0 END +
      CASE WHEN cp.float_shares IS NOT NULL AND cp.float_shares < 30000000 THEN 1 ELSE 0 END +
      CASE WHEN m.prior_1d_pct < 0 OR m.prior_4d_pct < 0 THEN 1 ELSE 0 END +
      CASE WHEN m.max_intraday_from_prev_pct >= 40 THEN 1 ELSE 0 END
    ) AS visible_quant_signal_count
  FROM movers m
  LEFT JOIN profile_latest cp ON cp.symbol = m.symbol
  WHERE m.ret_1d_pct >= (SELECT min_ret_pct FROM settings)
)
SELECT
  symbol,
  price_date,
  ROUND(prev_close::numeric, 4) AS prev_close,
  ROUND(open::numeric, 4) AS open,
  ROUND(high::numeric, 4) AS high,
  ROUND(low::numeric, 4) AS low,
  ROUND(close::numeric, 4) AS close,
  ROUND(ret_1d_pct::numeric, 2) AS ret_1d_pct,
  ROUND(gap_open_pct::numeric, 2) AS gap_open_pct,
  ROUND(max_intraday_from_prev_pct::numeric, 2) AS max_intraday_from_prev_pct,
  ROUND(prior_1d_pct::numeric, 2) AS prior_1d_pct,
  ROUND(prior_4d_pct::numeric, 2) AS prior_4d_pct,
  volume,
  ROUND(volume_ratio_vs_prev20::numeric, 2) AS volume_ratio_vs_prev20,
  ROUND(dollar_volume_m::numeric, 2) AS dollar_volume_m,
  ROUND(avg_dollar_20_prev_m::numeric, 2) AS avg_dollar_20_prev_m,
  ROUND(prev_dollar_volume_m::numeric, 2) AS prev_dollar_volume_m,
  ROUND((market_cap / 1000000.0)::numeric, 0) AS market_cap_m,
  ROUND((float_shares / 1000000.0)::numeric, 1) AS float_m,
  short_percent_of_float,
  sector,
  industry,
  visible_quant_signal_count,
  CASE
    WHEN gap_open_pct > 40 THEN '盘前/开盘已飞:只能接棒或审计'
    WHEN gap_open_pct BETWEEN 3 AND 30 AND visible_quant_signal_count >= 4 THEN '可提前:盘前点火候选'
    WHEN prior_1d_pct < 0 AND volume_ratio_vs_prev20 >= 5 THEN '可提前:弱势反转爆量候选'
    WHEN prior_4d_pct >= 40 THEN '接棒:前期已强'
    ELSE '需Web催化确认或不可提前'
  END AS replay_bucket
FROM scored
ORDER BY price_date, ret_1d_pct DESC;
