# TechnicalAgent

## 职责

基于 K 线数据计算主流技术指标，识别经典图形形态，输出技术面评分和交易信号。

---

## 输入（Input）

```json
{
  "price_data": "<DataFetcherAgent 输出的 price.daily 数组>",
  "indicators": ["RSI", "MACD", "BOLL", "KDJ", "MA", "VOLUME"],
  "pattern_detection": true,
  "time_range_days": 250
}
```

---

## 输出（Output）

```json
{
  "stock_code": "600519",
  "technical_score": 68,
  "signal": "看多",
  "indicators": {
    "RSI_14": { "value": 42.3, "signal": "中性，接近超卖区间" },
    "MACD": { "dif": 12.5, "dea": 10.2, "histogram": 2.3, "signal": "金叉，趋势向上" },
    "BOLL": { "upper": 1580, "mid": 1510, "lower": 1440, "signal": "价格在中轨上方运行" },
    "KDJ": { "k": 65, "d": 58, "j": 79, "signal": "多头排列" },
    "MA": { "MA5": 1505, "MA20": 1490, "MA60": 1450, "MA250": 1380, "signal": "多头排列，短期均线向上" }
  },
  "patterns_detected": [
    { "pattern": "均线金叉", "date": "2026-04-15", "reliability": "中" },
    { "pattern": "放量突破", "date": "2026-04-18", "reliability": "高" }
  ],
  "key_levels": {
    "support": [1480, 1450],
    "resistance": [1550, 1600]
  },
  "summary": "短期技术面偏多，建议关注 1550 压力位突破情况"
}
```

---

## 数据来源

- 依赖 DataFetcherAgent 的 `price` 数据（日 K 线 OHLCV）
- 指标计算使用 `pandas-ta` 或 `TA-Lib` 库，不依赖 LLM 计算数值

---

## 核心逻辑/算法

### 技术指标计算（代码实现，非 LLM）

| 指标 | 计算方式 |
|------|---------|
| RSI(14) | `pandas_ta.rsi(close, length=14)` |
| MACD(12,26,9) | `pandas_ta.macd(close)` |
| 布林带(20,2) | `pandas_ta.bbands(close, length=20, std=2)` |
| KDJ(9,3,3) | `pandas_ta.stoch(high, low, close)` |
| MA | `pandas_ta.sma(close, length=N)` |

### 技术评分模型

各指标信号打分（-2 强看空 → +2 强看多），加权求和后映射到 0–100：

| 指标 | 权重 |
|------|------|
| MACD 趋势 | 25% |
| MA 排列 | 25% |
| RSI 位置 | 20% |
| KDJ 方向 | 15% |
| 成交量配合 | 15% |

### 形态识别（LLM 辅助）

代码检测候选形态，LLM 负责解读和描述：
- 头肩顶/底（高低点序列分析）
- 双顶/双底
- 旗形整理
- 突破确认（放量 + 价格突破关键位）

---

## 与其他 Agent 的交互

- **依赖**：DataFetcherAgent
- **被依赖**：OrchestratorAgent、StockScreenerAgent（技术面条件过滤）

---

## Prompt 设计要点

- 技术指标数值由代码预计算，作为结构化 context 传入，LLM 只做信号解读和报告撰写
- 形态识别：给 LLM 提供近 N 日的 OHLCV 数据 + 候选形态的数学定义，让其判断置信度
- 明确要求 LLM 输出关键支撑/压力位的数值依据

---

## 示例

**用户请求**：茅台现在技术面怎么样？

**分析流程**：
1. 获取近 250 日 K 线
2. 计算所有指标（pandas-ta）
3. MACD 金叉 + MA 多头排列 → 技术评分 68（偏多）
4. LLM 生成自然语言摘要 + 关键位描述
5. 输出：信号看多，关注 1550 压力位
