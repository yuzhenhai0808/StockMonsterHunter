-- monster-stock-hunter: rolling_ignition_walkforward.sql
--
-- Purpose: walk-forward validation for the ignition prediction rules.
-- Uses the latest 12 complete sessions, builds 5-trading-day train windows,
-- and validates the frozen two-layer replay rule on the next 2 sessions.
--
-- Read-only. The MCP tool `rolling_ignition_walkforward` is canonical for
-- structured JSON output; this SQL is a same-mouth template for ad hoc review.

WITH settings AS (
  SELECT
    12::int AS lookback_days,
    5::int AS train_days,
    2::int AS validate_days,
    40::numeric AS monster_threshold,
    0.20::numeric AS min_close,
    1000000::bigint AS min_volume,
    500000::bigint AS min_avg_volume_20d,
    250000::numeric AS min_avg_dollar_20d,
    'yfinance'::text AS provider
),
days AS (
  SELECT row_number() OVER (ORDER BY price_date) AS rn, price_date
  FROM (
    SELECT DISTINCT price_date
    FROM market_data.us_equity_daily_prices
    WHERE provider = (SELECT provider FROM settings)
    ORDER BY price_date DESC
    LIMIT (SELECT lookback_days FROM settings)
  ) d
),
folds AS (
  SELECT
    gs AS fold,
    ARRAY(SELECT price_date FROM days WHERE rn BETWEEN gs AND gs + (SELECT train_days FROM settings) - 1 ORDER BY rn) AS train_dates,
    ARRAY(SELECT price_date FROM days WHERE rn BETWEEN gs + (SELECT train_days FROM settings) AND gs + (SELECT train_days FROM settings) + (SELECT validate_days FROM settings) - 1 ORDER BY rn) AS validate_dates
  FROM generate_series(
    1,
    (
      (SELECT count(*)::int FROM days)
      - (SELECT train_days FROM settings)
      - (SELECT validate_days FROM settings)
      + 1
    )
  ) gs
),
base_daily AS (
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
  WHERE p.provider = (SELECT provider FROM settings)
    AND p.price_date >= (SELECT min(price_date) FROM days) - INTERVAL '220 days'
    AND p.price_date <= (SELECT max(price_date) FROM days)
),
daily AS (
  SELECT
    b.*,
    AVG(b.volume) OVER (
      PARTITION BY b.symbol ORDER BY b.price_date
      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
    ) AS avg_volume_20_prev,
    AVG(b.close * b.volume) OVER (
      PARTITION BY b.symbol ORDER BY b.price_date
      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
    ) AS avg_dollar_20_prev,
    MAX(100 * (b.close - b.prev_close) / NULLIF(b.prev_close, 0)) OVER (
      PARTITION BY b.symbol ORDER BY b.price_date
      ROWS BETWEEN 180 PRECEDING AND 1 PRECEDING
    ) AS max_1d_ret_180_prev
  FROM base_daily b
),
profile_latest AS (
  SELECT DISTINCT ON (symbol)
    symbol,
    market_cap,
    float_shares,
    short_percent_of_float
  FROM market_data.us_equity_company_profile
  WHERE provider = (SELECT provider FROM settings)
    AND as_of_date <= (SELECT max(price_date) FROM days)
  ORDER BY symbol, as_of_date DESC
),
validation_candidates AS (
  SELECT
    f.fold,
    f.train_dates,
    f.validate_dates,
    d.price_date AS target_date,
    d.symbol,
    d.prev_close AS asof_close,
    100 * (d.prev_close - LAG(d.prev_close) OVER (PARTITION BY d.symbol ORDER BY d.price_date)) / NULLIF(LAG(d.prev_close) OVER (PARTITION BY d.symbol ORDER BY d.price_date), 0) AS asof_ret_1d_pct,
    d.open AS target_open,
    d.high AS target_high,
    d.close AS target_close,
    100 * (d.high - d.prev_close) / NULLIF(d.prev_close, 0) AS max_return_pct,
    CASE WHEN d.avg_dollar_20_prev < 5000000 THEN 1 ELSE 0 END +
    CASE WHEN d.volume >= d.avg_volume_20_prev * 3 THEN 1 ELSE 0 END +
    CASE WHEN d.open / NULLIF(d.prev_close, 0) BETWEEN 1.03 AND 1.30 THEN 1 ELSE 0 END +
    CASE WHEN cp.float_shares IS NOT NULL AND cp.float_shares < 30000000 THEN 1 ELSE 0 END +
    CASE WHEN COALESCE(d.max_1d_ret_180_prev, 0) >= 50 THEN 1 ELSE 0 END +
    CASE WHEN d.prev_close BETWEEN (SELECT min_close FROM settings) AND 30 THEN 1 ELSE 0 END AS replay_prediction_score,
    cp.float_shares
  FROM folds f
  JOIN daily d ON d.price_date = ANY(f.validate_dates)
  LEFT JOIN profile_latest cp ON cp.symbol = d.symbol
  WHERE d.prev_close >= (SELECT min_close FROM settings)
    AND d.avg_volume_20_prev >= (SELECT min_avg_volume_20d FROM settings)
    AND d.avg_dollar_20_prev >= (SELECT min_avg_dollar_20d FROM settings)
),
layered AS (
  SELECT
    *,
    CASE
      WHEN replay_prediction_score >= 6
        AND target_open / NULLIF(asof_close, 0) BETWEEN 0.90 AND 1.40
        THEN '主攻层'
      WHEN replay_prediction_score = 5
        AND (
          CASE WHEN float_shares IS NOT NULL AND float_shares < 30000000 THEN 1 ELSE 0 END +
          CASE WHEN asof_close < 5 THEN 1 ELSE 0 END +
          CASE WHEN max_return_pct >= 20 THEN 1 ELSE 0 END
        ) >= 2
        THEN '观察层'
      ELSE '剔除'
    END AS layer
  FROM validation_candidates
),
validation_monsters AS (
  SELECT
    f.fold,
    d.price_date,
    d.symbol
  FROM folds f
  JOIN daily d ON d.price_date = ANY(f.validate_dates)
  WHERE d.close >= (SELECT min_close FROM settings)
    AND d.volume >= (SELECT min_volume FROM settings)
    AND 100 * (d.close - d.prev_close) / NULLIF(d.prev_close, 0) >= (SELECT monster_threshold FROM settings)
),
train_monsters AS (
  SELECT
    f.fold,
    count(*) AS train_monster_count
  FROM folds f
  JOIN daily d ON d.price_date = ANY(f.train_dates)
  WHERE d.close >= (SELECT min_close FROM settings)
    AND d.volume >= (SELECT min_volume FROM settings)
    AND 100 * (d.close - d.prev_close) / NULLIF(d.prev_close, 0) >= (SELECT monster_threshold FROM settings)
  GROUP BY f.fold
),
candidate_summary AS (
  SELECT
    fold,
    count(*) FILTER (WHERE layer = '主攻层') AS main_candidate_count,
    count(*) FILTER (WHERE layer = '主攻层' AND max_return_pct >= 20) AS main_hit_count,
    count(*) FILTER (WHERE layer = '观察层') AS observation_candidate_count,
    count(*) FILTER (WHERE layer = '观察层' AND max_return_pct >= 20) AS observation_hit_count,
    count(*) FILTER (WHERE layer = '主攻层' AND max_return_pct < 10) AS main_misreport_count
  FROM layered
  GROUP BY fold
),
missed_summary AS (
  SELECT
    vm.fold,
    count(*) AS missed_monster_count
  FROM validation_monsters vm
  WHERE NOT EXISTS (
    SELECT 1
    FROM layered l
    WHERE l.fold = vm.fold
      AND l.target_date = vm.price_date
      AND l.symbol = vm.symbol
      AND l.layer IN ('主攻层', '观察层')
  )
  GROUP BY vm.fold
),
validation_summary AS (
  SELECT fold, count(*) AS validation_monster_count
  FROM validation_monsters
  GROUP BY fold
)
SELECT
  f.fold,
  f.train_dates,
  f.validate_dates,
  COALESCE(tm.train_monster_count, 0) AS train_monster_count,
  COALESCE(vs.validation_monster_count, 0) AS validation_monster_count,
  COALESCE(cs.main_candidate_count, 0) AS main_candidate_count,
  COALESCE(cs.main_hit_count, 0) AS main_hit_count,
  COALESCE(cs.observation_candidate_count, 0) AS observation_candidate_count,
  COALESCE(cs.observation_hit_count, 0) AS observation_hit_count,
  COALESCE(cs.main_misreport_count, 0) AS main_misreport_count,
  COALESCE(ms.missed_monster_count, 0) AS missed_monster_count
FROM folds f
LEFT JOIN train_monsters tm ON tm.fold = f.fold
LEFT JOIN validation_summary vs ON vs.fold = f.fold
LEFT JOIN candidate_summary cs ON cs.fold = f.fold
LEFT JOIN missed_summary ms ON ms.fold = f.fold
ORDER BY f.fold;
