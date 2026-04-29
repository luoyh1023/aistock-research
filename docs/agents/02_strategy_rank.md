# StrategyRankAgent

## 职责

对用户保存的选股策略进行历史回测，计算多个时间窗口的胜率和风险指标，生成社区策略排行榜，支持策略的公开分享和 fork。

---

## 输入（Input）

```json
{
  "action": "backtest",
  "strategy_id": "my_value_strategy_v2",
  "strategy_conditions": [...],
  "backtest_config": {
    "windows": ["3y", "1y", "6m", "3m"],
    "benchmark": "000300",
    "hold_period_days": 90,
    "rebalance": "monthly"
  }
}
```

`action` 可选：`backtest`（回测单个策略）/ `leaderboard`（获取排行榜）/ `compare`（对比多个策略）

---

## 输出（Output）

### 回测结果

```json
{
  "strategy_id": "my_value_strategy_v2",
  "backtest_windows": {
    "3y": {
      "win_rate": 0.63,
      "sharpe_ratio": 1.42,
      "max_drawdown": 0.18,
      "annual_return": 0.21,
      "vs_benchmark": 0.08,
      "avg_stocks_per_period": 32
    },
    "1y": { ... },
    "6m": { ... },
    "3m": { ... }
  },
  "equity_curve": [{ "date": "2024-01-01", "value": 1.0 }, ...],
  "monthly_returns": [...]
}
```

### 排行榜

```json
{
  "leaderboard": [
    {
      "rank": 1,
      "strategy_name": "低估值成长股",
      "author": "user123",
      "is_public": true,
      "win_rate_1y": 0.71,
      "sharpe_1y": 1.85,
      "max_drawdown_1y": 0.12,
      "follower_count": 234,
      "last_updated": "2026-04-20"
    }
  ],
  "total_public_strategies": 1820
}
```

---

## 数据来源

- 依赖 DataFetcherAgent 获取历史行情数据（用于回测期间的收益计算）
- 依赖 StockScreenerAgent 在历史各时间点运行策略，获取历史选股名单
- 策略和回测结果存储在数据库（PostgreSQL / SQLite）

---

## 核心逻辑/算法

### 1. 回测流程

```
历史时间序列（按月）
  → 在每个调仓日，用 StockScreenerAgent 运行历史快照数据
  → 获取该时点的选股名单
  → 计算持有 N 天后的收益（相对基准）
  → 统计胜率（跑赢基准的次数 / 总次数）
```

**胜率定义**：策略所选股票组合，在持有期内等权重收益 > 沪深 300 同期收益的比例

### 2. 风险指标计算

| 指标 | 计算方式 |
|------|---------|
| 夏普比率 | (年化收益 - 无风险利率) / 年化波动率 |
| 最大回撤 | max(1 - 当前净值 / 历史最高净值) |
| 胜率 | 跑赢基准的调仓周期数 / 总调仓周期数 |

### 3. 排行榜排序规则

默认按 1 年夏普比率降序，用户可切换到其他维度

### 4. 策略 Fork

复制策略条件到新 strategy_id，记录 parent_strategy_id，允许用户在此基础上添加/删除条件后重新回测

---

## 与其他 Agent 的交互

- **依赖**：StockScreenerAgent（历史选股）、DataFetcherAgent（历史行情）
- **被依赖**：OrchestratorAgent（展示排行榜/回测结果）

---

## Prompt 设计要点

- 回测计算全部用代码实现（pandas / numpy），LLM 不参与数值计算
- LLM 用于：生成回测结果的自然语言解读（"这个策略在熊市中表现较差，主要因为..."）
- 排行榜描述：LLM 为每个公开策略生成一句话摘要

---

## 数据库设计（规划）

```sql
-- 策略表
strategies(id, user_id, name, conditions_json, is_public, parent_id, created_at)

-- 回测结果表
backtest_results(strategy_id, window, win_rate, sharpe, max_drawdown, computed_at)

-- 排行榜缓存（每日更新）
leaderboard_cache(strategy_id, rank, window, score, updated_at)
```

---

## 示例

**用户操作**：将"低估值成长股"策略设为公开，查看其在 1 年维度的胜率排名

**处理流程**：
1. 标记策略为 `is_public = true`
2. 回测引擎计算 1 年内每月调仓的历史胜率
3. 与其他公开策略比较，当前策略排名第 3（1 年胜率 71%）
4. 展示在排行榜第 3 位
