# 模式 4:价值挖坑

**场景**: 基本面好但价格在低位等涨 | **持仓周期**: 1–6 月 | **预期收益**: +20-50% | **不给硬止损**

## 触发条件

用户关键词:"基本面" / "价值" / "长线" / "等涨" / "不着急" / "业绩好"

## 前置条件

必须确认 `db_health.fundamentals_coverage.usable_for_value_screening=true`,否则提示用户"基本面数据不足,回退到回调模式"。

## 流程

1. 调 `value_candidates()`,按市值分 3 档:
   - `0.3-2`(十亿美元,小盘)、`2-10`(中盘)、`10-200`(大盘)
2. 对感兴趣的候选调 `company_fundamentals(symbol)`,看:
   - 最近 4–5 季度的 revenue / operating_income / net_income 趋势是否向好
   - sector_median 对比:trailing_pe / price_to_book 是否显著低于同行
3. **不给止损价**,给"建议分批建仓区间"(当前价 -5% 到 +2%)
4. 价值投资需要故事验证:WebSearch 查近期是否有管理层变动、战略转型、回购计划、股息政策
5. **强制执行** [红旗复核](../workflow/red-flags.md) 的价值模式专项(会计问题 / SEC 调查 / 审计师辞职 / 高管抛售)
6. 明确告知用户:**价值模式持仓至少 3 个月,不适合短线**

## 输出必须包含

- 所用模式
- 每个市值档最多 3 只
- sector / industry / 市值
- 估值指标:trailing_pe / price_to_book / fcf_yield
- 质量指标:ROE / 利润率 / D/E / 营收增长
- 相对估值:距 52 周低点 %、相对同业 P/E 中位数
- 分批建仓区间(不给硬止损)
- **持仓周期和退出信号**(如:业绩出坑 / 行业复苏 / 估值修复到 P/E 20)

## 需要降级或排除

- 数据显示基本面好但新闻面揭示会计问题 / 监管调查 / 高管集体抛售
- 仅靠 `profit_margins > 0` 通过但实际是一次性利得(看 `company_fundamentals` 季度趋势判断)
- 审计师辞职 / SEC 调查 → 直接排除(不可恢复)

## 输出要求

最终总表、单票短评、价格目标概率表、报价校验表、输出顺序与自检清单统一按 [../output-contract.md](../core/output-contract.md) 执行;价值模式不给硬止损,改用"分批建仓区间"和"持仓周期与退出信号"替代,详见 [../risk-rules.md](../core/risk-rules.md) "价值模式特别规则"。
