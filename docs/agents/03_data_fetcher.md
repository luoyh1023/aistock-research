# DataFetcherAgent

## 职责

作为所有分析 Agent 的**唯一数据入口**，封装 AKShare 和 yfinance，将原始数据清洗、标准化后输出。其他 Agent 不得直接调用数据源。

---

## 输入（Input）

```json
{
  "stock_code": "600519",
  "market": "A",
  "data_types": ["price", "financials", "shareholders", "industry", "news"],
  "time_range": {
    "start": "2022-01-01",
    "end": "2026-04-21"
  },
  "frequency": "daily"
}
```

`data_types` 可选值：

| 值 | 含义 |
|----|------|
| `price` | K 线行情（OHLCV）|
| `financials` | 财务三表（利润表/资产负债表/现金流量表）|
| `valuation` | PE、PB、PS、股息率等估值指标 |
| `shareholders` | 前十大股东、机构持股 |
| `industry` | 行业分类、同行业公司列表 |
| `news` | 近期相关新闻标题+正文摘要 |
| `screener_snapshot` | 全市场基本面快照（供选股器用）|

---

## 输出（Output）

```json
{
  "stock_code": "600519",
  "stock_name": "贵州茅台",
  "market": "A",
  "price": {
    "daily": [{ "date": "2026-04-21", "open": 1500, "high": 1520, "low": 1495, "close": 1510, "volume": 1200000 }]
  },
  "financials": {
    "income_statement": [...],
    "balance_sheet": [...],
    "cash_flow": [...]
  },
  "valuation": {
    "pe_ttm": 28.5,
    "pb": 9.2,
    "dividend_yield": 2.1,
    "pe_percentile_5y": 35
  },
  "shareholders": {
    "top10": [...],
    "institution_ratio": 0.72,
    "holder_count_trend": [...]
  },
  "news": [
    { "date": "2026-04-20", "title": "...", "summary": "...", "source": "东方财富" }
  ],
  "fetched_at": "2026-04-21T10:00:00Z"
}
```

---

## 数据来源

| 数据类型 | 主要来源 | 备选 |
|---------|---------|------|
| A 股行情 | AKShare `stock_zh_a_hist` | - |
| 财务报表 | AKShare `stock_financial_report_sina` | - |
| 估值指标 | AKShare `stock_a_indicator_lg` | - |
| 股东结构 | AKShare `stock_zh_a_gdhs` | - |
| 港/美股行情 | yfinance | - |
| 新闻 | AKShare `stock_news_em` | 聚合新闻 API |
| 全市场快照 | AKShare `stock_zh_a_spot_em` | - |

---

## 核心逻辑/算法

1. **请求解析**：根据 `data_types` 确定需要调用的 AKShare/yfinance 接口列表
2. **并行拉取**：多个数据类型并行请求（asyncio）
3. **数据清洗**：
   - 统一日期格式为 `YYYY-MM-DD`
   - 处理停牌日（缺失值填充或标记）
   - 数值单位统一（万元 → 元）
4. **缓存**：相同请求 15 分钟内命中缓存（Redis / SQLite）
5. **输出标准化**：转换为统一 JSON 结构

---

## 与其他 Agent 的交互

- **被依赖**：OrchestratorAgent、FundamentalAgent、TechnicalAgent、SentimentAgent、IndustryAgent、StockScreenerAgent 均调用此 Agent
- **依赖**：无（直接调用外部数据源）

---

## Prompt 设计要点

DataFetcherAgent 不调用 LLM，是纯代码模块。无 Prompt。

---

## 港股数据覆盖说明（已知限制）

> 验证日期：2026-04，测试股票：00700 腾讯 / 01810 小米 / 03690 美团

| 数据类型 | A 股 | 港股 | 港股限制说明 |
|---------|------|------|------------|
| 基本信息 / 行情 | ✅ | ✅ | yfinance 正常覆盖 |
| 财务报表 | ✅ | ✅ | yfinance 财务三表可用 |
| 估值指标（PE/PB） | ✅ | ✅ 部分 | 分析师预测数据较少 |
| **股东变动 / 增减持** | ✅ | ❌ | `ak.stock_zh_a_gdhs` 仅支持 A 股；港股无大股东增减持接口 |
| **公司公告** | ✅ | ❌ | `ak.stock_notice_report_em` 仅支持 A 股；港股公告无法获取 |
| **券商研报** | ✅ | ❌ | `ak.stock_research_report_em` 仅支持 A 股；港股无研报数据 |
| 概念/行业分类 | ✅ | ⚠️ 部分 | 港股同行对比表数据稀缺 |

### 对各维度的影响

- **⑦ 催化剂**（catalyst）：港股 conf≈0.4，无真实公告数据，LLM 仅能基于行业知识推理
- **⑧ 研究报告**（research）：港股 conf=0.0，直接降级提示"暂无研报，可手动上传 PDF"
- **④ 股东结构**（shareholder）：港股 conf≈0.4，无大股东增减持记录

### 改进路径（非紧急，备查）

| 补充数据 | 方案 | 优先级 |
|---------|------|--------|
| 港股公告 | 港交所 HKEX 披露易免费 API | P2 |
| 港股研报 | 用户手动上传 PDF（已支持）；或接入万得/彭博（付费） | P2 |
| 港股股东变动 | SFC 权益披露数据 | P3 |

---

## 示例

**请求**：获取 600519 近 1 年日 K 线和财务报表

```python
result = DataFetcherAgent.fetch(
    stock_code="600519",
    data_types=["price", "financials"],
    time_range={"start": "2025-04-21", "end": "2026-04-21"}
)
```

**输出**：标准化的价格 DataFrame + 财务报表 DataFrame
