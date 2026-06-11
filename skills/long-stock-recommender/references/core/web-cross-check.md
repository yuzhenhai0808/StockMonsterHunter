# 三方认证（用户 + Web + 本地）

本文件规定每次"现在买 / 今天 / 盘前 / 盘中"语境下，long-stock 推荐必须做的三方报价/事件认证。社会面风险的红旗目录与处理见 [red-flags.md](../workflow/red-flags.md)。

## 三方认证模板

每个最终候选，在给出 entry / 止损 / 止盈 / 仓位前必须填以下表（内部草稿，不必全部输出，但报价校验表必出）：

```
用户输入: 券商最新报价 / 持仓成本 / 时间戳 / 是否盘前盘中
Web 验证: 最新价 / 时间 / 来源 / 成交量 / 新闻 / 红旗
本地数据: 昨收 / ATR14 / 量比 / 20日位置 / Float / Short%
状态判断: 推荐建仓 / 计划持有 / 被套需减 / 二次反抽 / 失效退出
操作    : 建仓 / 加仓 / 持有 / 减仓 / 清仓
失效条件: 哪个价格 / 多少时长收不回
账户影响: 最大亏损金额
```

## 当前可交易性硬门

普通 swing 请求可以基于最近完整日线给`日线候选/观察计划`;但凡用户问`今天 / 今日 / 现在买 / 盘前 / 夜盘 / 开盘 / 盘中 / 高收益当天机会`,必须先通过当前可交易报价硬门。

本地 `latest_prices` / `symbol_snapshot` / SQL scripts 只代表最近完整日线或候选初筛,**不能当作当前可交易报价**。缺少当前/延长交易报价时,只能写`本次不是当前可交易推荐,只是日线候选`,不得写`现在可买`、`主攻可做`或给正常仓位概率。

### 证据等级闸门

每只当日可操作候选必须先标注报价证据等级:

| 等级 | 条件 | 允许输出 |
|---|---|---|
| A 实时/夜盘可交易 | 用户券商实时报价,或两个公开源给出当前/延长交易报价,且有价格、时间戳、成交量或 bid/ask | 可给当前入场、仓位和正常概率 |
| B 单源延长交易 | 一个公开源给出 after-hours / overnight / premarket 报价,且有明确时间戳和成交量或 bid/ask | 可给条件计划;必须标注单源报价,开盘前复核 |
| C 最近完整日线 + 新闻 | 只有本地最近完整日线和新闻催化 | 只能列为`日线候选/待盘前报价`;概率置信度降低 |
| D 报价冲突/过期 | 公开源日期过旧、源之间冲突、只有昨收、无成交、无时间戳或无法解释价差 | 不给当前入场、仓位或概率;只能写`数据不完整/报价冲突` |

当用户语境是当日可操作时,只有 A/B 允许输出当前入场区间、仓位上限和正常概率;C/D 不得写成`可做`。

### 报价获取顺序

固定不可调换:

1. 先查当前/延长交易 quote 页面或用户券商盘口
2. 再查新闻催化、公司 IR、SEC/8-K 和红旗
3. 最后才用本地日线做 swing 技术背景

不能先基于日线价给当前入场表,再补一句"需实时复核"。

## 来源优先级（冲突时）

| 优先级 | 来源 |
|---|---|
| 1（最高）| 用户提供的券商报价（含时间戳） |
| 2 | 公司 IR / 官方 PR / SEC 8-K |
| 3 | Nasdaq / MarketWatch / Yahoo / Stocknear / StockAnalysis 等聚合站 |
| 4 | 本地日线收盘价 |

冲突时上层覆盖下层。例：本地库说 5/11，公司 IR 说 5/7 已发，则按 5/7。

价格冲突另按以下规则阻断:

- 用户券商报价优先用于交易价格,但必须标注`用户券商报价,需以盘口为准`
- Web/公开源优先用于新闻、SEC、融资/ATM/反拆红旗
- 本地日线只能做背景,不能替代盘中报价或新闻
- 若公开源之间价差或涨跌幅冲突 `>=3%`,该票只能标`报价冲突/只观察`
- 若实时/延长报价或用户券商报价与本地日线收盘价偏离 `>=5%`,旧的日线 entry、buy zone、止损、止盈、仓位、股数、概率和 R/R 全部作废,必须基于最新报价重算

## 用户报价纠错触发规则

用户说`我看到价格是 X`、`你价格不对`、`现在不是这个价`、`券商显示 X`、`已经跌到/涨到 X`时,必须立即停止沿用原推荐:

1. 承认原报价基准失效,把用户券商价作为最高优先级交易基准。
2. 用用户价重算入场区间、止损/退出位、第一/第二目标、建议仓位上限、计划股数和 R/R。
3. 若缺少成交量、bid/ask、时间戳或 Web 复核,只能输出`修正版状态表`,不得继续保留原`可做`结论。
4. 若用户价与 Web/本地偏离 `>=5%` 且无法解释为不同交易时段,该票降为`报价冲突/只观察`。

## 必出报价校验表（条件触发）

触发关键词与字段定义见 [output-contract.md](output-contract.md) "报价校验表"。本节强调**为什么要出**：

- 日线收盘 vs 当前价偏离 ≥5% 是 entry 报废的硬阈值
- 拿不到当前价 → 必须改写为"非当前可交易推荐，仅日线候选"，不允许"现在买"措辞
- 跨过原买区 → 改 `回踩做` 或 `二次突破做`

## WebSearch 模板（按场景）

### 场景 A：催化剂验证

```
"{ticker} news catalyst {current_month} {current_year}"
"{ticker} earnings OR guidance OR contract OR partnership {current_year}"
```

### 场景 B：资本事件红旗（市值 <$5 亿必查）

```
"{ticker} private placement OR dilution OR warrant OR S-1 {current_year}"
"{ticker} SEC filing {current_month} {current_year}"
"{ticker} reverse split OR Nasdaq compliance"
直接访问 sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker} 查近 30 天 filings
```

### 场景 C：管理层指引（财报 ≤30 天）

```
"{ticker} Q1/Q2 {current_year} guidance OR outlook OR revenue forecast"
```

### 场景 D：财报日期二次确认（财报跳空模式必做）

```
"{ticker} earnings date investor relations"
"{ticker} reports results {current_month} {current_year}"
"{ticker} q1 q2 results date site:globenewswire.com OR site:businesswire.com OR site:prnewswire.com"
```

公司 IR / 官方 PR / SEC 8-K 优先于聚合站 estimated earnings date。

### 场景 E：生科板块（Healthcare 必查）

```
"{ticker} FDA OR clinical trial OR PDUFA {current_year}"
clinicaltrials.gov 查临床读数日期
```

## 当 Web 无法访问

- 必须明确告知用户："本次未查社会面 / 报价，建议自行复核"
- 不允许"K 线漂亮就推荐"
- 概率必须打宽，不允许写得过窄

## 与 red-flags 的分工

- 本文件聚焦**报价 + 事件三方认证模板与查询语句**
- [red-flags.md](../workflow/red-flags.md) 聚焦**红旗目录、复核强度分级、insider/float/short 阈值、降级与排除规则**
