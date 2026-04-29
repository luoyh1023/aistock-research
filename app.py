"""
AIStock — Streamlit 研究报告界面。

用法：
    streamlit run app.py

功能：
    - 单股模式：输入股票代码，流式展示 11 维度研究报告各节
    - 对比模式：输入 2-4 支股票，并行生成后横向对比关键维度
    - 综合结论生成前持续显示提示 banner
    - 各节标注数据完整度置信度
    - 高级选项：自定义大牛名单（KOL 分析）
    - 报告导出：完整 Markdown 一键下载
    - 用户评分：每节 👍/👎 反馈
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import streamlit as st

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    _PLOTLY_OK = True
except ImportError:
    _PLOTLY_OK = False

sys.path.insert(0, str(Path(__file__).parent))

# 加载 .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# ── 页面配置 ─────────────────────────────────────────────────

st.set_page_config(
    page_title="AIStock 研究报告",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 访问密码保护 ─────────────────────────────────────────────
_auth_pwd = st.secrets.get("auth", {}).get("password", "")
if _auth_pwd and not st.session_state.get("_authed"):
    st.markdown("## 🔒 AIStock 研究报告")
    _entered = st.text_input("访问密码", type="password", key="_pwd_input")
    if st.button("进入", type="primary"):
        if _entered == _auth_pwd:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("密码错误，请重试")
    st.stop()

st.markdown("""
<style>
/* ══════════════════════════════════════════════════
   设计 Token 系统
   ══════════════════════════════════════════════════ */
:root {
  /* Brand */
  --accent:          #1a73e8;
  --accent-dark:     #1557b0;
  --accent-light:    #e8f0fe;
  --accent-teal:     #00897b;
  --accent-purple:   #7b1fa2;
  --accent-gold:     #f4b942;

  /* A股配色：红涨绿跌 */
  --bull:            #D32F2F;
  --bear:            #00796B;

  /* 页面 & 卡片背景 */
  --surface-page:    #f0f2f6;
  --surface-card:    #ffffff;
  --surface-card-alt:#fafcff;
  --surface-hero:    #ffffff;

  /* 边框 */
  --border-subtle:   #e8eaed;
  --border-accent:   #1a73e8;
  --border-synthesis:#1557b0;

  /* 阴影 */
  --shadow-card:     0 1px 3px rgba(0,0,0,0.06), 0 4px 12px rgba(0,0,0,0.07);
  --shadow-hover:    0 4px 12px rgba(0,0,0,0.10), 0 8px 24px rgba(0,0,0,0.08);
  --shadow-hero:     0 2px 12px rgba(0,0,0,0.08);
  --shadow-input:    0 0 0 3px rgba(26,115,232,0.18);

  /* 圆角 */
  --radius-card:     12px;
  --radius-input:    10px;
  --radius-chip:     20px;
  --radius-badge:    6px;

  /* 间距 */
  --gap-section:     1rem;
  --pad-card:        1.1rem 1.4rem;

  /* 字色 */
  --text-primary:    #202124;
  --text-secondary:  #5f6368;
  --text-muted:      #9aa0a6;
  --text-light:      #bdc1c6;

  /* 过渡 */
  --transition:      0.18s ease;
}

/* ── 页面底色 ───────────────────────────────────── */
.stApp, [data-testid="stAppViewContainer"] {
    background: var(--surface-page) !important;
}
/* 左右各留 15vw，最大 280px；内容区自然居中 */
.main .block-container {
    background: transparent;
    max-width: 100% !important;
    padding-top: 1.2rem !important;
    padding-left:  clamp(1.5rem, 15vw, 280px) !important;
    padding-right: clamp(1.5rem, 15vw, 280px) !important;
    margin-left: auto !important;
    margin-right: auto !important;
    box-sizing: border-box !important;
}

/* ══════════════════════════════════════════════════
   Section 卡片（白底 + 阴影 + 顶部色条）
   ══════════════════════════════════════════════════ */
.section-card {
    background: var(--surface-card);
    border: 1px solid var(--border-subtle);
    border-top: 3px solid var(--accent);
    border-radius: var(--radius-card);
    padding: var(--pad-card);
    margin: var(--gap-section) 0;
    box-shadow: var(--shadow-card);
    transition: box-shadow var(--transition), transform var(--transition);
}
.section-card:hover {
    box-shadow: var(--shadow-hover);
    transform: translateY(-1px);
}
.section-card-synthesis {
    background: var(--surface-card-alt);
    border: 1px solid var(--border-subtle);
    border-top: 3px solid var(--border-synthesis);
    border-radius: var(--radius-card);
    padding: var(--pad-card);
    margin: var(--gap-section) 0;
    box-shadow: var(--shadow-card);
}

/* ── 卡片头部 ───────────────────────────────────── */
.section-title {
    font-weight: 700;
    font-size: 1.0rem;
    color: var(--text-primary);
    margin-bottom: 0.15rem;
    letter-spacing: 0.01em;
}
.section-meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.55rem;
    flex-wrap: wrap;
}
.confidence-warn {
    background: #fff3e0;
    color: #e65100;
    font-size: 0.74rem;
    font-weight: 500;
    padding: 1px 8px;
    border-radius: var(--radius-chip);
}
.elapsed-tag {
    color: var(--text-muted);
    font-size: 0.74rem;
}

/* ══════════════════════════════════════════════════
   Pending 骨架卡片（shimmer 动画）
   ══════════════════════════════════════════════════ */
.pending-card {
    background: var(--surface-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-card);
    padding: var(--pad-card);
    margin: var(--gap-section) 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    overflow: hidden;
    position: relative;
}
.pending-card::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(
        90deg,
        transparent 0%,
        rgba(255,255,255,0.65) 50%,
        transparent 100%
    );
    animation: shimmer 1.6s infinite;
    transform: translateX(-100%);
}
@keyframes shimmer {
    100% { transform: translateX(100%); }
}
.pending-label {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 0.55rem;
}
.pending-bar {
    height: 9px;
    background: var(--border-subtle);
    border-radius: 6px;
    margin: 0.38rem 0;
}
.pending-bar.w-90 { width: 90%; }
.pending-bar.w-72 { width: 72%; }
.pending-bar.w-52 { width: 52%; }

/* ══════════════════════════════════════════════════
   Hero 搜索区域
   ══════════════════════════════════════════════════ */
.hero-block {
    background: var(--surface-hero);
    border-radius: 16px;
    padding: 2rem 2.2rem 1.6rem;
    box-shadow: var(--shadow-hero);
    border: 1px solid var(--border-subtle);
    margin-bottom: 1.2rem;
}
.hero-title {
    font-size: 1.55rem;
    font-weight: 800;
    color: var(--text-primary);
    letter-spacing: -0.02em;
    margin-bottom: 0.3rem;
}
.hero-sub {
    font-size: 0.88rem;
    color: var(--text-secondary);
    margin-bottom: 1.4rem;
}

/* Hero 内输入框样式增强 */
.hero-block [data-testid="stTextInput"] input {
    font-size: 1.0rem !important;
    border-radius: var(--radius-input) !important;
    border: 1.5px solid var(--border-subtle) !important;
    background: #fafbfd !important;
    transition: border-color var(--transition), box-shadow var(--transition);
}
.hero-block [data-testid="stTextInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: var(--shadow-input) !important;
    background: #fff !important;
}
.hero-block [data-testid="stButton"] > button[kind="primary"] {
    border-radius: var(--radius-input) !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em !important;
    transition: background var(--transition), transform var(--transition) !important;
}
.hero-block [data-testid="stButton"] > button[kind="primary"]:hover {
    transform: translateY(-1px) !important;
}

/* ══════════════════════════════════════════════════
   A股 bull / bear 标注
   ══════════════════════════════════════════════════ */
.bull { color: var(--bull); font-weight: 600; }
.bear { color: var(--bear); font-weight: 600; }

/* ══════════════════════════════════════════════════
   HTML 报告节排版
   ══════════════════════════════════════════════════ */
.report-html {
    color: var(--text-primary);
}
.report-html h2 {
    font-size: 1.0rem;
    font-weight: 700;
    margin: 0.8rem 0 0.3rem;
    color: var(--text-primary);
}
.report-html h3 {
    font-size: 0.9rem;
    font-weight: 600;
    margin: 0.7rem 0 0.2rem;
    color: var(--text-secondary);
}
.report-html p {
    margin: 0.2rem 0 0.5rem;
    line-height: 1.75;
    font-size: 0.91rem;
}
.report-html ul {
    padding-left: 1.2rem;
    margin: 0.2rem 0 0.5rem;
}
.report-html li {
    margin-bottom: 0.28rem;
    line-height: 1.68;
    font-size: 0.91rem;
}
.report-html table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    margin: 0.6rem 0;
    border-radius: 8px;
    overflow: hidden;
}
.report-html th {
    background: var(--accent-light);
    color: var(--accent-dark);
    padding: 6px 10px;
    text-align: left;
    font-weight: 600;
}
.report-html td {
    padding: 5px 10px;
    border-bottom: 1px solid var(--border-subtle);
}
.report-html tr:last-child td { border-bottom: none; }
.report-html tr:hover td { background: #f8faff; }

/* ══════════════════════════════════════════════════
   公司概况 — 档案 Chips
   ══════════════════════════════════════════════════ */
.info-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-bottom: 0.85rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid var(--border-subtle);
}
.info-chip {
    background: var(--accent-light);
    color: var(--accent-dark);
    font-size: 0.73rem;
    font-weight: 500;
    padding: 2px 10px;
    border-radius: var(--radius-chip);
    white-space: nowrap;
}

/* ── 概念标签 chips ─────────────────────────────── */
.concept-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.5rem 0 0.6rem;
}
.concept-chip {
    background: #f3e5f5;
    color: #7b1fa2;
    font-size: 0.72rem;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: var(--radius-chip);
    white-space: nowrap;
}

/* ── 定位句（粗体大字）───────────────────────────── */
.report-html .biz-positioning {
    font-size: 0.98rem;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.78;
    margin: 0.3rem 0 0.9rem;
}

/* ── 风险三格卡 ─────────────────────────────────── */
.risk-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.65rem;
    margin: 0.9rem 0 0.6rem;
}
.risk-card {
    background: #f7f9ff;
    border-radius: 8px;
    padding: 0.7rem 0.85rem;
    border-top: 3px solid var(--border-subtle);
}
.risk-card.low  { border-top-color: var(--bear);  }
.risk-card.mid  { border-top-color: #f57c00;      }
.risk-card.high { border-top-color: var(--bull);  }
.risk-label {
    font-size: 0.82rem;
    color: var(--text-secondary);
    font-weight: 600;
    margin-bottom: 0.22rem;
}
.risk-level {
    font-size: 0.86rem;
    font-weight: 700;
    margin-bottom: 0.18rem;
}
.risk-card.low  .risk-level { color: var(--bear);  }
.risk-card.mid  .risk-level { color: #e65100;      }
.risk-card.high .risk-level { color: var(--bull);  }
.risk-desc {
    font-size: 0.75rem;
    color: var(--text-secondary);
    line-height: 1.45;
}

/* ══════════════════════════════════════════════════
   研报分析师预测表格
   ══════════════════════════════════════════════════ */
.analyst-table-wrap {
    overflow-x: auto;
    margin: 0.2rem 0 1rem;
}
.analyst-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.83rem;
}
.analyst-table thead th {
    background: #f8f9fa;
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 0.74rem;
    letter-spacing: 0.03em;
    padding: 7px 12px;
    text-align: left;
    border-bottom: 2px solid #e8eaed;
    white-space: nowrap;
}
.analyst-table tbody td {
    padding: 7px 12px;
    border-bottom: 1px solid #f1f3f4;
    vertical-align: middle;
}
.analyst-table tbody tr:hover td { background: #fafbff; }
.rating-buy    { color: var(--bull); font-weight: 600; }
.rating-hold   { color: #f57c00; font-weight: 600; }
.rating-sell   { color: var(--bear); font-weight: 600; }
.rating-other  { color: var(--text-secondary); }
.eps-cell      { font-variant-numeric: tabular-nums; font-size: 0.8rem; color: var(--text-primary); }
.eps-pe        { font-size: 0.72rem; color: var(--text-muted); margin-left: 3px; }

/* ══════════════════════════════════════════════════
   KPI 卡片行（过往业绩）
   ══════════════════════════════════════════════════ */
.kpi-row {
    display: flex;
    gap: 0.75rem;
    margin: 0.2rem 0 1.2rem;
}
.kpi-card {
    flex: 1;
    background: var(--surface-card);
    border-radius: var(--radius-card);
    padding: 0.85rem 1rem 0.75rem;
    box-shadow: var(--shadow-card);
    border-top: 3px solid #e8eaf6;
    min-width: 0;
}
.kpi-label {
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--text-secondary);
    letter-spacing: 0.04em;
    margin-bottom: 0.3rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.kpi-value {
    font-size: 1.55rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.02em;
    line-height: 1.1;
    margin-bottom: 0.25rem;
}
.kpi-delta {
    font-size: 0.74rem;
    font-weight: 500;
    padding: 2px 7px;
    border-radius: 20px;
    display: inline-block;
}
.kpi-delta.up   { background: #fce4e4; color: var(--bull); }
.kpi-delta.down { background: #e3f7f5; color: var(--bear); }
.kpi-delta.neutral { background: #f1f3f4; color: var(--text-secondary); }

/* ══════════════════════════════════════════════════
   图表-文字桥接标签
   ══════════════════════════════════════════════════ */
.chart-bridge {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin: 0.8rem 0 0.4rem;
    padding-top: 0.8rem;
    border-top: 1px solid var(--border-subtle);
}
.chart-bridge-label {
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--text-muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    white-space: nowrap;
}
.chart-bridge-line {
    flex: 1;
    height: 1px;
    background: var(--border-subtle);
}

/* ══════════════════════════════════════════════════
   对比模式
   ══════════════════════════════════════════════════ */
.compare-header {
    font-weight: 700;
    font-size: 1.0rem;
    text-align: center;
    padding: 0.45rem 0.6rem;
    background: var(--accent-light);
    color: var(--accent-dark);
    border-radius: var(--radius-badge);
    margin-bottom: 0.5rem;
}
.rating-row { display: flex; gap: 0.4rem; margin-top: 0.3rem; }

/* ══════════════════════════════════════════════════
   响应式 — 手机端（≤768px）
   ══════════════════════════════════════════════════ */
@media (max-width: 768px) {
    .main .block-container {
        padding-left: 0.75rem  !important;
        padding-right: 0.75rem !important;
        max-width: 100% !important;
    }
    .risk-grid { grid-template-columns: 1fr !important; }
    .info-chips { gap: 0.3rem; }
    .section-card,
    .section-card-synthesis {
        padding: 0.75rem 0.9rem;
        margin: 0.6rem 0;
    }
    .hero-block {
        padding: 1.2rem 1rem 1rem;
    }
    .hero-title { font-size: 1.2rem; }
    .section-title { font-size: 0.93rem; }
    .report-html table { font-size: 0.76rem; }
    .report-html th,
    .report-html td { padding: 3px 5px; }
    [data-testid="stColumns"] {
        flex-direction: column !important;
        gap: 0 !important;
    }
    [data-testid="stColumn"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 0 !important;
    }
    [data-testid="stMetric"] { padding: 0.35rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.0rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.68rem !important; }
    .rating-row { gap: 0.2rem; }
    h1 { font-size: 1.35rem !important; }
    h2 { font-size: 1.1rem !important; }
}

/* ── 平板端（769–1024px）───────────────────────── */
@media (min-width: 769px) and (max-width: 1024px) {
    .main .block-container {
        padding-left: 1.2rem !important;
        padding-right: 1.2rem !important;
    }
    .report-html table { font-size: 0.82rem; }
}
</style>
""", unsafe_allow_html=True)


# ── 维度展示顺序 ──────────────────────────────────────────────

DISPLAY_ORDER = [
    # 公司自身
    "business", "performance", "forecast", "shareholder", "management",
    # 行业（含上下游 + 同行对比）
    "industry",
    # 外部聚合
    "catalyst", "research", "kol",
    "synthesis",
]

DIMENSION_TITLES = {
    "business":    "① 公司概况",
    "performance": "② 过往业绩",
    "forecast":    "③ 盈利预测",
    "shareholder": "④ 股东分析",
    "management":  "⑤ 管理层情况",
    "industry":    "⑥ 行业分析",
    "catalyst":    "⑦ 最新催化剂",
    "research":    "⑧ 研究报告",
    "kol":         "⑨ 大牛分析",
    "synthesis":   "📋 综合结论",
}

# 全部维度均已实现
IMPLEMENTED_DIMS = {
    "business", "industry", "performance", "shareholder",
    "catalyst", "management", "research", "forecast",
    "kol", "synthesis",
}


from src.utils.stock_search import search_stocks, is_stock_code, resolve_input, _normalize as _norm_code


def normalize_code(code: str) -> str:
    return _norm_code(code) if code.strip() else code


# ── 报告导出 ─────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """剥除 HTML 标签，保留纯文本（用于 Markdown 导出）"""
    import re
    # <h2>/<h3> → ## / ###
    html = re.sub(r"<h2[^>]*>", "\n\n## ", html)
    html = re.sub(r"</h2>", "\n", html)
    html = re.sub(r"<h3[^>]*>", "\n\n### ", html)
    html = re.sub(r"</h3>", "\n", html)
    # <li> → "- "
    html = re.sub(r"<li[^>]*>", "- ", html)
    html = re.sub(r"</li>", "\n", html)
    # <p> → newline
    html = re.sub(r"<p[^>]*>", "\n", html)
    html = re.sub(r"</p>", "\n", html)
    # 所有其余标签剥除
    html = re.sub(r"<[^>]+>", "", html)
    # 清理多余空行
    html = re.sub(r"\n{3,}", "\n\n", html).strip()
    return html


def build_report_markdown(
    code: str,
    stock_name: str,
    sections: dict,
    elapsed: float,
) -> str:
    lines = [
        f"# {stock_name}（{code}）AI 研究报告",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"> 总耗时：{elapsed:.0f} 秒  ",
        f"> 由 AIStock 生成，仅供参考，不构成投资建议",
        "",
    ]
    for dim in DISPLAY_ORDER:
        section = sections.get(dim)
        if section and section.is_ok:
            title = DIMENSION_TITLES.get(dim, dim)
            lines.append(f"---\n\n## {title}\n")
            # 内容可能是 HTML（business/performance）或纯 Markdown
            content = section.content
            if content.strip().startswith("<"):
                content = _html_to_text(content)
            lines.append(content)
            if section.data_sources:
                lines.append(f"\n*数据来源：{' · '.join(section.data_sources)}*")
            lines.append("")
    return "\n".join(lines)


# ── 图表渲染 ─────────────────────────────────────────────────

_CHART_BG   = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
_GRID_COLOR = "#e8eaed"   # 与 --border-subtle 一致
_NO_BAR     = dict(displayModeBar=False)


def _render_revenue_pies(chart_data: dict):
    """
    公司概况：主营收入结构饼图（按产品 + 按地区），并排展示。
    """
    if not _PLOTLY_OK:
        return
    product = chart_data.get("product", [])
    region  = chart_data.get("region",  [])
    if not product and not region:
        return

    # 产品/地区各自配色系 — pastel 轻色调（Material Design 200/300 级）
    PALETTE_PROD = ["#90CAF9", "#64B5F6", "#BBDEFB", "#B3E5FC", "#E1F5FE", "#E8EAF6"]
    PALETTE_REG  = ["#80CBC4", "#4DB6AC", "#B2DFDB", "#A5D6A7", "#C8E6C9", "#E8F5E9"]

    def _one_pie(items: list[dict], palette: list[str]):
        names  = [d["name"] for d in items]
        values = [d.get("pct") or 0 for d in items]
        colors = [palette[i % len(palette)] for i in range(len(names))]

        fig = go.Figure(go.Pie(
            labels=names,
            values=values,
            marker=dict(
                colors=colors,
                line=dict(color="white", width=2.5),
            ),
            textinfo="label+percent",
            textfont=dict(size=11, color="#333333"),
            insidetextorientation="radial",
            hovertemplate="%{label}<br>占比: %{percent}<extra></extra>",
            hole=0,
        ))
        fig.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=16, b=20),
            showlegend=False,
            **_CHART_BG,
        )
        return fig

    sections = []
    if product: sections.append((product, "产品结构", PALETTE_PROD))
    if region:  sections.append((region,  "地区结构", PALETTE_REG))

    if len(sections) == 1:
        st.markdown(
            f'<p style="font-size:0.8rem;color:#5f6368;text-align:center;margin:0.5rem 0 0">{sections[0][1]}</p>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(_one_pie(sections[0][0], sections[0][2]), use_container_width=True, config=_NO_BAR)
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(
                f'<p style="font-size:0.8rem;color:#5f6368;text-align:center;margin:0.5rem 0 0">{sections[0][1]}</p>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_one_pie(sections[0][0], sections[0][2]), use_container_width=True, config=_NO_BAR)
        with c2:
            st.markdown(
                f'<p style="font-size:0.8rem;color:#5f6368;text-align:center;margin:0.5rem 0 0">{sections[1][1]}</p>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_one_pie(sections[1][0], sections[1][2]), use_container_width=True, config=_NO_BAR)


def _render_performance_dashboard(chart_data: dict):
    """
    过往业绩 dashboard：
      1. KPI 卡片行（营收 / 净利润 / 净利率 / ROE / PE分位）
      2. 近五年关键财务指标表格（含颜色）
      3. 趋势图 2×2 — 细杆风格：
         左上: 营收 & 净利润 并排细杆
         右上: 毛利率 / 净利率 折线
         左下: ROE 面积折线
         右下: EPS 细杆
    """
    if not _PLOTLY_OK:
        return

    bars  = chart_data.get("financial_bars", {})
    years = bars.get("years", [])
    if not years:
        return

    def _pad(lst):
        return list(lst) + [None] * max(0, len(years) - len(lst))

    revenue        = _pad(bars.get("revenue",        []))
    revenue_growth = _pad(bars.get("revenue_growth", []))
    net_profit     = _pad(bars.get("net_profit",     []))
    gross_margin   = _pad(bars.get("gross_margin",   []))
    net_margin     = _pad(bars.get("net_margin",     []))
    roe            = _pad(bars.get("roe",            []))
    debt_ratio     = _pad(bars.get("debt_ratio",     []))
    eps            = _pad(bars.get("eps",            []))

    val_pct = chart_data.get("valuation_percentile", {})
    pe_data = (val_pct or {}).get("pe") or {}

    def _latest(lst):
        for v in reversed(lst):
            if v is not None:
                return v
        return None

    def _fmt(v, suffix="", dec=1):
        return f"{v:.{dec}f}{suffix}" if v is not None else "—"

    # ── 1. KPI 卡片行 ─────────────────────────────────────────
    lat_rev     = _latest(revenue)
    lat_growth  = _latest(revenue_growth)
    lat_np      = _latest(net_profit)
    lat_nm      = _latest(net_margin)
    lat_roe     = _latest(roe)
    pe_cur      = pe_data.get("current")
    pe_pct_val  = pe_data.get("percentile")
    pb_data     = (val_pct or {}).get("pb") or {}
    pb_cur      = pb_data.get("current")
    pb_pct_val  = pb_data.get("percentile")

    # 计算净利润同比增速（最近两个有效值）
    np_valid = [(y, v) for y, v in zip(years, net_profit) if v is not None]
    np_growth = None
    if len(np_valid) >= 2:
        prev_v, cur_v = np_valid[-2][1], np_valid[-1][1]
        if prev_v and prev_v != 0:
            np_growth = (cur_v - prev_v) / abs(prev_v) * 100

    # PE-TTM: 取百度历史中最后一个值
    val_history = chart_data.get("valuation_history", {})
    pe_ttm_hist = val_history.get("pe_ttm", [])
    pe_ttm_cur  = pe_ttm_hist[-1]["value"] if pe_ttm_hist else None

    def _kpi_delta_html(val, suffix="", is_pct=False, invert=False) -> str:
        if val is None:
            return ""
        cls = "up" if (val > 0) != invert else "down"
        sign = "↑" if val > 0 else "↓"
        txt = f"{sign} {abs(val):.1f}{suffix}"
        return f'<span class="kpi-delta {cls}">{txt}</span>'

    def _kpi_neutral_html(txt: str) -> str:
        return f'<span class="kpi-delta neutral">{txt}</span>'

    kpi_cards = [
        ("年度营收",
         f"{lat_rev:.1f}亿" if lat_rev is not None else "—",
         _kpi_delta_html(lat_growth, "%") if lat_growth is not None else ""),
        ("净利润",
         f"{lat_np:.1f}亿" if lat_np is not None else "—",
         _kpi_delta_html(np_growth, "%") if np_growth is not None else ""),
        ("净利率",
         f"{lat_nm:.1f}%" if lat_nm is not None else "—",
         ""),
        ("ROE",
         f"{lat_roe:.1f}%" if lat_roe is not None else "—",
         ""),
        ("PE（年化）",
         f"{pe_cur:.1f}" if pe_cur is not None else "—",
         _kpi_neutral_html(f"近5年 {pe_pct_val:.0f}% 分位") if pe_pct_val is not None else ""),
        ("PE-TTM",
         f"{pe_ttm_cur:.1f}" if pe_ttm_cur is not None else "—",
         ""),
        ("PB",
         f"{pb_cur:.2f}" if pb_cur is not None else "—",
         _kpi_neutral_html(f"近5年 {pb_pct_val:.0f}% 分位") if pb_pct_val is not None else ""),
    ]

    cards_html = "".join(
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'{delta}'
        f'</div>'
        for label, value, delta in kpi_cards
    )
    st.markdown(f'<div class="kpi-row">{cards_html}</div>', unsafe_allow_html=True)

    # ── 2. 五年关键财务指标表格 ──────────────────────────────
    st.markdown(
        '<p style="font-size:0.82rem;font-weight:600;color:#5f6368;'
        'margin:1.2rem 0 0.4rem;letter-spacing:0.04em;">近五年关键财务指标</p>',
        unsafe_allow_html=True,
    )

    def _cell(v, suffix="", dec=1, color_fn=None) -> str:
        if v is None:
            return "<td style='color:#aaa'>—</td>"
        txt = f"{v:.{dec}f}{suffix}"
        cls = color_fn(v) if color_fn else ""
        if cls:
            return f'<td><span class="{cls}">{txt}</span></td>'
        return f"<td>{txt}</td>"

    header = "<tr><th>指标</th>" + "".join(
        f"<th style='text-align:center'>{y}</th>" for y in years
    ) + "</tr>"

    rows_def = [
        ("营收",         revenue,        "亿",   1, None),
        ("营收增速",     revenue_growth, "%",    1, lambda v: "bull" if v > 0 else "bear"),
        ("净利润",       net_profit,     "亿",   1, None),
        ("毛利率",       gross_margin,   "%",    1, None),
        ("净利率",       net_margin,     "%",    1, None),
        ("ROE",          roe,            "%",    1, None),
        ("资产负债率",   debt_ratio,     "%",    1, lambda v: "bear" if v > 65 else ""),
        ("EPS",          eps,            "元",   2, None),
    ]

    tbody = ""
    for label, vals, suffix, dec, color_fn in rows_def:
        cells = "".join(_cell(v, suffix, dec, color_fn) for v in vals)
        tbody += f"<tr><td><strong>{label}</strong></td>{cells}</tr>"

    table_html = (
        '<div class="report-html" style="overflow-x:auto;margin:0.2rem 0 1.2rem">'
        f'<table><thead>{header}</thead><tbody>{tbody}</tbody></table>'
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

    # ── 3. 趋势图表 2×2 — 细杆风格 ──────────────────────────
    # 共用 layout 参数
    _AXIS_STYLE = dict(gridcolor=_GRID_COLOR, zeroline=False)
    _X_STYLE    = dict(showgrid=False, tickfont=dict(size=10))
    _MARGIN     = dict(l=8, r=8, t=38, b=44)
    _H          = 268
    _LEGEND     = dict(orientation="h", y=-0.28, x=0.5, xanchor="center",
                       font=dict(size=11))

    c_l, c_r = st.columns(2)

    # 左上：营收 & 净利润 并排细杆
    with c_l:
        fig1 = go.Figure()
        fig1.add_trace(go.Bar(
            x=years, y=revenue, name="营收",
            marker=dict(color="#1a73e8", opacity=0.88),
            text=[_fmt(v, "亿") if v else "" for v in revenue],
            textposition="outside", textfont=dict(size=10, color="#5f6368"),
            hovertemplate="%{x}  营收 %{y:.1f}亿<extra></extra>",
        ))
        fig1.add_trace(go.Bar(
            x=years, y=net_profit, name="净利润",
            marker=dict(color="#00897b", opacity=0.82),
            text=[_fmt(v, "亿") if v else "" for v in net_profit],
            textposition="outside", textfont=dict(size=10, color="#5f6368"),
            hovertemplate="%{x}  净利润 %{y:.1f}亿<extra></extra>",
        ))
        fig1.update_layout(
            title=dict(text="营收 & 净利润（亿）", font=dict(size=12, color="#5f6368")),
            barmode="group",
            bargap=0.38,       # 组间距 → 条形更细
            bargroupgap=0.10,  # 组内间距
            height=_H,
            margin=_MARGIN,
            yaxis=dict(**_AXIS_STYLE),
            xaxis=_X_STYLE,
            legend=_LEGEND,
            **_CHART_BG,
        )
        st.plotly_chart(fig1, use_container_width=True, config=_NO_BAR)

    # 右上：毛利率 / 净利率 折线
    with c_r:
        fig2 = go.Figure()
        if any(v is not None for v in gross_margin):
            fig2.add_trace(go.Scatter(
                x=years, y=gross_margin, name="毛利率",
                mode="lines+markers",
                line=dict(color="#00897b", width=2), marker=dict(size=6),
                hovertemplate="%{x}  毛利率 %{y:.1f}%<extra></extra>",
            ))
        if any(v is not None for v in net_margin):
            fig2.add_trace(go.Scatter(
                x=years, y=net_margin, name="净利率",
                mode="lines+markers",
                line=dict(color="#1a73e8", width=2, dash="dot"), marker=dict(size=6),
                hovertemplate="%{x}  净利率 %{y:.1f}%<extra></extra>",
            ))
        fig2.update_layout(
            title=dict(text="毛利率 / 净利率（%）", font=dict(size=12, color="#5f6368")),
            height=_H,
            margin=_MARGIN,
            yaxis=dict(**_AXIS_STYLE, ticksuffix="%"),
            xaxis=_X_STYLE,
            legend=_LEGEND,
            **_CHART_BG,
        )
        st.plotly_chart(fig2, use_container_width=True, config=_NO_BAR)

    c_l2, c_r2 = st.columns(2)

    # 左下：ROE 面积折线
    with c_l2:
        fig3 = go.Figure(go.Scatter(
            x=years, y=roe,
            mode="lines+markers",
            line=dict(color="#7b1fa2", width=2), marker=dict(size=6),
            fill="tozeroy", fillcolor="rgba(123,31,162,0.07)",
            hovertemplate="%{x}  ROE %{y:.1f}%<extra></extra>",
        ))
        fig3.update_layout(
            title=dict(text="ROE（%）", font=dict(size=12, color="#5f6368")),
            height=_H,
            margin=_MARGIN,
            yaxis=dict(**_AXIS_STYLE, ticksuffix="%"),
            xaxis=_X_STYLE,
            **_CHART_BG,
        )
        st.plotly_chart(fig3, use_container_width=True, config=_NO_BAR)

    # 右下：EPS 细杆（bargap 大 → 条形更细）
    with c_r2:
        fig4 = go.Figure(go.Bar(
            x=years, y=eps,
            marker=dict(color="#f4a918", opacity=0.88),
            text=[_fmt(v, "元", 2) if v else "" for v in eps],
            textposition="outside", textfont=dict(size=10, color="#5f6368"),
            hovertemplate="%{x}  EPS %{y:.2f}元<extra></extra>",
        ))
        fig4.update_layout(
            title=dict(text="EPS（元/股）", font=dict(size=12, color="#5f6368")),
            bargap=0.55,   # 更细的杆
            height=_H,
            margin=_MARGIN,
            yaxis=dict(**_AXIS_STYLE),
            xaxis=_X_STYLE,
            **_CHART_BG,
        )
        st.plotly_chart(fig4, use_container_width=True, config=_NO_BAR)

    # ── 4. PE / PB 历史折线（百度日频数据）─────────────────────
    pe_ttm_records = val_history.get("pe_ttm", [])
    pb_records     = val_history.get("pb",     [])

    if pe_ttm_records or pb_records:
        c_pe, c_pb = st.columns(2)

        _MARGIN_VAL = dict(l=8, r=8, t=38, b=28)

        with c_pe:
            if pe_ttm_records:
                dates_pe = [r["date"] for r in pe_ttm_records]
                vals_pe  = [r["value"] for r in pe_ttm_records]
                fig_pe = go.Figure(go.Scatter(
                    x=dates_pe, y=vals_pe, name="PE-TTM",
                    mode="lines",
                    line=dict(color="#1a73e8", width=1.5),
                    hovertemplate="%{x}  PE-TTM %{y:.1f}<extra></extra>",
                ))
                # 中位线
                import statistics as _stats
                pe_median = _stats.median(v for v in vals_pe if v is not None)
                fig_pe.add_hline(
                    y=pe_median,
                    line_dash="dot", line_color="#aaa", line_width=1,
                    annotation_text=f"中位 {pe_median:.1f}",
                    annotation_position="right",
                    annotation_font_size=10,
                )
                fig_pe.update_layout(
                    title=dict(text="PE-TTM 历史", font=dict(size=12, color="#5f6368")),
                    height=_H,
                    margin=_MARGIN_VAL,
                    yaxis=dict(**_AXIS_STYLE),
                    xaxis=dict(showgrid=False, tickfont=dict(size=10)),
                    **_CHART_BG,
                )
                st.plotly_chart(fig_pe, use_container_width=True, config=_NO_BAR)

        with c_pb:
            if pb_records:
                dates_pb = [r["date"] for r in pb_records]
                vals_pb  = [r["value"] for r in pb_records]
                fig_pb = go.Figure(go.Scatter(
                    x=dates_pb, y=vals_pb, name="PB",
                    mode="lines",
                    line=dict(color="#00897b", width=1.5),
                    fill="tozeroy", fillcolor="rgba(0,137,123,0.06)",
                    hovertemplate="%{x}  PB %{y:.2f}<extra></extra>",
                ))
                pb_median = _stats.median(v for v in vals_pb if v is not None)
                fig_pb.add_hline(
                    y=pb_median,
                    line_dash="dot", line_color="#aaa", line_width=1,
                    annotation_text=f"中位 {pb_median:.2f}",
                    annotation_position="right",
                    annotation_font_size=10,
                )
                fig_pb.update_layout(
                    title=dict(text="PB（市净率）历史", font=dict(size=12, color="#5f6368")),
                    height=_H,
                    margin=_MARGIN_VAL,
                    yaxis=dict(**_AXIS_STYLE),
                    xaxis=dict(showgrid=False, tickfont=dict(size=10)),
                    **_CHART_BG,
                )
                st.plotly_chart(fig_pb, use_container_width=True, config=_NO_BAR)


# ── 渲染函数 ─────────────────────────────────────────────────

def _bridge(label: str):
    """在图表与文字之间插入带标签的分割线。"""
    st.markdown(
        f'<div class="chart-bridge">'
        f'<span class="chart-bridge-label">{label}</span>'
        f'<span class="chart-bridge-line"></span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _colorize_signals(text: str) -> str:
    """
    对 LLM 结论文字进行关键词着色（A股惯例：利好=红，利空=绿）。
    在 markdown 文本中插入 inline HTML span，Streamlit unsafe_allow_html 会原样保留。
    注意：长词优先替换，避免短词被重复包裹。
    """
    # 利好关键词 → 红色（var(--bull)）
    BULL_KWS = [
        "筹码集中", "主动加仓", "持续流入", "大幅增持", "显著增持",
        "积极信号", "集中趋势", "净增持", "增持", "买入",
        "看多", "看好", "偏多", "偏健康", "健康", "积极",
        "流入", "集中",
    ]
    # 利空关键词 → 绿色（var(--bear)）
    BEAR_KWS = [
        "筹码分散", "持续流出", "大幅减持", "显著减持",
        "风险信号", "分散趋势", "净减持", "减持", "卖出",
        "看空", "谨慎", "警惕", "偏空", "偏弱", "需警惕",
        "流出", "分散",
    ]
    replaced: set[str] = set()
    for kw in BULL_KWS:
        if kw in text and kw not in replaced:
            text = text.replace(kw, f'<span style="color:var(--bull);font-weight:600">{kw}</span>')
            replaced.add(kw)
    for kw in BEAR_KWS:
        if kw in text and kw not in replaced:
            text = text.replace(kw, f'<span style="color:var(--bear);font-weight:600">{kw}</span>')
            replaced.add(kw)
    return text


def render_section(dimension: str, section, container, elapsed_sec: float | None = None):
    title = DIMENSION_TITLES.get(dimension, dimension)
    is_synthesis = dimension == "synthesis"
    anchor_id = f"section-{dimension}"

    with container.container():
        card_class = "section-card-synthesis" if is_synthesis else "section-card"
        warn = section.confidence_label()

        meta_parts = []
        if warn:
            meta_parts.append(f'<span class="confidence-warn">{warn}</span>')
        if elapsed_sec is not None:
            meta_parts.append(f'<span class="elapsed-tag">⏱ {elapsed_sec:.1f}s</span>')
        meta_html = f'<div class="section-meta">{"".join(meta_parts)}</div>' if meta_parts else ""

        # 卡片标题（含 anchor id 供 TOC 跳转）
        st.markdown(
            f'<div class="{card_class}" id="{anchor_id}">'
            f'<div class="section-title">{title}</div>'
            f'{meta_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

        if section.error:
            st.error(f"生成失败：{section.error}")
        else:
            cd = section.chart_data or {}

            if dimension == "performance":
                # 过往业绩：先仪表盘图表 → 桥接线 → AI 文字结论
                if cd.get("financial_bars", {}).get("years"):
                    _render_performance_dashboard(cd)
                    _bridge("AI 分析结论")
                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

            elif dimension == "business":
                # ① 档案 chips（行业 / 市值 / 员工 / 上市时间）
                meta = cd.get("company_meta", {})
                if meta:
                    def _fmt_cap(v: str) -> str:
                        """把市值数值标准化显示（支持原始元、亿、万亿格式输入）"""
                        v = str(v).strip()
                        if "万亿" in v:
                            return v
                        try:
                            num = float(v.replace("亿", "").replace(",", "").strip())
                            # 无"亿"后缀且超过1亿 → 视为原始元，先换算
                            if "亿" not in v and num > 1e8:
                                num = num / 1e8
                            if num >= 10000:
                                return f"{num/10000:.2f}万亿"
                            return f"{num:.0f}亿"
                        except Exception:
                            return v

                    def _fmt_listed(v: str) -> str:
                        """把上市日期格式化为 YYYY-MM，兼容 YYYYMMDD 和 YYYY-MM-DD"""
                        v = str(v).strip().replace("-", "").replace("/", "")
                        if len(v) >= 6:
                            return f"{v[:4]}-{v[4:6]}"
                        return v

                    def _fmt_industry(v: str) -> str:
                        """去掉申万行业末尾的罗马数字分级标注（Ⅰ Ⅱ Ⅲ 等）"""
                        import re as _re
                        return _re.sub(r'[\sⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$', '', v).strip()

                    chips = []
                    if meta.get("industry"):
                        chips.append(_fmt_industry(meta["industry"]))
                    if meta.get("market_cap"):
                        chips.append(f"市值 {_fmt_cap(meta['market_cap'])}")
                    if meta.get("employees"):
                        chips.append(f"员工 {meta['employees']}")
                    if meta.get("listed_date"):
                        chips.append(f"上市 {_fmt_listed(meta['listed_date'])}")
                    if chips:
                        chips_html = "".join(
                            f'<span class="info-chip">{c}</span>' for c in chips
                        )
                        st.markdown(
                            f'<div class="info-chips">{chips_html}</div>',
                            unsafe_allow_html=True,
                        )

                # ② 概念标签 chips（来自东财概念板块接口）
                concept_tags = cd.get("concept_tags", [])
                if concept_tags:
                    tags_html = "".join(
                        f'<span class="concept-chip">{t}</span>' for t in concept_tags
                    )
                    st.markdown(
                        f'<div class="concept-chips">{tags_html}</div>',
                        unsafe_allow_html=True,
                    )

                # ③ 分隔线（有档案chips 或 有概念chips 时才加）
                if meta or concept_tags:
                    st.markdown(
                        '<hr style="border:none;border-top:1px solid #e8eaed;margin:0.6rem 0 0.8rem"/>',
                        unsafe_allow_html=True,
                    )

                # ③ LLM 内容（定位句 + 主营业务 + 风险三格卡）
                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

                # ③ 收入结构图
                rp = cd.get("revenue_pie", {})
                if rp.get("product") or rp.get("region"):
                    _bridge("主营收入结构")
                    _render_revenue_pies(rp)

            elif dimension == "shareholder":
                # ④ 股东分析：各数据模块 → LLM 综合结论（在最下方）
                sh_holder  = cd.get("holder_count", [])
                sh_top10   = cd.get("top10", [])
                sh_free    = cd.get("top10_free", [])
                sh_north   = cd.get("northbound", [])
                sh_north_note = cd.get("northbound_note", "")
                sh_north_date = cd.get("northbound_data_date", "")

                def _sh_val(v, default="—"):
                    if v is None or str(v).strip() in ("nan", "None", ""):
                        return default
                    return str(v)

                def _fmt_num(v, unit_hint="") -> str:
                    """Convert large number to 万/亿 with compact display."""
                    try:
                        n = float(v)
                    except (TypeError, ValueError):
                        return _sh_val(v)
                    if abs(n) >= 1e8:
                        return f"{n/1e8:.2f}亿"
                    if abs(n) >= 1e4:
                        return f"{n/1e4:.0f}万"
                    return f"{n:,.0f}"

                def _fmt_pct(v, places=2) -> str:
                    """Format raw float as percentage string, e.g. 6.692... → '6.69%'."""
                    try:
                        f = float(v)
                        if f != f:  # float NaN check (NaN != NaN is True)
                            return "—"
                        return f"{f:.{places}f}%"
                    except (TypeError, ValueError):
                        return _sh_val(v)

                def _fmt_chg(v) -> str:
                    """增减列：文字标签原样保留，纯数字用 _fmt_num 压缩。"""
                    s = str(v).strip() if v is not None else ""
                    if s in ("", "nan", "None", "—"):
                        return "—"
                    # If it's a recognised text label, keep as-is
                    if any(kw in s for kw in ("不变", "新进", "增持", "减持", "减少", "增加")):
                        return s
                    # Try numeric
                    try:
                        return _fmt_num(float(s))
                    except ValueError:
                        return s

                # ── 模块一：股东人数趋势 ──────────────────────────────
                if sh_holder:
                    _bridge("股东人数趋势")
                    # line chart: reverse to oldest→newest
                    chart_rows = list(reversed(sh_holder[:8]))
                    chart_labels = [str(r.get("股东户数统计截止日", ""))[:10] for r in chart_rows]
                    chart_vals   = []
                    for r in chart_rows:
                        try:
                            chart_vals.append(float(r.get("股东户数-本次", 0) or 0))
                        except Exception:
                            chart_vals.append(0)
                    if any(v > 0 for v in chart_vals):
                        _y_min = min(v for v in chart_vals if v > 0)
                        _y_max = max(chart_vals)
                        _pad   = (_y_max - _y_min) * 0.15 or _y_max * 0.05
                        _fig_hc = go.Figure()
                        _fig_hc.add_trace(go.Scatter(
                            x=chart_labels, y=chart_vals,
                            mode="lines+markers",
                            line=dict(color="#1a73e8", width=2),
                            marker=dict(size=6),
                            hovertemplate="%{x}<br>股东户数：%{y:,.0f}<extra></extra>",
                        ))
                        _fig_hc.update_layout(
                            height=200, margin=dict(l=0, r=0, t=8, b=0),
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                            yaxis=dict(
                                range=[_y_min - _pad, _y_max + _pad],
                                tickformat=",d", gridcolor="#f0f0f0",
                            ),
                            xaxis=dict(tickangle=-30, gridcolor="#f0f0f0"),
                            showlegend=False,
                        )
                        st.plotly_chart(_fig_hc, use_container_width=True, config={"displayModeBar": False})
                    hdr = (
                        "<thead><tr>"
                        "<th>统计截止日</th><th>股东户数</th><th>较上期增减</th>"
                        "<th>增减比例</th><th>户均持股数</th><th>户均持股市值</th>"
                        "</tr></thead>"
                    )
                    rows = []
                    for r in sh_holder[:8]:
                        d_str   = _sh_val(r.get("股东户数统计截止日", ""))[:10]
                        cur     = _fmt_num(r.get("股东户数-本次"))
                        prev_d  = _fmt_num(r.get("股东户数-增减"))
                        raw_pct = r.get("股东户数-增减比例")
                        # color: negative=green(集中/看多), positive=red(分散)
                        try:
                            pct_f     = float(raw_pct)
                            chg_pct   = f"{pct_f:+.2f}%"
                            pct_color = "var(--bull)" if pct_f < 0 else ("var(--bear)" if pct_f > 0 else "inherit")
                        except Exception:
                            chg_pct, pct_color = _sh_val(raw_pct), "inherit"
                        avg_qty = _fmt_num(r.get("户均持股数量"))
                        avg_val = _fmt_num(r.get("户均持股市值"))
                        rows.append(
                            f"<tr>"
                            f"<td style='white-space:nowrap;color:var(--text-secondary)'>{d_str}</td>"
                            f"<td style='font-weight:500'>{cur}</td>"
                            f"<td>{prev_d}</td>"
                            f"<td style='color:{pct_color};font-weight:500'>{chg_pct}</td>"
                            f"<td>{avg_qty}</td>"
                            f"<td>{avg_val}</td>"
                            f"</tr>"
                        )
                    st.markdown(
                        '<p style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:0.3rem">'
                        '💡 股东人数下降代表筹码集中（看多信号），上升代表分散（需警惕）</p>'
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table"><{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                # ── 模块二：前十大股东 ────────────────────────────────
                if sh_top10:
                    _bridge("前十大股东")
                    hdr = (
                        "<thead><tr>"
                        "<th>名次</th><th>股东名称</th><th>股份类型</th>"
                        "<th>持股数</th><th>占总股本</th><th>增减</th><th>变动比率</th>"
                        "</tr></thead>"
                    )
                    rows = []
                    for r in sh_top10[:10]:
                        rank    = _sh_val(r.get("名次"))
                        name    = str(r.get("股东名称", ""))[:20]
                        stype   = _sh_val(r.get("股份类型"))
                        qty     = _fmt_num(r.get("持股数"))
                        pct     = _fmt_pct(r.get("占总股本持股比例"))
                        chg     = _fmt_chg(r.get("增减"))
                        chg_r   = _fmt_pct(r.get("变动比率"))
                        chg_style = ""
                        if chg not in ("不变", "—", "新进"):
                            chg_style = "color:var(--bear)" if "减" in chg else "color:var(--bull)"
                        rows.append(
                            f"<tr>"
                            f"<td style='color:var(--text-secondary)'>{rank}</td>"
                            f"<td style='font-weight:500'>{name}</td>"
                            f"<td style='color:var(--text-secondary);font-size:0.78rem'>{stype}</td>"
                            f"<td>{qty}</td>"
                            f"<td>{pct}</td>"
                            f"<td style='{chg_style};font-weight:500'>{chg}</td>"
                            f"<td style='{chg_style}'>{chg_r}</td>"
                            f"</tr>"
                        )
                    st.markdown(
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                # ── 模块三：流通股前十大（含股东性质） ───────────────
                if sh_free:
                    _bridge("流通股前十大")
                    nature_color = {
                        "证券投资基金": "#1a73e8",
                        "QFII": "#9c27b0",
                        "社保基金": "#2e7d32",
                        "保险": "#e65100",
                    }
                    hdr = (
                        "<thead><tr>"
                        "<th>名次</th><th>股东名称</th><th>股东性质</th>"
                        "<th>持股数</th><th>占流通股</th><th>增减</th><th>变动比率</th>"
                        "</tr></thead>"
                    )
                    rows = []
                    for r in sh_free[:10]:
                        rank    = _sh_val(r.get("名次"))
                        name    = str(r.get("股东名称", ""))[:22]
                        nature  = _sh_val(r.get("股东性质"), "其它")
                        nc      = nature_color.get(nature, "#5f6368")
                        qty     = _fmt_num(r.get("持股数"))
                        pct     = _fmt_pct(r.get("占总流通股本持股比例"))
                        chg     = _fmt_chg(r.get("增减"))
                        chg_r   = _fmt_pct(r.get("变动比率"))
                        chg_style = ""
                        if chg not in ("不变", "—", "新进"):
                            chg_style = "color:var(--bear)" if "减" in chg else "color:var(--bull)"
                        rows.append(
                            f"<tr>"
                            f"<td style='color:var(--text-secondary)'>{rank}</td>"
                            f"<td style='font-weight:500'>{name}</td>"
                            f"<td><span style='color:{nc};font-weight:500;font-size:0.78rem'>{nature}</span></td>"
                            f"<td>{qty}</td>"
                            f"<td>{pct}</td>"
                            f"<td style='{chg_style};font-weight:500'>{chg}</td>"
                            f"<td style='{chg_style}'>{chg_r}</td>"
                            f"</tr>"
                        )
                    st.markdown(
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                # ── 模块四：北向资金月度趋势（always shown） ──────────
                _bridge("北向资金（近12个月）")
                if sh_north_note:
                    # Non-eligible stock or data fetch error
                    st.markdown(
                        f'<p style="color:var(--text-secondary);font-size:0.85rem;padding:0.6rem 0">'
                        f'ℹ️ {sh_north_note}</p>',
                        unsafe_allow_html=True,
                    )
                elif not sh_north:
                    st.markdown(
                        '<p style="color:var(--text-secondary);font-size:0.85rem;padding:0.6rem 0">'
                        'ℹ️ 暂未获取到北向资金数据，请清除缓存后重试。</p>',
                        unsafe_allow_html=True,
                    )
                else:
                    from datetime import date as _date
                    _today = _date.today()
                    _lag_days = 0
                    if sh_north_date:
                        try:
                            _data_dt  = _date.fromisoformat(sh_north_date)
                            _lag_days = (_today - _data_dt).days
                        except Exception:
                            _lag_days = 0

                    if _lag_days > 365:
                        # 滞后超过12个月 — 数据严重过期，不具参考价值，直接说明
                        st.markdown(
                            f'<div style="background:#fff8e1;border-left:3px solid #f9a825;'
                            f'padding:0.7rem 1rem;border-radius:4px;margin:0.4rem 0;font-size:0.85rem">'
                            f'⚠️ <strong>北向资金数据已严重滞后</strong>（数据截至 {sh_north_date}，'
                            f'距今约 {_lag_days // 30} 个月），不具当前参考价值。'
                            f'<br>原因：AKShare 东财个股北向持仓接口（stock_hsgt_individual_em）'
                            f'数据未持续更新，建议在东方财富或同花顺直接查看最新北向持仓。</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        # 滞后在1年内 — 显示数据，但给出时效提示
                        if _lag_days > 90:
                            lag_warn = (
                                f'<p style="font-size:0.78rem;color:#e65100;margin-bottom:0.4rem">'
                                f'⚠️ 数据截至 {sh_north_date}，滞后约 {_lag_days // 30} 个月，仅供参考。</p>'
                            )
                        elif sh_north_date:
                            lag_warn = (
                                f'<p style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:0.3rem">'
                                f'数据截至 {sh_north_date}</p>'
                            )
                        else:
                            lag_warn = ""
                        hdr = (
                            "<thead><tr>"
                            "<th>月份</th><th>收盘价(元)</th><th>持股数量</th>"
                            "<th>占A股%</th><th>当月净增持</th>"
                            "</tr></thead>"
                        )
                        rows = []
                        for r in sh_north:
                            month   = _sh_val(r.get("月份"))
                            price   = _sh_val(r.get("当日收盘价"))
                            qty     = r.get("持股数量")
                            qty_str = _fmt_num(qty) if qty else "—"
                            pct     = _sh_val(r.get("持股数量占A股百分比"))
                            net     = r.get("当月净增持")
                            if net is not None:
                                net_str   = f"+{_fmt_num(net)}" if net > 0 else _fmt_num(net)
                                net_color = "color:var(--bull)" if net > 0 else ("color:var(--bear)" if net < 0 else "inherit")
                            else:
                                net_str, net_color = "—", "inherit"
                            rows.append(
                                f"<tr>"
                                f"<td style='white-space:nowrap;color:var(--text-secondary)'>{month}</td>"
                                f"<td>{price}</td>"
                                f"<td>{qty_str}</td>"
                                f"<td>{pct}</td>"
                                f"<td style='{net_color};font-weight:500'>{net_str}</td>"
                                f"</tr>"
                            )
                        st.markdown(
                            lag_warn +
                            '<div class="analyst-table-wrap">'
                            f'<table class="analyst-table">{hdr}<tbody>'
                            + "".join(rows) + "</tbody></table></div>",
                            unsafe_allow_html=True,
                        )

                # ── LLM 综合结论（最下方）────────────────────────────
                _bridge("AI 综合判断")
                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

            elif dimension == "management":
                # ⑤ 管理层情况：董事会名单 → 增减持 → 质押 → LLM 综合判断
                mg_board  = cd.get("board_members", [])
                mg_change = cd.get("shareholder_change", [])
                mg_pledge = cd.get("pledge", [])

                def _mg_val(v, default="—"):
                    if v is None or str(v).strip() in ("nan", "None", ""):
                        return default
                    return str(v)

                def _mg_num(v) -> str:
                    try:
                        n = float(v)
                        if n != n: return "—"
                        if abs(n) >= 1e8: return f"{n/1e8:.2f}亿"
                        if abs(n) >= 1e4: return f"{n/1e4:.0f}万"
                        return f"{n:,.0f}"
                    except (TypeError, ValueError):
                        return _mg_val(v)

                # ── 模块一：董事会/高管名单 ──────────────────────────
                if mg_board:
                    _bridge("董事会 / 高管名单")
                    hdr = (
                        "<thead><tr>"
                        "<th>姓名</th><th>职务</th><th>持股数</th><th>年薪(元)</th>"
                        "</tr></thead>"
                    )
                    rows = []
                    for r in mg_board[:15]:
                        name  = _mg_val(r.get("姓名"))
                        title = _mg_val(r.get("职务"))
                        hold  = _mg_num(r.get("持股数"))
                        sal   = _mg_num(r.get("年薪"))
                        rows.append(
                            f"<tr>"
                            f"<td style='font-weight:500'>{name}</td>"
                            f"<td style='color:var(--text-secondary);font-size:0.82rem'>{title}</td>"
                            f"<td>{hold}</td>"
                            f"<td>{sal}</td>"
                            f"</tr>"
                        )
                    st.markdown(
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                # ── 模块二：大股东/高管增减持 ──────────────────────
                _bridge("大股东 / 高管增减持（近2年）")
                if not mg_change:
                    st.markdown(
                        '<p style="color:var(--text-secondary);font-size:0.85rem;padding:0.4rem 0">'
                        'ℹ️ 近2年内无大股东/高管增减持记录</p>',
                        unsafe_allow_html=True,
                    )
                else:
                    hdr = (
                        "<thead><tr>"
                        "<th>日期</th><th>股东名称</th><th>变动类型</th>"
                        "<th>变动数量</th><th>变动比例</th>"
                        "<th>交易金额</th><th>占市值</th><th>影响度</th>"
                        "</tr></thead>"
                    )
                    # 注释提示
                    st.markdown(
                        '<p style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:0.3rem">'
                        '💡 影响度参考：占市值 &lt;0.1% 低（正常套现）/ 0.1-0.5% 中（需关注）/ &gt;0.5% 高（强信号）</p>',
                        unsafe_allow_html=True,
                    )
                    rows = []
                    for r in mg_change[:15]:
                        # 日期：多种列名兜底
                        dt = _mg_val(
                            r.get("变动截止日期") or r.get("公告日期") or
                            r.get("变动日期") or r.get("_date") or r.get("date", "")
                        )[:10]
                        # 股东名称
                        name = _mg_val(
                            r.get("股东名称") or r.get("shareholder") or r.get("name", "")
                        )[:18]
                        # 变动类型：部分接口把类型嵌入"变动数量"字段，尝试从中提取
                        typ_raw = r.get("变动类型") or r.get("变动方向") or r.get("type") or ""
                        qty_raw = r.get("变动股数") or r.get("变动数量") or r.get("变动股份数量") or ""
                        # 如果 typ 为空但 qty_raw 是文本（如"增持416000"），从中提取方向
                        if not typ_raw and isinstance(qty_raw, str):
                            if "增" in qty_raw:
                                typ_raw = "增持"
                            elif "减" in qty_raw:
                                typ_raw = "减持"
                        typ = _mg_val(typ_raw)
                        # 数量：若 qty_raw 混入文字，尝试提取纯数字部分
                        import re as _re
                        qty_num = _re.sub(r"[^\d\.\-]", "", str(qty_raw)) if qty_raw else ""
                        qty = _mg_num(qty_num) if qty_num else _mg_val(qty_raw)
                        rat  = _mg_val(r.get("变动比例") or r.get("持股变动比例", ""))
                        is_sell = "减" in typ or "卖" in typ
                        is_buy  = "增" in typ or "买" in typ
                        typ_color = "color:var(--bear)" if is_sell else ("color:var(--bull)" if is_buy else "inherit")
                        # 量化影响
                        tv    = r.get("_trade_value")
                        mp    = r.get("_pct_of_mktcap")
                        level = r.get("_impact_level")
                        tv_str = _mg_num(tv) if tv else "—"
                        mp_str = f"{mp:.3f}%" if mp is not None else "—"
                        level_color = (
                            "color:var(--bear);font-weight:700" if level == "高"
                            else "color:#e65100;font-weight:600" if level == "中"
                            else "color:var(--text-secondary)"
                        )
                        level_str = level if level else "—"
                        rows.append(
                            f"<tr>"
                            f"<td style='white-space:nowrap;color:var(--text-secondary)'>{dt}</td>"
                            f"<td style='font-weight:500'>{name}</td>"
                            f"<td style='{typ_color};font-weight:500'>{typ}</td>"
                            f"<td>{qty}</td>"
                            f"<td>{rat}</td>"
                            f"<td>{tv_str}</td>"
                            f"<td>{mp_str}</td>"
                            f"<td style='{level_color}'>{level_str}</td>"
                            f"</tr>"
                        )
                    st.markdown(
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                # ── 模块三：股权质押 ───────────────────────────────
                _bridge("股权质押情况")
                if not mg_pledge:
                    st.markdown(
                        '<p style="color:var(--text-secondary);font-size:0.85rem;padding:0.4rem 0">'
                        'ℹ️ 暂无质押数据</p>',
                        unsafe_allow_html=True,
                    )
                else:
                    hdr = (
                        "<thead><tr>"
                        "<th>股东名称</th><th>质押数量</th><th>质押比例</th><th>截止日期</th>"
                        "</tr></thead>"
                    )
                    rows = []
                    for r in mg_pledge[:8]:
                        name  = _mg_val(r.get("股东名称", ""))[:18]
                        qty   = _mg_num(r.get("质押数量") or r.get("质押股数"))
                        ratio_raw = r.get("质押比例")
                        try:
                            ratio_f = float(ratio_raw)
                            ratio_str = f"{ratio_f:.2f}%"
                            # 质押比例>50%高风险，>30%中风险
                            ratio_color = (
                                "color:var(--bear);font-weight:600" if ratio_f > 50
                                else "color:#e65100;font-weight:500" if ratio_f > 30
                                else "inherit"
                            )
                        except (TypeError, ValueError):
                            ratio_str, ratio_color = _mg_val(ratio_raw), "inherit"
                        dt = _mg_val(r.get("截止日期") or r.get("公告日期", ""))[:10]
                        rows.append(
                            f"<tr>"
                            f"<td style='font-weight:500'>{name}</td>"
                            f"<td>{qty}</td>"
                            f"<td style='{ratio_color}'>{ratio_str}</td>"
                            f"<td style='color:var(--text-secondary)'>{dt}</td>"
                            f"</tr>"
                        )
                    st.markdown(
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                # ── LLM 综合结论 ───────────────────────────────────
                _bridge("AI 综合判断")
                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

            elif dimension == "industry":
                # ⑥ 行业分析：LLM五节 → 同行对比表 → 行业资金流向
                ind_peer_table  = cd.get("peer_table", [])
                ind_fund_flow   = cd.get("fund_flow", {}) or {}
                ind_name        = cd.get("industry_name", "")

                def _ind_val(v, default="—"):
                    if v is None or str(v).strip() in ("nan", "None", ""):
                        return default
                    return str(v)

                # ── LLM 分析（产业链门槛 + 市场空间 + 第二增长曲线 + 竞争格局风险 + 综合判断）──
                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

                # ── 模块C：同行财务对比表 ────────────────────────────
                if ind_peer_table:
                    _bridge(f"同行财务对比（{ind_name or '行业'}，含目标公司高亮）")
                    # 检测可用列（有实际数据的列才显示）
                    has_mv  = any(r.get("mv",  "—") != "—" for r in ind_peer_table)
                    has_pe  = any(r.get("pe",  "—") != "—" for r in ind_peer_table)
                    has_roe = any(r.get("roe", "—") != "—" for r in ind_peer_table)
                    has_gm  = any(r.get("gm",  "—") != "—" for r in ind_peer_table)
                    has_rev = any(r.get("rev_yoy", "—") != "—" for r in ind_peer_table)
                    has_np  = any(r.get("np_yoy", "—") != "—" for r in ind_peer_table)
                    has_chg = any(r.get("chg", "—") != "—" for r in ind_peer_table)

                    th_parts = ["<th>代码</th><th>名称</th>"]
                    if has_mv:  th_parts.append("<th>总市值</th>")
                    if has_pe:  th_parts.append("<th>PE</th>")
                    if has_roe: th_parts.append("<th>ROE</th>")
                    if has_gm:  th_parts.append("<th>毛利率</th>")
                    if has_rev: th_parts.append("<th>营收增速</th>")
                    if has_np:  th_parts.append("<th>净利增速</th>")
                    if has_chg: th_parts.append("<th>今日涨跌</th>")
                    hdr = f"<thead><tr>{''.join(th_parts)}</tr></thead>"

                    rows = []
                    for r in ind_peer_table:
                        is_tgt    = r.get("is_target", False)
                        row_style = "background:#e8f0fe" if is_tgt else ""
                        rev_pos   = r.get("rev_pos")
                        chg_pos   = r.get("chg_pos")
                        rev_color = ("color:var(--bull)" if rev_pos is True
                                     else "color:var(--bear)" if rev_pos is False
                                     else "")
                        chg_color = ("color:var(--bull)" if chg_pos is True
                                     else "color:var(--bear)" if chg_pos is False
                                     else "color:var(--text-secondary)")
                        td = (
                            f"<td style='color:var(--text-secondary);font-size:0.8rem'>{_ind_val(r.get('code'))}</td>"
                            f"<td style='font-weight:{'700' if is_tgt else '500'}'>"
                            f"{'★ ' if is_tgt else ''}{_ind_val(r.get('name'))}</td>"
                        )
                        np_pos    = r.get("np_pos")
                        np_color  = ("color:var(--bull)" if np_pos is True
                                     else "color:var(--bear)" if np_pos is False
                                     else "")
                        if has_mv:  td += f"<td style='text-align:right'>{_ind_val(r.get('mv'))}</td>"
                        if has_pe:  td += f"<td style='text-align:right'>{_ind_val(r.get('pe'))}</td>"
                        if has_roe: td += f"<td style='text-align:right'>{_ind_val(r.get('roe'))}</td>"
                        if has_gm:  td += f"<td style='text-align:right'>{_ind_val(r.get('gm'))}</td>"
                        if has_rev: td += f"<td style='text-align:right;{rev_color}'>{_ind_val(r.get('rev_yoy'))}</td>"
                        if has_np:  td += f"<td style='text-align:right;{np_color}'>{_ind_val(r.get('np_yoy'))}</td>"
                        if has_chg: td += f"<td style='text-align:right;{chg_color}'>{_ind_val(r.get('chg'))}</td>"
                        rows.append(f"<tr style='{row_style}'>{td}</tr>")

                    st.markdown(
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{hdr}<tbody>'
                        + "".join(rows) + "</tbody></table>"
                        '<p style="font-size:0.72rem;color:#9aa0a6;margin-top:0.4rem">'
                        '★ 标记为目标公司；PE≤0 显示"亏损"；增速红=正增长/绿=负增长；数据来源：东财行情/东财业绩报表</p>'
                        '</div>',
                        unsafe_allow_html=True,
                    )

                # ── 模块D：行业资金流向 ───────────────────────────────
                if ind_fund_flow.get("found"):
                    _bridge("行业资金净流入")
                    flow_today = ind_fund_flow.get("今日")
                    flow_5d    = ind_fund_flow.get("5日")
                    flow_10d   = ind_fund_flow.get("10日")

                    def _flow_chip(label, val):
                        if val is None:
                            return ""
                        color = "var(--bull)" if val > 0 else "var(--bear)"
                        sign  = "+" if val > 0 else ""
                        return (
                            f"<div style='display:inline-block;margin:0 0.5rem 0.5rem 0;"
                            f"padding:0.4rem 1rem;border-radius:6px;"
                            f"background:{'#fff0f0' if val < 0 else '#f0fff4'};"
                            f"border:1px solid {color}'>"
                            f"<div style='font-size:0.75rem;color:var(--text-secondary)'>{label}</div>"
                            f"<div style='font-size:1.1rem;font-weight:700;color:{color}'>"
                            f"{sign}{val:.2f}亿</div></div>"
                        )

                    chips = (
                        _flow_chip("今日净流入", flow_today)
                        + _flow_chip("5日净流入", flow_5d)
                        + _flow_chip("10日净流入", flow_10d)
                    )
                    ind_label = ind_fund_flow.get("行业", ind_name)
                    st.markdown(
                        f'<p style="font-size:0.82rem;color:var(--text-secondary);margin-bottom:0.4rem">'
                        f'{ind_label} 行业资金流向</p>'
                        f'<div style="margin-bottom:0.6rem">{chips}</div>',
                        unsafe_allow_html=True,
                    )

            elif dimension == "forecast":
                # ③ 盈利预测：详细指标预测表 → 逐机构预测表 → LLM 分析
                fc_detail      = cd.get("detail_table", {})
                fc_reports     = cd.get("reports", [])
                fc_eps_range   = cd.get("eps_range", {})
                fc_profit_range = cd.get("profit_range", {})
                fc_ratings     = cd.get("ratings", {})
                fc_count       = cd.get("report_count", 0)

                # ── 详细指标预测表 ──────────────────────────────────
                if fc_detail and fc_detail.get("columns"):
                    cols = fc_detail["columns"]
                    rows_data = fc_detail["rows"]
                    # 区分实际值列和预测列
                    def _is_forecast_col(c):
                        return "预测" in c
                    th_cells = "".join(
                        f"<th style='background:{'#e8f0fe' if _is_forecast_col(c) else '#f8f9fa'}"
                        f";color:{'#1a73e8' if _is_forecast_col(c) else 'var(--text-secondary)'}'>{c}</th>"
                        for c in cols
                    )
                    tbody = ""
                    for row in rows_data:
                        metric = row.get(cols[0], "")
                        td_cells = f"<td style='font-weight:500;white-space:nowrap'>{metric}</td>"
                        for c in cols[1:]:
                            val = row.get(c, "")
                            is_fc = _is_forecast_col(c)
                            style = "color:#1a73e8;" if is_fc else "color:var(--text-secondary);"
                            td_cells += f"<td style='text-align:right;{style}'>{val}</td>"
                        tbody += f"<tr>{td_cells}</tr>"
                    detail_html = (
                        '<div class="analyst-table-wrap" style="margin-bottom:1.2rem">'
                        f'<table class="analyst-table">'
                        f'<thead><tr>{th_cells}</tr></thead>'
                        f'<tbody>{tbody}</tbody></table>'
                        '<p style="font-size:0.72rem;color:#9aa0a6;margin-top:0.4rem">'
                        '预测数据根据各机构发布的研究报告摘录所得，蓝色列为预测均值。</p>'
                        '</div>'
                    )
                    _bridge("详细指标预测")
                    st.markdown(detail_html, unsafe_allow_html=True)

                def _rating_cls_fc(r: str) -> str:
                    if any(k in str(r) for k in ("买入", "强烈推荐", "推荐")):
                        return "rating-buy"
                    if any(k in str(r) for k in ("增持", "跑赢")):
                        return "rating-buy"
                    if any(k in str(r) for k in ("中性", "持有", "观望")):
                        return "rating-hold"
                    if any(k in str(r) for k in ("卖出", "减持", "回避")):
                        return "rating-sell"
                    return "rating-other"

                if fc_reports:
                    all_years = sorted({yr for r in fc_reports for yr in r.get("eps", {})})

                    # 表头：机构·研究员·日期·评级 + 各年EPS + 各年净利润
                    yr_heads = "".join(
                        f"<th>{yr}E EPS(元)</th>"
                        for yr in all_years
                    )
                    thead = (
                        "<thead><tr>"
                        "<th>日期</th><th>机构</th><th>研究员</th><th>评级</th>"
                        f"{yr_heads}"
                        "</tr></thead>"
                    )

                    rows = []
                    # ── 一致预期汇总行（首行，灰底）─────────────────
                    if fc_eps_range:
                        consensus_cells = ""
                        for yr in all_years:
                            er = fc_eps_range.get(yr, {})
                            pr = fc_profit_range.get(yr, {})
                            eps_avg = f"{er['avg']:.2f}" if er.get("avg") else "—"
                            np_avg  = f"{pr['avg']:.1f}" if pr.get("avg") else "—"
                            eps_rng = (f"<br><span style='color:#9aa0a6;font-size:0.72rem'>"
                                       f"{er.get('min',0):.2f}~{er.get('max',0):.2f}</span>") if er.get("min") else ""
                            np_rng  = (f"<br><span style='color:#9aa0a6;font-size:0.72rem'>"
                                       f"{pr.get('min',0):.1f}~{pr.get('max',0):.1f}</span>") if pr.get("min") else ""
                            consensus_cells += (
                                f"<td><strong>{eps_avg}</strong>{eps_rng}"
                                f"<br><span style='color:#9aa0a6;font-size:0.72rem'>净利:{np_avg}亿{np_rng}</span></td>"
                            )
                        cnt = cd.get("institution_count", fc_count)
                        rows.append(
                            f"<tr style='background:#f8f9fa'>"
                            f"<td colspan='4' style='font-weight:600;color:var(--text-secondary)'>"
                            f"一致预期（{cnt}家机构）</td>"
                            f"{consensus_cells}</tr>"
                        )

                    # ── 逐机构行 ────────────────────────────────────
                    for r in fc_reports:
                        yr_cells = ""
                        for yr in all_years:
                            eps = r.get("eps", {}).get(yr)
                            if eps is not None:
                                yr_cells += f'<td><span class="eps-cell">{eps:.2f}</span></td>'
                            else:
                                yr_cells += "<td>—</td>"

                        researcher = r.get("researcher", "") or "—"
                        rc = _rating_cls_fc(r["rating"])
                        rows.append(
                            f"<tr>"
                            f"<td style='white-space:nowrap;color:var(--text-secondary)'>{r['date']}</td>"
                            f"<td style='white-space:nowrap;font-weight:500'>{r['institution']}</td>"
                            f"<td style='color:var(--text-secondary);font-size:0.8rem'>{researcher}</td>"
                            f"<td><span class='{rc}'>{r['rating'] or '—'}</span></td>"
                            f"{yr_cells}</tr>"
                        )

                    table_html = (
                        '<div class="analyst-table-wrap">'
                        f'<table class="analyst-table">{thead}<tbody>'
                        + "".join(rows)
                        + "</tbody></table></div>"
                    )
                    st.markdown(table_html, unsafe_allow_html=True)
                    _bridge("AI 分析")

                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

            elif dimension == "research":
                # ⑧ 研究报告：研报库 + 四维度定向提取 + 用户上传
                from src.data.report_parser import (
                    save_uploaded_report, load_uploaded_reports, UPLOAD_DIR
                )

                r_code       = cd.get("stock_code", "")
                reports_list = cd.get("reports", [])

                # 补充磁盘上已上传但本次未在 chart_data 中的研报（跨 session 保持）
                uploaded_on_disk = load_uploaded_reports(r_code) if r_code else []
                disk_keys = {(u["institution"], u["date"]) for u in uploaded_on_disk}
                cd_uploaded = {
                    (r["institution"], r["date"])
                    for r in reports_list if r.get("source") == "uploaded"
                }
                for u in uploaded_on_disk:
                    if (u["institution"], u["date"]) not in cd_uploaded:
                        reports_list.append({
                            "date": u["date"], "institution": u["institution"],
                            "title": u["title"], "rating": u.get("rating", ""),
                            "eps_forecast": u.get("eps_forecast", {}),
                            "source": "uploaded", "filename": u.get("filename", ""),
                        })

                # ── 模块A：研报库（expander）────────────────────────
                auto_count     = sum(1 for r in reports_list if r.get("source") != "uploaded")
                uploaded_count = sum(1 for r in reports_list if r.get("source") == "uploaded")
                lib_label = f"📚 研报库（{auto_count} 条自动获取"
                if uploaded_count:
                    lib_label += f" + {uploaded_count} 条用户上传"
                lib_label += "）"

                # 每行勾选状态
                sel_key = f"_rsel_{r_code}"
                if sel_key not in st.session_state:
                    st.session_state[sel_key] = set(range(len(reports_list)))

                def _rating_cls(rt: str) -> str:
                    rt = str(rt)
                    if any(k in rt for k in ("买入", "强烈推荐", "推荐", "增持", "跑赢")):
                        return "rating-buy"
                    if any(k in rt for k in ("中性", "持有", "观望")):
                        return "rating-hold"
                    if any(k in rt for k in ("卖出", "减持", "回避")):
                        return "rating-sell"
                    return "rating-other"

                with st.expander(lib_label, expanded=True):
                    if not reports_list:
                        st.caption("暂无研报，请上传 PDF")
                    else:
                        # 全选 / 全不选
                        col_all, col_none, _ = st.columns([1, 1, 6])
                        with col_all:
                            if st.button("全选", key=f"_rall_{r_code}", use_container_width=True):
                                st.session_state[sel_key] = set(range(len(reports_list)))
                                st.rerun()
                        with col_none:
                            if st.button("全不选", key=f"_rnone_{r_code}", use_container_width=True):
                                st.session_state[sel_key] = set()
                                st.rerun()

                        st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

                        for idx, r in enumerate(reports_list):
                            src_tag = "📎" if r.get("source") == "uploaded" else "🤖"
                            is_checked = idx in st.session_state[sel_key]
                            rc = _rating_cls(r.get("rating", ""))
                            title_short = r["title"][:32] + "…" if len(r["title"]) > 32 else r["title"]
                            rating_html = f'<span class="{rc}" style="font-size:0.72rem">{r.get("rating") or "—"}</span>'

                            col_cb, col_meta = st.columns([0.5, 9])
                            with col_cb:
                                checked = st.checkbox("", value=is_checked,
                                                      key=f"_rcb_{r_code}_{idx}",
                                                      label_visibility="collapsed")
                                if checked and idx not in st.session_state[sel_key]:
                                    st.session_state[sel_key].add(idx)
                                elif not checked and idx in st.session_state[sel_key]:
                                    st.session_state[sel_key].discard(idx)
                            with col_meta:
                                st.markdown(
                                    f'<div style="display:flex;align-items:center;gap:8px;'
                                    f'padding:3px 0;border-bottom:1px solid var(--border-subtle)">'
                                    f'<span style="font-size:0.9rem">{src_tag}</span>'
                                    f'<span style="color:var(--text-secondary);font-size:0.75rem;'
                                    f'white-space:nowrap">{r["date"]}</span>'
                                    f'<span style="font-weight:500;font-size:0.82rem;flex:1">'
                                    f'{r["institution"]}</span>'
                                    f'{rating_html}'
                                    f'<span style="color:var(--text-secondary);font-size:0.78rem">'
                                    f'{title_short}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )

                    # ── 上传研报 ──────────────────────────────
                    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
                    with st.form(key=f"_upload_form_{r_code}", clear_on_submit=True):
                        up_file = st.file_uploader(
                            "上传研报 PDF（支持拖拽）",
                            type=["pdf"],
                            key=f"_upfile_{r_code}",
                            label_visibility="visible",
                        )
                        up_institution = st.text_input(
                            "机构名称（选填）",
                            placeholder="如：中信证券、华泰证券",
                            key=f"_upinst_{r_code}",
                        )
                        submitted = st.form_submit_button("📎 保存上传", use_container_width=True)
                        if submitted and up_file is not None:
                            saved = save_uploaded_report(
                                r_code,
                                up_file.name,
                                up_file.read(),
                                institution=up_institution.strip() or "",
                            )
                            st.success(f"✅ 已上传：{saved['title']}（{len(saved.get('text',''))}字正文）")
                            st.session_state[f"_rupload_done_{r_code}"] = True
                            st.rerun()

                # ── 模块B：分析控制 ────────────────────────────────
                sel_indices = st.session_state.get(sel_key, set())
                sel_reports = [reports_list[i] for i in sorted(sel_indices) if i < len(reports_list)]

                col_btn, col_info = st.columns([2, 8])
                with col_btn:
                    reanalyze_btn = st.button(
                        "🔍 分析选中研报",
                        key=f"_ranalyze_{r_code}",
                        use_container_width=True,
                        type="primary",
                    )
                with col_info:
                    st.caption(f"已选 {len(sel_reports)} / {len(reports_list)} 篇研报参与分析")

                if reanalyze_btn and sel_reports and r_code:
                    # 获取完整研报（含正文）用于重新分析
                    from src.data.report_parser import fetch_all_reports
                    all_r = fetch_all_reports(r_code)
                    all_with_text = {
                        (r["institution"], r["date"]): r
                        for r in all_r["auto"] + all_r["uploaded"]
                    }
                    full_sel = []
                    for r in sel_reports:
                        key_t = (r["institution"], r["date"])
                        full_sel.append(all_with_text.get(key_t, r))

                    with st.spinner(f"正在分析 {len(full_sel)} 篇研报..."):
                        from src.agents.research import ResearchAgent
                        new_result = ResearchAgent().analyze_selected(
                            r_code, full_sel,
                            model=st.session_state.get("_report_model", "claude-sonnet"),
                        )
                    st.session_state["_research_new_analysis"] = {
                        "code": r_code, "result": new_result,
                    }
                    # 直接更新当前 cd 以立即渲染
                    cd.update(new_result)

                # ── 模块C：四维度分析结果 ──────────────────────────
                # 检查 session state 中是否有新分析结果
                pending = st.session_state.get("_research_new_analysis", {})
                if pending and pending.get("code") == r_code:
                    cd.update(pending.get("result", {}))

                ind_view = cd.get("industry_view", "")
                str_view = cd.get("strategy_view", "")
                mgmt_view= cd.get("management_view", "")
                disagree = cd.get("key_disagreements", "")
                price_ref= cd.get("price_ref", {})

                if any([ind_view, str_view, mgmt_view, disagree]):
                    _bridge("研报提取结果")

                    CARD_STYLE = (
                        "border:1px solid var(--border-subtle);border-radius:10px;"
                        "padding:14px 16px;background:var(--surface-card);margin-bottom:10px"
                    )
                    CARDS = [
                        ("🏭 行业认知", "var(--accent)",        ind_view),
                        ("🎯 公司战略", "var(--accent-teal)",   str_view),
                        ("🔧 经营管理", "var(--accent-purple)", mgmt_view),
                        ("⚡ 主要分歧", "var(--accent-gold)",   disagree),
                    ]
                    for label, color, content in CARDS:
                        if content:
                            st.markdown(
                                f'<div style="{CARD_STYLE}">'
                                f'<div style="font-weight:700;font-size:0.85rem;margin-bottom:8px;color:{color}">'
                                f'{label}</div>'
                                f'<div style="font-size:0.85rem;color:var(--text-primary);line-height:1.7">'
                                f'{_colorize_signals(content)}</div></div>',
                                unsafe_allow_html=True,
                            )

                    # 折叠的价格参考
                    if price_ref and price_ref.get("range") not in ("暂无", "", None):
                        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
                        with st.expander("💰 价格参考（仅供参考，不构成投资建议）", expanded=False):
                            pr_range = price_ref.get("range", "暂无")
                            pr_avg   = price_ref.get("avg")
                            pr_note  = price_ref.get("note", "")
                            avg_str  = f"均值约 {pr_avg:.0f}元" if pr_avg else ""
                            st.markdown(
                                f'<div style="display:flex;gap:24px;align-items:center;padding:8px 0">'
                                f'<div><span style="color:var(--text-secondary);font-size:0.8rem">目标价区间</span>'
                                f'<div style="font-size:1.1rem;font-weight:700;color:var(--accent)">{pr_range}</div></div>'
                                + (f'<div><span style="color:var(--text-secondary);font-size:0.8rem">均值</span>'
                                   f'<div style="font-size:1rem;font-weight:600">{avg_str}</div></div>' if avg_str else "")
                                + f'<div style="color:var(--text-secondary);font-size:0.82rem">{pr_note}</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                elif not reports_list:
                    st.info("暂无研报。请上传 PDF 研报后点击「🔍 分析选中研报」。")

            elif dimension == "catalyst":
                # ⑦ 最新催化剂：结构化信号卡片 + 跟进节点 + 环境判断
                catalysts   = cd.get("catalysts", [])
                watchpoints = cd.get("watchpoints", [])
                env         = cd.get("environment", "neutral")
                env_summary = cd.get("environment_summary", "")

                # 环境色
                env_color = {
                    "positive": "var(--bull)",
                    "negative": "var(--bear)",
                }.get(env, "var(--text-secondary)")
                env_icon = {"positive": "🟢", "negative": "🔴"}.get(env, "⚪")
                env_label = {"positive": "积极", "negative": "谨慎", "neutral": "中性"}.get(env, "中性")

                # ── 模块A：催化剂信号卡片列表 ──────────────────────
                if catalysts:
                    importance_map = {"high": ("高", "#c62828"), "medium": ("中", "#e65100"), "low": ("低", "#546e7a")}
                    cat_cards = []
                    for cat in catalysts:
                        sig      = cat.get("signal", "neutral")
                        imp      = cat.get("importance", "medium")
                        imp_lbl, imp_color = importance_map.get(imp, ("中", "#e65100"))
                        border   = "var(--bull)" if sig == "positive" else ("var(--bear)" if sig == "negative" else "#9e9e9e")
                        bg       = "#fff8f8" if sig == "positive" else ("#f3fdf9" if sig == "negative" else "#fafafa")
                        icon     = "🟢" if sig == "positive" else ("🔴" if sig == "negative" else "⚪")
                        date_str = cat.get("date", "")[:10]
                        title_t  = cat.get("title", "")
                        cat_type = cat.get("category", "")
                        summary  = cat.get("summary", "")
                        cat_cards.append(
                            f'<div style="border-left:4px solid {border};background:{bg};'
                            f'border-radius:6px;padding:10px 14px;margin-bottom:8px;">'
                            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                            f'<span style="font-size:1rem">{icon}</span>'
                            f'<span style="color:var(--text-secondary);font-size:0.78rem;white-space:nowrap">{date_str}</span>'
                            f'<span style="font-weight:600;font-size:0.9rem;flex:1">{title_t}</span>'
                            f'<span style="font-size:0.7rem;color:var(--text-secondary);'
                            f'background:var(--border-subtle);border-radius:10px;padding:1px 7px">{cat_type}</span>'
                            f'<span style="font-size:0.7rem;color:{imp_color};font-weight:600;'
                            f'border:1px solid {imp_color};border-radius:10px;padding:1px 7px">{imp_lbl}</span>'
                            f'</div>'
                            f'<div style="color:var(--text-secondary);font-size:0.82rem;padding-left:28px">{summary}</div>'
                            f'</div>'
                        )
                    st.markdown(
                        '<div style="margin-bottom:6px;font-weight:600;color:var(--text-primary)">📋 近期催化剂信号</div>'
                        + "".join(cat_cards),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div style="color:var(--text-secondary);font-size:0.85rem;padding:12px 0">'
                        '暂无匹配的催化剂公告（90天内）</div>',
                        unsafe_allow_html=True,
                    )

                # ── 模块B：重点跟进节点 ──────────────────────────────
                if watchpoints:
                    wp_rows = []
                    for wp in watchpoints:
                        tl      = wp.get("timeline", "")
                        event   = wp.get("event", "")
                        rationale = wp.get("rationale", "")
                        wp_rows.append(
                            f'<div style="display:flex;gap:12px;padding:8px 0;'
                            f'border-bottom:1px solid var(--border-subtle);align-items:flex-start;">'
                            f'<span style="background:var(--accent-light);color:var(--accent);'
                            f'font-size:0.72rem;font-weight:600;border-radius:10px;padding:2px 8px;'
                            f'white-space:nowrap;margin-top:1px">{tl}</span>'
                            f'<div style="flex:1">'
                            f'<span style="font-weight:600;font-size:0.88rem">{event}</span>'
                            f'<span style="color:var(--text-secondary);font-size:0.78rem;margin-left:8px">{rationale}</span>'
                            f'</div>'
                            f'</div>'
                        )
                    st.markdown(
                        '<div style="margin-top:16px;margin-bottom:6px;font-weight:600;color:var(--text-primary)">📍 重点跟进节点</div>'
                        + '<div style="border:1px solid var(--border-subtle);border-radius:8px;padding:4px 14px">'
                        + "".join(wp_rows)
                        + '</div>',
                        unsafe_allow_html=True,
                    )

                # ── 模块C：综合环境判断 ──────────────────────────────
                if env_summary:
                    st.markdown(
                        f'<div style="margin-top:16px;padding:12px 16px;border-radius:8px;'
                        f'background:var(--surface-card-alt);border:1px solid var(--border-subtle);">'
                        f'<span style="font-weight:600;color:{env_color}">{env_icon} 催化剂环境：{env_label}</span>'
                        f'<span style="color:var(--text-secondary);font-size:0.88rem;margin-left:10px">{env_summary}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            else:
                # 其余维度：文字（HTML or Markdown）
                st.markdown(
                    f'<div class="report-html">{_colorize_signals(section.content)}</div>',
                    unsafe_allow_html=True,
                )

        # 数据来源 + 用户评分
        col_src, col_rate = st.columns([6, 1])
        with col_src:
            if section.data_sources:
                st.caption(f"数据来源：{' · '.join(section.data_sources)}")
        with col_rate:
            rating_key = f"rating_{dimension}_{id(section)}"
            current = st.session_state.get(rating_key, None)
            r_col1, r_col2 = st.columns(2)
            with r_col1:
                if st.button("👍", key=f"up_{rating_key}",
                             help="这节内容有帮助",
                             type="primary" if current == "up" else "secondary"):
                    st.session_state[rating_key] = "up"
                    st.rerun()
            with r_col2:
                if st.button("👎", key=f"dn_{rating_key}",
                             help="这节内容需要改进",
                             type="primary" if current == "down" else "secondary"):
                    st.session_state[rating_key] = "down"
                    st.rerun()


def render_pending(dimension: str, container):
    title = DIMENSION_TITLES.get(dimension, dimension)
    with container.container():
        st.markdown(
            f'<div class="pending-card">'
            f'  <div class="pending-label">{title}</div>'
            f'  <div class="pending-bar w-90"></div>'
            f'  <div class="pending-bar w-72"></div>'
            f'  <div class="pending-bar w-52"></div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── 右侧 TOC 组件 ────────────────────────────────────────────

def _render_toc_component(display_order: list, titles: dict):
    """
    通过零高度 iframe 注入 JS，在父页面右侧插入固定目录。
    使用 window.parent.document 操作父 DOM（Streamlit 标准 hack）。
    桌面端（≥1280px）显示，手机端自动隐藏。
    """
    import streamlit.components.v1 as components

    toc_items_js = "[" + ",".join(
        f'{{id:"section-{dim}",label:{repr(titles.get(dim, dim))}}}'
        for dim in display_order
    ) + "]"
    n_sections = len(display_order)

    html = f"""
<script>
(function() {{
  var ITEMS = {toc_items_js};
  var pdoc  = window.parent.document;

  /* ── 1. 注入样式（幂等） ── */
  if (!pdoc.getElementById('_ai_toc_style')) {{
    var s = pdoc.createElement('style');
    s.id  = '_ai_toc_style';
    s.textContent = `
      #_ai_toc {{
        position:fixed; top:72px; right:14px;
        width:176px;
        background:#fff;
        border:1px solid #e8eaed;
        border-radius:12px;
        box-shadow:0 2px 8px rgba(0,0,0,0.09);
        padding:10px 0 8px;
        z-index:9999;
        max-height:calc(100vh - 100px);
        overflow-y:auto;
        font-family:-apple-system,"PingFang SC",sans-serif;
      }}
      #_ai_toc::-webkit-scrollbar {{ width:3px; }}
      #_ai_toc::-webkit-scrollbar-thumb {{ background:#ddd; border-radius:3px; }}
      ._toc_hd {{
        font-size:0.68rem; font-weight:700; color:#9aa0a6;
        letter-spacing:0.09em; text-transform:uppercase;
        padding:0 12px 7px; border-bottom:1px solid #f0f0f0; margin-bottom:4px;
      }}
      ._toc_a {{
        display:block; font-size:0.76rem; color:#5f6368;
        padding:3px 12px; cursor:pointer;
        border-left:2px solid transparent;
        transition:all 0.14s; text-decoration:none;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        line-height:1.55;
      }}
      ._toc_a:hover {{ color:#1a73e8; background:#f0f5ff; }}
      ._toc_a._toc_active {{
        color:#1a73e8; border-left-color:#1a73e8;
        font-weight:600; background:#e8f0fe;
      }}
      ._toc_a._toc_pending {{ color:#c5c8cc; cursor:default; pointer-events:none; }}
      @media(max-width:1400px){{ #_ai_toc{{display:none;}} }}
    `;
    pdoc.head.appendChild(s);
  }}

  /* ── 2. 注入 TOC 容器（幂等） ── */
  if (!pdoc.getElementById('_ai_toc')) {{
    var toc = pdoc.createElement('div');
    toc.id  = '_ai_toc';
    var hd  = pdoc.createElement('div');
    hd.className = '_toc_hd';
    hd.textContent = '目  录';
    toc.appendChild(hd);
    ITEMS.forEach(function(item) {{
      var a = pdoc.createElement('a');
      a.className   = '_toc_a _toc_pending';
      a.dataset.sec = item.id;
      a.textContent = item.label;
      a.onclick = function(e) {{
        e.preventDefault();
        var el = pdoc.getElementById(item.id);
        if (el) el.scrollIntoView({{behavior:'smooth', block:'start'}});
      }};
      toc.appendChild(a);
    }});
    pdoc.body.appendChild(toc);
  }}

  /* ── 3. 轮询等待 section 元素出现，绑定 IntersectionObserver ── */
  var observer = null;
  var pollCount = 0;

  function attach() {{
    var secs = pdoc.querySelectorAll('[id^="section-"]');
    if (!secs.length) return;
    if (observer) observer.disconnect();
    observer = new window.parent.IntersectionObserver(function(entries) {{
      entries.forEach(function(e) {{
        var lnk = pdoc.querySelector('._toc_a[data-sec="' + e.target.id + '"]');
        if (!lnk) return;
        if (e.isIntersecting) {{
          pdoc.querySelectorAll('._toc_a').forEach(function(l){{
            l.classList.remove('_toc_active');
          }});
          lnk.classList.add('_toc_active');
          lnk.classList.remove('_toc_pending');
        }}
      }});
    }}, {{ rootMargin:'-15% 0px -65% 0px', threshold:0 }});
    secs.forEach(function(sec) {{
      observer.observe(sec);
      var lnk = pdoc.querySelector('._toc_a[data-sec="' + sec.id + '"]');
      if (lnk) lnk.classList.remove('_toc_pending');
    }});
  }}

  var timer = setInterval(function() {{
    pollCount++;
    attach();
    if (pdoc.querySelectorAll('[id^="section-"]').length >= {n_sections}
        || pollCount > 200) clearInterval(timer);
  }}, 600);
}})();
</script>
"""
    components.html(html, height=0, scrolling=False)


# ── 单股报告模式 ─────────────────────────────────────────────

def run_single_report(
    code: str,
    model: str,
    force_refresh: bool,
    custom_kols: list[str] | None = None,
    debug_dims: set[str] | None = None,
):
    from src.agents.report_orchestrator import ReportOrchestrator

    if debug_dims:
        dim_labels = " · ".join(DIMENSION_TITLES.get(d, d) for d in debug_dims)
        banner = st.info(f"🔬 调试模式：仅运行 {dim_labels}", icon="🔬")
    else:
        banner = st.warning(
            "⏳ 报告生成中，各维度陆续展示。**综合结论尚未生成，请以最终完整结论为准。**",
            icon="⚠️",
        )

    containers: dict[str, st.empty] = {}
    for dim in DISPLAY_ORDER:
        containers[dim] = st.empty()

    # 右侧 TOC（在 section 容器创建后立即注入）
    _render_toc_component(DISPLAY_ORDER, DIMENSION_TITLES)

    # 只对将要运行的维度显示 pending 骨架
    active_dims = debug_dims if debug_dims else set(IMPLEMENTED_DIMS)
    for dim in IMPLEMENTED_DIMS:
        if dim != "synthesis" and dim in active_dims:
            render_pending(dim, containers[dim])

    orchestrator = ReportOrchestrator()
    start_time = time.time()
    dim_times: dict[str, float] = {}
    all_sections: dict = {}
    stock_name = code

    try:
        for section in orchestrator.run(
            code, model=model, force_refresh=force_refresh,
            custom_kols=custom_kols, debug_dims=debug_dims,
        ):
            elapsed = time.time() - start_time
            dim_times[section.dimension] = elapsed

            # 保存 stock_name
            if section.dimension == "business" and section.is_ok:
                import re
                m = re.search(r"[（(]?\s*([^\s（()]+)\s*[）)]?", section.content[:60])

            containers[section.dimension].empty()
            render_section(section.dimension, section, containers[section.dimension],
                           elapsed_sec=elapsed)
            all_sections[section.dimension] = section

        total_elapsed = time.time() - start_time
        banner.empty()
        st.success(f"✅ 报告生成完成（耗时 {total_elapsed:.0f} 秒）")

        # ── 持久化 sections 到 session state，使研报库等交互元素跨 rerun 正常工作
        st.session_state["_report_code"]     = code
        st.session_state["_report_model"]    = model
        st.session_state["_report_sections"] = all_sections
        st.session_state["_report_containers"] = {dim: containers[dim] for dim in DISPLAY_ORDER}

        # ── 导出按钮 ─────────────────────────────────────────
        from src.models.report import ReportSection
        try:
            # 尝试从 info 获取股票名称
            from src.agents import data_fetcher
            info = data_fetcher.fetch(code, data_types=["info"]).get("info", {})
            stock_name = info.get("股票简称", code)
        except Exception:
            stock_name = code

        md_content = build_report_markdown(code, stock_name, all_sections, total_elapsed)
        filename = f"{code}_{stock_name}_研究报告_{datetime.now().strftime('%Y%m%d')}.md"

        st.download_button(
            label="⬇️ 下载完整报告（Markdown）",
            data=md_content.encode("utf-8"),
            file_name=filename,
            mime="text/markdown",
        )

    except Exception as e:
        banner.empty()
        st.error(f"报告生成失败：{e}")
        import traceback
        st.code(traceback.format_exc())


# ── 对比模式 ─────────────────────────────────────────────────

def run_comparison(codes: list[str], model: str, force_refresh: bool):
    from src.agents.report_orchestrator import ReportOrchestrator

    COMPARE_DIMS = ["business", "performance", "forecast", "research", "kol", "synthesis"]

    st.info(f"正在并行生成 {len(codes)} 支股票的研究报告，请稍候...")

    all_results: dict[str, dict] = {c: {} for c in codes}
    progress = st.progress(0)
    status_text = st.empty()

    def run_one(code):
        sections = {}
        for section in ReportOrchestrator().run(code, model=model, force_refresh=force_refresh):
            sections[section.dimension] = section
        return code, sections

    completed = 0
    with ThreadPoolExecutor(max_workers=len(codes)) as ex:
        futures = {ex.submit(run_one, c): c for c in codes}
        for future in as_completed(futures):
            code, sections = future.result()
            all_results[code] = sections
            completed += 1
            progress.progress(completed / len(codes))
            status_text.text(f"完成 {completed}/{len(codes)}: {code}")

    progress.empty()
    status_text.empty()
    st.success("✅ 全部完成")

    st.markdown("---")
    st.subheader("📊 关键维度横向对比")

    for dim in COMPARE_DIMS:
        dim_title = DIMENSION_TITLES.get(dim, dim)
        st.markdown(f"### {dim_title}")
        cols = st.columns(len(codes))
        for i, code in enumerate(codes):
            section = all_results[code].get(dim)
            with cols[i]:
                st.markdown(f'<div class="compare-header">{code}</div>', unsafe_allow_html=True)
                if section and section.is_ok:
                    st.markdown(section.content[:800] + ("..." if len(section.content) > 800 else ""))
                elif section and section.error:
                    st.error(section.error)
                else:
                    st.caption("未生成")
        st.divider()

    with st.expander("查看各股完整报告"):
        for code in codes:
            st.markdown(f"## {code} 完整报告")
            sections = all_results.get(code, {})
            for dim in DISPLAY_ORDER:
                section = sections.get(dim)
                if section and section.is_ok:
                    title = DIMENSION_TITLES.get(dim, dim)
                    st.markdown(f"### {title}")
                    st.markdown(section.content)


# ── JS 注入：强制设置布局留白 ────────────────────────────────

def _apply_layout_margins():
    """
    用 JS 直接操作父 DOM 的 .block-container，绕过 Streamlit Emotion CSS 的高优先级覆盖。
    CSS !important 在 Streamlit 1.56+ 中无法赢过 Emotion 内联样式，只有 JS 能可靠生效。
    """
    import streamlit.components.v1 as components
    components.html("""
<script>
(function() {
  function applyMargins() {
    var pdoc = window.parent.document;
    var el = pdoc.querySelector('.block-container');
    if (!el) return false;
    el.style.setProperty('padding-left',  'clamp(1.5rem, 15vw, 280px)', 'important');
    el.style.setProperty('padding-right', 'clamp(1.5rem, 15vw, 280px)', 'important');
    el.style.setProperty('max-width', '100%', 'important');
    return true;
  }
  /* 立刻尝试一次，再轮询直到成功 */
  if (!applyMargins()) {
    var t = setInterval(function() {
      if (applyMargins()) clearInterval(t);
    }, 80);
    setTimeout(function() { clearInterval(t); }, 5000);
  }
  /* Streamlit 重渲染后样式可能被重置，用 MutationObserver 持续保持 */
  var pdoc = window.parent.document;
  var mo = new window.parent.MutationObserver(function() { applyMargins(); });
  var root = pdoc.querySelector('.block-container');
  if (root) mo.observe(root, { attributes: true, attributeFilter: ['style'] });
})();
</script>
""", height=0, scrolling=False)

_apply_layout_margins()


# ── 主界面 ────────────────────────────────────────────────────

tab_single, tab_compare = st.tabs(["📊 单股报告", "⚖️ 多股对比"])

with tab_single:
    # ── Hero 搜索区 ──────────────────────────────────────────
    st.markdown('<div class="hero-block">', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-title">AI 股票研究报告</div>'
        '<div class="hero-sub">输入股票代码或公司中文名称，生成覆盖 10 个维度的深度研究报告</div>',
        unsafe_allow_html=True,
    )
    col_input, col_model, col_btn = st.columns([3, 2, 1])
    with col_input:
        stock_input = st.text_input(
            "股票代码或名称",
            placeholder="输入代码（600519）或中文名（贵州茅台、招商银行…）",
            label_visibility="collapsed",
            key="single_code",
        )
    with col_model:
        model_choice = st.selectbox(
            "模型",
            ["claude-sonnet", "claude-haiku", "claude-opus"],
            label_visibility="collapsed",
            key="single_model",
        )
    with col_btn:
        generate_btn = st.button("生成报告", type="primary", use_container_width=True, key="single_btn")

    # ── 智能名称解析：实时搜索 + 候选选择 ──────────────────
    resolved_code: str | None = None   # 最终确定的股票代码

    if stock_input.strip():
        _raw = stock_input.strip()
        if is_stock_code(_raw):
            # 直接是合法代码，无需搜索
            resolved_code = normalize_code(_raw)
        else:
            # 中文名称搜索
            _candidates = search_stocks(_raw)

            if len(_candidates) == 0:
                st.markdown(
                    '<p style="font-size:0.82rem;color:#b0351a;margin:0.3rem 0 0.1rem">'
                    '⚠️ 未找到匹配的 A 股公司，请检查名称或直接输入股票代码'
                    '（港股请输入 5 位代码，如 01810）</p>',
                    unsafe_allow_html=True,
                )
            elif len(_candidates) == 1:
                # 唯一匹配，自动确认
                _m = _candidates[0]
                resolved_code = _m["code"]
                st.markdown(
                    f'<p style="font-size:0.82rem;color:#1a73e8;margin:0.3rem 0 0.1rem">'
                    f'✓ 已识别：<strong>{_m["name"]}</strong>（{_m["code"]}）</p>',
                    unsafe_allow_html=True,
                )
            else:
                # 多条匹配，展示选择器
                st.markdown(
                    '<p style="font-size:0.82rem;color:#5f6368;margin:0.3rem 0 0.2rem">'
                    '找到多家相似公司，请选择：</p>',
                    unsafe_allow_html=True,
                )
                _opt_labels = [f"{c['name']}  ({c['code']})" for c in _candidates]
                _sel_label = st.radio(
                    "选择公司",
                    _opt_labels,
                    horizontal=True,
                    label_visibility="collapsed",
                    key="single_company_select",
                )
                _sel_idx   = _opt_labels.index(_sel_label)
                resolved_code = _candidates[_sel_idx]["code"]

    with st.expander("⚙️ 高级选项"):
        force_refresh = st.checkbox("强制刷新缓存", value=False, key="single_refresh")
        st.markdown("**自定义大牛名单**（留空则使用默认：段永平、张坤、但斌等8位）")
        kol_input = st.text_input(
            "大牛名单",
            placeholder="例如：段永平, 张坤, 李录（逗号分隔）",
            label_visibility="collapsed",
            key="single_kol",
        )

        st.divider()

        # ── 调试模式：只运行选中维度 ────────────────────────────
        debug_mode = st.checkbox(
            "🔬 调试模式（只运行选中维度，速度更快）",
            value=False,
            key="single_debug",
        )
        debug_dims: set[str] | None = None
        if debug_mode:
            _dim_options = {
                "① 公司概况":   "business",
                "② 过往业绩":   "performance",
                "③ 盈利预测":   "forecast",
                "④ 股东分析":   "shareholder",
                "⑤ 管理层情况": "management",
                "⑥ 行业分析":   "industry",
                "⑦ 最新催化剂": "catalyst",
                "⑧ 研究报告":   "research",
                "⑨ 大牛分析":   "kol",
                "📋 综合结论":   "synthesis",
            }
            selected_labels = st.multiselect(
                "选择要运行的维度",
                options=list(_dim_options.keys()),
                default=["① 公司概况"],
                key="single_debug_dims",
            )
            debug_dims = {_dim_options[l] for l in selected_labels} if selected_labels else None
            if debug_dims:
                st.caption(f"将只运行：{' · '.join(selected_labels)}，其余维度跳过")

    st.markdown('</div>', unsafe_allow_html=True)  # end hero-block

    if generate_btn and resolved_code:
        custom_kols = [k.strip() for k in kol_input.split(",") if k.strip()] if kol_input else None
        run_single_report(
            resolved_code, model_choice, force_refresh,
            custom_kols=custom_kols, debug_dims=debug_dims,
        )
    elif generate_btn and stock_input.strip() and not resolved_code:
        st.warning("请从候选列表中选择公司，或直接输入股票代码")
    elif generate_btn:
        st.warning("请输入股票代码或公司名称")
    elif (
        not generate_btn
        and st.session_state.get("_report_code")
        and st.session_state.get("_report_sections")
    ):
        # ── 交互式 rerun：从 session state 重新渲染已生成报告 ──────────────────
        _cached_code     = st.session_state["_report_code"]
        _cached_sections = st.session_state["_report_sections"]

        # 重建容器并重新渲染
        _containers: dict[str, st.empty] = {}
        for _dim in DISPLAY_ORDER:
            _containers[_dim] = st.empty()
        _render_toc_component(DISPLAY_ORDER, DIMENSION_TITLES)

        # 若有新的研报分析结果，先更新 cached section 的 chart_data，再统一渲染
        _new_analysis = st.session_state.pop("_research_new_analysis", None)
        if _new_analysis and _new_analysis.get("code") == _cached_code:
            _rsec = _cached_sections.get("research")
            if _rsec and _rsec.chart_data:
                _rsec.chart_data.update(_new_analysis["result"])

        for _dim in DISPLAY_ORDER:
            _sec = _cached_sections.get(_dim)
            if _sec:
                render_section(_dim, _sec, _containers[_dim])
    else:
        st.markdown("""
        <div style="margin-top:0.6rem">
        <p style="font-size:0.82rem;color:#5f6368;margin-bottom:0.7rem;font-weight:600;letter-spacing:0.04em;text-transform:uppercase">
          覆盖 11 个分析维度
        </p>
        </div>
        """, unsafe_allow_html=True)

        dims_info = [
            ("① 公司概况",  "公司定位、收入结构（含毛利率）、客户/供应商集中度、竞争定位"),
            ("② 过往业绩",  "营收/利润多年趋势、PE/PB历史分位"),
            ("③ 盈利预测",  "分析师一致预期 EPS·评级分布"),
            ("④ 股东分析",  "持股结构、机构动向、散户情绪"),
            ("⑤ 管理层情况","减持/质押信号、行为判断"),
            ("⑥ 行业分析",  "产业链位置 + 战略门槛 + 市场空间量化 + 同行对比"),
            ("⑦ 最新催化剂","近期重大公告、正面/负面信号"),
            ("⑧ 研究报告",  "多份研报聚合：各机构具体预测 + 共识结论"),
            ("⑨ 大牛分析",  "知名投资人风格匹配与公开观点"),
            ("📋 综合结论",  "AI 综合判断：看多 / 中性 / 看空"),
        ]
        rows_html = "".join(
            f"<tr><td style='white-space:nowrap;font-weight:600;padding:5px 12px 5px 0'>{d}</td>"
            f"<td style='color:#5f6368;font-size:0.86rem;padding:5px 0'>{desc}</td></tr>"
            for d, desc in dims_info
        )
        st.markdown(
            f'<div class="report-html">'
            f'<table style="font-size:0.87rem">'
            f'<tbody>{rows_html}</tbody></table></div>'
            f'<p style="font-size:0.78rem;color:#9aa0a6;margin-top:0.6rem">'
            f'💡 高级选项中可自定义大牛名单（默认：段永平、张坤、但斌等8位）</p>',
            unsafe_allow_html=True,
        )

with tab_compare:
    st.markdown('<div class="hero-block">', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-title">多股横向对比</div>'
        '<div class="hero-sub">输入 2–4 支股票，并行生成报告并横向比较关键维度</div>',
        unsafe_allow_html=True,
    )
    compare_cols = st.columns(4)
    compare_inputs_raw = []
    for i, col in enumerate(compare_cols):
        with col:
            c = st.text_input(
                f"股票 {i+1}", placeholder=f"代码或名称 {i+1}",
                label_visibility="visible", key=f"compare_{i}"
            )
            compare_inputs_raw.append(c.strip())

    # ── 对比模式：名称搜索结果展示 ──────────────────────────────
    compare_resolved: list[str] = []
    for i, raw in enumerate(compare_inputs_raw):
        if not raw:
            continue
        if is_stock_code(raw):
            compare_resolved.append(normalize_code(raw))
        else:
            _cands = search_stocks(raw)
            if len(_cands) == 0:
                st.caption(f"股票{i+1}：未找到 '{raw}'，请检查名称")
            elif len(_cands) == 1:
                compare_resolved.append(_cands[0]["code"])
                st.caption(f"股票{i+1} ✓ {_cands[0]['name']} ({_cands[0]['code']})")
            else:
                _opt_lbls = [f"{c['name']} ({c['code']})" for c in _cands]
                _sel = st.selectbox(
                    f"股票{i+1} — 选择公司",
                    _opt_lbls,
                    key=f"compare_select_{i}",
                )
                compare_resolved.append(_cands[_opt_lbls.index(_sel)]["code"])

    col_m2, col_b2 = st.columns([2, 1])
    with col_m2:
        model_choice2 = st.selectbox(
            "模型", ["claude-sonnet", "claude-haiku", "claude-opus"],
            label_visibility="collapsed", key="compare_model"
        )
    with col_b2:
        compare_btn = st.button("开始对比", type="primary", use_container_width=True, key="compare_btn")

    with st.expander("⚙️ 高级选项"):
        force_refresh2 = st.checkbox("强制刷新缓存", value=False, key="compare_refresh")

    st.markdown('</div>', unsafe_allow_html=True)  # end hero-block

    if compare_btn:
        valid_codes = compare_resolved
        if len(valid_codes) < 2:
            st.warning("请至少输入 2 支股票（代码或公司名称）")
        else:
            run_comparison(valid_codes, model_choice2, force_refresh2)
    else:
        st.info("在上方输入 2-4 支股票代码后点击「开始对比」，系统将并行生成报告并横向展示关键维度。")
