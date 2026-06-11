---
name: long-stock-recommender
description: 从 TradingAgents 本地 PostgreSQL 美股行情库中按不同策略模式(追涨 / 超跌反弹 / 回调买入 / 价值挖坑 / 财报跳空 / 叠加信号)筛选可做多股票候选,结合动量、波动率、成交量、财报窗口、新闻/SEC 催化、基本面和止盈止损规则给出 swing 持仓周期(1 周 - 6 月)的交易参考。适用于用户提出股票推荐、高收益股票筛选、暴涨股、想赚得多、翻身机会、只能做多、止盈止损、抄底反弹、价值投资、基本面好的长线股、结合数据库/MCP 推荐美股等请求。
---

# 美股做多候选筛选

**工作方式**: swing 做多 = 1 周-6 月持仓的趋势/价值/财报跳空埋伏。决策路径只有一条:候选生成与首推前三连校验 → 红旗降级 → 真假分类 → 报价认证 → 入场后减仓/锁利/被套处理。这条路径上的文件全部默认必读;只有真正分流的场景(高位事件票、复盘)和具体模式才按需加载。

## 定位

本 skill 是 **swing 视角**的做多候选筛选,持仓周期通常 **1 周 - 6 个月**。它专门寻找:

- 趋势跟随、回调买入、超跌反弹、价值挖坑、财报跳空埋伏、叠加信号(高胜率组合)六类做多机会。
- 结合本地日线 + 基本面 + 财报窗口 + Web 催化 + 红旗复核给出可执行的入场区间、止损、止盈、仓位上限和概率区间。
- 用主导偏向 + 三连校验拒绝"看起来都还行"的故事股,避免重蹈 TSEM / AEVA 利好出尽教训。

分工:

- `monster-stock-hunter`: 当天可能 +15%~+100% 的妖股、榜首、翻倍票;盘中决策,不在本 skill 范围内。
- `day-trading-recommender`: 持仓 < 6 小时不过夜的高赔率日内做多;盘中/盘前决策。
- `long-stock-recommender`: 持仓 ≥1 周的 swing 做多;包括财报前 3-14 天分散埋伏与跨财报持有。

## MCP 绑定规则

本 skill 使用当前宿主平台已配置的 TradingAgents market-data MCP。不要假设只能在 Codex 里运行;Codex、Claude Code 或其他 MCP 客户端都应绑定到同一个常驻 SSE 服务。

- 后端 FastMCP 服务名: `tradingagents-market-data`
- 共享 SSE 地址: `http://127.0.0.1:18080/sse`
- 不同客户端工具名前缀不同: Codex 多为裸名(`db_health`),Claude Code 多为 `mcp__<server-name>__<tool-name>`(例如 `mcp__tradingagents-market-data__query_market_data` 或 `mcp__mydatabase__db_health`)

需要用到的工具: `db_health`、`query_market_data`、`latest_prices`、`symbol_snapshot`、`long_momentum_candidates`、`earnings_dates`、`oversold_bounce_candidates`、`pullback_candidates`、`value_candidates`、`company_fundamentals`。

如果当前运行环境没有暴露这些 MCP 工具,必须先告知用户"当前平台未暴露 TradingAgents market-data MCP 工具",并提示检查该平台 MCP 配置是否指向 `http://127.0.0.1:18080/sse`。

## 三层加载

### 第 1 层 - 默认必读 (任何 swing 输出都加载)

这 7 个文件构成"swing 做多"的硬合约,每次回答前都读完。文件之间**不重不漏**,每个职责只在一处定义:

| # | 文件 | 唯一职责 (canonical) | 不职责 (指向其他文件) |
|---|---|---|---|
| 1 | [core/output-contract.md](references/core/output-contract.md) | swing 总表、单票短评、价格目标概率表、报价校验表、最终自检 | 字段口径定义全部指向 sibling |
| 2 | [core/risk-rules.md](references/core/risk-rules.md) | 各模式仓位上限、止损硬线、止盈节点、用户状态守门、大盘环境约束 | 入场后减仓/锁利动作链 → exit-framework |
| 3 | [core/web-cross-check.md](references/core/web-cross-check.md) | WebSearch 模板、来源优先级、报价校验触发、证据等级 A/B/C/D 闸门 | 红旗清单 → red-flags;补仓认证 → exit-framework |
| 4 | [workflow/causal-first.md](references/workflow/causal-first.md) | 默认路由总流程 Step 0-5、首推前三连校验(财报反应/估值/指引)、共用 SQL 字段与公式、模式 5 候选 SQL 模板、关键判断纪律 | 入场后操作 → exit-framework;红旗清单 → red-flags |
| 5 | [workflow/red-flags.md](references/workflow/red-flags.md) | 红旗目录、复核强度分级、insider/float/short 阈值、资本事件、利好出尽、估值冲突 | 仓位上限阈值 → risk-rules;MOU 阶段 → catalyst-stage |
| 6 | [workflow/pump-vs-real.md](references/workflow/pump-vs-real.md) | 真业绩拐点/板块情绪炒作/利好出尽/庄家操纵分类、三问检查 | 红旗具体阈值 → red-flags |
| 7 | [workflow/exit-framework.md](references/workflow/exit-framework.md) | 三种参与方式、减仓/锁利节点、被套处理、补仓门槛 | 是否入场的判断 → causal-first;仓位硬上限 → risk-rules |

跳过这层任何一个文件都会出错: 漏 `causal-first` 会跳过首推前三连校验导致重蹈 TSEM 教训;漏 `red-flags` 会把利好出尽当真催化;漏 `pump-vs-real` 会把情绪炒作包装成业绩拐点;漏 `exit-framework` 会给不出减仓/锁利节点。

### 第 2 层 - 真分流 (按场景触发)

只有这两个文件是真正的"按需读取":

| 触发条件 | 文件 |
|---|---|
| 高位事件票、MOU/LOI、控制权交易、救命融资、`现在还能不能入`、`这只票属于第几段` | [references/workflow/catalyst-stage.md](references/workflow/catalyst-stage.md) |
| 用户请求复盘、问类似历史案例、问某条规则来源 | [references/case-studies.md](references/case-studies.md) |

非高位事件票的常规 swing 候选可以不读 `catalyst-stage`;非复盘对话不读 `case-studies`。

### 第 3 层 - 模式触发 (按用户关键词读 1-2 个)

| 用户关键词 | 必读模式 | 主 MCP 工具 |
|---|---|---|
| 无 / 推荐 / 强势 / 动量 | [modes/momentum.md](references/modes/momentum.md) (默认追涨) | `long_momentum_candidates` |
| 超跌 / 反弹 / 抄底 / 跌多了 / RSI 超卖 | [modes/bounce.md](references/modes/bounce.md) | `oversold_bounce_candidates` |
| 回调 / 回踩 / 等回调 / 洗盘 | [modes/pullback.md](references/modes/pullback.md) | `pullback_candidates` |
| 基本面 / 价值 / 长线 / 等涨 / 业绩好 | [modes/value.md](references/modes/value.md) | `value_candidates` + `company_fundamentals` |
| 想赚多 / 翻身 / 暴涨 / 波动太小 / 高回报 / MXL/POET 那种 / 财报机会 | [modes/earnings-gap.md](references/modes/earnings-gap.md) | `earnings_dates` + SQL |
| 叠加 / 同时满足 / 最高胜率 / 最有把握 / 既 X 又 Y / 最可能赚钱 | [modes/stacked.md](references/modes/stacked.md) | 多 MCP 工具交集 + SQL |

如果用户说"也看一下 X 的",可同时加载多个模式 reference 并输出多模式清单。

## 模式组合兼容性

|  | 追涨 | 反弹 | 回调 | 价值 | 跳空 |
|---|---|---|---|---|---|
| 追涨 | — | ❌ 数学互斥 | ⚠️ 逻辑冲突 | ⚠️ 估值相反 | ✅ 黄金 |
| 反弹 | ❌ | — | ❌ | ⚠️ | ❌ |
| 回调 | ⚠️ | ❌ | — | ⚠️ | ✅ 黄金 |
| 价值 | ⚠️ | ⚠️ | ⚠️ | — | ⚠️ |
| 跳空 | ✅ | ❌ | ✅ | ⚠️ | — |

只有三组能叠(详见 [references/modes/stacked.md](references/modes/stacked.md)):

- A: 追涨 + 跳空 + 历史财报正反应 🔥🔥🔥(MXL 4/24 +76% 样板)
- B: 回调 + 跳空窗口 🔥🔥
- C: 价值 + 业绩拐点 🔥

## 必要 scripts

这些 SQL 只做本地日线/财报窗口初筛;不能替代 Web 最新报价、新闻和红旗复核。

| Script | 何时执行 |
|---|---|
| [scripts/earnings_reaction_check.sql](scripts/earnings_reaction_check.sql) | **首推前三连校验 Step 3.A**,候选 `days_until ≤ 30` 必跑;查过去 4 次财报次日反应,执行历史财报反应一票否决 |
| [scripts/mode5_candidates.sql](scripts/mode5_candidates.sql) | 模式 5 财报跳空步骤 1+2 候选初筛 |
| [scripts/stacked_signals.sql](scripts/stacked_signals.sql) | 模式 6 叠加信号:把 momentum/pullback/value 候选与财报窗口做 JOIN |

## 不可丢的执行纪律

- 输出必须以中文为主,优先表格,再展开前 1-2 只主导偏向最清晰的候选。
- 每只首推必须先写出"这只票不是因为什么都好,而是因为 ______";写不出就不该当首推。
- **首推前三连校验**:任何模式,准备让用户买的那只先跑历史财报反应(若 ≤30 天)、估值 sanity、管理层指引;任一项推翻主导偏向即降级或排除并重选首推(详见 `workflow/causal-first.md` Step 3)。
- 用户出现亏损、回本、心态失衡时,立即按 `core/risk-rules.md` "用户状态守门"切换为风险处置/恢复模式;**拒绝推荐模式 5**。
- 用户点名 ticker 不等于推荐,只能进"待验证池",必须按当前模式跑全市场同一把尺子排序。
- 输出必须区分来源(`MCP筛选` / `Web催化` / `财报窗口` / `用户点名复核`)。
- 价格目标必须给概率区间(例 `35%-45%`),不允许单点;A/B 证据等级才能给入场仓位概率,缺报价时只能写"日线候选,概率置信度降低"。
- 最终回答前必须执行 `core/output-contract.md` 的输出前自检清单。
