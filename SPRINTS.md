# AIStock Sprint 执行记录

> 里程碑总览 — 最后更新：2026-04-22

## 总体进度

```
Sprint 1  ✅  浏览器里能看到报告骨架（2个维度）
Sprint 2  ✅  Phase 1 完整：多维度 + 综合结论        ← 第一个可展示版本
Sprint 3  ✅  盈利预测 + 上下游 + 催化剂修复
Sprint 4  ✅  大牛分析（KOLAgent）+ 用户自定义       ← 11维度全部实现
Sprint 5  ✅  整体打磨：UI / 文档 / 维度同步
Sprint 6  ✅  导出 + 评分 + 耗时显示                 ← 完整产品交付
Sprint 7  ✅  可视化升级 + 响应式 CSS               ← 当前最新
```

---

## Sprint 1 ✅ — 报告骨架

**目标**：浏览器里能看到报告骨架（2个维度）

**交付物：**
- `src/models/report.py` — ReportSection dataclass，统一输出格式
- `src/agents/business.py` — BusinessAgent（主营业务）
- `src/agents/industry.py` — IndustryAgent（行业分析 + 同行公司列表）
- `src/agents/report_orchestrator.py` — 骨架，Step1 并行调度 + yield 流式
- `app.py` — Streamlit 界面，pending 占位卡片，综合结论 banner

**关键技术决策：**
- ThreadPoolExecutor + Generator yield 实现流式逐节展示
- 每个 Agent 独立 MD5 缓存 key（code + model + 日期）

---

## Sprint 2 ✅ — Phase 1 完整

**目标**：多维度 + 综合结论，第一个可展示版本

**交付物：**
- `src/agents/performance.py` — PerformanceAgent（8年财务趋势 + PE/PB分位）
- `src/agents/peer.py` — PeerAgent（同行对比，★标注目标股）
- `src/agents/catalyst.py` — CatalystAgent（最新催化剂）
- `src/agents/shareholder.py` — ShareholderAgent（持股结构 + 机构 + 散户情绪）
- `src/agents/management.py` — ManagementAgent（减持/增持/质押信号）
- `src/data/report_parser.py` — pdfplumber 解析东财研报 PDF
- `src/agents/research.py` — ResearchAgent（多研报聚合：共识/分歧/目标价）
- `src/agents/report_synthesizer.py` — ReportSynthesizer（看多/中性/看空评级）
- `app.py` — 新增多股对比 Tab

**关键技术修复：**
- `push2.eastmoney.com` 代理失效 → 全系引入 `stock_yjbb_em` fallback
- `_get_recent_report_date()` 修复：4月应取上年Q3（A股 Q4 披露截止4月30日）

**验证（贵州茅台 600519）：**
- 全维度生成成功，置信度 0.9–1.0
- 综合评级：【中性偏多】，PE 20.6 处于近5年0%分位

---

## Sprint 3 ✅ — 盈利预测 + 上下游 + 催化剂修复

**目标**：补全 Phase 3 新维度，修复已知接口问题

**交付物：**
- `src/agents/forecast.py` — ForecastAgent（东财分析师一致预期 + EPS 2025–2028）
- `src/agents/supply_chain.py` — SupplyChainAgent（产业链位置 + 议价权 + LLM知识）
- `src/data/akshare_client.py` — `fetch_news` 替换为 `stock_individual_notice_report`
- `src/data/akshare_client.py` — 新增 `fetch_forecast`（`stock_profit_forecast_em`）
- CatalystAgent confidence 从 0.4 → 1.0

---

## Sprint 4 ✅ — 大牛分析

**目标**：KOLAgent 上线，支持默认 + 用户自定义大牛名单

**交付物：**
- `src/data/kol_config.py` — 8位默认KOL档案（段永平/张坤/但斌/林园/傅鹏博/丘栋荣/朱少醒/邱国鹭）
- `src/agents/kol.py` — KOLAgent（默认/自定义两种模式，区分"有据"vs"风格推断"）
- `app.py` 高级选项 — 自定义大牛名单输入框（逗号分隔，库中无的自动 LLM 补充）

---

## Sprint 5 ✅ — 整体打磨

**目标**：代码/文档/UI 与 11维度现状对齐

**交付物：**
- `app.py` — 首页维度表更新（全部11个，去掉"状态"列，加入大牛提示）
- `app.py` — caption、注释、COMPARE_DIMS 更新
- `report_synthesizer.py` — 系统提示更新，置信度分母 5 → 8
- `report_orchestrator.py` — 文档注释更新为 Step 1–4

---

## Sprint 6 ✅ — 报告导出 + 用户评分 + 耗时显示

**目标**：完整产品体验，用户可导出报告、评价内容质量

**交付物：**
- **⏱ 各维度耗时**：每节卡片显示该维度完成时的累计耗时
- **👍/👎 评分**：每节内容下方评分按钮，session_state 保存
- **⬇️ Markdown 导出**：报告完成后一键下载完整报告文件
- `app.py` 整体重构：CSS 优化，布局更清晰，移除废弃占位逻辑

---

## Sprint 7 ✅ — 可视化升级 + 响应式 CSS

**目标**：图表从无到有，收入结构可视化，业绩仪表盘，移动端适配

**交付物：**

### 公司概况升级（BusinessAgent）
- 分析维度：公司定位 / 收入结构 / 客户集中度 / 供应商依赖 / 竞争定位
- 输出 HTML：`<span class="bull">` 利好 / `<span class="bear">` 利空 / `<strong>` 关键数字
- `src/models/report.py` — `ReportSection` 新增 `chart_data: Optional[dict]`；维度标题 `"business"` → `"公司概况"`
- `src/data/akshare_client.py` — 新增 4 个工具函数：
  - `_ak_market_prefix(code)` → `"SH600519"` / `"SZ301590"`
  - `_latest_annual_rows(df)` → 过滤到最新 12-31 年报行
  - `_normalize_pct(v)` → 小数 0.867 → 86.77%（检测 `≤1`）
  - `_normalize_yi(v)` → 元 1.46e11 → 1460 亿（检测 `>1e6`）
  - 重写 `fetch_business_overview()`：主营构成 + 地区构成 + 主营介绍，数据归一化

### 收入结构横向条形图（替代饼图）
- `src/agents/business.py` — 新增 `_build_chart_data()`，输出 `{"revenue_pie": {"product": [...], "region": [...]}}`
- `app.py` — 新增 `_render_revenue_bars()`：横向 `go.Bar`，标签外显示"占比% 毛利率%"，最大项高亮蓝色

### 过往业绩仪表盘（先图后文）
- `src/agents/performance.py` — 新增解析函数 `_parse_yi()` / `_parse_pct()` / `_parse_eps()`，新增 `_build_chart_data(records, val_pct)` 输出 `financial_bars` + `valuation_percentile`
- `app.py` — 新增 `_render_performance_dashboard()`：
  - 5列 metric cards（营收 / 毛利率 / ROE / 资产负债率 / 当前PE分位）
  - HTML 数据表（5年×7指标，增速/负债率条件着色）
  - 2×2 趋势图：营收柱状图 / 毛利率+净利率折线 / ROE 面积图 / EPS 柱状图
- 渲染顺序：**先图后文**（仪表盘 → LLM 文字结论）

### 响应式 CSS（方案 A：CSS media queries）
- 手机端 `≤768px`：多列强制堆叠（`flex-direction: column`），边距缩减，metric 字号缩小
- 平板端 `769–1024px`：左边距调整
- 无需服务端 User-Agent 检测，纯 CSS 实现

### 自测试工具链
- `scripts/test_chart_data.py` — CLI 诊断：打印 AKShare 原始列名 + `_build_chart_data` 解析结果，支持 `--all/--raw/--revenue/--perf`
- `scripts/debug_charts.py` — Standalone Streamlit 调试页（3 Tab：业绩仪表盘 / 收入结构 / 粘贴自定义 JSON）

**关键 Bug 修复：**
- `stock_zygc_em` 返回全量历史 92 条 → `_latest_annual_rows()` 过滤到最新年报
- pct/gm 为小数（非百分比）→ `_normalize_pct()` 自动转换
- 收入为元（非亿元）→ `_normalize_yi()` 自动转换
- 旧缓存无 `chart_data` → 用"强制刷新"选项清除

**依赖新增：**
- `requirements.txt` — `plotly>=5.18.0`，`streamlit>=1.35.0`
