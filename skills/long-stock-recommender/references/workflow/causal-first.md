# 筛选工作流（所有模式共享）

本文件是 long-stock-recommender 所有模式的统一工作流。模式文件只描述各自的筛选公式与门槛，整体流程必须按本文件顺序执行。

## 路由总流程

```
用户输入
  ↓
意图识别 → 选模式（SKILL.md 意图路由表）
  ↓
Read 对应 references/modes/<mode>.md
  ↓
通用前置（Step 0：数据健康检查）
  ↓
报价前置检查（仅"今天/盘前/盘中"触发）
  ↓
模式专属筛选流程（modes/*.md）
  ↓
首推前三连校验（TSEM 教训，强制）
  ↓
强制后置：社会面复核（workflow/red-flags.md）
  ↓
按 core/output-contract.md 组装输出
  ↓
输出前自检（output-contract.md 清单）
```

## Step 0：通用前置（每次必做）

调 `db_health`：
- `yfinance.usable_for_screening=true` 且 `latest_date` 不超过 2 个交易日
- `earnings_calendar.last_sync` 是否新鲜（>48 小时要在输出顶部警示）
- 价值模式额外确认 `fundamentals_coverage.usable_for_value_screening=true`，否则提示"基本面数据不足，回退到回调模式"

## Step 1：当前报价前置检查（条件触发）

**触发关键词**：今天 / 今日 / 现在买 / 盘前 / 夜盘 / 开盘怎么配 / 高收益当天机会。

任一关键词命中时本步骤强制执行（详细规则见 [output-contract.md](../core/output-contract.md) "报价校验表"）：

1. 查当前/延长交易 movers 与候选 ticker quote 页面，拿最新价、时间戳、bid/ask 或成交量/成交额
2. 用户提供的券商报价 > 网页延迟价；用用户给出的价重算 entry/止损/止盈/股数/仓位/R:R
3. 整理报价矩阵 → 报价校验表
4. 偏离 ≥5% 时 MCP buy_zone 作废，按最新价重算
5. 越过原买区 → 参与方式改 `回踩做` 或 `二次突破做`
6. 拿不到当前价 → 写明"非当前可交易推荐，仅日线候选"

## Step 2：模式专属筛选

按所选模式执行 `references/modes/<mode>.md` 的步骤。

| 模式 | 主 MCP 工具 | 关键脚本 |
|---|---|---|
| 追涨 | `long_momentum_candidates` + `earnings_dates` + `symbol_snapshot` | — |
| 超跌反弹 | `oversold_bounce_candidates` + `earnings_dates` | — |
| 回调买入 | `pullback_candidates` + `earnings_dates` + `company_fundamentals` | — |
| 价值挖坑 | `value_candidates` + `company_fundamentals` | — |
| 财报跳空 | `earnings_dates` + `query_market_data` | `mode5_candidates.sql` + `earnings_reaction_check.sql` |
| 叠加信号 | 主模式 MCP + `earnings_dates` + `query_market_data` | `stacked_signals.sql` + `earnings_reaction_check.sql` |

## Step 3：首推前三连校验（TSEM 教训，强制）

**对最终准备首推的候选**（不是整个候选池，是排名第一准备让用户买的那只）执行。任一项推翻主导偏向即降级或排除并重选首推。

### Step 3.0  先写出主导偏向

一句话写出 `这只票不是因为什么都好，而是因为 ______`。可选偏向：

- `财报前一致预期太低，但板块景气先行抬升`
- `估值已经贴地，而基本面刚出现拐点`
- `回调到关键均线，但主升趋势没坏`
- `同业财报先爆，它的预期还没完全抬起来`

写不出 → 这只票只是"看起来都还行"，不该当首推。

### Step 3.A  历史财报反应检查（财报 ≤30 天必跑）

调 `earnings_dates`，若首选 `days_until ≤ 30` 强制跑 [scripts/earnings_reaction_check.sql](../../scripts/earnings_reaction_check.sql)，查过去 4 次次日反应。**与模式无关，所有模式都跑**。

| 历史正反应 | days_until | 处理 |
|---|---|---|
| ≤1/4 | 任意 | **直接排除**（利好出尽型） |
| 2/4，且 EPS 超预期仍跌 ≥2 次 | 任意 | 降级，标注"财报前必须清仓" |
| 任意 | ≤7 天 | **直接排除**（不管历史） |
| <3/4 | 8-14 天 | 排除 |
| <2/4 | 15-30 天 | 排除 |

### Step 3.B  估值 sanity check（所有模式，所有市值）

调 `company_fundamentals`，看是否**直接推翻**当前主导偏向：

- `trailing_pe` vs `sector_median.median_trailing_pe` 超 3 倍 → 标注"故事股定价"
- `price_to_book > 5` 且 `ROE < 20%` → 标注"估值不支持下方"
- `free_cashflow_ttm` 翻负 + CapEx 扩张期 → 排除"价值模式"和"长线"，仅短线允许
- `forward_pe >> trailing_pe`（分析师预期下滑）→ 降级

纪律：不是看到估值贵就排除，只有估值与主导偏向冲突才否决。

### Step 3.C  管理层指引检查（所有模式）

WebSearch `"{ticker} Q1/Q2 guidance {current_year}"` 或 `"next quarter revenue outlook"`：

- 指引中值 < 上季度实际 → 标注"指引负面，财报前建议空仓"
- 指引高于分析师一致预期 → 正向信号
- 查不到指引 → 跳过

## Step 4：强制后置 — 社会面复核

详见 [red-flags.md](red-flags.md)。

核心：本地库无新闻、无定增、无 insider、无监管动态，必须 WebSearch 主动获取；任何红旗必须在输出中显式说明（降级/排除/标记风险）。

红旗优先级：

1. 会计问题 / SEC 调查 / 审计师辞职 → 直接排除
2. 刚完成定增 / ATM / 反向拆股 → 直接排除
3. 30 天内临床读数 / PDUFA → 标注二元风险
4. insider 集中抛售触阈 → 按分档降级
5. 微盘股 + 纯技术异动 → 排除
6. Float < 20M 或 Short > 20% → 标注 squeeze 属性

## Step 5：按 output-contract 组装输出 + 自检

按 [output-contract.md](../core/output-contract.md) 的"默认输出顺序"和"输出前自检清单"产出。

## 数据表与字段约定

主数据表：`market_data.us_equity_daily_prices`，字段为 `symbol / price_date / open / high / low / close / volume / provider`，默认 `provider='yfinance'`。手写 SQL 或执行 scripts 时不得使用旧表名 `us_equity_daily` 或旧字段 `date`。

各模式 SQL 计算共用指标（在 `cli/recommend_long_candidates.py` 与 `scripts/*.sql` 中均使用同一定义）：

- `ret_1d`、`ret_5d`、`ret_10d`：1/5/10 日涨跌幅
- `ma10`、`ma20`：10/20 日均线
- `avg_vol_20d`：过去 20 日平均成交量
- `high_20d`、`low_20d`：20 日最高/最低
- `range_20d = high_20d / low_20d - 1`：20 日振幅
- `volume_ratio = volume / avg_vol_20d`：放量倍数
- `close_to_high_ratio = close / high_20d`：距 20 日高比例
- `close_position = (close - low) / (high - low)`：当日收盘位置
- `dollar_volume = close * volume`：当日成交额
- `atr14`：按 14 日真实波幅；`atr14_pct = atr14 / close`

## 默认追涨过滤条件（modes/momentum.md 调用）

按价格档位 `1-10 / 10-100 / 100-500` 分别筛，避免跨档重复。默认门槛：

```
volume > 1,000,000
avg_vol_20d > 150,000
dollar_volume > 3,000,000
close > ma10  AND  ma10 >= ma20
ret_1d  BETWEEN -0.08 AND 0.80
ret_5d  BETWEEN  0.05 AND 2.50
ret_10d BETWEEN  0.08 AND 3.50
range_20d BETWEEN 0.25 AND 6.00
volume_ratio > 1.15
close_to_high_ratio > 0.72
close_position > 0.65
```

按价格档位的 ATR 下限：`1-10 → 0.045`，`10-100 → 0.035`，`100-500 → 0.02`；用户显式传入 `--min-atr-pct` 时覆盖。

## 评分与交易计划公式

```
momentum  = min(ret_5d, 0.75)*1.6 + min(ret_10d, 1.1)*1.0
volatility= min(range_20d, 2.0)*0.75 + min(atr14_pct, 0.25)*1.5
volume    = min(volume_ratio, 6.0)*0.2
quality   = close_to_high_ratio*1.2 + close_position*0.9
extension_penalty  = max(ret_5d - 0.9, 0)*1.2 + max(ret_1d - 0.35, 0)*1.0
weak_close_penalty = max(0.55 - close_position, 0)*1.5
score = momentum + volatility + volume + quality - extension_penalty - weak_close_penalty
```

默认交易计划（追涨模式；其他模式止损/止盈在 [risk-rules.md](../core/risk-rules.md)）：

```
buy_low      = close * 0.985
buy_high     = close * 1.015
atr_stop     = close - 1.15 * atr14
trend_stop   = ma10 * 0.97
day_low_stop = low * 0.98
raw_stop     = max(atr_stop, trend_stop, day_low_stop)
stop_loss in [close*0.82, close*0.94]
take_profit  = max(high_20d * 1.08, close + 1.8 * (close - stop_loss), close + 1.35 * atr14)
```

最低 R:R 默认 1.6（详见 [risk-rules.md](../core/risk-rules.md) 各模式止盈表）。
output-contract.md 总表的 `入场区间` 列默认 `close*0.985 - close*1.015`。

## 模式 5 候选筛选 SQL 模板

财报跳空模式（mode5_candidates.sql 摘要）：

```sql
WITH earnings_window AS (
  SELECT symbol, earnings_date, eps_estimate, timing
  FROM market_data.us_equity_earnings
  WHERE earnings_date BETWEEN CURRENT_DATE + 3 AND CURRENT_DATE + 14
    AND eps_estimate IS NOT NULL
)
SELECT e.symbol, e.earnings_date, e.timing,
       l.close,
       ROUND(100 * l.close / l.high_20d, 1)        AS pct_of_20d_high,
       ROUND(l.close * l.avg_vol_20d / 1e6, 1)     AS avg_dollar_vol_m,
       f.sector, f.industry,
       ROUND(f.market_cap / 1e9, 2)                AS market_cap_b,
       ROUND(100 * f.revenue_growth, 1)            AS rev_growth_pct,
       ROUND(100 * f.short_percent_of_float, 1)    AS short_pct
FROM earnings_window e
JOIN latest_features l   ON l.symbol = e.symbol
JOIN fundamentals    f   ON f.symbol = e.symbol
WHERE f.market_cap BETWEEN 5e8 AND 1e11
  AND l.close * l.avg_vol_20d > 1e7              -- 日均成交 > $1000 万
  AND l.close / l.high_20d < 0.90                -- 距 20 日高 > 10%
  AND f.sector IN ('Technology','Communication Services',
                   'Industrials','Consumer Cyclical')
  AND f.revenue_growth > 0.10
ORDER BY f.revenue_growth DESC, (l.close / l.high_20d) ASC
LIMIT 30;
```

拿到 30 只初筛后再对每只调 `symbol_snapshot` 查 RSI14（要求 < 65），按 [modes/earnings-gap.md](../modes/earnings-gap.md) 步骤 3 打分取前 8 只。

模式 5 打分逻辑：

```
score = 0
score += min(revenue_growth_pct, 80) / 4          # 最多 +20
score += 10 if 同行业龙头近 30 天爆过 else 0
score += 10 if 近 30 天分析师上调 else 0
score += 5  if short_pct in 10..20 else 0
score += 5  if market_cap in 5..30 (亿) else 0    # sweet spot 额外加分
score += 5  if insider 近 30 天净买入 else 0
score -= 10 if RSI > 60 else 0
score -= 15 if 已有定增/权证/可转债 else 0
```

分数 ≥ 30 进入推荐，分数 < 15 剔除，输出最多 8 只。

## 关键判断纪律

- **先找主导偏向，再做校验**。每只候选先回答"这次到底靠什么赚钱"，只能选一个主导变量
- **用户点名不等于推荐**。点名 ticker 进"待验证池"，按当前模式重新跑全市场 + 同一把尺子排序；不进 Top 3 不给仓位，只列"复核：观察/不推荐"
- **输出必须区分来源**：`MCP筛选` / `Web催化` / `财报窗口` / `用户点名复核`
- **不要把所有利多叠满才推荐**：那是共识票，不是高赔率票
- **校验是为了否证主导偏向，不是为了堆加分**
- 用户表达"赚太少 / 赔了想翻本" → 不要默认追涨，提供"追涨稳健仓 + 财报跳空进攻仓"组合
- 用户问"为什么没推已暴涨的 X" → 解释事后，再用模式 5 找下一个还没爆的 X
- 用户情绪不稳（刚亏完/焦虑/急翻身）→ **拒绝推荐模式 5**
- 用户问"最有把握 / 同时符合多个模式" → 才进模式 6，**不主动追求多信号交集**
