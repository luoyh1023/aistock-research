# AIStock 问题追踪

> 创建日期：2026-04-28 | 来源：集成测试 + 代码审查

优先级定义：
- **P0** — 功能失效，用户完全无法使用
- **P1** — 功能受损，核心展示有明显缺陷
- **P2** — 数据缺失/质量偏低，但有降级兜底
- **P3** — 体验优化，不影响核心功能

状态：`open` / `in-progress` / `resolved`

---

## 🔴 P1 — 高优先级

### #001 — `synthesis` 最终结论在 Streamlit rerun 后可能消失
- **状态**：open
- **发现**：Phase 2 UI 验证，301590 优优绿能（用户复现）
- **现象**：报告生成时页面正常，用户交互（如研报区按钮点击）触发 Streamlit rerun 后，综合结论一节不显示
- **根因**：`st.session_state["_report_sections"]` 在 `orchestrator.run()` 完全结束后才写入；synthesis 是最后生成的维度。若用户在 synthesis 生成前触发 rerun，session state 中不含 synthesis，重渲染时该节为空
- **影响**：synthesis 是最终投资判断，缺失影响报告可用性
- **修复方向**：改为流式写入 session state（每生成一个 section 就写入），不等全部完成

---

### #002 — `business.company_meta` 缺少核心字段
- **状态**：open
- **发现**：Phase 2 CLI 验证，600036 招商银行
- **现象**：`chart_data["company_meta"]` 只有 `{"industry": "银行Ⅱ"}`，缺少 `name`、`market_cap`、`pe`、`pb`、`employees`、`list_date` 等字段，UI 报告头部 chip 标签显示"—"
- **根因**：`BusinessAgent._build_company_meta()` 未从 `info` / `financials` 字段中提取并写入这些 key
- **影响**：公司概况模块顶部标签信息全空，影响第一印象
- **修复方向**：检查 `_build_company_meta()` 实现，补全字段映射

---

### #003 — `industry.peer_table` 市值/PE/毛利率全为"—"
- **状态**：open
- **发现**：Phase 2 CLI 验证，600036 招商银行（银行股）；预计其他行业亦存在
- **现象**：同行对比表 16 行数据中，`mv`、`pe`、`gm` 全为"—"，只有 `roe` 有值；同行对比表中有效列太少，信息密度不足
- **根因**：`screener_snapshot` 数据未能成功映射到 `peer_table` 的 `mv`/`pe`/`gm` 字段；银行股 `gm`（毛利率）本身不适用，但 `mv`/`pe` 应有数据
- **影响**：行业对比表实质上只剩 ROE 一列，分析价值大幅降低
- **修复方向**：排查 `IndustryAgent._build_peer_table()` 中 screener 数据的字段映射逻辑

---

## 🟡 P2 — 中优先级

### #004 — `management.board_members` 对银行股返回空
- **状态**：open
- **发现**：Phase 2 CLI 验证，600036 招商银行
- **现象**：`board_members = []`（0 人），LLM 用通识知识补充了内容，但无结构化高管数据
- **根因**：AKShare 高管接口（`stock_zh_a_gdhs` 或类似接口）对部分银行股/大盘股返回空列表
- **影响**：管理层模块缺乏具体人名、职务；conf ≈ 0.5~0.6 偏低
- **修复方向**：补充备用接口（如 `ak.stock_individual_info_em` 或年报解析）；或扩大降级时的 LLM 提示以明确标注数据不足

---

### #005 — `business.concept_tags` 为空
- **状态**：open
- **发现**：Phase 2 CLI 验证，600036 招商银行
- **现象**：`concept_tags = []`，公司概况无概念板块标签
- **根因**：AKShare 概念接口对银行等传统行业返回空，或 BusinessAgent 未调用概念数据
- **影响**：首屏缺少概念标签，对于偏主题投资的用户信息不完整
- **修复方向**：在 `BusinessAgent` 中引入 `concepts` 数据类型，映射为 `concept_tags`

---

### #006 — `industry.fund_flow` 行业资金流向数据 `found=False`
- **状态**：open
- **发现**：Phase 2 CLI 验证，600036 招商银行（`银行Ⅱ`）
- **现象**：`fund_flow = {"行业": "银行Ⅱ", "found": False, "今日": None, ...}`，行业资金流向数据获取失败
- **根因**：`fetch_industry_fund_flow()` 用行业名称匹配东财行业资金流排行，但"银行Ⅱ"在东财的二级分类命名与排行表中的一级分类名不一致，导致 match 失败
- **影响**：行业分析模块缺少资金流向色块（模块 D）
- **修复方向**：扩展名称匹配逻辑（模糊匹配 / 一二级行业映射表）

---

### #007 — 集成测试中 600519/300750 因代理闪断 FATAL
- **状态**：open（环境问题，非代码 bug）
- **发现**：Phase 1 CLI 批量测试
- **现象**：`claude -p` CLI 收到 `ECONNRESET`，IndustryAgent 超时（300s），整个股票分析失败
- **根因**：测试时代理网络瞬间断开（同时段 push2.eastmoney.com 也出现 ProxyError），属于环境不稳定
- **影响**：重跑可恢复，数据已缓存；但说明应用对代理不稳定没有重试机制
- **修复方向**：`model_router.py` 的 `claude -p` 调用增加超时重试（retry 1-2 次）；或为 LLM 调用增加 fallback 到 API 模式

---

## 🔵 P3 — 低优先级 / 待规划

### #008 — `performance.financial_bars` 缺少经营现金流数据
- **状态**：open
- **来源**：ARCHITECTURE.md
- **现象**：过往业绩图表无现金流趋势
- **接口**：`ak.stock_cash_flow_sheet_by_report_em()`（待接入）

---

### #009 — 管理层高管背景标签未实现
- **状态**：open（Sprint 10 规划项）
- **来源**：ARCHITECTURE.md
- **现象**：⑤ 管理层只有姓名/职务，无华为系/阿里系等背景标签，无"管理层DNA × 战略匹配度"评分
- **接口**：年报 PDF 解析（待调研）

---

### #010 — 行业景气指数未接入
- **状态**：open
- **来源**：ARCHITECTURE.md
- **现象**：⑥ 行业分析无行业整体 PE / 近期板块 K 线走势
- **接口**：`ak.stock_board_industry_hist_em()`（待接入）

---

### #011 — 港股研报无法自动获取
- **状态**：open（已知数据源限制）
- **来源**：Phase 1 测试，文档已记录于 ARCHITECTURE.md + docs/agents/03_data_fetcher.md
- **现象**：00700/01810/03690 研报维度 conf=0.0，提示"可手动上传 PDF"
- **根因**：东财研报 API 仅覆盖 A 股
- **修复方向**：引导用户手动上传 PDF（已支持）；长期可接入 HKEX 或彭博港股研报

---

### #012 — 港股公告/催化剂数据稀缺
- **状态**：open（已知数据源限制）
- **来源**：Phase 1 测试，文档已记录
- **现象**：港股 catalyst/shareholder 维度 conf≈0.4
- **修复方向**：接入 HKEX 披露易公告 API（免费）

---

### #013 — KOL（大牛分析）模块暂搁置
- **状态**：暂缓（产品方向待确认）
- **来源**：用户决策
- **现象**：KOL 模块返回空节，UI 展示空白卡片
- **修复方向**：待产品方向确认后恢复；考虑重新定位为"知名基金经理持仓匹配"而非"KOL 观点"

---

## 已解决 ✅

| # | 问题 | 解决日期 | 方案 |
|---|------|---------|------|
| — | 研报模块 JSON 解析失败（LLM 输出含未转义双引号）| 2026-04 | 4 层解析策略 + SYSTEM_PROMPT 明确禁止 ASCII 双引号 |
| — | 催化剂模块 chart_data 为空（Streamlit 热更新未生效）| 2026-04 | 重启 Streamlit 服务 + 清除缓存 |
| — | 研报分析结果 Streamlit rerun 后消失（DuplicateElementKey）| 2026-04 | 将 `_new_analysis` 处理移至 render loop 之前 |
| — | 港股数据覆盖限制未文档化 | 2026-04 | 记录至 ARCHITECTURE.md + docs/agents/03_data_fetcher.md |
