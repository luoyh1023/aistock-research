# AIStock 项目进展记录

> 最后更新：2026-04-23

---

## 当前阶段：Sprint 8 进行中 🚧

**视觉设计系统全面重构（Design System Sprint）— 公司概况板块重设计完成，过往业绩待确认后实现。**

---

## Sprint 执行状态

| Sprint | 目标 | 状态 | 完成日期 |
|--------|------|------|---------|
| Sprint 1 | 报告骨架（Business + Industry，Streamlit 界面） | ✅ 完成 | 2026-04 |
| Sprint 2 | Phase 1 完整：多维度 + 综合结论，第一个可展示版本 | ✅ 完成 | 2026-04 |
| Sprint 3 | 盈利预测 + 上下游分析上线，修复催化剂新闻接口 | ✅ 完成 | 2026-04-22 |
| Sprint 4 | KOLAgent 大牛分析上线，支持用户自定义大牛名单 | ✅ 完成 | 2026-04-22 |
| Sprint 5 | 整体打磨：UI 更新、文档同步、11维度全部启用 | ✅ 完成 | 2026-04-22 |
| Sprint 6 | 报告导出、用户评分、各维度耗时显示 | ✅ 完成 | 2026-04-22 |
| Sprint 7 | 可视化升级（收入结构横向条形图 + 业绩仪表盘）+ 响应式 CSS | ✅ 完成 | 2026-04-22 |
| Sprint 8 | Design System 重构：公司概况重设计 + 调试模式 + 布局留白 JS 修复 | 🚧 进行中 | 2026-04-23 |

---

## 已实现维度全览

| # | 维度 | Agent | 数据来源 | 置信度范围 |
|---|------|-------|---------|-----------|
| ① | 公司概况 | BusinessAgent | 东财主营构成(zygc) + 同花顺主营介绍(zyjs) + 财务摘要 | 1.0 |
| ② | 行业分析 | IndustryAgent | stock_yjbb_em 行业分类 | 1.0 |
| ③ | 过往业绩 | PerformanceAgent | 财务三表 + 估值历史 | 1.0 |
| ④ | 同行对比 | PeerAgent | yjbb 同行业全量快照 | 1.0 |
| ⑤ | 股东分析 | ShareholderAgent | 东财前十大股东 + 机构持股 | 0.9–1.0 |
| ⑥ | 最新催化剂 | CatalystAgent | 东财重大公告（替代失效新闻接口） | 1.0 |
| ⑦ | 管理层情况 | ManagementAgent | 公告 + 增减持 + 质押 | 0.9 |
| ⑧ | 研究报告 | ResearchAgent | 东财研报 PDF（pdfplumber） | 1.0 |
| ⑨ | 盈利预测 | ForecastAgent | 东财分析师一致预期 | 1.0 |
| ⑩ | 上下游情况 | SupplyChainAgent | 公司信息 + LLM 产业知识 | 0.7 |
| ⑪ | 大牛分析 | KOLAgent | 内置KOL档案 + LLM知识库 | 0.6 |
| 📋 | 综合结论 | ReportSynthesizer | 全部维度输出 | 1.0 |

---

## Sprint 各阶段交付详情

### Sprint 1 ✅ — 报告骨架
- `src/models/report.py` — ReportSection dataclass
- `src/agents/business.py` — BusinessAgent
- `src/agents/industry.py` — IndustryAgent（含同行公司列表）
- `src/agents/report_orchestrator.py` — 流式调度骨架
- `app.py` — Streamlit 界面，pending 占位、banner

### Sprint 2 ✅ — Phase 1 完整
- `src/agents/performance.py` — PerformanceAgent
- `src/agents/peer.py` — PeerAgent
- `src/agents/catalyst.py` — CatalystAgent
- `src/agents/shareholder.py` — ShareholderAgent
- `src/agents/management.py` — ManagementAgent
- `src/data/report_parser.py` — 研报 PDF 解析（pdfplumber）
- `src/agents/research.py` — ResearchAgent
- `src/agents/report_synthesizer.py` — 综合结论
- `app.py` — 多股对比 Tab

### Sprint 3 ✅ — 盈利预测 + 上下游 + 催化剂修复
- `src/agents/forecast.py` — ForecastAgent（东财分析师一致预期）
- `src/agents/supply_chain.py` — SupplyChainAgent（产业链分析）
- 修复 `fetch_news`：`stock_news_em` → `stock_individual_notice_report`（confidence 0.4→1.0）
- 新增 `fetch_forecast`：`stock_profit_forecast_em` 全量过滤

### Sprint 4 ✅ — 大牛分析
- `src/data/kol_config.py` — 8位默认KOL档案（段永平/张坤/但斌/林园等）
- `src/agents/kol.py` — KOLAgent，支持默认/自定义两种模式
- `app.py` 高级选项：自定义大牛名单输入框（逗号分隔）

### Sprint 5 ✅ — 整体打磨
- 首页维度表更新为全部 11 个，去掉"状态"列
- Synthesizer 系统提示更新为涵盖全部维度
- Synthesizer 置信度分母从 5 → 8
- Orchestrator 注释更新，COMPARE_DIMS 更新

### Sprint 6 ✅ — 报告导出 + 用户评分 + 耗时显示
- 各维度卡片显示累计耗时（⏱ Xs）
- 每节 👍/👎 评分按钮（session_state 保存）
- 报告完成后出现 Markdown 下载按钮
- app.py 整体重构，CSS 优化

### Sprint 7 ✅ — 可视化升级 + 响应式 CSS

**核心变更：**

**① 公司概况升级（BusinessAgent → 公司概况）**
- 分析维度扩展：公司定位 / 收入结构 / 客户集中度 / 供应商依赖 / 竞争定位
- LLM 输出格式：HTML，`<span class="bull">`（红） / `<span class="bear">`（绿） / `<strong>`
- `src/models/report.py` — 新增 `chart_data: Optional[dict]` 字段，`DIMENSION_TITLES["business"]` → `"公司概况"`
- `src/data/akshare_client.py` — 新增 `_ak_market_prefix()` / `_latest_annual_rows()` / `_normalize_pct()` / `_normalize_yi()`；重写 `fetch_business_overview()`，pct/gm 归一化为百分比，收入归一化为亿元

**② 收入结构横向条形图（替代饼图）**
- `src/agents/business.py` — 新增 `_build_chart_data()`，生成 `revenue_pie.product` / `revenue_pie.region`
- `app.py` — 新增 `_render_revenue_bars()`，横向条形图 + 毛利率标注，"其他(补充)"行过滤

**③ 过往业绩仪表盘（先图后文）**
- `src/agents/performance.py` — 新增 `_parse_yi()` / `_parse_pct()` / `_parse_eps()`，新增 `_build_chart_data(records, val_pct)`
- `app.py` — 新增 `_render_performance_dashboard()`：5列 metric cards + HTML 数据表 + 2×2 趋势图（营收柱 / 毛利净利率折线 / ROE 填充折线 / EPS 柱）
- 渲染顺序：图表 → LLM 文字（先图后文）

**④ 响应式 CSS（方案 A）**
- 手机端（≤768px）：多列 → 单列堆叠，`flex-direction: column`，边距缩减
- 平板端（769–1024px）：边距调整
- 仅通过 CSS media queries，无需服务端 User-Agent 检测

**⑤ 自测试工具链**
- `scripts/test_chart_data.py` — CLI 诊断脚本，打印 AKShare 原始列名 + chart_data 解析结果
- `scripts/debug_charts.py` — Standalone Streamlit 调试页，3 Tab：业绩仪表盘 / 收入结构 / 自定义 JSON

**关键 Bug 修复：**
- `stock_zygc_em` 返回全量历史（92条） → `_latest_annual_rows()` 过滤到最新 12-31 年报
- pct/gm 为小数（0.867） → `_normalize_pct()` 检测 `≤1` 则乘以 100
- 收入为元（1.46e11） → `_normalize_yi()` 检测 `>1e6` 则除以 1e8

---

### Sprint 8 🚧 — Design System 重构（进行中）

**设计目标：** 轻量现代浅色风格，内容结构清晰，图文不割裂，响应式适配。

**已完成：**

**① 布局留白 JS 修复**
- CSS `!important` 无法覆盖 Streamlit 1.56 Emotion 内联样式
- 方案：`_apply_layout_margins()` 通过 `st.components.v1.html()` 注入 JS，用 `window.parent.document.querySelector('.block-container').style.setProperty()` 直接操作父 DOM
- MutationObserver 监听 `style` 属性变化，Streamlit 重渲染后自动重新应用

**② 右侧 TOC + IntersectionObserver 滚动联动**
- `_render_toc_component()` 通过零高度 iframe 注入 JS + CSS 到父页面
- 轮询检测 `[id^="section-"]` 元素出现，绑定 IntersectionObserver 高亮当前节
- 宽屏（≥1400px）显示，窄屏自动隐藏

**③ 公司概况板块全面重设计**

*内容结构（线框图已确认）：*
```
[档案栏 chips]  行业 · 市值 X亿 · 员工 N · 上市 YYYY-MM
────────────── 分隔线 ──────────────
[公司定位]  1-2句精炼定位（bull/bear/strong HTML标注）
[主营业务与核心产品]  2-4条 bullet
[三格风险评分卡]  客户集中度 / 供应商依赖 / 竞争壁垒
──── 主营收入结构 ────
[产品结构饼图]  [地区结构饼图]  （pastel 色系，并排）
```

*技术实现：*
- `business.py` SYSTEM_PROMPT + ANALYSIS_PROMPT 约束 LLM 严格按 HTML 模板输出
- LLM 输出后用 `re.sub` 裁剪首尾 ` ```html ``` ` 代码围栏（Bug 修复）
- `company_meta` 取自 `stock_individual_info_em`，降级兜底：yjbb 快照补充行业+总市值
- 饼图 `_render_revenue_pies()`：产品蓝系 pastel / 地区青绿系 pastel，白色分割线
- `render_section()` business 分支：chips → 分隔线 → LLM HTML → bridge → 双饼图
- CSS 新增：`.info-chips` / `.info-chip` / `.biz-positioning` / `.risk-grid` / `.risk-card[.low|.mid|.high]`

**④ 调试模式（⚙️ 高级选项）**
- `ReportOrchestrator.run()` 新增 `debug_dims: set[str] | None` 参数
- `_active(dim)` / `_fr(dim)` 辅助函数：跳过非选中维度，对选中维度 force_refresh
- UI：调试模式 checkbox + 维度 multiselect（默认选"公司概况"）
- 效果：单维度调试 5–10 秒出结果，不用等全套 2–3 分钟

**⑤ 过往业绩仪表盘升级（已完成渲染层，待视觉确认）**
- KPI 卡片行：年度营收 / 净利润（含同比计算）/ 净利率 / ROE / PE分位
- 表格：新增净利润行，共8行指标
- 图表细杆化：`bargap=0.38`（营收&净利润分组并排）、`bargap=0.55`（EPS）
- 净利率折线改为点虚线区分毛利率

**待确认 / 进行中：**
- 过往业绩视觉效果用户尚未确认（上一版截图只看了公司概况）
- 概念标签（需 AKShare 数据源评估，暂缓）

---

## 待优化 Backlog（后续迭代）

### SupplyChainAgent 深化
1. **产业链结构图**：以文本图形式呈现完整产业链，标注公司位置：
   ```
   [上游：高粱/小麦供应商] → [茅台：酿造/陈化/灌装] → [下游：经销商/直销]
   ```
2. **议价权与护城河量化**：不只描述"是什么"，要分析"有多深"——对上游锁价能力、对下游定价权、核心技术壁垒是否可替代

### 右侧 TOC 目录 + 滚动联动高亮
用户已提出需求：右侧固定目录，点击标题跳转；左侧内容滑动时右侧高亮联动。
Streamlit 限制：无原生锚点跳转 + 无滚动事件监听，需通过注入 JS 实现，方案待评估。

### 已知技术问题
| 接口 | 状态 | 影响 | 长期方案 |
|------|------|------|---------|
| `push2.eastmoney.com` | ❌ 代理不稳定 | fallback 到 yjbb，影响数据丰富度 | 待评估直连方案或备用源 |
| `stock_news_em` | ❌ 正则错误 | 已用公告接口替代 | 等 AKShare 修复 |

### 可扩展功能
- 报告历史追踪（同一股票跨时间对比）
- 用户评分数据持久化（当前仅 session）
- 港股 / 美股支持（yfinance 已接入，需各 Agent 适配）
- Web 界面迁移评估（Streamlit 已满足需求，暂缓）
