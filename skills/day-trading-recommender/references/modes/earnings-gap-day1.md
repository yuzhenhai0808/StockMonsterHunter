# 模式 3:财报跳空首日

**场景**: 昨日盘后或今晨盘前刚公布财报、出现 >5% gap 的股票,**当天**跟风 | **持仓**: 开盘 30 分钟 - 收盘 | **目标**: +6~15% | **胜率**: ~55%(比微盘高,有基本面支撑)

**与 long-stock-recommender 的 earnings-gap 模式的区别**: 那边是"提前 3-14 天布局财报前",这边是"财报已出,当天跟风首日动量"。完全不同的时机。

## 触发条件

用户关键词:"财报跳空" / "财报后首日" / "earnings gap" / "刚出财报" / "昨晚财报好" / "TXN 那种刚出的"

## 流程

### Step 1 - 找"刚报过"的股

调 `earnings_dates(within_days=-2)` (past 2 days) 或直接用 SQL:

```sql
SELECT symbol, earnings_date, eps_estimate, eps_reported, eps_surprise_pct, timing
FROM market_data.us_equity_earnings
WHERE earnings_date >= CURRENT_DATE - INTERVAL '2 days'
  AND earnings_date <= CURRENT_DATE
  AND eps_reported IS NOT NULL
ORDER BY eps_surprise_pct DESC NULLS LAST
LIMIT 30;
```

(若 SQL 不可用,退回 WebSearch `"stocks reported earnings yesterday beat"`)

### Step 2 - 对每只查盘前/昨收 gap

调 `latest_prices(symbols=...)` 和 `symbol_snapshot(symbols=..., lookback_days=30)` 拿最新收盘。
WebSearch 补盘前价:`"{ticker} premarket price today"`

**盘前 gap % = (盘前价 / 昨收 - 1) × 100**

保留:
- **gap ≥ +5%**(正向跳空)
- 排除 **gap < 0**(坏消息型)
- 排除 **gap > 25%**(已经过度反应,赔率恶化)
- 若是 `EPS/guidance` 都不错,但盘前/开盘价格反应明显偏弱 → 直接标注"市场不买账",不要因为文字面利好就强行保留

### Step 3 - 管理层指引复核

WebSearch:
```
"{ticker} Q{next quarter} guidance beat raised"
"{ticker} earnings call transcript {today}"
```

- 指引上调 + EPS 超预期 → 🟢 双重正向,保留
- EPS 超预期但指引不变或下调 → 🟡 观察,降级
- EPS 超预期但财报电话会提示"需求放缓" → 🔴 "利好出尽"型,**排除**

**补充提醒**:
- 不要只盯半导体/AI/生科。像 `KFRC` 这种 staffing / business services 名字,只要满足"EPS beat + 指引高于预期 + 盘后强反应",同样属于本模式的标准候选
- 对非热门板块票,更要重视"指引是否超过 sell-side 一致预期",因为这类票的上涨往往来自预期差修复,不是题材热度

### Step 4 - 历史财报反应复核(复用 long-stock-recommender 脚本)

Read [../../../long-stock-recommender/scripts/earnings_reaction_check.sql](../../../long-stock-recommender/scripts/earnings_reaction_check.sql),通过 `query_market_data` 执行。

**关键判断(本模式特有)**:
- 过去 4 次次日反应 ≥3 次上涨 → 🟢 首日跟风胜率高
- 过去 4 次 ≤1 次上涨 → **排除**(TSEM 型,超预期仍跌)
- 过去 4 次 2 次上涨 → 🟡 降级,R/R 要求提到 2.0

### Step 5 - 流动性 + 板块联动

- 调 `symbol_snapshot`,确认 20 日均成交额 ≥ $5M(已经是财报后,流动性通常够)
- WebSearch `"{sector} stocks today after {ticker} earnings"` 看同板块是否联动 → 有联动 → 催化面更扎实

### Step 6 - R/R 计算

财报跳空首日的 ATR 通常 4-8%,适合标准 -4% 止损:

- 入场上限价 = 开盘价附近或盘前最高点下方 1%
- 硬止损 = 跳空缺口下沿(如果 gap 是 +8%,缺口下沿约在昨收 +2%)
- 第一止盈 = +8%(吃跳空延伸)
- 第二止盈 = +15%(若板块联动强势可继续持有到收盘)

### Step 6A - 盘中路径确认(新增)

财报后首日不只看最终收盘,还要看**盘中怎么走**:

- 开盘后 30-60 分钟若**持续站不回盘前高点附近**,说明资金接力一般
- 若利好很足,但价格先砸穿昨收/盘前低点,再慢慢拉回 → 标注"路径差,不适合追"
- 若尾盘才勉强修复到平盘附近,不算标准强势首日延续

处理规则:

- `文字面超预期 + 路径强` → 保留
- `文字面超预期 + 路径差` → 降级或放弃

因为本模式赚的是**财报后首日动量延续**,不是赌全天来回震荡后的尾盘修复。

### Step 7 - 输出

按 [../output-contract.md](../core/output-contract.md) 输出模板,额外标注本模式特有字段。

## 输出必须包含

(在 [../output-contract.md](../core/output-contract.md) 输出模板基础上,补充本模式特有字段)

**本模式特有字段**:
- 财报日期 + EPS 超预期 %
- 指引方向(上调/维持/下调)
- 历史 4 次次日反应(如 "3/4 上涨")
- 盘中路径判断(强承接 / 先砸后拉 / 利好出尽)
- 板块联动情况
- 催化 URL(earnings press release / transcript)

## 需要降级或排除

- 财报盘中公布(极少,但有) → 排除,等第二天再看
- 生物科技 FDA 数据 + 财报同日 → 二元风险,排除
- 中概股财报 + 地缘政治摩擦日 → 排除
- EPS 超预期但营收 miss → 降级,R/R 要求 ≥ 2.0
- "利好出尽"型历史反应 → 即使这次也超预期,仍**排除**(TSEM 教训)

## TXN 4/23 样板(事后验证)

用户 TXN 持仓是 4/23 后建的,我们看下当时的模式适用度:

| 检查项 | TXN 4/23 实况 | 本模式判断 |
|---|---|---|
| EPS 超预期 % | +22.76% | 🟢 |
| 盘前 gap | +19.43% 首日 | 🟢 在 +5%~+25% 舒适区 |
| 指引 | Q2 $1.77-$2.05,大超预期 | 🟢 双重正向 |
| 历史反应 | Q1 2026 次日 +19% | 首次跳空且可信 |
| 板块联动 | BofA $320、TD Cowen $300 上调 | 🟢 |
| 流动性 | 7800万股日均 | 🟢 |

**本模式会给出的输出**:首日跟风,入场 $279 以下,硬止损 $267(-4%),第一止盈 $300(+8%),第二止盈 $320(+15%),14:45 前清仓。R/R = 2.0 ✅。

用户实际做法是**第 4 天在 $274 建仓并准备拿 1-2 周** → 不是本模式,是 swing 追涨,所以当前 -1.8% 很正常。**两个模式错配**。

## 输出要求

最终候选总表、单票展开、报价校验、概率与最终自检统一按 [../output-contract.md](../core/output-contract.md) 执行;本模式只补充上文"本模式特有字段"和"利好出尽型直接排除"纪律。
