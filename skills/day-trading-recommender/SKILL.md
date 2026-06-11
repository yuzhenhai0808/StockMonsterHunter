---
name: day-trading-recommender
description: 从 TradingAgents 本地 PostgreSQL 美股日线库 + WebSearch 盘前/盘中实时消息筛选**当天可操作**的高赔率日内(day trade)做多候选,持仓周期 <6 小时,不过夜。默认优先寻找**高弹性、高波动、单次能打出大肉**的进攻型机会,同时覆盖财报后延续、盘前 gap、强势突破、轧空、受约束的涨幅榜龙头追击、微盘爆拉等场景。每只候选必带入场上限价/硬止损/分批止盈/失效信号/最大持仓时间。适用于用户提出日内交易、短线、盘前挂单、盘中追涨、今日买卖、想要高赔率机会、HTCO/YAAS 那种微盘暴拉、财报后首日跳空、突破新高跟风、轧空挤压、只盯涨幅 top1、龙头追击、短线赚 10% 就走等请求。
---

# 美股日内交易候选筛选

**工作方式**: 日内 = <6 小时不过夜的高赔率做多。决策路径只有一条:候选生成与三连校验 → 红旗降级 → 真假分类 → 报价认证 → 入场后阶梯止盈/止损/失效。这条路径上的文件全部默认必读;只有真正分流的场景(高位事件票、复盘)和具体模式才按需加载。

## 定位

本 skill 是**严格日内**(<6 小时不过夜)的高赔率做多候选筛选。它专门寻找:

- 当日可贡献单次大肉(`+8%~+50%` 单笔波动)的进攻型机会
- 财报后延续、盘前 gap、强势突破、轧空、龙头追击、微盘爆拉、临床事件、事前催化埋伏等场景
- 每只候选必带入场上限价、硬止损、分批止盈、失效信号和最大持仓时间

分工:

- `monster-stock-hunter`: 当天可能 `+15%~+100%` 的妖股、榜首、翻倍票;专攻最猛的票
- `day-trading-recommender`: 高赔率但相对主流、更干净的日内机会;持仓 < 6 小时不过夜
- `long-stock-recommender`: swing(1 周 - 6 月)持仓的趋势 / 价值 / 财报跳空埋伏;不在本 skill 范围

## MCP 绑定规则

本 skill 使用当前宿主平台已配置的 TradingAgents market-data MCP。

- 后端 FastMCP 服务名: `tradingagents-market-data`
- 共享 SSE 地址: `http://127.0.0.1:18080/sse`
- 不同客户端工具名前缀不同:Codex 多为裸名(`db_health`),Claude Code 多为 `mcp__<server-name>__<tool-name>`

需要用到的工具:`db_health`、`query_market_data`、`latest_prices`、`symbol_snapshot`、`long_momentum_candidates`、`earnings_dates`、`company_fundamentals`、`list_market_tables`、`describe_table`。

本地日线表固定为 `market_data.us_equity_daily_prices`,日期字段 `price_date`,默认 `provider='yfinance'`。手写 SQL 不得使用旧表名 `us_equity_daily` 或旧字段 `date`。

如果当前运行环境没有暴露这些 MCP 工具,必须先告知用户"当前平台未暴露 TradingAgents market-data MCP 工具",并提示检查该平台 MCP 配置是否指向 `http://127.0.0.1:18080/sse`。

## 三层加载

### 第 1 层 - 默认必读 (任何日内输出都加载)

这 7 个文件构成"日内做多"的硬合约,每次回答前都读完。文件之间**不重不漏**,每个职责只在一处定义:

| # | 文件 | 唯一职责 (canonical) | 不职责 (指向其他文件) |
|---|---|---|---|
| 1 | [core/output-contract.md](references/core/output-contract.md) | 日内候选总表(12 列)、单票展开、报价校验、概率与最终自检 | 字段口径定义全部指向 sibling |
| 2 | [core/risk-rules.md](references/core/risk-rules.md) | 日内仓位上限、止损硬线、止盈节点、用户状态守门、概率校准、`14:45 ET` 强制清仓 | 入场后做 T 阶梯具体动作 → exit-framework |
| 3 | [core/web-cross-check.md](references/core/web-cross-check.md) | 报价/事件三方认证、证据等级 A/B/C/D 闸门、报价获取顺序、夜盘/盘后异动池 | 红旗清单 → red-flags;补仓认证 → exit-framework |
| 4 | [workflow/causal-first.md](references/workflow/causal-first.md) | 数据新鲜度、用户点名防锚定、强制日内三连校验、时间段路由、模式路由特殊规则、复盘闭环 | 入场后操作 → exit-framework;红旗清单 → red-flags |
| 5 | [workflow/red-flags.md](references/workflow/red-flags.md) | 直接拒绝清单、微盘 Pump 评分、反拆首日窄例外 | 仓位上限阈值 → risk-rules;MOU 阶段 → catalyst-stage |
| 6 | [workflow/pump-vs-real.md](references/workflow/pump-vs-real.md) | 真催化龙头/squeeze/纯 pump/利好出尽分类、日内分时盘口判断 | Pump 评分阈值 → red-flags |
| 7 | [workflow/exit-framework.md](references/workflow/exit-framework.md) | 五种参与方式、失效信号、被套反抽处置、补仓门槛、切换 swing 硬规则 | 是否入场的判断 → causal-first;仓位硬上限 → risk-rules |

跳过这层任何一个文件都会出错: 漏 `causal-first` 会用榜单名次当主攻;漏 `red-flags` 会把反拆/ATM 票当真催化;漏 `pump-vs-real` 会把纯 pump 包装成主攻;漏 `exit-framework` 会给不出止盈止损阶梯。

### 第 2 层 - 真分流 (按场景触发)

只有这两个文件是真正的"按需读取":

| 触发条件 | 文件 |
|---|---|
| 高位事件票、MOU/LOI、控制权交易、救命融资、`现在还能不能入`、`这只票属于第几段` | [references/workflow/catalyst-stage.md](references/workflow/catalyst-stage.md) |
| 用户请求复盘、问类似历史案例、问某条规则来源 | [references/case-studies.md](references/case-studies.md) |

低位爆量、无 MOU/控制权事件的常规候选可以不读 `catalyst-stage`;非复盘对话不读 `case-studies`。

### 第 3 层 - 模式触发 (按用户关键词读 1-2 个)

| 用户关键词 | 必读模式 |
|---|---|
| 无 / 日内 / short / day / 短线 / 今天有什么 / 今日买卖 / 推荐今天股票 | [modes/high-odds-radar.md](references/modes/high-odds-radar.md) (默认) |
| 财报预判 / 提前埋伏财报 / 赌财报 / 吃第一口肉 / 财报前布局 / earnings pre-position | [modes/earnings-pre-position.md](references/modes/earnings-pre-position.md) 🏆 |
| 盘前 / premarket / gap / 高开 / 盘前涨幅榜 | [modes/premarket-gap.md](references/modes/premarket-gap.md) |
| top1 / 涨幅榜 / 榜首 / top gainer / 龙头追击 | [modes/top-gainer-momentum.md](references/modes/top-gainer-momentum.md) |
| 微盘 / 暴拉 / HTCO 那种 / 翻倍 / 仙股 / pump | [modes/micro-pump.md](references/modes/micro-pump.md) |
| 财报跳空 / 财报后首日 / earnings gap / 刚出财报 | [modes/earnings-gap-day1.md](references/modes/earnings-gap-day1.md) |
| 突破 / 新高 / breakout / 创新高 | [modes/breakout.md](references/modes/breakout.md) |
| 轧空 / squeeze / 空头挤压 / 逼空 | [modes/short-squeeze.md](references/modes/short-squeeze.md) |
| 临床催化 / ASCO / AACR / AdCom / PDUFA / Phase 3 数据 / EDSA 那种 | [modes/catalyst-event-window.md](references/modes/catalyst-event-window.md) |
| 事前预判 / 第一口 / 还没涨 / 提前埋伏 / 爆发前 / pre-catalyst | [modes/pre-catalyst-radar.md](references/modes/pre-catalyst-radar.md) |

用户没说明用哪种,**默认走高赔率进攻雷达**,优先找能贡献单次大肉的候选,不要先把低波动稳健票放主推。详细路由特殊规则(财报预判优先级提升、事前催化雷达豁免、生科白名单)见 [references/workflow/causal-first.md](references/workflow/causal-first.md) "模式路由特殊规则"。

## 必要 scripts

这些 SQL 只做本地日线/财报窗口初筛;不能替代 Web 最新报价、新闻和红旗复核。

| Script | 何时执行 |
|---|---|
| [scripts/gap_candidates.sql](scripts/gap_candidates.sql) | 盘前 gap 扫描、缺口 + 量能异常候选 |
| [scripts/micro_momentum.sql](scripts/micro_momentum.sql) | 微盘爆拉跟风:3 日涨幅 > 50% 且流动性未崩 |
| [scripts/squeeze_candidates.sql](scripts/squeeze_candidates.sql) | 轧空猎手:高空头 + 低 float + 近期启动 |
| [../long-stock-recommender/scripts/earnings_reaction_check.sql](../long-stock-recommender/scripts/earnings_reaction_check.sql) | 财报跳空首日 / 财报预判埋伏:跨 skill 复用,查过去 4 次财报次日反应 |

## 不可丢的执行纪律

- 输出必须以中文为主;推荐 2 只及以上时先给候选总表,再展开前 1-2 只主攻。
- 每只首推必须先写出"这单不是因为它面面俱到,而是因为 ______";写不出就不该当首推。
- 所有最终候选必须通过 `core/web-cross-check.md` 的三方认证;A/B 证据等级才能给主攻/备攻、入场和概率;C/D 只能列观察。
- 用户出现亏损、回本、心态失衡或当日已连亏 2 笔时,立即按 `core/risk-rules.md` "用户状态守门"切换为风险处置/恢复模式。
- 用户点名 ticker 不等于推荐,只能进"待验证池",必须按当前模式跑全市场同一把尺子排序(详见 `workflow/causal-first.md` "用户点名防锚定")。
- **不过夜是硬线**:任何输出必须明确"今日 14:45 ET 强制清仓"。
- 默认日内 edge 是赔率不是胜率,接受较低命中率;不要把多个利多硬拼成结论。
- 微盘票必须显示 `workflow/red-flags.md` Pump 总分;总分 ≥ 15 直接拒绝。
- 数据来源透明 —— 每条催化必须带 WebSearch URL。
- 最终回答前必须执行 `core/output-contract.md` 的最终自检清单。
