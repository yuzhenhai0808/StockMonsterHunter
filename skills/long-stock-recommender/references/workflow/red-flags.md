# 做多候选红旗清单

本文件是 long-stock-recommender 的统一红旗目录。以下任一情况命中时,必须按规则**降级**或**直接排除**,并在输出中显式说明命中的红旗、是降级还是排除、若仍允许参与时的仓位上限。

红旗优先级遵循 [screening-workflow.md](causal-first.md) "Step 4 强制后置 — 社会面复核" 的处理顺序。报价/事件认证模板见 [web-cross-check.md](../core/web-cross-check.md);仓位/止损硬线见 [risk-rules.md](../core/risk-rules.md)。

## 直接排除(不可恢复类)

任一命中即直接从候选池剔除,不允许"先标注再保留":

- 会计问题 / SEC 调查 / 审计师辞职 / CFO 突然离职
- 近 30 天内完成 Private Placement / 私募配售
- 持续性 ATM offering / SEPA / equity line 正在进行
- Reverse stock split 公告(Nasdaq 合规压力 / 退市风险信号)
- Phase III 临床刚失败 / 主要终点未达
- 价值模式专属:一次性利得撑利润、营收/利润连续 4 季下滑且无业绩拐点

## 降级或标注风险类

命中后允许保留为候选,但必须在输出中显式标"X 红旗,已降级",且仓位减半,不允许作为首推:

### 资本结构红旗

- S-1 / S-3 filing 已递交待生效 → 标"增发风险后置"
- 活跃 warrant 行权期 → 标"warrant 抛压"
- 存在可转债 → 标"转股稀释 + 套利空头"
- Float < 20M 股 → 标"超低浮动,涨跌可能来自 squeeze 而非趋势"
- Short interest > 20% of float 或 Days to cover > 7 → 标"squeeze 型投机,不适合常规趋势交易"

### 财报相关红旗

- 财报日 ≤ 7 天 → 直接排除新仓(不论历史反应)
- 历史 4 次财报次日 ≤1 次上涨 → 直接排除("利好出尽型",见 AEVA / TSEM 案例)
- 财报日 8-14 天且历史 < 3/4 正反应 → 排除
- 财报日 15-30 天且历史 < 2/4 正反应 → 排除
- 管理层下季度指引中值低于上季度实际 → 标"指引负面,财报前必须空仓"
- 历史财报次日盘中先深砸再修复 ≥ 2 次 → 降级,标"路径差,不适合埋伏重仓"

### 估值与基本面冲突

- `trailing_pe` > sector 中位数 3 倍 → 标"故事股定价,赔率来自预期差不是估值安全"
- `price_to_book > 5` 且 `ROE < 20%` → 标"估值不支持下方"
- `free_cashflow_ttm` 翻负 + CapEx 扩张期 → 排除"价值模式"和"长线"推荐,仅短线允许
- `forward_pe >> trailing_pe`(分析师预期 EPS 下滑) → 降级

### Insider 抛售触阈(过去 30 天 Form 4 净额)

| 价格档位 | 净卖出降级阈值 | 净卖出排除阈值 |
|---|---|---|
| $1–$10 | > $500K | > $2M |
| $10–$100 | > $1M | > $5M |
| $100–$500 | > $10M | (高市值不做硬排除,降级处理) |

净买入则可作为正面佐证:小盘 > $500K、中盘 > $1M、大盘 > $5M 触发加分。

### 板块二元事件

- 生科股 30 天内有关键临床读数 / PDUFA → 标"二元事件,非趋势交易"
- 未 FDA 批准产品收入占比 > 80% 且现金 < 12 个月 → 直接排除(破产风险)

## 数据来源

- [openinsider.com](https://openinsider.com):过去 30 天 Form 4 净额(免费按 ticker 聚合)
- yfinance: `Ticker(sym).insider_transactions`
- nasdaq.com:短兴趣页面(每月两次发布)
- finviz.com 公司页:Float / Short Float / Short Ratio 一次可见
- sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}:近 30 天 filings
- clinicaltrials.gov:临床读数日期
- FDA calendar:PDUFA 日期

WebSearch 模板见 [web-cross-check.md](../core/web-cross-check.md) "WebSearch 模板"。

## 复核强度分级(按市值/板块决定查多少)

先调 `company_fundamentals` 拿 `market_cap` 和 `sector`,按下表选择最少 WebSearch 查询数:

| 标的类型 | 复核强度 | 最少 WebSearch |
|---|---|---|
| 市值 < $1 亿 + Healthcare/Biotech | 最高强度(全部检查项 + 定增/权证/FDA) | 3 次 |
| 市值 < $5 亿 任何行业 | 高强度(资本事件 + 新闻催化) | 2 次 |
| 市值 $5 亿 – $100 亿 | 中等强度(新闻催化 + insider) | 1–2 次 |
| 市值 > $100 亿 | 轻量(财报/指引/重大事件) | 1 次 |
| 价值模式任何市值 | 必须查管理层/战略/回购/股息 + 会计问题 | 2 次 |

## 关键原则

**对市值 < $1 亿的小盘股,默认假设 K 线是被资本操作的,举证责任反转 —— 必须找到干净的基本面/催化剂证据才能推荐,而不是"没发现红旗就推荐"。**

无法执行 WebSearch 时,必须明确告知用户"本次未查社会面,建议自行复核",不允许"K 线漂亮就推荐"。

## 输出要求

任一红旗命中,在输出中必须直接说明:

- 命中的红旗是什么
- 是降级还是直接排除
- 仍允许参与时的仓位上限
- 触发的处理动作必须可量化(例:"排除,改推第二名"或"降级,仓位减半 + 标财报前空仓")

红旗触发的总表"结论"列只能写 `观察 / 放弃` 中的一个,不允许写"可做"。
