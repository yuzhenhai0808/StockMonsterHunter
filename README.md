# StockMonsterHunter

> 本地化美股行情库 + MCP 服务 + Claude Code / Codex 交易 skill 三件套
>
> *A local US-equity market-data warehouse, an MCP server, and three opinionated trading skills for Claude Code / Codex.*

把 AI 编程助手（Claude Code、Codex 等）变成一个**有本地数据、有可复现筛选逻辑、有明确交易纪律**的美股做多研究工作台。

- **本地数据** —— yfinance / FMP 日线、财报日历、基本面，全部落到本地 PostgreSQL，离线可查、可回测，不被某个云端 API 卡脖子。
- **MCP 服务** —— 用一个常驻 FastMCP 进程，把行情库封装成 15 个只读工具暴露给任意 MCP 客户端。
- **交易 skill** —— 把「筛候选 → 三连校验 → 红旗降级 → 报价认证 → 入场后纪律」这套流程固化成三个面向不同持仓周期的 skill，让助手不再凭感觉荐股。

> ⚠️ **免责声明**：本项目是**交易研究与学习工具**，所有输出都是基于公开行情数据的量化筛选与流程辅助，**不构成任何投资建议**。美股有风险，做多同样可能巨亏，盈亏自负。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [整体架构](#整体架构)
- [三个交易 skill](#三个交易-skill)
- [Quick Start](#quick-start)
- [MCP 工具清单](#mcp-工具清单15-个)
- [在 Claude Code / Codex 里用 skill](#在-claude-code--codex-里用-skill)
- [Docker 部署](#docker-部署)
- [目录结构](#目录结构)
- [与上游 TradingAgents 的关系](#与上游-tradingagents-的关系)
- [License](#license)

---

## 它解决什么问题

让 AI 助手荐股，最大的两个坑是：**数据不可控**（每次现查、慢、易断、无法回测）和**逻辑不可控**（一会儿追涨一会儿抄底，没有统一纪律，事后无法复盘）。

StockMonsterHunter 用三层把这两件事钉死：

| 层 | 作用 | 产物 |
| --- | --- | --- |
| **数据层** | 把全市场美股日线 + 财报 + 基本面同步进本地 PostgreSQL | `market_data.*` 表，可离线 SQL / 回测 |
| **工具层** | 一个常驻 MCP 服务把数据库封装成结构化工具 | 15 个只读 MCP 工具 |
| **策略层** | 三个 skill 把不同持仓周期的筛选与风控流程固化 | 每个候选都带入场区间 / 止损 / 止盈 / 失效信号 |

每个候选都不是「裸推一个代码」，而是**带完整交易计划**（入场上限价、硬止损、分批止盈、失效信号、最大持仓时间）。

---

## 整体架构

```
            ┌─────────────────────────────────────────────┐
            │  数据源: yfinance / FMP (日线 / 财报 / 基本面)  │
            └───────────────────────┬─────────────────────┘
                                    │  cli/ 同步脚本
                                    ▼
            ┌─────────────────────────────────────────────┐
            │   PostgreSQL  ·  schema: market_data         │
            │   us_equity_daily_prices / earnings /        │
            │   company_profile / quarterly_financials ... │
            └───────────────────────┬─────────────────────┘
                                    │  只读 SQL
                                    ▼
            ┌─────────────────────────────────────────────┐
            │   FastMCP 服务  postgres_market_data_server  │
            │   常驻进程, 暴露 15 个只读工具                  │
            │   stdio / streamable-http / sse              │
            └───────────────────────┬─────────────────────┘
                                    │  MCP 协议
            ┌───────────────────────┼─────────────────────┐
            ▼                       ▼                     ▼
   ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │ monster-stock-  │   │ day-trading-     │   │ long-stock-      │
   │ hunter (妖股)    │   │ recommender(日内) │   │ recommender(swing)│
   └─────────────────┘   └──────────────────┘   └──────────────────┘
        Claude Code / Codex 等 MCP 客户端按持仓周期选择 skill
```

---

## 三个交易 skill

三个 skill 按**持仓周期与进攻性**分工，覆盖从「盘中抓妖股第一口肉」到「跨财报持有数月」的全谱系。它们共享同一套底层数据和 MCP 工具，但决策路径、风控强度和目标收益完全不同。

| Skill | 持仓周期 | 目标 | 典型场景 |
| --- | --- | --- | --- |
| 🐉 **monster-stock-hunter** | 盘中做 T（不过夜） | 当日 `+15%~+100%` 的妖股、龙头、翻倍票，**吃第一口肉** | 点火预判（涨幅 0~15%、催化 <4h、未上 Top3）、AI 微盘题材、低流通逼空、微盘 biotech 暴拉、榜首事后审计 |
| ⚡ **day-trading-recommender** | 日内（<6 小时不过夜） | 高赔率但相对干净的日内做多，单笔 `+8%~+50%` | 财报后延续、盘前 gap、强势突破、轧空、龙头追击、微盘爆拉 |
| 📈 **long-stock-recommender** | swing（1 周 ~ 6 月） | 趋势 / 价值 / 财报跳空的稳健做多 | 追涨、超跌反弹、回调买入、价值挖坑、财报跳空埋伏、叠加信号 |

**共同的纪律框架**（每个 skill 都强制走一遍）：

```
候选生成 → 首推前三连校验(财报反应/估值/指引) → 红旗降级
        → 真假分类 → 报价认证 → 入场后阶梯止盈/止损/失效处理
```

每个 skill 目录下还带了：

- `SKILL.md` —— skill 主文档，固化决策路径与各模式阈值
- `references/` —— 量化阈值表、打分公式、真实案例复盘（case-studies）
- `scripts/` —— 可直接跑的 SQL / shell（如妖股周回测 `monster_weekly_backtest.sql`、点火 replay `ignition_candidate_replay.sql`、滚动 walk-forward 验证 `rolling_ignition_walkforward.sql`）

---

## Quick Start

### 1. 准备 PostgreSQL

```bash
docker run -d --name postgres18 --restart unless-stopped \
  -p 5432:5432 \
  -e POSTGRES_USER=admin -e POSTGRES_PASSWORD='your-password' \
  -e POSTGRES_DB=appdb \
  -v ~/.postgres18/data:/var/lib/postgresql/data \
  postgres:18.3
```

### 2. 装依赖

项目用 [uv](https://github.com/astral-sh/uv) 管理依赖：

```bash
uv sync
# 如果需要 OpenBB provider
uv sync --extra openbb
```

### 3. 配置本地环境

复制并按需修改 `.env`（仓库里提供的是本地 dev 默认值）：

```ini
PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=appdb
PGUSER=admin
PGPASSWORD=your-password
```

### 4. 同步行情

**首次全量**（刷新 universe 并同步全市场日线）：

```bash
.venv/bin/python cli/sync_us_universe_to_postgres.py \
  --refresh-universe --use-db-universe --provider yfinance
```

**日常增量**（已有 universe）：

```bash
.venv/bin/python cli/sync_us_universe_to_postgres.py \
  --use-db-universe --provider yfinance
```

**财报日历**（每日一次，默认只刷新本周财报相关 symbols）：

```bash
.venv/bin/python cli/sync_earnings_to_postgres.py --use-db-universe --calendar-week
```

**基本面**（用于价值筛选，建议按周滚动刷新；每只 symbol 约 4 次 yfinance 请求，用请求预算控制速率）：

```bash
.venv/bin/python cli/sync_fundamentals_to_postgres.py \
  --use-db-universe \
  --concurrency 4 \
  --sleep-seconds 0.1 \
  --requests-per-minute 240 \
  --calls-per-symbol 4 \
  --skip-recent-hours 168
```

> 💡 `scripts/sync_today.sh` 把上面几条按 job group **串行**编排（避免 prices / earnings / fundamentals 同时打 Yahoo 被限流）。需要后台并跑时显式设 `SERIAL_JOBS=0`；用 Docker 分片跑全市场基本面可设 `FUND_SHARDS=3 FUND_CONCURRENCY=4 FUND_REQUESTS_PER_MINUTE=120 ./scripts/sync_today.sh fundamentals`。

### 5. 本地直接跑筛选（不经 MCP）

```bash
.venv/bin/python cli/recommend_long_candidates.py --limit 2
```

默认返回 `1-10` / `10-100` / `100-500` 三个价格档位，每档最多两个候选，附带入场区间、止损、止盈。

### 6. 启动 MCP 服务

**单客户端 stdio 模式**（默认，适合只给一个客户端独占）：

```bash
.venv/bin/python mcp_servers/postgres_market_data_server.py
```

**共享 streamable HTTP 模式**（推荐，多客户端共用一个常驻进程）：

```bash
.venv/bin/python mcp_servers/postgres_market_data_server.py \
  --transport streamable-http --host 127.0.0.1 --port 18080
```

客户端统一连 `http://127.0.0.1:18080/mcp` 即可。若某些客户端只支持 SSE，可改用 `--transport sse`，地址为 `http://127.0.0.1:18080/sse`。

> ⚠️ 同一时间**只起一个** MCP 服务监听 `18080`，多个客户端共享同一进程与同一行情库，不要重复起服务。

### 7. 在 AI 助手里调用 skill

见下方 [在 Claude Code / Codex 里用 skill](#在-claude-code--codex-里用-skill)。

---

## MCP 工具清单（15 个）

FastMCP 服务名为 `tradingagents-market-data`。客户端工具名前缀因平台而异：Codex 多为裸名（`db_health`），Claude Code 多为 `mcp__<server-name>__<tool>`（如 `mcp__tradingagents-market-data__db_health`，若注册名为 `mydatabase` 则是 `mcp__mydatabase__db_health`）。

**基础 / 诊断**

| 工具 | 作用 |
| --- | --- |
| `db_health` | 数据库连接 + 三类数据（OHLCV / earnings / fundamentals）覆盖与新鲜度 |
| `list_market_tables` / `describe_table` | schema 探测 |
| `query_market_data` | 只读 SQL 直查 |

**量价研判**

| 工具 | 作用 |
| --- | --- |
| `latest_prices` | 最新 OHLCV + 1d/5d 涨跌 + 20 日量比 |
| `symbol_snapshot` | 单票完整快照（MA10/20/50、ATR14、RSI14、相对 SPY 超额收益） |
| `earnings_dates` | 未来 N 天财报日，带 exclude / downgrade / ok 推荐 |
| `company_fundamentals` | 单票基本面深度档（近 4-5 季趋势 + 行业中位数对比） |

**做多筛选模式**

| 工具 | 对应策略 |
| --- | --- |
| `long_momentum_candidates` | 追涨（动量突破，三价格档 + 完整 trade plan） |
| `oversold_bounce_candidates` | 超跌反弹（RSI<30、20 日低点附近、MA200 之上） |
| `pullback_candidates` | 回调买入（60 日强势 + 5 日回调 + 触 MA20 不破） |
| `value_candidates` | 价值挖坑（盈利 + 低估值 + 价格在 52 周低位附近，市值分档） |

**妖股 / 回测验证**

| 工具 | 作用 |
| --- | --- |
| `monster_weekly_backtest` | 近 N 日妖股复盘 + 点火前特征（前日弱势、跳空、量比、领先涨幅、float/市值） |
| `ignition_candidate_replay` | 用 `target_date` 之前的数据重放点火候选，避免未来函数泄漏 |
| `rolling_ignition_walkforward` | 滚动 walk-forward 验证两层点火规则（主攻层 / 观察层） |

> 所有工具均为**只读**，不会写库。`db_health` 会标记覆盖过稀的 provider 为 `usable_for_screening=false`，避免误对空表筛选。

---

## 在 Claude Code / Codex 里用 skill

### Claude Code

注册 MCP（推荐 streamable HTTP，多客户端共享）后，直接说自然语言或用 slash 命令触发 skill：

```
/monster-stock-hunter      # 找当日可能点火的妖股 / 翻倍票
/day-trading-recommender   # 找当天可操作的高赔率日内做多
/long-stock-recommender    # 找 swing 周期的趋势 / 价值 / 财报做多
```

也可以直接说需求，助手会按持仓周期自动选 skill，例如：

- 「帮我找几只**今天能打板**的微盘妖股」→ `monster-stock-hunter`
- 「**盘前**有没有 gap 后能追的，赚 10% 就走」→ `day-trading-recommender`
- 「推荐几只**基本面好的长线**美股，要止盈止损」→ `long-stock-recommender`

### Codex

在 `~/.codex/config.toml` 里把 MCP 指向同一个常驻服务：

```toml
[mcp_servers.mydatabase]
enabled = true
url = "http://127.0.0.1:18080/mcp"
```

更多客户端配置示例见 [`mcp-config.example.md`](./mcp-config.example.md)。

---

## Docker 部署

`docker-compose.yml` 提供两个服务，都通过外部 `tradingnet` 网络连到 `postgres18`：

- `market-data-mcp` —— 常驻 FastMCP 服务（默认 streamable HTTP，暴露到宿主机 `http://127.0.0.1:18080/mcp`）
- `market-data-sync` —— 循环每日同步一次 yfinance 日线 + 本周财报 + 基本面

```bash
docker network create tradingnet     # 若尚未存在
cp .env.tools .env.tools.local        # 按需编辑 PG 账号密码 / 可选 FMP_API_KEY
docker compose up -d market-data-mcp market-data-sync
```

镜像本身**不包含任何 LLM 依赖**（LangChain / LangGraph / OpenAI SDK 等都已移除），只做数据同步与行情工具暴露。

---

## 目录结构

```
cli/
  fetch_fmp_openbb_to_postgres.py     # 单 symbol 增量同步 + 低层 DB 工具(open_db, ensure_schema, ...)
  sync_us_universe_to_postgres.py     # 美股 universe 批量刷新与批量日线同步
  sync_earnings_to_postgres.py        # 财报日历同步(历史 prints + 未来 calendar)
  sync_fundamentals_to_postgres.py    # profile + quarterly_financials 并发同步
  recommend_long_candidates.py        # 按价格档位的动量筛选(追涨模式 CLI 入口)
mcp_servers/
  postgres_market_data_server.py      # FastMCP 服务(15 个只读工具)
skills/
  monster-stock-hunter/               # 妖股猎手(盘中做 T, 点火预判)
  day-trading-recommender/            # 日内做多(<6 小时不过夜)
  long-stock-recommender/             # swing 做多(1 周 - 6 月)
    SKILL.md                          #   决策路径 + 各模式阈值
    references/                       #   量化阈值 / 打分公式 / 案例复盘
    scripts/                          #   可直接跑的回测 / 筛选 SQL
scripts/
  sync_today.sh                       # 每日同步编排(默认串行, 防限流)
docker-compose.yml                    # market-data-mcp + market-data-sync
mcp-config.example.md                 # Claude Code / Codex MCP 配置示例
```

---

## 与上游 TradingAgents 的关系

本仓库 fork 自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)，但**已移除原多 agent LLM 分析框架**（LangChain / LangGraph / OpenAI SDK 等），只保留并大幅扩展了「让 AI 助手基于本地行情库做美股做多研究」的最小工具集：本地数据同步、MCP 行情服务、三个交易 skill。

感谢上游项目提供的起点。

---

## License

[MIT](./LICENSE)。本项目仅供学习与研究，**不构成投资建议**，使用者需自行承担一切交易风险。
