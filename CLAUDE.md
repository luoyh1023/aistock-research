# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AIStock 是一个 AI 驱动的股票分析应用，由多个专职 Agent 协作完成选股、基本面/技术面/情绪面分析、研报聚合和行业分析等功能。

## Key Documents

- [PRD.md](PRD.md) — 产品需求文档，所有功能的权威描述
- [ARCHITECTURE.md](ARCHITECTURE.md) — 技术架构总览，数据流和 Agent 交互
- [docs/agents/](docs/agents/) — 每个 Agent 的详细设计文档

## Architecture Summary

```
用户请求
    ↓
OrchestratorAgent
    ↓ 分发
DataFetcherAgent (AKShare / yfinance)
    ↓ 数据
分析层 Agents（并行）
├── FundamentalAgent
├── TechnicalAgent
├── SentimentAgent
├── ResearchReportAgent
└── IndustryAgent
    ↓ 汇总
应用层 Agents
├── StockScreenerAgent
└── StrategyRankAgent
```

## Data Sources

- **AKShare** — A 股数据主力源（财务报表、行情、股东、行业）
- **yfinance** — 港股/美股补充
- 新闻/情绪：可对接聚合新闻 API 或爬虫

## Development Conventions

- 每个 Agent 独立为一个 Python 模块，放在 `src/agents/` 下
- Agent 间通过统一的 `AgentMessage` 数据结构通信
- 所有数据抓取必须经过 `DataFetcherAgent`，不允许其他 Agent 直接调用数据源
- 新增 Agent 必须先在 `docs/agents/` 下创建对应文档

## Agent Document Template

新增 Agent 时，参考 [docs/agents/00_overview.md](docs/agents/00_overview.md) 中的模板，文档包含：职责、输入/输出、数据来源、核心逻辑、与其他 Agent 的交互、示例。
