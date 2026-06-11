# MCP 不可用时的优雅降级 (按需加载)

> **触发**: 仅在以下任一情况发生时加载本文件,默认请求**不**加载,以节省 token:
> - `mcp__mydatabase__db_health` 调用本身失败 / 数据库连接被拒
> - 多个 MCP 工具连续返回错误(`symbol_snapshot` / `company_fundamentals` 等)
> - 用户在 prompt 里明确说"我没有 MCP" / "数据库挂了" / "MCP 不可用"
> - 单次请求里某只候选的 MCP 调用失败需要降级路径

MCP 不是必需品,只是更准的源。MCP 不可用时 skill **仍能跑**,只是证据等级和仓位档自动降一档。**绝不允许**把"MCP 失败"等同于"skill 整体废掉"。

每次回答开始前先运行 `mcp__mydatabase__db_health`(若 MCP 注册了)判定当前层级:

## L0 - MCP 全部正常(基线,不读本文件)

`db_health` 返回成功,各 provider 数据新鲜度正常。

- 按 [web-cross-check.md §Web + MCP 分工矩阵](web-cross-check.md) 走完整 6 步流程
- 证据等级 A/B/C/D 按 [output-contract.md §证据等级与概率](output-contract.md) 标准
- 三段加仓状态机正常生效
- 输出顶部不需要特别声明
- **本文件根本不需要加载** — 这是默认基线

## L1 - MCP 部分失败(单工具或单候选)

某个 MCP 工具调用失败(如 `company_fundamentals` 某只票返回空),或 `db_health` 显示某 provider 数据陈旧。

- **第 1 步重试 1 次**: 同样工具同样参数再调一次。许多 MCP 失败是瞬时的
- **第 2 步 Web 替代**: 重试仍失败时,用 Web 替代源拿同字段:
  - 流通盘 / short float / 市值 → StockAnalysis / Yahoo / Finviz
  - ATR14% / 量比 → TradingView / Barchart
  - 历史财报反应 → StockTwits earnings 页 / AlphaQuery / EarningsWhispers
  - 未来财报日历 → MarketBeat / Nasdaq earnings calendar
- **第 3 步标注**: 替代成功时单票详情表 `MCP 调用记录` 行写 `<工具> 失败 → Web 替代 [来源]`,**该票不降级**
- **第 4 步降一档**: Web 替代也失败时,该字段填 `N/A`,**该票证据等级降一档**(B→C, C→D),**但仍允许列入候选池供观察**;不再强制降到 D 整票废掉

## L2 - MCP 完全离线(全局)

`db_health` 直接调用失败 / 数据库连接被拒 / 所有 MCP 工具返回错误 / 用户当前环境根本没注册 MCP。

- **顶部强制声明**(必须出现在报价基准页眉之前):

  ```
  ⚠️ MCP 数据库当前不可用 — 本次回答走 Web 100% 模式
  - 流通盘 / short float / ATR / 量比 / 历史 来自 Web 替代源(StockAnalysis / Finviz / TradingView 等)
  - 证据等级整体降一档(A→B / B→C / C→D)
  - 仓位强制限制到预判仓档(账户风险 0.15%-0.5%)
  - 三段加仓状态机降级:不允许升确认仓 / 主攻仓
  ```

- **Web 替代源补强**(必须用,不允许跳过):
  - 当前 quote: 第 [web-cross-check.md §最低 WebSearch 调用清单](web-cross-check.md) 第 2 类
  - 流通 / short float / 市值: WebFetch StockAnalysis 或 Finviz quote 页
  - ATR / 量比: WebFetch TradingView 或 Barchart 技术面
  - 历史翻倍基因: 不可用,只能依赖 Web 报道里的"180 天高低"或"曾经 +X%"描述,标注证据弱
  - 未来财报: WebSearch `"{ticker} earnings date"`

- **仍可输出主攻/备攻**,但:
  - 表格 `证据等级` 列上限 = B(没有 MCP 量化结构,理论 A 级达不到)
  - `仓位段` 列只允许 `预判`,不允许 `确认/主攻`
  - `建议本金` 按预判仓档(账户风险 1/3 的下限)反推
  - 单票详情表 `MCP 调用记录` 行写 `MCP 全局离线 — 走 Web 100% 模式`

- **绝对不允许**:
  - 因 MCP 离线就拒绝输出任何候选(用户合理诉求,要尽力服务)
  - 假装跑了 MCP 调用(执行透明性硬要求)
  - 把 Web 替代数据写成 MCP 来源(数据溯源硬要求)

## 层级判定优先级

- `db_health` 调用本身失败 → 直接 L2
- `db_health` 成功但所有候选的 `symbol_snapshot` 都失败 → L2
- 部分票成功部分失败 → 该票走 L1,整体 L0
- 同一票多个工具部分失败 → 该票走 L1,失败工具按 Web 替代
- 用户在 prompt 里明确说"我没有 MCP" / "数据库挂了" → 直接 L2,跳过 db_health 检测
