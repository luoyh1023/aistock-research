# StockScreenerAgent

## 职责

基于用户自定义的多维条件，从全市场股票池中筛选符合条件的股票，支持条件的保存、复用和逐步叠加优化。

---

## 输入（Input）

```json
{
  "strategy_id": "my_value_strategy_v2",
  "conditions": [
    { "field": "pe_ttm", "operator": "<", "value": 20 },
    { "field": "roe_annual", "operator": ">", "value": 15 },
    { "field": "revenue_growth_yoy", "operator": ">", "value": 0.15 },
    { "field": "market_cap_billion", "operator": "between", "value": [50, 500] },
    { "field": "industry", "operator": "in", "value": ["消费", "医药"] },
    { "field": "ma20_cross_ma60", "operator": "=", "value": true }
  ],
  "stock_universe": "A_ALL",
  "run_date": "2026-04-21"
}
```

**支持的条件维度：**

| 维度 | 示例字段 |
|------|---------|
| 估值 | `pe_ttm`, `pb`, `ps`, `dividend_yield` |
| 盈利 | `roe_annual`, `net_profit_growth_yoy`, `revenue_growth_yoy`, `gross_margin` |
| 财务健康 | `debt_ratio`, `current_ratio`, `fcf_yield` |
| 规模 | `market_cap_billion`, `total_assets_billion` |
| 行业 | `industry`, `sector`, `concept_tags` |
| 技术面 | `ma_cross`, `rsi_14`, `price_above_ma20`, `breakout_volume` |

---

## 输出（Output）

```json
{
  "strategy_id": "my_value_strategy_v2",
  "run_date": "2026-04-21",
  "total_screened": 5200,
  "passed_count": 47,
  "results": [
    {
      "stock_code": "600519",
      "stock_name": "贵州茅台",
      "industry": "白酒",
      "market_cap_billion": 1890,
      "pe_ttm": 28.5,
      "roe_annual": 32.1,
      "score": 88
    }
  ],
  "condition_stats": [
    { "condition": "pe_ttm < 20", "pass_count": 1200, "pass_rate": 0.23 },
    { "condition": "roe_annual > 15", "pass_count": 800, "pass_rate": 0.15 }
  ]
}
```

---

## 数据来源

- 依赖 DataFetcherAgent 的 `screener_snapshot`（全市场基本面 + 技术面快照）
- 快照建议每日盘后更新并缓存，避免实时拉取全市场数据

---

## 核心逻辑/算法

### 1. 条件解析

- 支持用户通过 UI 配置条件，也支持自然语言输入（LLM 解析为结构化条件）
- 自然语言示例："找 PE 低于 20 且 ROE 超过 15% 的消费股"

### 2. 筛选执行

- 基于全市场快照 DataFrame，用 pandas 条件过滤（非 LLM）
- 技术面条件（均线金叉等）在快照中预计算好

### 3. 结果评分

对通过筛选的股票，可选计算综合得分（基于各指标偏离度加权）

### 4. 条件统计

输出每个条件的通过率，帮助用户了解哪个条件最有筛选力，便于策略优化

---

## 与其他 Agent 的交互

- **依赖**：DataFetcherAgent（全市场快照）
- **可选联动**：FundamentalAgent（对筛选结果做二次评分）、TechnicalAgent（添加技术面信号）
- **被依赖**：StrategyRankAgent（获取历史日期的选股结果用于回测）

---

## Prompt 设计要点

- 自然语言 → 结构化条件的解析：给 LLM 提供支持字段列表和运算符列表，避免幻觉字段名
- 核心筛选逻辑用代码实现，LLM 不参与数值计算
- LLM 仅用于：自然语言解析、结果摘要描述

---

## 示例

**用户输入**：找出 PE 低于 20、ROE 大于 15% 的消费和医药股，市值 50–500 亿，且 MA20 上穿 MA60

**处理流程**：
1. （如为自然语言）LLM 解析为结构化条件 JSON
2. 加载全市场快照（约 5200 只 A 股）
3. pandas 依次过滤：PE → ROE → 行业 → 市值 → 均线金叉
4. 剩余 47 只，按综合得分排序
5. 返回结果列表 + 各条件通过率统计
