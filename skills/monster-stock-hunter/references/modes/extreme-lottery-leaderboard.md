# 极端彩票榜首池(WOK/YAAS/HTCO/MWC 型)

> **角色: 事后审计 + 防漏展示,默认主线不加载**。在用户明确点名"WOK 那种 / YAAS / 已经 +80% / 翻倍 100% / 极端彩票 / 最疯狂"时读取;用户问`+40%以上 / 高涨幅 / 大涨 / 高回报 / 翻倍 / 最猛 / 彩票仓`时也必须读取,用于完整展示 40%+ 涨幅榜审计池。全景优先请求中,本池必须前置展示,再分层降级。
> 默认主线由 [../workflow/ignition-forecast.md](../workflow/ignition-forecast.md) 5 原型负责,**先识别还没起爆的票**(涨幅 `0%~+15%`、催化 `<4h`、未上 Top 3),让用户吃到第一口肉;本文件只在"已涨 +80% 后的极端彩票"场景下作为事后审计和防漏展示入口,不作为主攻推荐池。
> HCWB 型盘后低基数点火若涨幅 `<+30%` 且未上 AH Top 3,优先归入 [../workflow/ignition-forecast.md §原型 1 盘后低基数初燃](../workflow/ignition-forecast.md);只有涨幅 `>=+30%~+50%` 才转入本文件审计。

**场景**: 用户明确点名问"最疯狂 / WOK 那种 / 一天 100%-300% / halt runner / SPAC 壳股 / redeem squeeze",或询问"有没有 40%+ / 大涨幅 / 高回报 / 最猛 / 翻倍 / 彩票仓"。若用户说"回血/翻本"且已经亏损,先按风险处置,本模式只能作为风险对照。  
**目标**: 防止最猛榜首票被红旗过滤后静默消失;同时明确这类票不是可重仓推荐。  
**持仓**: 1-30 分钟,只做触发,不隔夜。

## 重要定位

本模式负责**展示和审计**极端彩票,不是把它们包装成正常推荐。

- 红旗票也必须展示,因为它们可能是当天涨幅最大的票
- 40%+ 票也必须展示,因为用户问高涨幅时首先要看到完整榜单,再看能否参与
- 展示不等于推荐
- 任何输出都必须写清楚为什么不能满仓
- 若用户已亏损、满仓、急于翻本,只允许`观察/极小仓触发`,不得给正常主攻

## 强制扫描源

至少尝试以下来源中的 2 类:

- Web: `top gainers today`, `intraday top gainers`, `unusual volume stocks`, `stock movers today`, `halt resume`
- 实时/准实时 movers 页面: StockTitan / ADVFN / Nasdaq / StockAnalysis / Stocknear / 券商榜单,必须覆盖 after-hours / premarket 榜单
- 盘后公告/财报源: `after-hours gainers`, `earnings movers`, `8-K`, `press release`, `guidance`, `contract`, `FDA`, `partnership`
- IPO/new listing 源: `IPO`, `Nasdaq listing`, `ADS began trading`, `initial public offering closing`, `recently listed stocks`,用于 MWC 型上市初期流动性错配防漏
- MCP/本地库: 最近完整交易日涨幅榜、低价高成交、相对量异常

如果只能拿到本地完整日线,必须标注: `不是盘中实时扫描,只能做事后复盘/次日观察`。
如果 Web 显示盘后/盘前榜首与本地日线涨幅榜冲突,以当前或最近延长交易报价作为次日候选排序依据,但必须写清报价时间和来源。

## 入池条件

优先级声明: 本池只是**防漏展示**,不是自动推荐入口。若候选同时命中 [../workflow/causal-first.md §已涨太高过滤](../workflow/causal-first.md),按已涨太高过滤降级,默认 `观察/极小仓触发`,不得包装为主攻/备攻或写正常仓位、正常概率表。

满足任一即可进入`极端彩票榜首池`,不得静默过滤:

- 当日/当前涨幅 `>=40%` 且用户请求包含 `+40%以上 / 高涨幅 / 大涨 / 高回报 / 翻倍 / 最猛 / 彩票仓 / 极端`
- 当日/当前涨幅 `>=80%`
- IPO / new listing squeeze: 上市 `<=10` 个交易日的新股、ADS、IPO closing 后短期交易票,若盘前/盘中涨幅 `>=40%`,或成交额 `>= $5M`,或进入 Web movers Top 10,必须入池;即使 `company_fundamentals` 无数据、本地日线不足、ATR/流通为空,也不能静默删除
- 价格 `$0.1-$10`,成交额 `>= $10M`,相对量 `>=50x`
- 日内振幅 `>=80%`
- LULD/停牌恢复后仍在涨幅榜 Top 10
- 最近有反拆/合股/资本重组,且当天突然爆量 `>=100x`
- SPAC/壳股或停牌恢复票因大额赎回、deal vote、延期 notes、redeem squeeze 或停牌恢复后流通压缩而突然爆量;必须确认流通压缩或停牌恢复结构,否则只能列为无源 pump 审计
- 最近一次收盘后因财报、8-K、合同、指引、FDA、合作公告等跳涨 `>=20%`,或盘后成交额 `>= $5M`,或进入公开 after-hours / premarket movers Top 3；即使最近完整日线收跌、收在低点、或不在日线涨幅榜,也必须进入`盘后点火/次日延续`审计池
- HCWB 型盘后低基数点火: 最近完整常规盘收跌、量比 `<1` 或成交额很低,但收盘后因财报、8-K、PR、临床/监管、合同、控制权/融资公告等跳涨 `>=50%`,或延长交易价相对收盘偏离 `>=40%`。这类票通常不满足日线涨幅 SQL,但必须进入极端彩票审计;若有 going concern、Nasdaq 合规、权证/融资稀释等红旗,默认只能`观察/极小仓触发`。
- 软催化极端放大型: `MOU/LOI` 本身不是硬合同,但若叠加热门主题、前一日爆量、盘前延续、低价小盘、LULD 向上恢复和成交额快速超过 `$100M`,必须进入`极端彩票榜首池`;只能按小仓二突/停牌恢复战术处理,不得按基本面重估处理
- TDIC 型退潮反抽: `MOU/LOI + AI/Web3/crypto 等热门叙事 + 前日 >=500% 暴涨 + 次日从高位崩盘并收近低点` 后,即使盘后/盘前再涨 `+50%~+100%`,也必须进入彩票池审计但默认降级为`观察/极小仓触发`;不得按正常次日延续或主攻处理

## IPO squeeze / new listing squeeze

MWC 型特征:

- 上市 `<=10` 个交易日,本地日线、ATR、fundamentals、float 字段可能不完整
- IPO/ADS listing/IPO closing 新闻可追溯,但盘前涨幅主要来自流动性错配、低可交易流通和榜单资金
- Web movers 显示 `+40%~+300%` 或成交额突然超过 `$5M`,但 quote/bid-ask 可能不完整

正确处理:

- 必须展示在`当前盘前/盘中 40%+ 全景审计池`和`极端彩票榜首池`
- 不得因为 `company_fundamentals: no data`、历史不足 10 日或 ATR 缺失而删除
- 默认分类为`IPO squeeze / new listing squeeze`,操作结论为`已飞等回踩`或`只审计不追`
- 只有出现回踩承接、二次突破放量、或 LULD 向上恢复后站稳,且报价 A/B 与当前流动性硬门通过,才允许`极小仓触发`
- 不得按基本面重估或正常主攻处理;IPO 定价、承销商稳定/锁定结构和早期流动性不透明都必须作为仓位降级理由

## MCP 回放/盘后防漏 SQL

当 MCP 可用且需要复盘最近完整交易日,必须跑类似下面的查询。不要只依赖普通动量候选器,因为 WOK 型票常被市值、均量、红旗或基本面过滤掉。

```sql
WITH d AS (
  SELECT max(price_date) AS day
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
),
prev AS (
  SELECT max(price_date) AS day
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date < (SELECT day FROM d)
),
base AS (
  SELECT symbol, avg(volume)::numeric AS avg_vol20
  FROM market_data.us_equity_daily_prices
  WHERE provider = 'yfinance'
    AND price_date < (SELECT day FROM d)
    AND price_date >= (SELECT day FROM d) - INTERVAL '40 days'
  GROUP BY symbol
)
SELECT
  u.symbol,
  u.price_date,
  u.open,
  u.high,
  u.low,
  u.close,
  u.volume,
  p.close AS prev_close,
  round(((u.close / NULLIF(p.close, 0)) - 1) * 100, 2) AS change_pct,
  round(((u.high / NULLIF(u.low, 0)) - 1) * 100, 2) AS intraday_range_pct,
  round((u.volume / NULLIF(b.avg_vol20, 0))::numeric, 1) AS rel_volume,
  round((u.close * u.volume)::numeric, 0) AS dollar_volume
FROM market_data.us_equity_daily_prices u
JOIN market_data.us_equity_daily_prices p
  ON p.symbol = u.symbol
 AND p.provider = u.provider
 AND p.price_date = (SELECT day FROM prev)
LEFT JOIN base b ON b.symbol = u.symbol
WHERE u.provider = 'yfinance'
  AND u.price_date = (SELECT day FROM d)
  AND u.close BETWEEN 0.1 AND 10
  AND (u.close * u.volume) >= 10000000
  AND (
    ((u.close / NULLIF(p.close, 0)) - 1) >= 0.80
    OR (u.volume / NULLIF(b.avg_vol20, 0)) >= 50
    OR ((u.high / NULLIF(u.low, 0)) - 1) >= 0.80
  )
ORDER BY
  ((u.close / NULLIF(p.close, 0)) - 1) DESC,
  (u.volume / NULLIF(b.avg_vol20, 0)) DESC
LIMIT 50;
```

若用户问的是盘中实时机会,用 Web/券商 movers 替代 `d=max(price_date)`,但字段和入池阈值保持一致。

## 红旗不排除,只降级

以下情况不能静默删除,必须在表里展示并降级:

- reverse split / share consolidation
- authorized share increase / capital restructuring
- offering / ATM / shelf / toxic financing
- 无硬催化,只有 Web3/AI/crypto/biotech 泛叙事
- MOU/LOI/探索性合作被市场按确定订单重估,但没有合同金额、交割条件或收入确认路径
- 20 日均量极低,当日突然爆量
- 点差宽、成交不连续、频繁停牌
- SPAC/壳股未能确认赎回压缩,或停牌恢复后成交断续

## 无源极端动量战术例外 (DSY/VSME 型)

默认规则仍然是: 没有 48 小时内明确 PR/SEC/财报/合同/FDA/并购等硬催化,不得写正常`主攻/备攻/推荐`,不得给正常仓位或基本面概率。但 2026-06-10 的 DSY/VSME 型复盘证明,部分无源票在当前成交额、LULD/二突和榜单资金同时极端确认时,会成为当天最大机会。此时不能只写`只审计不追`后结束,必须额外给**战术触发条件**或明确写`无战术触发,放弃`。

允许从`只审计不追`升级为`无源极端动量战术票`的最低门槛:

- 当前日或当前阶段成交额 `>= $100M`,或开盘后 30 分钟成交额已超过前一日 20 日均成交额 `100x`。
- 当前涨幅 `>=100%`,且日内/盘前高点相对前收 `>=300%`;说明资金已经从候选发现转为真实流动性事件。
- 价格在 09:45 ET 后仍能重新站回 VWAP/开盘价/盘前中枢之一,或 LULD 向上恢复后 3-5 分钟不跌回触发位。
- bid-ask 可接受、成交连续,且不是单笔孤岛。
- 未发现当天直接 offering/ATM/toxic financing、停牌重大负面、退市处置等硬拒绝红旗。

战术限制:

- 结论只能写`极小仓二突/停牌恢复战术`,不能写`主攻/备攻`。
- 账户风险上限 `0.15%-0.35%`;不得用用户想翻倍或榜首涨幅放大本金。
- 只允许`回踩 VWAP 不破 / 二突日内高点 / LULD 向上恢复后站稳`三种触发;禁止开盘第一根垂直线市价追。
- 第一止盈必须快: `+10%-20%` 先兑现一半以上;跌回 VWAP/触发位立即作废。
- 收盘位置若低于日内区间 `20%`、或从高点回撤 `>=50%` 且无新催化,次日默认退潮审计,不得继续包装成多日主升。

输出要求:

- 在`40%+ 全景审计池`的`分类`列可写`无源极端动量战术票`,操作结论写`仅二突/仅 LULD 恢复/只观察`。
- 在`稳定三篮复核表`里,这类票仍属于`只审计不交易`篮子,除非实时结构满足上方触发条件;满足后也只是`极小仓战术`,不是主升候选。
- 如果同一轮里硬催化票和无源极端动量票并存,必须同时展示:硬催化票代表因果确定性,无源极端动量票代表盘口赔率;不得让硬催化票因为"消息更真"自动排在战术机会之前。

## 评分

输出 `彩票可交易分` 和 `爆雷分`:

- 彩票可交易分:
  - 当前涨幅 Top 3: +2
  - 成交额 >= $50M: +2
  - 相对量 >= 500x: +2
  - 仍在 VWAP 上方: +2
  - 二次突破成功: +2
  - SPAC/壳股赎回压缩或停牌恢复结构可确认: +1
- 爆雷分:
  - 反拆/合股历史: +2
  - 资本重组/融资授权: +2
  - 无硬催化: +2
  - 日内振幅 >=100%: +2
  - 前日高点到次日收盘回撤 >=80%,且收近全天低点: +2
  - 点差/停牌/成交不连续: +2
  - SPAC/壳股结构无法确认: +2

若爆雷分 >=6,只能`观察/极小仓`;若用户亏损后求翻本,直接禁止参与。

## 参与方式

只允许:

- 回踩 VWAP 不破
- 二次突破放量
- LULD 向上恢复后站稳前高

禁止:

- 直线拉升市价追
- 满仓
- 补仓摊低
- 跌破 VWAP 后幻想回拉
- 用"今天要回本"倒推仓位

## 仓位

仓位按 [../core/risk-rules.md](../core/risk-rules.md) 降级执行。本模式只额外强调: 点差过宽、停牌频繁或成交不连续时,即使入池也应继续降级或放弃。

## 输出要求

最终候选总表、单票详情表、概率表、报价校验和用户可见输出顺序统一按 [../core/output-contract.md](../core/output-contract.md) 执行。本模式只额外要求:

- `极端彩票榜首池`必须展示,即使最终不推荐;普通推荐请求中放在 [../core/output-contract.md](../core/output-contract.md) 的`场景审计附录`,不得替代或前置于`最终候选总表`
- 全景优先请求(`+40% / 翻倍 / 最猛 / 高回报 / 彩票仓 / 极端`)中,`极端彩票榜首池`/`40%+ 全景审计池`必须前置到报价基准之后;这是该场景的首屏主表,不是后置附录
- IPO/new listing squeeze 票必须在审计池中展示;缺 fundamentals 或历史不足只能写成 C/D 证据和仓位降级,不能省略
- 审计池中必须包含`彩票分/爆雷分`或在`红旗/可交易性`中写清等价判断
- 全局`单票详情表`必须回答: 为什么能涨、为什么不能满仓、只有什么结构才允许做、失效点、若用户已亏损是否禁止参与
- 概率和报价等级只按全局输出合约处理,本模式不得另设格式

## WOK 参考规则

WOK 型特征:

- 20 日均量极低
- 当日量比数百到数千倍
- 日涨幅 `100%-300%`
- 可能伴随反拆/资本结构重组/合规风险
- 催化往往是 AI/Web3/crypto/生物数据等泛叙事

正确处理:

- 必须展示在极端彩票榜首池
- 不得因为红旗静默过滤
- 不得列为正常主攻
- 不得给满仓或半仓建议
- 只能写`极小仓触发`或`观察`

## TDIC 参考规则

TDIC 型特征:

- MOU/LOI 或探索性 AI 合作引发前一日数倍到十倍级暴涨
- 次日开盘仍在高位,但盘中无法守住 VWAP/开盘价/前日承接区
- 日内从高点回撤 `>=80%`,收盘接近全天低点
- 盘后或次日盘前可能再出现 `+50%~+100%` 反抽,但缺少新增硬催化

正确处理:

- 必须展示在极端彩票榜首池,因为它可能仍是公开榜单焦点
- 默认标为`退潮反抽/只观察`,不是`现任榜首延续`
- 只有重新站回 VWAP、开盘价或前日尾盘承接区,且成交额回流、无新增红旗,才允许`极小仓二突`
- 不得因为盘后反抽幅度大就给正常仓位;若用户目标是翻本/回血,直接禁止作为主攻

## HCWB 参考规则

HCWB 型特征:

- 常规盘没有强势确认,甚至收跌、收在低位或量比 `<1`
- 盘后/夜盘因财报、8-K、PR、临床/监管或合同公告突然跳涨 `+50%` 以上
- 延长交易价与常规盘收盘价偏离巨大,但成交连续性、点差和报价源可能不足
- 低价生物科技或小盘股常伴随 going concern、Nasdaq 最低买价、权证/融资稀释等红旗

正确处理:

- 必须展示在`盘后点火/次日延续池`或`极端彩票榜首池`,不能因常规盘日线不强而静默过滤
- 不得自动升为主攻;先核延长交易报价、成交量/bid-ask、48 小时公告和 7 天红旗
- 只有站稳盘后承接区、点差可控且开盘后 reclaim VWAP/开盘价或完成二突,才允许`极小仓触发`
- 如果报价源冲突、成交不连续或红旗过重,写`观察/报价冲突/红旗过重`,不写建议本金和正常概率
