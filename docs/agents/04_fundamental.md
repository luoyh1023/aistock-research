# FundamentalAgent

## 职责

对单支股票进行基本面分析。核心定位是**分析工具**而非黑盒结论机器：
- 透明化所有分析假设（DCF 参数、行业对比基准等）
- 允许用户调整假设后重新分析
- 追踪历次分析结果，展示基本面质量的演变趋势
- 多模型分析结果独立缓存，支持对比

---

## 缓存策略（双驱动）

**数据缓存（财务原始数据）**
- 默认有效期：3 个月
- 季报节点强制失效：4月底、8月底、10月底（A股财报季后）
- 用户可随时手动触发刷新

**分析结果缓存**
- 缓存 key：`{stock_code}_{model}_{assumptions_hash}_{data_version}`
- 假设参数不变 + 数据未刷新 → 直接返回缓存
- 数据刷新后：标记为"分析待更新"，不自动删除旧结果
- 用户可对比"刷新前"和"刷新后"的分析变化

**分析更新触发信号**
- 季报发布 → 自动标记缓存为"数据已更新，建议重新分析"
- 股价偏离分析时估值中枢 ±20% → 提示重新分析
- 用户手动点击"刷新分析"

---

## 输入（Input）

```json
{
  "stock_code": "600519",
  "model": "claude-sonnet",
  "force_refresh_data": false,
  "force_refresh_analysis": false,
  "assumptions": {
    "revenue_growth_rate": null,
    "wacc": null,
    "terminal_growth_rate": 0.03,
    "pe_comparison_years": 5
  }
}
```

`assumptions` 说明：
- `null` 表示使用系统自动计算值（基于历史数据均值）
- 用户传入具体值时，使用用户值并在报告中标注"用户自定义"
- 修改假设后，分析结果视为新版本（新 cache key）

---

## 输出（Output）

```json
{
  "stock_code": "600519",
  "stock_name": "贵州茅台",
  "analysis_id": "600519_sonnet_a3f2_2025Q3",
  "data_version": "2025Q3",
  "model_used": "claude-sonnet-4-6",
  "analyzed_at": "2026-04-21T11:00:00Z",
  "cache_hit": false,

  "verdict": {
    "valuation": "合理偏低估",
    "recommendation": "增持",
    "confidence_basis": ["PE处历史低位", "ROE持续高位", "现金流健康"]
  },

  "assumptions_used": {
    "revenue_growth_rate": 0.12,
    "revenue_growth_source": "近3年均值（2022-2024）",
    "wacc": 0.08,
    "wacc_source": "白酒行业均值",
    "terminal_growth_rate": 0.03,
    "pe_comparison_years": 5,
    "user_overrides": []
  },

  "valuation_detail": {
    "pe_current": 20.6,
    "pe_percentile_5y": 12,
    "pe_comment": "当前 PE 处于近5年 12% 分位，偏低",
    "pb_current": 6.93,
    "pb_percentile_5y": 8,
    "dcf_fair_value": 1720,
    "dcf_bear": 1450,
    "dcf_base": 1720,
    "dcf_bull": 1980
  },

  "financial_health": {
    "score": 88,
    "flags": [],
    "positives": ["经营现金流/净利润 > 1，现金质量好", "零有息负债", "ROE 连续5年 > 25%"]
  },

  "key_assumptions_impact": [
    {
      "assumption": "营收增速",
      "current_value": "12%",
      "sensitivity": "每变化 1%，DCF 估值变化约 ±3%"
    }
  ],

  "full_report": "完整分析报告（Markdown）",

  "history_comparison": {
    "previous_analysis_id": "600519_sonnet_a3f2_2025Q2",
    "previous_verdict": "中性",
    "changes": ["营收增速从 8% 升至 12%", "PE 分位从 28% 降至 12%"]
  }
}
```

---

## 数据来源

- 依赖 `DataFetcherAgent` 的 `financials`、`valuation`、`price` 数据
- 行业均值 WACC 由代码基于同行业数据计算，非 LLM 编造

---

## 核心逻辑/算法

### 1. 假设参数自动计算

| 参数 | 自动计算方式 |
|------|------------|
| `revenue_growth_rate` | 近3年营收年化复合增长率（CAGR）|
| `wacc` | 无风险利率（10年期国债）+ 行业 Beta × 风险溢价 |
| `terminal_growth_rate` | 默认 3%（GDP 长期增长率），用户可覆盖 |

### 2. 估值评分（多维加权）

| 方法 | 权重 |
|------|------|
| PE 历史分位 | 30% |
| PB 历史分位 | 15% |
| DCF 内在价值 vs 当前价 | 35% |
| PEG | 10% |
| 财务健康度 | 10% |

综合分 0–100：<35 = 低估，35–65 = 合理，>65 = 高估

### 3. 财务健康度检查（代码逻辑，非 LLM）

触发黄色预警：
- 应收账款增速 > 营收增速 × 1.5
- 经营现金流 / 净利润 < 0.7（连续 2 季）
- 商誉 / 净资产 > 30%
- 资产负债率 > 行业均值 + 15%

### 4. 历史分析对比

每次分析完成后保存快照，下次分析时自动对比：
- 假设参数的变化
- 关键财务指标的变化
- 最终结论的变化及原因

### 5. LLM 的职责边界

- **代码做**：所有数值计算（DCF、分位数、财务健康分）
- **LLM 做**：解读计算结果的含义、识别异常模式、生成自然语言报告、评估不同假设下的合理性
- LLM 不做数学，只做判断和表达

---

## 与其他 Agent 的交互

- **依赖**：DataFetcherAgent（必须先完成数据获取）
- **被依赖**：OrchestratorAgent、StockScreenerAgent（基本面评分过滤）

---

## Prompt 设计要点

- 给 LLM 传入结构化的计算结果（JSON），不让 LLM 自己算数字
- 明确传入"用户自定义的假设" vs "系统自动计算的假设"，让 LLM 在报告中区分呈现
- 要求 LLM 输出"核心假设对结论的影响"（sensitivity 分析的文字版）
- 结论部分要求 LLM 基于数据得出，不允许泛泛而谈
- Temperature 0.2（低随机性，保持分析一致性）

---

## 示例

**场景 1：首次分析**
```
用户: 分析茅台，用 claude-sonnet
→ DataFetcherAgent 拉取数据（或命中缓存）
→ 代码计算：PE 分位 12%、DCF 基准值 1720、健康分 88
→ 假设：营收增速 12%（系统自动）、WACC 8%（系统自动）
→ LLM 生成报告，缓存结果
→ 返回完整分析
```

**场景 2：用户调整假设**
```
用户: 我觉得增速只有 6%，重新分析
→ 假设 hash 变化 → 新 cache key → 重新调用 LLM
→ DCF 重算：保守情景下估值 1380（vs 原来 1450）
→ 结论可能从"增持"变为"中性"
→ 两次分析结果并排展示
```

**场景 3：季报后返回**
```
用户: 上次分析是 3 个月前，现在再看看
→ 检测到 2025Q4 财报已发布，数据缓存已失效
→ 提示："数据已更新（2025Q4 财报），建议刷新分析"
→ 用户确认 → 刷新数据 → 重新分析 → 显示与上次分析的变化
```
