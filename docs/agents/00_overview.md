# Agent 协作总览

## Agent 列表

| 编号 | Agent | 文档 | 阶段 |
|------|-------|------|------|
| 00 | OrchestratorAgent | 本文件 | Phase 1 |
| 01 | StockScreenerAgent | [01_stock_screener.md](01_stock_screener.md) | Phase 2 |
| 02 | StrategyRankAgent | [02_strategy_rank.md](02_strategy_rank.md) | Phase 4 |
| 03 | DataFetcherAgent | [03_data_fetcher.md](03_data_fetcher.md) | Phase 1 |
| 04 | FundamentalAgent | [04_fundamental.md](04_fundamental.md) | Phase 1 |
| 05 | TechnicalAgent | [05_technical.md](05_technical.md) | Phase 2 |
| 06 | SentimentAgent | [06_sentiment.md](06_sentiment.md) | Phase 3 |
| 07 | ResearchReportAgent | [07_research_report.md](07_research_report.md) | Phase 3 |
| 08 | IndustryAgent | [08_industry.md](08_industry.md) | Phase 4 |

---

## OrchestratorAgent

### 职责
接收用户请求，拆解为子任务，调度合适的 Agent 并行执行，汇总结果输出给用户。

### 输入
```json
{
  "user_query": "分析贵州茅台 600519 的基本面和技术面",
  "context": { "user_id": "xxx", "history": [] }
}
```

### 输出
```json
{
  "report": "综合分析报告（Markdown）",
  "sections": {
    "fundamental": "...",
    "technical": "..."
  },
  "metadata": { "duration_ms": 8200, "agents_used": ["DataFetcher", "Fundamental", "Technical"] }
}
```

### 核心逻辑
1. 解析用户意图（NLU）：识别股票代码、分析维度、时间范围
2. 生成执行计划：确定调用哪些 Agent、执行顺序（DataFetcher 必须先于分析层）
3. 并行调度分析层 Agent
4. 等待全部完成后，合并结果，生成最终报告

### 与其他 Agent 的交互
- 调度所有 Agent，是唯一对用户直接响应的 Agent
- 不直接调用数据源

---

## Agent 文档模板

新增 Agent 时，复制以下模板：

```markdown
# [Agent Name]

## 职责

## 输入（Input）
```json
{}
```

## 输出（Output）
```json
{}
```

## 数据来源

## 核心逻辑/算法
1. 步骤一
2. 步骤二

## 与其他 Agent 的交互
- 依赖：
- 被依赖：

## Prompt 设计要点

## 示例
```
