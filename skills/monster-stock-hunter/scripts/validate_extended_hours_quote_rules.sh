#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

check() {
  local file="$1"
  local pattern="$2"
  if ! grep -Fq "$pattern" "$ROOT/$file"; then
    echo "missing: $file :: $pattern" >&2
    exit 1
  fi
}

check "references/core/web-cross-check.md" "延长时段价格获取强化"
check "references/core/web-cross-check.md" "夜盘、盘前、盘后价格不是\"不可获取\""
check "references/core/web-cross-check.md" "quote_attempts = 用户券商? → quote页? → 第二公开源? → movers? → 新闻? → MCP背景"
check "references/core/web-cross-check.md" "只有 movers 榜单价格或涨幅,但没有 quote 页/成交量/bid-ask → 最高 C"
check "references/core/web-cross-check.md" "如果某个网页同时显示 regular close 和 premarket/AH 价"

check "references/core/output-contract.md" '夜盘/盘前/盘后请求不得写`无法获取盘前价`作为默认结论'
check "references/core/output-contract.md" "movers 发现价"

check "references/modes/extended-hours-scalp.md" "夜盘/盘前价格默认可通过用户券商、公开 quote 页和 movers 页面获取"
check "SKILL.md" "夜盘/盘前价格可获取硬门"

check "SKILL.md" "全景优先硬门"
check "SKILL.md" "IPO/new listing 防漏"
check "references/core/output-contract.md" "当前盘前/盘中 40%+ 全景审计池"
check "references/core/output-contract.md" "持仓/破位处置表"
check "references/core/web-cross-check.md" "盘前榜单快照多时间戳规则"
check "references/core/web-cross-check.md" "Movers 必展示但不替代 Quote"
check "references/core/web-cross-check.md" "IPO / New Listing Fallback"
check "references/workflow/causal-first.md" "CODX 型盘前破位接棒降级"
check "references/modes/extreme-lottery-leaderboard.md" "IPO squeeze / new listing squeeze"
check "references/workflow/exit-framework.md" "跑路 / 减仓 / 补仓 决策表硬门"

echo "extended-hours and panoramic lottery rule validation passed"
