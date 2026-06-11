---
name: monster-stock-hunter
description: 从 WebSearch 与可用的 TradingAgents MCP 行情工具中**提前识别当日可能点火的美股妖股/龙头/翻倍票**,默认主线是`点火预判 → 启动确认 → 接棒升级`(涨幅 0%~+15%、催化 <4h、未上 Top 3),让用户吃到第一口肉而非坐车追榜。涵盖盘后低基数初燃、催化日历埋伏、同业传导接棒、微盘公告窗口、二次点火重启 5 类点火前夜原型;重点关注 AI 微盘题材(如 AI agent、AI data center、AI infrastructure、AI sales platform、AI pivot)与低流通 squeeze 的组合;同时支持准榜首挑战者、高弹性翻倍候选、涨幅榜榜首事后审计、微盘 biotech、低流通 squeeze、盘前高开延续、第二天修复等场景。适用于用户提出妖股、龙头、翻倍票、第一口肉、还没起爆、点火预判、抓到就是大肉、错了就快速止损、ENVB/HTCO/YAAS/INHD/CHAI 那种、AI 小票、AI 微盘、AI 数据中心、AI agent、top gainer、涨幅榜第一、只做最猛的票、当前不是榜首但可能成为榜首、微盘 biotech 暴拉等请求。
---

# 美股妖股猎手

**工作方式**: 妖股 = 高赔率做 T,目标是**吃第一口肉**。决策默认主线是`点火预判 → 启动确认 → 接棒升级 → 撤退`,从涨幅 `0%~+15%` 阶段就识别原型,不是等到已涨 +50%~+200% 才追榜。决策路径: 5 原型识别 → 三连校验 → 因果链筛候选 → 红旗降级 → 真假分类 → 报价认证 → 三段加仓 → 做 T/止盈/被套反抽。**files 按场景渐进式加载**(详见下方 §渐进式加载),不要无脑全读;`top-runner`(榜首事后)默认只在用户明确问榜首/Top1 时加载;`extreme-lottery-leaderboard`默认不参与主攻,但用户问`+40%以上 / 高涨幅 / 高回报 / 翻倍 / 最猛 / 彩票仓`时必须加载做防漏审计。

## 定位

本 skill 不是稳健选股。它专门寻找:

- **点火前夜**(默认主线): 涨幅 `0%~+15%`、催化 `<4h`、未上 Top 3 的当日可能点火的票,让用户吃到第一口肉而非坐车。
- 当天可能打出 `+15%` 到 `+100%` 波动的妖股、接棒挑战者(接棒升级,不再是默认主攻入口)。
- 低流通、强催化、biotech 点火、逼空、SPAC/壳股停牌这类高赔率机会。
- 已涨 +50%~+200% 的榜首/极端彩票仅作**事后审计与防漏**,不作默认主攻。
- 可接受低胜率,但必须抓错立刻止损,不得把主攻理解为重仓。

**妖性先行**: 候选必须先证明"有妖股空间",再谈风控。若没有低流通/高空头/翻倍基因/ATR 高波动/量能斜率/新鲜硬催化/同业传导中的至少 2 项,默认不是 monster,只能转给 `day-trading-recommender` 或列为普通观察。`安全但不妖`不是本 skill 的目标。

分工:

- `day-trading-recommender`: 高赔率但相对主流、更干净的日内机会。
- `monster-stock-hunter`: 点火预判、妖股、翻倍票、微盘事件点火;榜首/极端彩票事后审计。

## 渐进式加载 (canonical, 2026-05-15 加固)

**SKILL.md 之外的所有文件都按需 Read,不允许"无脑全读"**。判断当前请求属于哪个场景,只 Read 该场景列出的文件;省下 50%+ token,场景外文件用引用链接而非预读。

### 第 0 层 - 绝对必读 (任何输出都先读)

只 2 个文件,提供输出格式和仓位安全底线 — 这两个不读会写出格式不合规或仓位失控的答案:

| 文件 | 何时用 |
|---|---|
| [core/output-contract.md](references/core/output-contract.md) | 输出格式、表格字段、证据等级 A/B/C/D、最终自检 — **每次输出前必读** |
| [core/risk-rules.md](references/core/risk-rules.md) | 仓位、止损、账户风险刹车 — **每次给具体仓位前必读** |

**这层结束后判断场景,只读对应场景的 §第 1 层 文件。**

### 第 1 层 - 场景必读 (按当前请求场景选 1 组,每组 3-5 个文件)

不要每次都把整层全读,**根据用户消息判断属于下面哪个场景,只 Read 对应组**:

#### S1 - 妖股推荐 / 今日机会 / 妖股雷达 / 彩票仓 (默认场景)

用户没明确点名的开放式推荐请求 → 走点火预判主线。读这 4 个核心文件:

- [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) — 5 原型识别、早信号、三段加仓
- [workflow/causal-first.md](references/workflow/causal-first.md) — 池子扫描排序、因果评分、已涨太高过滤
- [core/web-cross-check.md](references/core/web-cross-check.md) — Web+MCP 分工矩阵、最低 WebSearch 调用清单
- [workflow/red-flags.md](references/workflow/red-flags.md) — 红旗枚举、降级/拒绝
- [workflow/pump-vs-real.md](references/workflow/pump-vs-real.md) — 真催化/squeeze/pump 三类分类(按需,只在不确定催化真假时额外读取)

用户明确说`想翻倍 / 本金翻倍 / 最猛 / 高弹性 / 一次抓翻倍`时,仍先保留 S1 的点火预判主线,但必须在第 2 层额外加载 [modes/high-elasticity-lottery.md](references/modes/high-elasticity-lottery.md);不要把这类高赔率诉求误判为风险处置。

用户同时明确时间约束为`十日内 / 10日内 / 一两周 / 很快看到结果 / 不想hold long`时,必须按 [modes/high-elasticity-lottery.md §闭环周期硬门](references/modes/high-elasticity-lottery.md) 先分 `T+0~10 快闭环 / T+11~40 中周期 / T+40+ 长周期`。最终主攻/备攻只能来自 `T+0~10 快闭环`;`REPL` 这类 BLA/CRL/PDUFA 监管票若硬判决在 1-8 周后,只能放入中周期观察池,不得混进十日翻倍主推。

用户明确说`QTEX 这种 / STAK 这种 / 连续多日上涨 / 多日主升 / 稳中向上 / 每天都涨 / 叙事升级 / 不是1-3天 / 不要日内 / 能撑一周 / 10日内连续翻倍`时,这是 **S1 + high-elasticity 的多日主升浪子场景**,必须按 [modes/high-elasticity-lottery.md §多日主升浪硬门](references/modes/high-elasticity-lottery.md) 先筛;若描述匹配`低流通 + 高空头 + 连续 PR/财报/8-K 催化 + 热门叙事迁移 + 爆量后不死`,必须进一步套用 [modes/high-elasticity-lottery.md §STAK 型连续叙事升级主升浪](references/modes/high-elasticity-lottery.md)。不得把`快打彩票 / 单日冲高 / 盘前一根大阳 / 1-3个交易日基础行情`票当作主推;OLOX 这类"单日收购新闻爆量 + 盘后回落 + 后续节点不足"只能列条件观察或不匹配,除非出现全新 10 日内验证节点并重新站回平台。

用户同时出现`彩票仓 / 极端 / 最疯狂 / 暴涨 100% / 涨 200% / 榜首 / WOK 那种 / YAAS 那种`与`想翻倍 / 本金翻倍 / 高弹性`时,这是**组合场景 S1 + high-elasticity + extreme-lottery 审计**,必须额外加载 [modes/extreme-lottery-leaderboard.md](references/modes/extreme-lottery-leaderboard.md)。此时 `extreme-lottery` 只用于防漏审计和降级说明,不替代点火预判主线,也不自动给主攻。

**全景优先硬门 (canonical, 2026-05-20)**: 用户问`+40% / 翻倍 / 最猛 / 高回报 / 彩票仓 / 极端 / 大涨`时,输出目标从"保守推荐优先"切换为**全景优先**。加载顺序固定为: [core/output-contract.md](references/core/output-contract.md) + [core/risk-rules.md](references/core/risk-rules.md) + [core/web-cross-check.md](references/core/web-cross-check.md) + [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) + [workflow/causal-first.md](references/workflow/causal-first.md) + [modes/extreme-lottery-leaderboard.md](references/modes/extreme-lottery-leaderboard.md) + [modes/high-elasticity-lottery.md](references/modes/high-elasticity-lottery.md)。首屏必须先展示`当前盘前/盘中 40%+ 全景审计池`,再分`可交易候选 / 已飞等回踩 / 只审计不追 / 报价冲突 / 退潮减仓`;不得把 MWC/SLXN 这类已翻倍票因"不适合主攻"而省略。

**IPO/new listing 防漏**: 对上市 `<=10` 个交易日的新股、IPO、ADS 首发、刚完成 offering/IPO closing 的票,若 Web movers/quote 显示盘前/盘中 `>=40%`、成交额 `>= $5M` 或进入 movers Top 10,即使本地日线不足、`company_fundamentals` 无数据、ATR/流通字段缺失,也必须进入极端彩票审计池。缺本地数据只影响证据等级和仓位,不能作为静默删除理由。

#### S2 - 用户已持仓 / 被套 / 亏损 / 补仓 / 减仓 / 持仓处置

关键词: `被套 / 套牢 / 亏损 / 回本 / 翻本 / 回血 / 解套 / 补仓 / 该不该卖 / 该不该减仓 / 我成本是 X`。只有当用户表达**已经亏损、已持仓、想追回亏损或仓位过重**时才进入本场景;若只是表达"想多赚/想高赔率",回到 S1 + high-elasticity-lottery。读这 3 个:

- [workflow/exit-framework.md](references/workflow/exit-framework.md) — 被套反抽 7 层降仓 / 补仓修复 / 止盈阶梯
- [core/web-cross-check.md](references/core/web-cross-check.md) — 三方认证 + 用户报价纠错
- [workflow/red-flags.md](references/workflow/red-flags.md) — 红旗确认是否需要立即清仓

不读 ignition-forecast / causal-first(用户已经在持仓中,不需要找新候选)。

#### S3 - 财报埋伏 / 微盘 biotech / 临床 / FDA / 想赌财报

关键词: `财报埋伏 / 财报彩票 / 财报后 / FDA / 临床 / biotech / 专利 / ENVB 那种`。读这 4 个:

- [core/pre-recommend-triplet.md](references/core/pre-recommend-triplet.md) — 历史反应/估值/指引 + 微盘资本事件硬门
- [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) — 原型 2 (催化日历) / 原型 4 (微盘公告)
- [core/web-cross-check.md](references/core/web-cross-check.md)
- [workflow/red-flags.md](references/workflow/red-flags.md)

#### S4 - 高位事件票 / MOU / 控制权交易 / 救命融资 / 这只票第几段

关键词: `高位还能不能入 / MOU / LOI / 控制权 / 救命融资 / 现在追还来不来得及 / 这只票第几段`。读:

- [workflow/catalyst-stage.md](references/workflow/catalyst-stage.md) — 催化硬度 + 阶段位置
- [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) §明确放弃 — 已涨太高过滤
- [core/web-cross-check.md](references/core/web-cross-check.md)

#### S5 - 复盘 / 上次推荐怎么样 / 历史案例

关键词: `复盘 / 上次那只 / 历史 / 类似的票 / 这个规则哪来的`。读:

- [case-studies.md](references/case-studies.md) — 全部历史案例
- [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) — 用 5 原型框架对照案例

#### S6 - 单纯报价校验 / 用户提供新价格 / 你价格不对

关键词: `券商显示 / 你的价格不对 / 现在不是这个价 / 已经跌到 X / 重新报一遍`。读:

- [core/web-cross-check.md](references/core/web-cross-check.md) — 用户报价纠错触发规则
- [workflow/exit-framework.md](references/workflow/exit-framework.md) §被套反抽(若已持仓)

#### S7 - MCP 调用失败 / 数据库挂了 / 我没有 MCP

任何场景下若 MCP 工具调用失败或用户表明 MCP 不可用,在原场景文件之上**额外加载**:

- [core/mcp-fallback.md](references/core/mcp-fallback.md) — L0/L1/L2 三层降级

#### S8 - 预测 / 命中验证 / 最近一周妖股复盘

关键词: `预测 / 提前发现 / 第一口肉 / 命中率 / 最近一周妖股 / 涨幅40%以上 / 为什么后知后觉 / skill是否可信 / 验证预测`。读:

- [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) — 点火前预测评分卡、5 原型、可提前/不可提前判定
- [core/output-contract.md](references/core/output-contract.md) — 预测候选日志表、预测验证表、推荐质量复盘表
- [case-studies.md](references/case-studies.md) — 成功/失败/漏网案例校准

本场景优先调用 MCP `rolling_ignition_walkforward` 做滚动 `5天训练 + 2天验证` 闭环;若用户只问单日或指定日期,再调用 MCP `ignition_candidate_replay` 做历史日期前一日回放;若用户问最近一周妖股样本池,调用 MCP `monster_weekly_backtest` 做 `>=40%` 复盘。[scripts/rolling_ignition_walkforward.sql](scripts/rolling_ignition_walkforward.sql)、[scripts/monster_weekly_backtest.sql](scripts/monster_weekly_backtest.sql) 与 [scripts/ignition_candidate_replay.sql](scripts/ignition_candidate_replay.sql) 作为同口径 SQL 模板。若 MCP/SQL 都无法运行,必须明确降级为`纸面规则复盘`,不得声称已验证命中率。

优先级: 若用户同时说`复盘`和`最近一周 / 命中率 / 预测 / 第一口肉 / 涨幅40%以上`,按 S8 处理,不要落回 S5 普通案例复盘。

### 第 2 层 - mode 触发 (在 §第 1 层 之上按用户关键词增量 Read 1-3 个)

mode 文件是具体打法/筛选模板,只在用户用对应关键词时增量读,**不要预读整组**:

| 用户关键词 | 增量 Read mode |
|---|---|
| 还没上榜 / 可能成为榜首 / 准妖股 / 接棒 / 挑战者 / 下一只最猛 | [modes/future-leader-challenger.md](references/modes/future-leader-challenger.md) |
| **榜首能不能追 / Top1 / 今天最强 / 涨幅榜第一**(明确点名) | [modes/top-runner.md](references/modes/top-runner.md) **(事后追榜,默认主线不加载)** |
| ENVB 那种 / biotech / 专利 / FDA / 临床 / 药股暴拉 | [modes/biotech-spike.md](references/modes/biotech-spike.md) |
| low float / 低流通 / squeeze / 逼空 / 轧空妖股 | [workflow/pump-vs-real.md](references/workflow/pump-vs-real.md) + [workflow/red-flags.md](references/workflow/red-flags.md) |
| **AI 小票 / AI 微盘 / AI agent / AI data center / AI infrastructure / AI sales platform / AI pivot / INHD 或 CHAI 那种** | [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) + [workflow/causal-first.md](references/workflow/causal-first.md) + [workflow/red-flags.md](references/workflow/red-flags.md);若涨幅 `>=40%` 同时加载 [modes/extreme-lottery-leaderboard.md](references/modes/extreme-lottery-leaderboard.md) |
| RDAC 那种 / 停牌票 / halt runner / SPAC 壳股 / redeem squeeze | [modes/extreme-lottery-leaderboard.md](references/modes/extreme-lottery-leaderboard.md) + [workflow/red-flags.md](references/workflow/red-flags.md) |
| 高开 / 盘前暴拉 / gap and go / 开盘冲锋 | [modes/future-leader-challenger.md](references/modes/future-leader-challenger.md) |
| **WOK 那种 / YAAS / 最疯狂 / 暴涨 100% / 涨 200% / 极端彩票 / 彩票仓极端 / 高回报 / 大涨 40% 以上 / 100%+ 股票**(2026-05 后扩展) | [modes/extreme-lottery-leaderboard.md](references/modes/extreme-lottery-leaderboard.md) **(事后审计/防漏展示,默认主线不主攻;只要用户问到 +40%+ / 100%+ / 高回报就自动加载,不必非要点名 WOK/YAAS)** |
| **想翻倍 / 本金翻倍 / 想赚得多 / 要极端 / 最猛 / 抓到就大肉 / 高弹性 / 一次抓翻倍 / 十日内 / 10日内 / 很快看到结果 / 不想hold long / QTEX这种 / STAK这种 / 稳中向上 / 每天都涨 / 叙事升级 / 连续多日上涨 / 多日主升 / 不是1-3天** | [modes/high-elasticity-lottery.md](references/modes/high-elasticity-lottery.md) |
| 下跌回头 / 低开修复 / 反包 / 急跌回拉 / RXT 这种 / 二次修复 / 抄底 / 跌得狠 / 跌幅榜 / Top Losers / 涨跌 Top 结合 / 错杀修复 | [modes/reclaim-bounce-hunter.md](references/modes/reclaim-bounce-hunter.md) |
| 夜盘 / 盘前做T / after-hours / premarket scalp / 快进快出 | [modes/extended-hours-scalp.md](references/modes/extended-hours-scalp.md) |
| 昨天暴拉今天还能不能接 / 第二天还能不能做 / 延续 | [modes/reclaim-bounce-hunter.md](references/modes/reclaim-bounce-hunter.md) |
| 财报埋伏 / 赌财报 / 财报彩票 / 近期翻倍 / 高弹性财报 | [modes/earnings-lottery.md](references/modes/earnings-lottery.md) |

S1 默认妖股请求增量加载 [modes/future-leader-challenger.md](references/modes/future-leader-challenger.md)(接棒池) + [modes/reclaim-bounce-hunter.md](references/modes/reclaim-bounce-hunter.md)(原型 5 实现)。`top-runner` 默认不加载,只在用户明确点名"榜首/Top1"时才读。**`extreme-lottery` 自 2026-05 起放宽自动加载触发**: 只要用户问到 `+40% 以上 / +50%+ / +80%+ / 100%+ / 翻倍 / 高回报 / 想赚多 / 最猛 / 高弹性` 等任一高弹性诉求, 即使没点名 WOK/YAAS, 也必须加载该 mode 做完整图景展示 (扫描全 ret_1d 段, 不允许过滤 +30%+ 票, 即便最终决策档默认为观察/极小仓)。

### 加载清单总结(对照执行,不允许超载)

| 场景 | Read 文件数 | 文件清单 |
|---|---:|---|
| S1 妖股推荐(默认) | 6-8 | 第 0 层 2 + S1 4 + 默认 mode 2 |
| S2 持仓处置 | 5 | 第 0 层 2 + S2 3 |
| S3 财报埋伏 | 6-7 | 第 0 层 2 + S3 4 + earnings-lottery 1 |
| S4 高位事件票 | 5 | 第 0 层 2 + S4 3 |
| S5 复盘 | 4 | 第 0 层 2 + S5 2 |
| S6 报价校验 | 3-4 | 第 0 层 2 + S6 1-2 |
| S7 MCP 失败 | +1 | 任何场景 + mcp-fallback |
| S8 预测/验证复盘 | 5 | 第 0 层 2 + S8 3 + 回放 SQL |
| S1 + 高弹性翻倍 | 7-9 | 第 0 层 2 + S1 核心 4-5 + high-elasticity 1 + 默认 mode 0-2 |
| S1 + 高弹性 + 极端彩票审计 | 8-10 | 第 0 层 2 + S1 核心 4-5 + high-elasticity 1 + extreme-lottery 1 + 默认 mode 0-2;若用户问 +40%/翻倍/最猛,按`全景优先`把 40%+ 审计池前置 |

每场景平均 5 个文件,不是旧设计的 9+ 个。如果当前场景需要超过 8 个文件,先怀疑是否场景判定错了;但`高弹性翻倍`和`极端彩票审计`是明确例外,不能为了控制文件数省略 high-elasticity 或 extreme-lottery。

### 加载前自检 (Codex 必做)

在输出任何候选前,先用一句内部判断确认本轮加载路径:

```text
场景 = S?
触发 mode = [...]
必读文件 = [...]
是否需要 extreme-lottery 审计 = 是/否
```

若用户原话同时包含`妖股/彩票仓/极端/翻倍/本金翻倍`,正确加载路径是:

```text
S1 默认妖股推荐
+ core/output-contract.md
+ core/risk-rules.md
+ workflow/ignition-forecast.md
+ workflow/causal-first.md
+ core/web-cross-check.md
+ workflow/red-flags.md
+ workflow/pump-vs-real.md (催化真假不确定时)
+ modes/high-elasticity-lottery.md
+ modes/extreme-lottery-leaderboard.md
```

### 默认池子扫描顺序(canonical,causal-first.md 引用)

```text
点火预判池 (5 原型, ignition-forecast)
  → 预测候选池 (0%-15% 点火前 / 15%-40% 接棒 / 40%+ 事后审计)
  → 因果假设池
  → 盘后点火/次日延续池
  → 挑战者池
  → 跌幅错杀修复池 (Top Losers + reclaim-bounce)
  → 二次点火池
  → 现任榜首池 (只作防漏 / 已过热对照,不作主攻入口)
  → 已过热放弃池
```

现任榜首池显式后移到挑战者池之后,纠正"已涨 = 可信"的锚定偏差。

### HCWB 型盘后低基数点火防漏

若某票在最近完整常规盘没有进入本地涨幅榜、甚至收跌或低量,但收盘后因财报、8-K、临床/监管、合同、融资/控制权交易等公告出现 `after-hours / overnight / premarket +50%` 级别跳涨,必须进入`盘后点火/次日延续池`和`极端彩票榜首池`审计。典型特征:

- 常规盘 `ret_1d <= 0` 或成交额很低,导致本地日线 SQL 漏掉。
- 延长交易涨幅 `>=50%`,或延长交易价相对常规盘收盘偏离 `>=40%`。
- 催化来自 48 小时内可追溯 PR/IR/SEC/财报,但常伴随 going concern、Nasdaq 合规、权证/融资稀释、低价低流动性红旗。
- 处理原则: `必须展示,不自动推荐`。只有拿到 A/B 报价且开盘前后出现连续成交、点差可控、reclaim/二突结构,才允许从观察升级为极小仓触发。

### AMST 型盘后公告点火防漏

HCWB 规则扩展到"常规盘低量 + 盘后企业客户/合同/商业进展 PR 点火"。若常规盘成交额很低或收跌,但盘后出现可追溯 PR/IR/SEC 公告并带来 `after-hours / overnight / premarket +50%` 以上跳涨,必须进入`Web升级观察`、`盘后点火/次日延续池`和`预测评分总表`。本地日线低量只说明 SQL 会漏,不能作为静默删除理由。

典型样本: `AMST 2026-05-18` 常规盘低量收弱,盘后因 NurseMagic 企业客户公告跳涨约 `+90%`。正确输出:展示为条件观察/盘后点火延续,再用当前 quote、成交连续性、spread、红旗和 R/R 决定是否升级;若不推荐,必须写清是流动性、报价或结构原因。

## 本地防漏 scripts

这些 SQL 只做本地日线初筛;不能替代 Web 最新报价、新闻和红旗复核。

| Script | 何时执行 |
|---|---|
| [scripts/leaderboard_audit.sql](scripts/leaderboard_audit.sql) | 默认妖股、榜首、复盘防漏;合并涨幅榜、低价高成交妖股、高成交额强动量 |
| [scripts/hyper_elastic_audit.sql](scripts/hyper_elastic_audit.sql) | 用户问翻倍/想赚多/极端/最猛/高弹性时;按翻倍基因 + ATR + 流通筛选高弹性候选 |
| [scripts/reclaim_bounce_audit.sql](scripts/reclaim_bounce_audit.sql) | 默认二次点火池、RXT 型修复、低开/急跌反包 |
| MCP `monster_weekly_backtest` / [scripts/monster_weekly_backtest.sql](scripts/monster_weekly_backtest.sql) | S8 最近一周/最近 N 日妖股复盘;筛 `ret_1d >=40%` 样本并输出点火前量价痕迹 |
| MCP `ignition_candidate_replay` / [scripts/ignition_candidate_replay.sql](scripts/ignition_candidate_replay.sql) | S8 预测命中验证;按历史日期前一日可见数据模拟当时的预测候选,防止未来函数 |
| MCP `rolling_ignition_walkforward` / [scripts/rolling_ignition_walkforward.sql](scripts/rolling_ignition_walkforward.sql) | S8 闭环验证首选;最近 N 个交易日滚动生成 `5天训练 + 2天验证` 折,输出主攻层/观察层命中、漏网、误报与规则采纳表 |

## 不可丢的执行纪律

- **渐进式加载第一原则**: 收到用户请求后,**第一步先判断属于 §渐进式加载 里的 S1-S8 哪个场景**,再 Read 该场景列出的文件 + §第 0 层 必读 2 个 + 关键词触发的 mode 1-3 个;**不允许把 references/ 下文件无脑全读**。每次 Read 文件前,在内部判断"这个文件是否在当前场景的清单里",不在就不读。判断错可以中途纠正再 Read 补漏,但禁止"为了保险全读"。
- 输出必须以中文为主,且用户可见格式唯一服从 [core/output-contract.md](references/core/output-contract.md): 先一句话结论 + 当前时段与报价基准 + 最终候选总表,再展开前 1-2 只;mode 文件只能补充字段或审计附录,不得改变首屏结构。
- **Web + MCP 双调用硬门**(canonical 在 [core/web-cross-check.md §Web + MCP 分工矩阵](references/core/web-cross-check.md)): 任何今日/盘前/盘中/盘后/夜盘/妖股/彩票仓/翻倍/极端请求,必须按固定顺序跑完:
  - **Web (实时变化)**: 当前段 movers 榜 / 候选 quote / 48h 催化 / 7 天红旗
  - **MCP (量化+基本盘+历史)**: `symbol_snapshot` + `company_fundamentals` + `earnings_dates`(原型 2/4)+ `query_market_data` SQL
  - 不允许只跑一边互相替代;Web 不能给标准化量化结构,MCP 不能给当前 quote 和催化新闻
  - **MCP 调用失败时按需加载 [mcp-fallback.md](references/core/mcp-fallback.md) 走三层降级**(L0 不读 / L1 单点失败 Web 替代 / L2 全局离线 顶部声明+预判仓档),不允许因 MCP 失败拒绝输出
- **明确催化硬门 (canonical, 2026-05-21)**: `主攻/备攻/推荐/可买` 必须先找到 48 小时内可追溯硬催化: 公司 PR/IR、SEC 8-K/6-K、财报/指引、FDA/临床结果、合同/订单、并购/控制权、监管/指数/政策等。只有涨幅榜、盘前量、低流通、ATR、short float、历史翻倍基因,但没有明确催化时,只能写`无源异动审计/观察/不推荐`,不得给主攻、备攻、建议本金、正常概率或"推荐"措辞。用户问 `40%+ / 翻倍 / 最猛` 时仍要展示这类票在全景审计池,但必须明确"没有硬利好,只审计不追"。
- **AI 微盘主题重点关注 (canonical, 2026-06-09)**: 当市场出现 INHD/CHAI 型行情,开放式妖股扫描必须额外扫 `AI agent / AI data center / AI infrastructure / AI sales platform / AI development agreement / AI pivot` 关键词 + 当前 movers。AI 只作为主题加速器,不能替代硬催化: 有金额、客户、合同、部署/交付节点、SEC/PR 时间戳且低流通/低基数/量能跃迁成立,可进 AI 微盘点火池;只有 AI 贴标签、旧闻重炒、反拆后低流通或无收入路径,只能写`AI 主题审计/观察`。
- **催化时间戳拆解硬门 (canonical, 2026-05-22)**: 任何`强 Web 消息 / 硬催化 / 并购 / 合同 / 融资 / 财报 / FDA`分析,必须按 [core/web-cross-check.md §催化时间戳拆解硬门](references/core/web-cross-check.md) 区分`事件/签署时间 → 原始 PR 时间 → SEC/交易所披露时间 → 媒体转发时间 → 行情反应时间`;用户追问"几点发布/是不是刚发"或最终候选依赖该催化时,按 [core/output-contract.md §催化时间链表](references/core/output-contract.md) 输出。媒体转发时间不能替代 PR/SEC 首发时间;时间链不完整时降为观察。
- **夜盘/盘前价格可获取硬门**: 今日/盘前/夜盘/盘后/妖股请求不得默认写"盘前价无法获取"。必须先按 [core/web-cross-check.md §延长时段价格获取强化](references/core/web-cross-check.md) 尝试`用户券商 → quote页 → 第二公开源 → movers发现价 → 新闻描述 → MCP背景`;只有缺 quote 页、成交量/bid-ask 或源冲突时才降级,并写清失败点。
- **默认主线是点火预判,不是追榜**: 任何无明确点名的妖股请求,必须按 [workflow/ignition-forecast.md](references/workflow/ignition-forecast.md) 5 原型扫描并展示结果,再展示挑战者池与二次点火池;`top-runner` 默认只在用户明确问"榜首/Top1/涨幅榜第一"时才读。**全景优先例外**: 用户问`+40%以上 / 大涨 / 高涨幅 / 高回报 / 翻倍 / 最猛 / 彩票仓 / 极端`时,必须同步加载 `extreme-lottery` 并在首屏报价基准之后展示`当前盘前/盘中 40%+ 全景审计池`,然后再说明哪些可交易、哪些等回踩、哪些只观察;不得因为票已过热、红旗重、IPO 数据不足或不适合主攻而省略。
- **盘中/AH 突发 Web 升级硬门 (canonical, 2026-06-09)**: 用户问`今天 / 今日 / 翻倍 / 最猛 / 高回报 / 彩票仓 / 极端`时,必须扫描`当前 movers Top10 + premarket Top10 + intraday Top10 + after-hours/overnight Top10`;所有 Web 显示 `>=40%`、任一交易段 Top3、或当前成交额 `>= $10M` 的票必须进入`40%+ 全景审计池`,即使 `symbol_snapshot` 返回 no data、最近完整日线低量/收跌、fundamentals 不完整或本地 SQL 未入榜。此类票标为 `Web-only C/D 审计` 或 `Web升级观察`,先展示再降级;不得因为 MCP 缺失静默删除。`most traded/most active/unusual volume` 只能辅助流动性,不能替代涨幅榜。2026-06-08 的 INHD/SUNE/NPT/BYAH/TDIC/RGNT 是本硬门校准样本。
- **路径型/软催化降级硬门**: FDA `may support / pathway / Q-sub minutes`、AI development agreement、MOU/LOI、reverse merger、反拆后低流通动量、AI pivot 等,默认不是可直接主推的硬收入催化。除非同时取得 A/B 级 quote、站回 VWAP/开盘价/盘前承接区或二突、成交连续且红旗可控,否则只能写`观察 / 已飞等回踩 / 极小仓触发`,不得给正常主攻/备攻。
- **反新闻追涨硬门**: 开放式`预测 / 今日 / 40%+ / 高回报 / 翻倍`请求,最终排序必须先来自 [workflow/ignition-forecast.md §多维度权重综合评分](references/workflow/ignition-forecast.md),再用 Web 新闻和 movers 做升级/降级。盘前/盘中榜单 Top 1-3 只能进入`40%+审计池`或`Web升级观察`,不得因"涨幅大 + 有新闻"直接成为首选;除非它同时通过`预测评分表`的综合分门槛、红旗检查和当前流动性硬门。若最终首选来自榜单前三,回答必须显式写出它击败纸面雷达候选的量化原因(至少 4 个维度),否则降为观察。
- **预测闭环必须留痕**: 任何开放式妖股预测/第一口肉请求,必须输出`预测候选日志表`;任何最近一周/命中率/skill 是否可信请求,优先输出`滚动 walk-forward 摘要表 + 验证明细表 + 规则落地表`,再输出单日`预测验证表`,并把结果分为`命中 / 条件未触发 / 漏网 / 误报 / 快打命中但持有失败 / 不可提前发现`。预测是概率雷达,不是收益承诺;A/B/C/D 证据等级仍决定是否能给仓位。回放预测分 `5` 的低流通/弱反转/低基数票必须进入`Web升级候选`复核,不得因未达 `6` 分直接丢弃。**每月跑完 30 日 walk-forward 闭环后,把规则验证结果以 `✓/⚠/✗/?` 标签回写 [workflow/ignition-forecast.md §点火前预测评分卡](references/workflow/ignition-forecast.md),并归档完整数据到 [references/validation/walkforward-YYYYMM.md](references/validation/);若该月跑了监督发现 (数据驱动找新规则), 同时归档 [references/validation/supervised-discovery-YYYYMM.md](references/validation/) 并以 holdout-validated 标签链接到 ignition-forecast.md §本期监督发现。最近一期: [walkforward-202605.md](references/validation/walkforward-202605.md) + [supervised-discovery-202605.md](references/validation/supervised-discovery-202605.md)。单期 ✓ 不代表永久结论, 3 个月连续通过才能去掉单期后缀, 才能把发现规则写进 `_IGNITION_CANDIDATE_REPLAY_SQL`。**
- **妖性硬门优先于稳健美观**: 最终主攻/备攻必须通过 [workflow/causal-first.md](references/workflow/causal-first.md) 的`妖性评分`;妖性不足时不要为了凑推荐给普通慢票。宁可说"今天没有真正妖的 A/B 级",也不要推荐不妖的安全票。
- **用户表达高赔率诉求是合理需求,不是心态问题**: 用户说"想翻倍 / 想赚得多 / 要极端 / 最猛"时,加载 [modes/high-elasticity-lottery.md](references/modes/high-elasticity-lottery.md) 真实地找高弹性候选,**不允许**直接降档说教。降档处置只针对"已亏损/被套/真心态崩/财务风险"四类真实信号(详见 [core/risk-rules.md §情绪与恢复模式](references/core/risk-rules.md))。
- **诚实优于凑数**: 高弹性扫描没找到 A 级候选时,必须直接说"今天没找到"并解释什么条件下才会出现,不允许把 C 级硬写成主攻。
- **原型 2/4 候选必须先跑 [core/pre-recommend-triplet.md](references/core/pre-recommend-triplet.md) 强制项**: 原型 2(催化日历埋伏)强制跑校验 1 历史反应;原型 4(微盘公告窗口)强制跑微盘资本事件 7 天/30 天硬门;未跑完不得给主攻/备攻。
- **三段加仓必须按 ignition-forecast §三段加仓状态机 顺序升级**: 预判仓 1/3 → 确认仓 2/3 → 主攻仓满仓,不允许跳级,不允许在垂直拉升中加仓。
- 所有最终候选必须先通过 [core/web-cross-check.md](references/core/web-cross-check.md) 的三方认证;缺少可复核报价时只能观察。
- 对今日/盘前/妖股/彩票仓请求,如果没有明确展示或说明`after-hours/overnight gainers`与`盘后点火/次日延续池`,不得称为完整扫描;尤其要防止漏掉 RGNT/HCWB 型"常规盘不强、盘后突然点火"票。
- 用户出现亏损、回本、翻本、满仓、重仓或心态失衡时,立即按 [core/risk-rules.md](references/core/risk-rules.md) + [workflow/exit-framework.md](references/workflow/exit-framework.md) 切换为风险处置/恢复模式;此时三段加仓状态机**禁用**,只允许单笔小仓试错。
- 极端彩票池是防漏审计,不是自动推荐;红旗票要展示,但按 [workflow/red-flags.md](references/workflow/red-flags.md) + [core/risk-rules.md](references/core/risk-rules.md) 降级。
- 榜首只作防漏与资金响应验证;默认先写`因 -> 传导 -> 未定价证据 -> 确认信号 -> 证伪信号`,再写价格计划。
- 默认寻找最强单一叙事,不是最全面好看;高波动是机会空间,没有结构才是问题。
- 最终回答前必须执行 [core/output-contract.md](references/core/output-contract.md) 的最终自检。
