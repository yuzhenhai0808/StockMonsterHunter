# TradingAgents MCP client config examples

This project exposes market-data tools through one shared MCP server:

- Server implementation: `mcp_servers/postgres_market_data_server.py`
- FastMCP service name: `tradingagents-market-data`
- Streamable HTTP URL: `http://127.0.0.1:18080/mcp`
- SSE URL: `http://127.0.0.1:18080/sse`

Do not start a second MCP server when one is already running on port `18080`.
Codex should connect to the existing streamable HTTP URL.

## Codex

`/Users/smzdm/.codex/config.toml` currently uses:

```toml
[mcp_servers.mydatabase]
enabled = true
url = "http://127.0.0.1:18080/mcp"
```

Depending on the Codex runtime, tools may appear as bare tool names such as
`db_health`, or under the configured server name `mydatabase`.

## Claude Code

Recommended server name:

```text
tradingagents-market-data
```

Recommended streamable HTTP target for Codex:

```text
http://127.0.0.1:18080/mcp
```

Recommended SSE target for Claude Code:

```text
http://127.0.0.1:18080/sse
```

When registered with that name, Claude Code usually exposes tools with names like:

```text
mcp__tradingagents-market-data__db_health
mcp__tradingagents-market-data__query_market_data
mcp__tradingagents-market-data__latest_prices
mcp__tradingagents-market-data__symbol_snapshot
mcp__tradingagents-market-data__long_momentum_candidates
mcp__tradingagents-market-data__earnings_dates
mcp__tradingagents-market-data__company_fundamentals
```

If you register the server under another name, replace the middle segment with
that name. For example, a Claude Code server named `mydatabase` may expose
`mcp__mydatabase__db_health`.

## Required tools

The stock recommendation skills expect these MCP tools to be available:

- `db_health`
- `query_market_data`
- `latest_prices`
- `symbol_snapshot`
- `long_momentum_candidates`
- `earnings_dates`
- `oversold_bounce_candidates`
- `pullback_candidates`
- `value_candidates`
- `company_fundamentals`

If a client cannot see these tools, fix that client's MCP registration instead
of bypassing MCP with direct PostgreSQL access or a database Studio.
