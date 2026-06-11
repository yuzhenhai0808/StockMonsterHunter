# scripts/

可执行 SQL 模板。Claude 通过 Read 读取后用 query_market_data MCP 工具执行。

不要加载到 context 中,按需读取。

| 文件 | 何时使用 | 参数 |
|---|---|---|
| [earnings_reaction_check.sql](earnings_reaction_check.sql) | 模式 5 步骤 0 一票否决 | 替换 `{tickers}` 为 `('T1','T2',...)` |
| [mode5_candidates.sql](mode5_candidates.sql) | 模式 5 步骤 1+2 候选初筛 | 无参数,直接跑 |

## 使用流程(示例:模式 5)

1. Read mode5_candidates.sql → 用 query_market_data 执行 → 得到候选列表
2. Read earnings_reaction_check.sql → 把 `{tickers}` 替换为候选 → 执行 → 得到历史反应
3. 按一票否决规则过滤,剩余的才进入社会面复核
