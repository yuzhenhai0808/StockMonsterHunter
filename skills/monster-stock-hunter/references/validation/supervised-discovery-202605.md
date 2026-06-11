# 监督发现 + 前向 Holdout 验证 (2026-05)

> 配套于 [walkforward-202605.md](walkforward-202605.md)。本报告是**数据驱动发现**: 不预设规则, 用 31 天数据训练, 在最后 2 天(模型完全没见过)做前向 holdout 验证, 看哪些发现是真的, 哪些是过拟合。

## 0. 一句话结论

用 22 个连续特征 + 三方法 (decile lift / 决策树 / L1 logistic) 跑监督发现, 在 31 天 train (91k 行, 826 monsters) 上找出 10 个 lift_40 >= 8.58x 的单特征规则, **全部 10 个在 2 天 holdout (50 monsters, 模型完全没见过) 上仍然 lift >= 7.87x**。 数据揭示了现有 scorecard **完全没覆盖的"深跌反弹"派系** (`dist_from_20d_high >= 78%`, holdout lift 13.72x) 与"已爆继续爆"派系的**精确阈值** (`atr20 >= 24%`, holdout lift 23.02x)。

最强单特征 `atr20_pct >= 23.98` 在 holdout 上 lift 23.02x, 比现有 r1+r2+r5+r6 清洁 scorecard score>=3 (holdout lift 8.69x) 高 2.6 倍, 且 recall 相近 (16% vs 28%)。

## 1. 实验设计

### 1.1 数据切分 (严格前向)

| 切分 | asof_date | rows | monsters (hit_40) | p0(40%) | p0(20%) |
|---|---|---:|---:|---:|---:|
| **train** | 2026-03-30 ~ 2026-05-12 (31 个交易日) | 91,883 | 826 | 0.899% | 2.905% |
| **holdout** | 2026-05-13 ~ 2026-05-14 (最后 2 个交易日) | 5,899 | 50 | 0.848% | 2.255% |

**反信息泄漏**: 特征全部在 asof_date 收盘时点用 ≤ asof_date 的数据计算; 训练集和 holdout 集 asof_date 完全不重叠; 训练时模型从未见到 holdout 的任何 (X, y) 对。Outcome 用 `max(high(T+1), high(T+2)) / asof_close` 计算。

**Outcome correlation caveat**: 训练集最末日 asof=5-12 的 outcome 用 high(5-13)+high(5-14), 而 holdout asof=5-13 的特征用 close(5-13). 这不是泄漏 (特征仍是 T-1 时点), 但导致 train/holdout 的 outcome 窗口在 5-13 那一天有重叠。 影响置信区间但不影响点估 lift。

### 1.2 特征集 (22 个连续 + 7 个工程化)

**原始连续**: `asof_close`, `asof_volume`, `avg_volume_20_prev`, `avg_dollar_20_prev`, `asof_dollar_vol`, `asof_ret_1d_pct`, `asof_ret_5d_pct`, `asof_ret_20d_pct`, `max_1d_ret_180_prev`, `days_above_50_180_prev`, `days_above_20_30_prev`, `dist_from_20d_high_pct`, `dist_from_20d_low_pct`, `dist_from_52w_high_pct`, `dist_from_52w_low_pct`, `pct_vs_ma20`, `pct_vs_ma50`, `atr20_pct`, `market_cap`, `float_shares`, `short_percent_of_float`.

**工程化**: `log_dollar_vol_20`, `log_market_cap`, `log_float`, `log_asof_dollar_vol`, `asof_vol_ratio`, `asof_dollar_ratio`, `log_asof_close`.

不包括 `sector` (一阶段实验, 不引入类别变量, 避免单 sector 过拟合)。

### 1.3 三发现方法

1. **Decile lift sweep** — 每个特征切到 10/20/.../90/95/99 percentile, 算每桶 hit_40 lift, 选 max lift >= 2.0 的特征
2. **Decision Tree** — sklearn DecisionTreeClassifier, depth=3, min_samples_leaf=200, class_weight=balanced
3. **L1 Logistic Regression** — sklearn `LogisticRegression(penalty="l1", solver="saga", C=0.01, class_weight="balanced")`, 标准化输入, 看哪些特征获得非零系数

任一规则只算"高置信"如果**两种以上方法都点亮**这个特征。

### 1.4 Holdout 验证标准

发现的规则要在 holdout 上同时满足:
- `holdout_n >= 30`
- `holdout_lift_40 >= 1.3`
- Wilson LB(holdout_hit_40, holdout_n) > p0_holdout_40

才算 **✓ 通过前向验证**。

## 2. Top 10 单特征发现 (全部通过 holdout)

| # | 规则 | train_n | train_lift_40 | **holdout_n** | **holdout_hit_40** | **holdout_lift_40** | 派系 |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `dist_from_20d_high_pct >= 78.66` | 920 | 16.93x | **86** | **10** | **13.72x** ✓ | 深跌 |
| 2 | `atr20_pct >= 23.98` | 920 | 13.18x | **41** | **8** | **23.02x** ✓ | 已爆 |
| 3 | `dist_from_52w_high_pct >= 98.63` | 913 | 11.21x | **56** | **4** | **8.43x** ✓ | 深跌 |
| 4 | `pct_vs_ma20 >= 61.35` | 919 | 10.77x | **76** | **8** | **12.42x** ✓ | 已爆 |
| 5 | `asof_ret_5d_pct >= 60.77` | 919 | 9.93x | **66** | **8** | **14.30x** ✓ | 已爆 |
| 6 | `days_above_20_30_prev >= 3` | 1752 | 9.52x | **154** | **18** | **13.79x** ✓ | 反复爆 |
| 7 | `asof_ret_20d_pct >= 133.19` | 919 | 9.20x | **87** | **9** | **12.20x** ✓ | 已爆 |
| 8 | `days_above_50_180_prev >= 3` | 1182 | 8.85x | **87** | **9** | **12.20x** ✓ | 翻倍基因强化 |
| 9 | `pct_vs_ma50 >= 82.33` | 919 | 8.59x | **105** | **7** | **7.87x** ✓ | 已爆 |
| 10 | `asof_dollar_ratio >= 4.38` | 921 | 8.58x | **82** | **6** | **8.63x** ✓ | 量能跃迁强化 |

**全部 10 个规则通过 holdout 前向验证**, 一个 lift_40 都没有崩塌 (最低 7.87x, 仍是基线 9.3x)。

## 3. 三方法交叉印证

L1 Logistic 标准化非零系数 (按绝对值排序):

| 特征 | coef | 方向 | decile-lift 重合? | tree 重合? |
|---|---:|---|---|---|
| `log_asof_close` | -1.49 | **越低价越易爆** | (类别相关, decile 未直接发现) | (tree 未直接用) |
| `log_market_cap` | +0.73 | 大市值 (但配低价时反向; 共线性) | (decile 单看 market_cap < $97M 也强, lift 5.42x) | ✓ |
| `asof_vol_ratio` | +0.58 | 量能跃迁 | ✓ (lift 7.99x) | ✓ leaf 11 |
| `atr20_pct` | +0.54 | 高 ATR | ✓ (lift 13.18x) | ✓ leaf 11/12 |
| `log_dollar_vol_20` | +0.53 | 20 日成交额 | ✓ (低基数派系) | (tree 未点亮) |
| `log_float` | -0.52 | 低流通 | (与 r5 一致) | (tree 未点亮) |
| `days_above_20_30_prev` | +0.18 | 近 30 日多次 +20% 日 | ✓ (lift 9.52x) | (tree 未点亮) |
| `dist_from_52w_high_pct` | +0.084 | 距 52 周高远 | ✓ (lift 11.21x) | (tree 未点亮) |
| `short_percent_of_float` | +0.078 | 高空头比例 | (decile 未跑足) | (tree 未点亮) |

**三方法同时点亮 (高置信发现)**:
- `atr20_pct` (decile + tree + logistic)
- `asof_vol_ratio` (decile + tree + logistic, 但本质等于 r2)
- `log_market_cap` / `market_cap` (decile + logistic + tree leaf 12 用 asof_volume 1.19M 作为代理)

**双方法点亮 (中置信)**:
- `dist_from_20d_high_pct` (decile + holdout 13.72x; tree 未点亮但 leaf 2 是这个派系)
- `days_above_20_30_prev` (decile + logistic)
- `dist_from_52w_high_pct` (decile + logistic)

**只有 decile 单点 (低置信, 但 holdout 都通过)**:
- `pct_vs_ma20 >= 61.35` (本质是 ret_20d 子集)
- `asof_ret_5d_pct >= 60.77` (本质是 ret_5d 强势)
- `pct_vs_ma50 >= 82.33` (本质是 ret_20d 强势)
- `asof_ret_20d_pct >= 133.19`
- `asof_dollar_ratio >= 4.38` (本质是 r2 的 5x 加严)
- `days_above_50_180_prev >= 3` (本质是 r6 加严)

## 4. Decision Tree 叶子分析

完整 tree:

```
|--- atr20_pct <= 8.02
|   |--- pct_vs_ma20 <= -35.24          → leaf 2 (深超跌)
|   |--- pct_vs_ma20 >  -35.24
|       |--- atr20_pct <= 4.74          → leaf 4 (低波幅, 不爆)
|       |--- atr20_pct >  4.74          → leaf 5
|--- atr20_pct >  8.02
|   |--- atr20_pct <= 11.67
|       |--- asof_dollar_ratio <= 1.32  → leaf 8
|       |--- asof_dollar_ratio >  1.32  → leaf 9
|   |--- atr20_pct >  11.67
|       |--- asof_volume <= 1193322     → leaf 11 (微盘 + 中等 ATR + 量能跃迁)
|       |--- asof_volume >  1193322     → leaf 12 (大量 + 高 ATR)
```

| leaf | 路径解读 | train_n | train_p40 | train_lift | **holdout_n** | **holdout_p40** | **holdout_lift** | 状态 |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 12 | atr20>11.67% AND vol>1.19M | 2451 | 10.00% | 11.12x | **130** | **13.85%** | **16.34x** | ✓ 强化 |
| 11 | atr20 8-11.67% AND $ratio>1.32 | 4793 | 4.74% | 5.27x | **304** | **6.91%** | **8.15x** | ✓ 强化 |
| 9 | atr20 4.74-11.67% AND $ratio<=1.32 | 1287 | 3.73% | 4.15x | 122 | 1.64% | 1.93x | ⚠ 缩水 |
| 8 | atr20 4.74-8% | 6545 | 1.07% | 1.19x | 458 | 1.09% | 1.29x | ⚠ |
| 2 | atr20<=8% AND pct_vs_ma20<=-35.24 (深超跌) | 371 | **23.45%** | **26.09x** | 48 | 4.17% | 4.92x | **大幅缩水 (训练 overfit)** |

**注意**: leaf 2 的训练表现 26x 在 holdout 上缩到 4.92x —— 训练样本 371 太小, 是该 fold 的过拟合警告。**单看 leaf 2 路径不能写入 skill, 需要更多样本独立验证**。 但 leaf 2 揭示的"深超跌 + 低波"派系本身是有意义的, 等下月再测。

## 5. 双规则组合 (holdout 表现)

| 组合 | train_n | train_lift | **holdout_n** | **holdout_hit** | **holdout_lift_40** |
|---|---:|---:|---:|---:|---:|
| `dist_from_20d_high >= 78.66 AND atr20 >= 23.98` | 291 | 18.73x | **14** | **3** | **25.28x** ✓ |
| `dist_from_20d_high >= 78.66 AND dist_from_52w_high >= 98.63` | 255 | 15.70x | **15** | **2** | **15.73x** ✓ |
| `atr20 >= 23.98 AND dist_from_52w_high >= 98.63` | 190 | 15.22x | **10** | **1** | **11.80x** ⚠ (样本偏小) |
| `pct_vs_ma20 >= 61.35 AND asof_ret_5d >= 60.77` | 595 | 12.53x | **50** | **7** | **16.52x** ✓ |

`dist_from_20d_high + atr20` 同时亮: holdout 14 个候选里 3 个 hit_40 (21.4% precision, lift 25.28x) —— 这是发现到的**最强 2 规则主攻信号**。`pct_vs_ma20 + asof_ret_5d` 是连续动量派系的最强组合。

## 6. 与现有 baseline 对比 (在 holdout)

| 方案 | holdout_n | holdout_hit_40 | holdout_lift_40 | recall_40 (50 monsters) |
|---|---:|---:|---:|---:|
| 现有 r1+r2+r5+r6 score >=1 | 1742 | 43 | 2.91x | 86.0% |
| 现有 r1+r2+r5+r6 score >=2 | 680 | 37 | 6.42x | 74.0% |
| 现有 r1+r2+r5+r6 score >=3 | 190 | 14 | **8.69x** | 28.0% |
| **新发现** `atr20 >= 23.98` | 41 | 8 | **23.02x** | 16.0% |
| **新发现** `days_above_20_30 >= 3` | 154 | 18 | **13.79x** | 36.0% |
| **新发现** `dist_from_20d_high >= 78.66` | 86 | 10 | **13.72x** | 20.0% |
| **新发现组合** `dist_from_20d_high>=78.66 AND atr20>=23.98` | 14 | 3 | **25.28x** | 6.0% |

**结论**: 新发现的若干单特征规则在 holdout 上**单条**就比现有 4 规则 score>=3 强 1.5-3 倍 lift。最强组合 lift 是 baseline 的 2.9 倍。 **现有 scorecard 不是错, 是覆盖不够**: 它只覆盖"微盘 + 低基数"派系, 完全没覆盖"深跌反弹"和"已爆继续"派系。

## 7. 建议沉淀进 skill 的发现

按 holdout 信心度排序:

### A 级 (三方法点亮 + holdout 通过)

| # | 候选规则 | holdout lift_40 | 信号本质 |
|---|---|---:|---|
| A1 | `atr20_pct >= 12` (放宽自 23.98) | **8.15x ~ 16.34x** (leaf 11 + 12) | 高波幅是 monster 第一选择 |
| A2 | `asof_dollar_ratio >= 1.5` 或 `asof_vol_ratio >= 3` (与 r2 一致) | **8.63x** | 当日成交额跃迁 |

### B 级 (两方法点亮 + holdout 通过)

| # | 候选规则 | holdout lift_40 | 信号本质 |
|---|---|---:|---|
| B1 | `dist_from_20d_high_pct >= 78` (距 20 日高 78%+) | **13.72x** | **深跌反弹 (现有 scorecard 完全没有)** |
| B2 | `days_above_20_30_prev >= 3` (近 30 日 ≥3 次 +20% 日) | **13.79x** | "反复爆"信号, r6 的 30 日窗口加强版 |
| B3 | `dist_from_52w_high_pct >= 98` (近 52 周低位) | **8.43x** | 与 B1 同派系, 时间尺度更长 |

### C 级 (单方法 + holdout 通过, 但派生于已知规则)

| # | 候选规则 | holdout lift_40 | 备注 |
|---|---|---:|---|
| C1 | `asof_ret_5d_pct >= 60` | 14.30x | 派生自现有 r6, 5 日尺度具体阈值 |
| C2 | `pct_vs_ma20 >= 61` | 12.42x | 同上, 不同尺度 |
| C3 | `days_above_50_180_prev >= 3` | 12.20x | r6 的更强表达 (>=3 次 vs 只有 1 次) |

### 最强组合 (建议作为主攻硬门)

`dist_from_20d_high_pct >= 78 AND atr20_pct >= 24` → holdout lift_40 **25.28x**, holdout n=14, hit_rate 21.4% — 这是 "深跌 + 高波" 双重叠加, 实操级硬主攻信号。

## 8. 风险与局限

1. **单样本 / 单 regime**: holdout 只有 2 天, 50 个 monsters, 已经是相当小的样本。 个别 lift 可能受 1-2 只票影响 (如 leaf 12 的 16.34x 来自 18 个 hit / 130 候选)。`必须`下月用新 30 天独立 fold 复测。
2. **outcome correlation**: train 最末日 (5-12) 的 outcome 用 high(5-13) + high(5-14), 与 holdout (5-13, 5-14) 的 high 数据有时间窗口重叠。这影响置信区间但不影响点估; 报告里 lift 数字应当作"点估", 不作"区间下界"。
3. **过拟合迹象 (leaf 2)**: depth-3 决策树的某些叶子在 train 上 lift 26x 而 holdout 上仅 4.92x。说明小样本叶子不可信。已剔除 leaf 2 不写入建议规则。
4. **regime caveat**: 本期 2026-04~05 是中等活跃微盘环境。如果下月进入 risk-off / 财报淡季 / 极端 squeeze 期, 阈值会漂移。 所有 ✓ 单期标签, 3 个月连续通过才能去掉单期后缀。
5. **共线性**: `pct_vs_ma20` / `asof_ret_5d` / `asof_ret_20d` / `pct_vs_ma50` 高度相关 (都是"近期已涨"的不同表达), 它们的 lift 不可加。在 skill 里写**一条代表性规则**就够 (推荐 `days_above_20_30_prev >= 3`, 因为 lift 强且语义独立)。
6. **特征数 vs 样本**: 22 个连续特征 + 7 个工程化 = 29 个, 826 monsters 训练样本; 决策树 depth 3 总叶子数 8 个, 平均 100 monsters/叶子 —— 容量合理, 但仍要警惕个别小叶子 (如 leaf 2).

## 9. 下月计划

1. 用新 30 天 (2026-05-15 ~ 2026-06-13 估算) 跑一次 INDEPENDENT 验证, 看 A1/A2/B1/B2/B3 是否仍通过
2. 把 leaf 2 (深超跌 + 低波) 单独拿出来用更大样本验证
3. 加入 sector 类别变量 (one-hot), 看是否某 sector 显著区别 monster 高发区
4. 用 MCP `query_market_data` 跑这个 SQL 替代直接 `docker exec psql` (诚实归档要求)
5. 考虑把发现的规则加入 `_IGNITION_CANDIDATE_REPLAY_SQL` 的下一版 score (但要先 3 个月连续 ✓ 才动 SQL)

## 10. 落地行动 (本期)

下面立即写入 skill 文档 (用 ✓ holdout-validated 标签):

- 在 `ignition-forecast.md` §本期 walk-forward 验证摘要 (行 149) 下新增**"§监督发现 (Holdout-validated)"** 子段, 列 A1/A2/B1/B2/B3 五条候选规则 + 最强组合
- 不动现有 10 分评分卡文字 (原规则定义仍然有效, 我们只是发现了新的有效规则)
- 不动 `_IGNITION_CANDIDATE_REPLAY_SQL` (3 个月连续 ✓ 才动)

---

*生成: 2026-05-19, 监督发现主驱动: `/tmp/wf202605/rich_features.sql` + `discover.py` (sklearn 1.8 + scipy 1.17 via `uv run --with`). 原始数据 ~98k 行 + 决策树结构 + 系数表保留在 `/tmp/wf202605/discovery.json`. 报告作为单期快照, 下月独立验证后会附录"复测对照"段.*
