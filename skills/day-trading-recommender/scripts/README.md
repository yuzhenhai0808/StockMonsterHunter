# Day-Trading SQL 脚本

本目录下的 SQL 通过 `query_market_data` MCP 工具执行。

## 脚本清单

| 脚本 | 用途 | 何时使用 |
|---|---|---|
| [gap_candidates.sql](gap_candidates.sql) | 找昨日收盘相对近期高点有缺口 + 量能异常的候选 | premarket-gap 模式的本地数据库补全 |
| [micro_momentum.sql](micro_momentum.sql) | 微盘 3 日涨幅 > 50% 且流动性未崩的候选 | micro-pump 模式步骤 1 |
| [squeeze_candidates.sql](squeeze_candidates.sql) | 高空头 + 低流通 + 近期启动的轧空候选 | short-squeeze 模式步骤 1 |

## 与 long-stock-recommender 脚本的复用

本 skill 的 earnings-gap-day1 模式直接复用:
- `../../long-stock-recommender/scripts/earnings_reaction_check.sql` — 历史财报反应检查

不拷贝,通过相对路径引用。

## 使用约定

- 脚本中的 `{tickers}` 占位符需替换为 `('T1','T2',...)` 后传入 `query_market_data`
- 所有脚本默认使用 `market_data.us_equity_daily_prices` 的 `provider='yfinance'`
- 日期字段统一为 `price_date`;不要使用旧表名 `us_equity_daily` 或旧日期字段 `date`
- 日内使用时注意本地数据库是日线,最新数据只到**昨日收盘**,盘前/盘中实时数据需 WebSearch 补
