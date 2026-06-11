-- monster-stock-hunter: ignition_candidate_replay.sql
--
-- Purpose: simulate the candidate list that would have been visible using
-- only data available up to the prior complete trading day for a historical
-- target session. This prevents future leakage when validating predictions.
--
-- Read-only. Execute through TradingAgents MCP query_market_data.
-- Change settings.target_date to replay another session.

WITH settings AS (
  SELECT
    DATE '2026-05-15' AS target_date,
    0.20::numeric AS min_close,
    500000::bigint AS min_avg_volume_20d,
    250000::numeric AS min_avg_dollar_20d
),
asof_day AS (
  SELECT max(price_date) AS asof_date
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date < (SELECT target_date FROM settings)
),
history AS (
  SELECT
    p.symbol,
    p.price_date,
    p.open,
    p.high,
    p.low,
    p.close,
    p.volume,
    LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.price_date) AS prev_close
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '220 days'
    AND p.price_date <= (SELECT asof_date FROM asof_day)
),
features AS (
  SELECT
    h.symbol,
    max(h.price_date) AS asof_date,
    (ARRAY_AGG(h.open ORDER BY h.price_date DESC))[1] AS asof_open,
    (ARRAY_AGG(h.high ORDER BY h.price_date DESC))[1] AS asof_high,
    (ARRAY_AGG(h.low ORDER BY h.price_date DESC))[1] AS asof_low,
    (ARRAY_AGG(h.close ORDER BY h.price_date DESC))[1] AS asof_close,
    (ARRAY_AGG(h.close ORDER BY h.price_date DESC))[6] AS close_5d_ago,
    (ARRAY_AGG(h.volume ORDER BY h.price_date DESC))[1] AS asof_volume,
    (ARRAY_AGG(h.prev_close ORDER BY h.price_date DESC))[1] AS prev_close,
    AVG(h.volume) FILTER (WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days') AS avg_volume_20d_proxy,
    AVG(h.close * h.volume) FILTER (WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days') AS avg_dollar_20d_proxy,
    AVG(h.close) FILTER (WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days') AS ma20_proxy,
    AVG(100 * (h.high - h.low) / NULLIF(h.prev_close, 0)) FILTER (
      WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days'
    ) AS atr20_pct_proxy,
    MAX(h.high) FILTER (WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '20 days') AS high_20d,
    MIN(h.low) FILTER (WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '20 days') AS low_20d,
    MAX(h.high) FILTER (WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '220 days') AS high_52w_proxy,
    MAX(100 * (h.close - h.prev_close) / NULLIF(h.prev_close, 0)) FILTER (
      WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '180 days'
    ) AS max_1d_ret_180d_pct,
    SUM(CASE WHEN 100 * (h.close - h.prev_close) / NULLIF(h.prev_close, 0) >= 50 THEN 1 ELSE 0 END) FILTER (
      WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '180 days'
    ) AS days_above_50pct,
    SUM(CASE WHEN 100 * (h.close - h.prev_close) / NULLIF(h.prev_close, 0) >= 20 THEN 1 ELSE 0 END) FILTER (
      WHERE h.price_date >= (SELECT asof_date FROM asof_day) - INTERVAL '30 days'
    ) AS days_above_20_30_prev
  FROM history h
  GROUP BY h.symbol
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
    AND as_of_date <= (SELECT asof_date FROM asof_day)
  ORDER BY symbol, as_of_date DESC
),
target_day AS (
  SELECT
    p.symbol,
    p.open AS target_open,
    p.high AS target_high,
    p.low AS target_low,
    p.close AS target_close,
    p.volume AS target_volume
  FROM market_data.us_equity_daily_prices p
  WHERE p.provider = 'yfinance'
    AND p.price_date = (SELECT target_date FROM settings)
),
scored AS (
  SELECT
    f.*,
    cp.sector,
    cp.industry,
    cp.market_cap,
    cp.float_shares,
    cp.short_percent_of_float,
    cp.fifty_two_week_high,
    t.target_open,
    t.target_high,
    t.target_low,
    t.target_close,
    t.target_volume,
    100 * (f.asof_close - f.prev_close) / NULLIF(f.prev_close, 0) AS asof_ret_1d_pct,
    100 * (f.asof_close - f.close_5d_ago) / NULLIF(f.close_5d_ago, 0) AS asof_ret_5d_pct,
    100 * (f.high_20d - f.asof_close) / NULLIF(f.high_20d, 0) AS pullback_from_20d_high_pct,
    100 * (COALESCE(cp.fifty_two_week_high, f.high_52w_proxy) - f.asof_close) / NULLIF(COALESCE(cp.fifty_two_week_high, f.high_52w_proxy), 0) AS dist_from_52w_high_pct,
    100 * (f.asof_close - f.ma20_proxy) / NULLIF(f.ma20_proxy, 0) AS pct_vs_ma20,
    100 * (t.target_high - f.asof_close) / NULLIF(f.asof_close, 0) AS next_day_max_from_asof_pct,
    100 * (t.target_close - f.asof_close) / NULLIF(f.asof_close, 0) AS next_day_close_from_asof_pct,
    (
      CASE WHEN f.avg_dollar_20d_proxy < 5000000 THEN 1 ELSE 0 END +
      CASE WHEN f.asof_volume >= f.avg_volume_20d_proxy * 3 THEN 1 ELSE 0 END +
      CASE WHEN (cp.float_shares IS NOT NULL AND cp.float_shares < 30000000) OR COALESCE(cp.market_cap, 999999999999) < 300000000 THEN 1 ELSE 0 END +
      CASE WHEN COALESCE(f.max_1d_ret_180d_pct, 0) >= 50 OR COALESCE(f.days_above_20_30_prev, 0) >= 3 THEN 1 ELSE 0 END +
      CASE WHEN f.asof_close BETWEEN (SELECT min_close FROM settings) AND 30 THEN 1 ELSE 0 END +
      CASE WHEN COALESCE(f.atr20_pct_proxy, 0) >= 12 THEN 1 ELSE 0 END +
      CASE WHEN 100 * (f.high_20d - f.asof_close) / NULLIF(f.high_20d, 0) >= 78 THEN 1 ELSE 0 END
    ) AS replay_prediction_score,
    (
      CASE WHEN f.asof_close * f.asof_volume >= 25000000 THEN 3 WHEN f.asof_close * f.asof_volume >= 5000000 THEN 2 WHEN f.asof_close * f.asof_volume >= 1000000 THEN 1 ELSE 0 END +
      CASE WHEN f.asof_volume >= f.avg_volume_20d_proxy * 5 THEN 2 WHEN f.asof_volume >= f.avg_volume_20d_proxy * 3 THEN 1.5 WHEN f.asof_volume >= f.avg_volume_20d_proxy * 2 THEN 1 ELSE 0 END +
      CASE WHEN COALESCE(f.atr20_pct_proxy, 0) >= 24 THEN 3 WHEN COALESCE(f.atr20_pct_proxy, 0) >= 18 THEN 2 WHEN COALESCE(f.atr20_pct_proxy, 0) >= 12 THEN 1.5 WHEN COALESCE(f.atr20_pct_proxy, 0) >= 8 THEN 1 ELSE 0 END +
      CASE WHEN COALESCE(cp.market_cap, 999999999999) < 30000000 THEN 2 WHEN COALESCE(cp.market_cap, 999999999999) < 300000000 THEN 1 ELSE 0 END +
      CASE WHEN 100 * (f.high_20d - f.asof_close) / NULLIF(f.high_20d, 0) >= 78 THEN 2 WHEN 100 * (f.high_20d - f.asof_close) / NULLIF(f.high_20d, 0) >= 50 THEN 1 ELSE 0 END +
      CASE WHEN COALESCE(f.days_above_20_30_prev, 0) >= 3 THEN 2 WHEN COALESCE(f.days_above_20_30_prev, 0) >= 1 THEN 1 ELSE 0 END +
      CASE WHEN 100 * (f.asof_close - f.prev_close) / NULLIF(f.prev_close, 0) > 40 THEN -1 WHEN 100 * (f.asof_close - f.prev_close) / NULLIF(f.prev_close, 0) BETWEEN 5 AND 15 THEN 1.5 WHEN 100 * (f.asof_close - f.prev_close) / NULLIF(f.prev_close, 0) BETWEEN 0 AND 5 THEN 2 ELSE 1 END
    ) AS weighted_prediction_score
  FROM features f
  LEFT JOIN profile_latest cp ON cp.symbol = f.symbol
  LEFT JOIN target_day t ON t.symbol = f.symbol
  WHERE f.asof_close >= (SELECT min_close FROM settings)
    AND f.avg_volume_20d_proxy >= (SELECT min_avg_volume_20d FROM settings)
    AND f.avg_dollar_20d_proxy >= (SELECT min_avg_dollar_20d FROM settings)
),
candidates AS (
  SELECT *
  FROM scored
  WHERE replay_prediction_score >= 3
    AND (
      asof_ret_1d_pct <= 40
      OR COALESCE(max_1d_ret_180d_pct, 0) >= 50
      OR COALESCE(atr20_pct_proxy, 0) >= 12
      OR pullback_from_20d_high_pct >= 50
      OR COALESCE(days_above_20_30_prev, 0) >= 3
    )
)
SELECT
  (SELECT target_date FROM settings) AS target_date,
  symbol,
  asof_date,
  ROUND(asof_close::numeric, 4) AS asof_close,
  ROUND(asof_ret_1d_pct::numeric, 2) AS asof_ret_1d_pct,
  ROUND((avg_dollar_20d_proxy / 1000000)::numeric, 2) AS avg_dollar_20d_m,
  ROUND((asof_close * asof_volume / 1000000)::numeric, 2) AS asof_dollar_volume_m,
  ROUND((asof_volume / NULLIF(avg_volume_20d_proxy, 0))::numeric, 2) AS asof_volume_ratio,
  ROUND(atr20_pct_proxy::numeric, 2) AS atr20_pct,
  ROUND(asof_ret_5d_pct::numeric, 2) AS asof_ret_5d_pct,
  ROUND(pct_vs_ma20::numeric, 2) AS pct_vs_ma20,
  ROUND(max_1d_ret_180d_pct::numeric, 2) AS max_1d_ret_180d_pct,
  days_above_50pct,
  days_above_20_30_prev,
  ROUND((market_cap / 1000000.0)::numeric, 0) AS market_cap_m,
  ROUND((float_shares / 1000000.0)::numeric, 1) AS float_m,
  short_percent_of_float,
  ROUND(pullback_from_20d_high_pct::numeric, 2) AS pullback_from_20d_high_pct,
  ROUND(dist_from_52w_high_pct::numeric, 2) AS dist_from_52w_high_pct,
  replay_prediction_score,
  ROUND(weighted_prediction_score::numeric, 2) AS weighted_prediction_score,
  CASE
    WHEN replay_prediction_score >= 6
      AND asof_ret_1d_pct BETWEEN 0 AND 15
      AND asof_close * asof_volume >= avg_dollar_20d_proxy * 1.5
      THEN '纸面雷达:优先盘前Web升级'
    WHEN replay_prediction_score >= 6
      AND asof_close * asof_volume < avg_dollar_20d_proxy * 0.5
      THEN '静态壳风险:无当前量能则剔除'
    WHEN replay_prediction_score = 5
      AND (
        (CASE WHEN float_shares IS NOT NULL AND float_shares < 30000000 THEN 1 ELSE 0 END) +
        (CASE WHEN avg_dollar_20d_proxy < 5000000 THEN 1 ELSE 0 END) +
        (CASE WHEN asof_ret_1d_pct < 0 THEN 1 ELSE 0 END) +
        (CASE WHEN COALESCE(max_1d_ret_180d_pct, 0) >= 50 THEN 1 ELSE 0 END)
      ) >= 2
      THEN 'Web升级观察:查movers/gap/48h催化/当前成交额'
    WHEN replay_prediction_score >= 6
      THEN '纸面雷达:待触发'
    ELSE '观察'
  END AS replay_gate_action,
  CASE
    WHEN asof_ret_1d_pct BETWEEN 0 AND 15 THEN '0-15点火前'
    WHEN asof_ret_1d_pct > 15 AND asof_ret_1d_pct <= 40 THEN '15-40接棒'
    WHEN asof_ret_1d_pct > 40 THEN '40+审计'
    ELSE '弱势反转观察'
  END AS replay_layer,
  ROUND(target_open::numeric, 4) AS target_open,
  ROUND(target_high::numeric, 4) AS target_high,
  ROUND(target_low::numeric, 4) AS target_low,
  ROUND(target_close::numeric, 4) AS target_close,
  ROUND(next_day_max_from_asof_pct::numeric, 2) AS next_day_max_from_asof_pct,
  ROUND(next_day_close_from_asof_pct::numeric, 2) AS next_day_close_from_asof_pct,
  CASE
    WHEN next_day_max_from_asof_pct >= 40 THEN '妖股命中候选'
    WHEN next_day_max_from_asof_pct >= 20 THEN '强命中候选'
    WHEN next_day_max_from_asof_pct >= 10 THEN '快打命中候选'
    WHEN target_high IS NULL THEN '无目标日数据'
    ELSE '未命中或误报'
  END AS replay_result
FROM candidates
ORDER BY weighted_prediction_score DESC, replay_prediction_score DESC, next_day_max_from_asof_pct DESC NULLS LAST
LIMIT 100;
