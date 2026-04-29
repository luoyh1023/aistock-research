# IndustryAgent

## 职责

对特定行业进行深度分析，包括产业链上下游梳理、竞争格局、核心技术解读（含图示）、景气度周期判断和政策跟踪。

> 当前为 Phase 4 规划文档，具体实现细节待 Phase 3 完成后细化。

---

## 输入（Input）

```json
{
  "industry_code": "白酒",
  "trigger": "company",
  "stock_code": "600519",
  "analysis_modules": ["supply_chain", "competition", "technology", "cycle", "policy"]
}
```

`trigger` 说明：
- `company`：从某公司出发分析其所在行业
- `industry`：直接指定行业进行分析

---

## 输出（Output）

```json
{
  "industry": "白酒",
  "supply_chain": {
    "upstream": ["高粱（四川/贵州产区）", "小麦（制曲）", "水源"],
    "midstream": ["酿造（茅台/五粮液等）", "储存陈化"],
    "downstream": ["经销商体系", "电商渠道", "免税"],
    "key_companies_by_tier": { ... },
    "chart_description": "产业链图（用 Mermaid 或 SVG 描述）"
  },
  "competition": {
    "market_share": [{ "company": "茅台", "share": 0.42 }],
    "cr4": 0.72,
    "moat_analysis": "茅台具备品牌 + 地理标志 + 产能双重护城河",
    "competitive_dynamics": "高端格局稳定，中端白酒竞争激烈"
  },
  "technology": {
    "key_tech": "酱香型白酒酿造工艺（坤沙工艺）",
    "tech_explanation": "通俗解释（含示意图描述）",
    "tech_barriers": "12987 工艺 + 微生物菌群 + 储酒周期"
  },
  "cycle": {
    "current_phase": "复苏期",
    "phase_reasoning": "...",
    "leading_indicators": ["批价走势", "渠道库存", "经销商信心指数"]
  },
  "policy": [
    { "date": "2026-03-01", "policy": "...", "impact": "中性" }
  ],
  "summary": "行业综合分析报告"
}
```

---

## 数据来源

| 模块 | 数据来源 |
|------|---------|
| 产业链 | LLM 知识 + 行业报告 + AKShare 上市公司分类 |
| 竞争格局 | AKShare 行业财务数据 + 市场份额数据 |
| 技术 | LLM 知识库（技术类细节）|
| 景气周期 | 行业 PMI、价格指数（AKShare）|
| 政策 | 政府公告、AKShare 宏观数据 |

---

## 核心逻辑/算法

### 1. 产业链图生成

- 代码生成 Mermaid 图或 ECharts 桑基图描述
- LLM 填充各节点的公司名称和市场地位

### 2. 竞争格局分析

- 计算 CR4/CR10（行业前 4/10 家公司市场份额之和）
- LLM 分析护城河类型：成本优势、品牌、网络效应、转换成本、规模效应

### 3. 核心技术解读（差异化功能）

- 对技术门槛高的行业（半导体、新能源、创新药等），LLM 生成：
  - 技术原理通俗解释（类比日常生活）
  - 技术壁垒的商业意义
  - 中国与全球技术差距评估

### 4. 景气度判断

- 基于量价数据、库存周期、订单数据
- 参考 Kitchin 周期（库存）、Juglar 周期（设备投资）

---

## 与其他 Agent 的交互

- **依赖**：DataFetcherAgent（行业财务数据）
- **被依赖**：OrchestratorAgent

---

## Prompt 设计要点

- 产业链分析：给定行业名称，要求 LLM 按上/中/下游三层输出，每层列举 3–5 个环节和代表企业
- 技术解读：要求"用高中生能理解的方式解释"，并给出商业层面的影响
- 不要求 LLM 提供精确的市场份额数字（容易幻觉），数字由数据来源提供

---

## 待细化（Phase 4）

- [ ] 产业链可视化图的前端实现方案
- [ ] 景气度量化模型
- [ ] 行业间比较（跨行业配置建议）
