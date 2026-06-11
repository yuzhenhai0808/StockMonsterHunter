# 首推前三连校验 (Pre-Recommend Triplet)

本文件是**任何模式首推前的强制硬门**。无论是 monster 日内还是 swing 持仓,只要要输出"主攻 / 备攻 / 推荐", 都必须先跑本文件对应的校验项。

本文件只负责**三连校验定义 + monster/swing 适用矩阵 + 缺失输出降级规则**。不负责:

- 仓位/账户风险 → [risk-rules.md](risk-rules.md)
- 红旗清单 → [../workflow/red-flags.md](../workflow/red-flags.md)
- 催化硬度 → [../workflow/catalyst-stage.md](../workflow/catalyst-stage.md)
- 5 原型识别 → [../workflow/ignition-forecast.md](../workflow/ignition-forecast.md)

## 目的

TSEM 教训(2026 年用户复盘):无论模式多严,首推一票前必须先回答 3 个问题——**历史是否上涨过、估值是否合理、指引是否一致**——否则容易掉进"看技术面像点火但其实利好出尽 / 估值已经透支 / 指引早就下修"的陷阱。

本 skill 是日内,采用"轻量版":只强制最关键两项,其它选填。swing 模式(由 `long-stock-recommender` 调用)走全套。

## 三连校验

### 校验 1: 历史反应

**适用**: monster 原型 2 / 原型 4 强制;其它原型选填;swing 全部强制。

**口径**:

- 查过去 4 次类似事件(财报、FDA、合同公告、控制权交易)的次日反应
- 数据来源: `company_fundamentals` MCP + 财报历史 + Web 新闻档案
- 统计字段:
  - 4 次中次日上涨次数
  - 最大正反应(绝对涨幅)
  - 最大负反应(绝对跌幅)
  - 最近一次反应方向
  - EPS beat 仍下跌次数

**一票否决条件(命中任一)**:

- 4 次中 `<=1` 次次日上涨,且最大正反应 `<+8%`
- 4 次中 EPS beat 仍次日下跌 `>=2` 次 → 标注**利好出尽型**,默认放弃
- 4 次中最大负反应 `<=-15%` 且最近一次为负反应

**输出要求**: 即使没有触发一票否决,也必须在全局`单票详情表`写出 4 次完整记录,例如:

```text
历史反应 (4 次): 1 次 +35% / 1 次 -8% / 2 次 -3%~-5%; 最近一次 -5%; 利好出尽风险低
```

### 校验 2: 估值/基本面快查

**适用**: monster 原型 4 微盘强制;其它原型选填;swing 全部强制。

**口径**:

- monster 日内: 只看 1-2 个关键比率
  - P/E 是否极端透支(TTM P/E > 同业中位数 3 倍)
  - 流动现金/季度烧钱 < 2 个季度 → 标注"现金压力"
- swing: 全套(P/E / P/B / ROE / 现金流 / 负债)按 long-stock-recommender 走

**降级条件**:

- TTM P/E > 同业中位数 3 倍 + 当前价已透支当年指引 → 降一档(主攻 → 备攻)
- 现金 < 2 个季度烧钱 + biotech 未盈利 → monster 原型 4 触发更严的资本事件检查

**输出要求**(monster 选填,触发后必出):

```text
估值/基本面: TTM P/E 45x (同业中位 18x), 现金 $8M / 季度烧钱 $5M = 1.6 季度, 现金压力
```

### 校验 3: 指引/前瞻一致性

**适用**: swing 全部强制;monster 选填(只在原型 2 财报埋伏选触发)。

**口径**:

- 公司最近一次指引方向(上调 / 维持 / 下调 / 撤回)
- 分析师一致预期 vs 实际指引 gap
- 前瞻评论是否提到逆风(汇率、关税、客户集中)

**降级条件**:

- 最近一次指引下调或撤回,但市场仍按 beat 预期定价 → 不得作为主攻
- 公司指引 < 分析师一致预期 `>=15%` → 标注"预期差风险"

## 微盘 biotech 资本事件扩展硬门

**适用**: monster 原型 4(微盘公告窗口)强制;市值 `<$2 亿` 任何 biotech/生科类强制。

源自 MEMORY.md `microcap-biotech-must-check-capital-events`: 市值 `<$1 亿` 生科股推荐前必须先查定增/权证/S-1 再看技术面。本文件把这条规则扩展到所有 monster 原型 4 候选。

### 7 天硬门(命中任一一票否决)

最近 7 个日历日内出现以下任一项,**不得进入预判仓**,只能观察或放弃:

- offering / public offering / direct offering / registered direct
- ATM (At-The-Market offering) program 启动或扩容
- shelf registration S-3 EFFECT 或新增 shelf
- reverse split / share consolidation 完成或刚生效
- toxic financing / convertible note 含恶意条款
- equity line of credit (ELOC) 签约或抽贷
- S-1/F-1 registration EFFECT(允许公开二次发行)

### 30 天组合硬门(2 项命中即一票否决)

最近 30 天内,以下 5 项中任意 2 项命中:

- 反拆完成
- F-1/S-1 EFFECT
- ELOC 签约
- ATM program 启动
- 大股东减持(Form 144 文件)

→ 标注"稀释组合风险",不得给主攻;只能极小观察仓或放弃。

### 数据源

- SEC EDGAR 实时(优先,8-K/S-1/F-1/424B5/4 文件)
- 公司 IR/PR Wire(次优,GlobeNewswire/BusinessWire/PRNewswire)
- 主流财经媒体(防漏,Nasdaq/MarketWatch)

### 输出要求(原型 4 候选必出)

```text
资本事件检查 (7 天/30 天):
- 7 天内 offering/ATM/反拆/S-1 EFFECT: 无 / 命中 [类型] [日期]
- 30 天内组合 (反拆+EFFECT+ELOC+ATM+减持): X/5 项命中
- 结论: 通过 / 一票否决 / 稀释组合风险降级
```

## monster vs swing 适用范围矩阵

| 校验项 | monster 日内 | swing 持仓 |
|---|---|---|
| 校验 1 历史反应 | 原型 2/4 强制;其它选填 | 全部强制 |
| 校验 2 估值/基本面 | 原型 4 强制;其它选填 | 全部强制 |
| 校验 3 指引一致性 | 原型 2 选填;其它选填 | 全部强制 |
| 微盘 biotech 资本事件 | 原型 4 强制 + 市值 `<$2 亿` biotech 强制 | 全部强制 |

monster 默认只强制校验 1 + 微盘资本事件;其他选填但**触发后必出**。swing 三连全跑。

## 缺失输出降级规则

**任一强制校验未跑或数据不全时**:

- 不得给该候选`主攻 / 备攻 / 建议本金 / 最大亏损 / 正常概率表`
- 只能标`观察 / 数据不完整`,在表格`结论`列写明缺失了哪项校验
- 不得绕过本文件直接输出

**示例不合规输出**(禁止):

```text
| TICKER | 主攻 | $5.20 | $4.95 | $5.85 | ... |
```

**示例合规输出**(允许):

```text
| TICKER | 观察/历史反应未跑 | - | - | - | - | 待 pre-recommend-triplet §校验1 完成 |
```

## 引用关系

- 本文件由 [SKILL.md](../../SKILL.md) §渐进式加载 在 S3 财报埋伏 / S4 高位事件票场景或原型 2/4 候选出现时加载
- [../workflow/ignition-forecast.md §5 原型](../workflow/ignition-forecast.md) 在原型 2/4 强制引用本文件
- [../core/output-contract.md §最终自检](output-contract.md) 加一条:"原型 2/4 候选是否跑完 pre-recommend-triplet 强制项"
