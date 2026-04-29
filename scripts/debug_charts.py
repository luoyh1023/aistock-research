"""
图表渲染独立调试页面
用法：
    streamlit run scripts/debug_charts.py

完全使用硬编码 mock 数据，不依赖 API / LLM / 缓存。
目的：确认 Plotly 图表在 Streamlit 中能正确渲染，
     与数据管道无关，快速定位是"数据问题"还是"渲染问题"。

页面分三个 Tab：
  Tab 1 — 过往业绩 dashboard（metric cards + 数据表格 + 4图）
  Tab 2 — 公司概况 收入结构横向条形图
  Tab 3 — 自定义数据（粘贴 JSON chart_data 验证）
"""

import sys
import json
from pathlib import Path

import streamlit as st

try:
    import plotly.graph_objects as go
    PLOTLY_OK = True
except ImportError:
    PLOTLY_OK = False
    st.error("❌ plotly 未安装：pip install plotly")

# ── Mock 数据 ────────────────────────────────────────────────

MOCK_PERFORMANCE = {
    "financial_bars": {
        "years":          ["2020", "2021", "2022", "2023", "2024"],
        "revenue":        [460.2,  573.1,  645.4,  591.3,  612.8],
        "revenue_growth": [14.5,   24.5,   12.6,   -8.3,   3.6],
        "net_profit":     [39.8,   49.3,   52.2,   61.5,   58.2],
        "gross_margin":   [23.1,   22.0,   23.5,   26.0,   25.4],
        "net_margin":     [8.6,    8.6,    8.1,    10.4,   9.5],
        "roe":            [11.9,   9.8,    9.5,    10.3,   9.8],
        "debt_ratio":     [60.2,   61.5,   63.3,   66.1,   65.0],
        "eps":            [1.73,   1.89,   1.81,   2.10,   1.95],
    },
    "valuation_percentile": {
        "pe": {"current": 18.5, "percentile": 35.0, "min": 12.3, "max": 32.6, "median": 19.4},
        "pb": {"current": 1.9,  "percentile": 40.0, "min": 1.2,  "max": 3.8,  "median": 2.1},
    },
}

MOCK_REVENUE = {
    "product": [
        {"name": "充电模块", "pct": 96.15, "revenue_yi": 6.95, "gm": 28.84},
        {"name": "其他",    "pct": 3.85,  "revenue_yi": 0.28, "gm": 48.33},
    ],
    "region": [
        {"name": "内销", "pct": 81.08, "revenue_yi": 5.86, "gm": 23.64},
        {"name": "外销", "pct": 18.92, "revenue_yi": 1.37, "gm": 55.06},
    ],
}

# ── 图表组件（复制自 app.py，独立可运行）────────────────────

_CHART_BG = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#f8f9fa")
_NO_BAR   = dict(displayModeBar=False)


def render_performance_dashboard(cd: dict):
    bars  = cd.get("financial_bars", {})
    years = bars.get("years", [])
    if not years:
        st.warning("⚠️ financial_bars.years 为空，无法渲染")
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

    val_pct = cd.get("valuation_percentile", {})
    pe_data = (val_pct or {}).get("pe") or {}

    def _latest(lst):
        for v in reversed(lst):
            if v is not None:
                return v
        return None

    def _fmt(v, suffix="", dec=1):
        return f"{v:.{dec}f}{suffix}" if v is not None else "—"

    # ── 卡片 ─────────────────────────────────────────────────
    st.markdown("#### 关键指标卡片")
    c1, c2, c3, c4, c5 = st.columns(5)
    lat_rev    = _latest(revenue)
    lat_growth = _latest(revenue_growth)
    lat_gm     = _latest(gross_margin)
    lat_roe    = _latest(roe)
    lat_debt   = _latest(debt_ratio)
    pe_cur     = pe_data.get("current")
    pe_pct_val = pe_data.get("percentile")

    with c1:
        delta = f"{lat_growth:+.1f}%" if lat_growth is not None else None
        st.metric("最新年度营收", _fmt(lat_rev, "亿"), delta=delta, delta_color="normal")
    with c2:
        st.metric("毛利率", _fmt(lat_gm, "%"))
    with c3:
        st.metric("ROE", _fmt(lat_roe, "%"))
    with c4:
        st.metric("资产负债率", _fmt(lat_debt, "%"))
    with c5:
        pe_delta = f"近5年{pe_pct_val:.0f}%分位" if pe_pct_val is not None else None
        st.metric("当前PE", _fmt(pe_cur), delta=pe_delta, delta_color="off")

    # ── 数据表格 ──────────────────────────────────────────────
    st.markdown("#### 近五年关键财务指标")

    def _cell(v, suffix="", dec=1, color_fn=None) -> str:
        if v is None:
            return "<td style='color:#aaa'>—</td>"
        txt = f"{v:.{dec}f}{suffix}"
        cls = color_fn(v) if color_fn else ""
        if cls:
            return f'<td><span style="color:{"#D32F2F" if cls=="bull" else "#00796B"};font-weight:600">{txt}</span></td>'
        return f"<td>{txt}</td>"

    header = "<tr><th>指标</th>" + "".join(
        f"<th style='text-align:center'>{y}</th>" for y in years
    ) + "</tr>"

    rows_def = [
        ("营收",        revenue,        "亿",  1, None),
        ("营收增速",    revenue_growth, "%",   1, lambda v: "bull" if v > 0 else "bear"),
        ("毛利率",      gross_margin,   "%",   1, None),
        ("净利率",      net_margin,     "%",   1, None),
        ("ROE",        roe,            "%",   1, None),
        ("资产负债率",  debt_ratio,     "%",   1, lambda v: "bear" if v > 65 else ""),
        ("每股收益EPS", eps,            "元",  2, None),
    ]

    tbody = ""
    for label, vals, suffix, dec, color_fn in rows_def:
        cells = "".join(_cell(v, suffix, dec, color_fn) for v in vals)
        tbody += f"<tr><td><strong>{label}</strong></td>{cells}</tr>"

    table_css = """
    <style>
    .dbg-table { width:100%; border-collapse:collapse; font-size:0.9rem; }
    .dbg-table th { background:#e8f0fe; padding:6px 10px; text-align:left; }
    .dbg-table td { padding:5px 10px; border-bottom:1px solid #eee; }
    </style>
    """
    st.markdown(
        f'{table_css}<div style="overflow-x:auto">'
        f'<table class="dbg-table"><thead>{header}</thead><tbody>{tbody}</tbody></table></div>',
        unsafe_allow_html=True,
    )

    # ── 趋势图表 2×2 ──────────────────────────────────────────
    st.markdown("#### 趋势图表")
    c_l, c_r = st.columns(2)

    with c_l:
        fig1 = go.Figure(go.Bar(
            x=years, y=revenue,
            marker_color="#1a73e8",
            text=[_fmt(v, "亿") for v in revenue],
            textposition="outside", textfont_size=11,
        ))
        fig1.update_layout(
            title="营收（亿）", height=280,
            margin=dict(l=10, r=10, t=40, b=10),
            yaxis=dict(gridcolor="#e0e0e0", zeroline=False),
            xaxis=dict(showgrid=False),
            **_CHART_BG,
        )
        st.plotly_chart(fig1, use_container_width=True, config=_NO_BAR)

    with c_r:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=years, y=gross_margin, name="毛利率",
            mode="lines+markers",
            line=dict(color="#00897b", width=2.5), marker=dict(size=7),
        ))
        fig2.add_trace(go.Scatter(
            x=years, y=net_margin, name="净利率",
            mode="lines+markers",
            line=dict(color="#1a73e8", width=2.5), marker=dict(size=7),
        ))
        fig2.update_layout(
            title="毛利率 / 净利率 %", height=280,
            margin=dict(l=10, r=10, t=40, b=40),
            yaxis=dict(gridcolor="#e0e0e0", zeroline=False, ticksuffix="%"),
            xaxis=dict(showgrid=False),
            legend=dict(orientation="h", y=-0.32, x=0.5, xanchor="center"),
            **_CHART_BG,
        )
        st.plotly_chart(fig2, use_container_width=True, config=_NO_BAR)

    c_l2, c_r2 = st.columns(2)

    with c_l2:
        fig3 = go.Figure(go.Scatter(
            x=years, y=roe,
            mode="lines+markers",
            line=dict(color="#8e24aa", width=2.5), marker=dict(size=8),
            fill="tozeroy", fillcolor="rgba(142,36,170,0.08)",
        ))
        fig3.update_layout(
            title="ROE %", height=270,
            margin=dict(l=10, r=10, t=40, b=10),
            yaxis=dict(gridcolor="#e0e0e0", zeroline=False, ticksuffix="%"),
            xaxis=dict(showgrid=False),
            **_CHART_BG,
        )
        st.plotly_chart(fig3, use_container_width=True, config=_NO_BAR)

    with c_r2:
        fig4 = go.Figure(go.Bar(
            x=years, y=eps,
            marker_color="#f4b942",
            text=[_fmt(v, "元", 2) for v in eps],
            textposition="outside", textfont_size=11,
        ))
        fig4.update_layout(
            title="EPS（元/股）", height=270,
            margin=dict(l=10, r=10, t=40, b=10),
            yaxis=dict(gridcolor="#e0e0e0", zeroline=False),
            xaxis=dict(showgrid=False),
            **_CHART_BG,
        )
        st.plotly_chart(fig4, use_container_width=True, config=_NO_BAR)


def render_revenue_bars(chart_data: dict):
    product = chart_data.get("product", [])
    region  = chart_data.get("region",  [])
    if not product and not region:
        st.warning("⚠️ product 和 region 均为空")
        return

    PALETTE = ["#1a73e8", "#4a90d9", "#7bb3f0", "#afd0f7", "#c3d9ff"]

    def _one_chart(items: list, title: str):
        names  = [d["name"] for d in items]
        pcts   = [d.get("pct") or 0 for d in items]
        gms    = [d.get("gm") for d in items]
        colors = [PALETTE[i % len(PALETTE)] for i in range(len(names))]
        if pcts:
            colors[pcts.index(max(pcts))] = "#1a73e8"
        text_labels = []
        for p, g in zip(pcts, gms):
            t = f"{p:.1f}%"
            if g is not None:
                t += f"  毛利率 {g:.1f}%"
            text_labels.append(t)
        fig = go.Figure(go.Bar(
            y=names, x=pcts, orientation="h",
            marker=dict(color=colors, line=dict(color="#fff", width=0.8)),
            text=text_labels, textposition="outside", textfont=dict(size=11),
        ))
        max_x = max(pcts) * 1.55 if pcts else 100
        fig.update_layout(
            title=dict(text=title, font=dict(size=13)),
            height=max(180, len(names) * 46 + 70),
            margin=dict(l=10, r=16, t=40, b=10),
            xaxis=dict(
                title="收入占比（%）", range=[0, max_x],
                showgrid=True, gridcolor="#e8e8e8", zeroline=False,
            ),
            yaxis=dict(autorange="reversed", tickfont=dict(size=12)),
            **_CHART_BG,
        )
        return fig

    sections = []
    if product: sections.append((product, "按产品"))
    if region:  sections.append((region,  "按地区"))

    if len(sections) == 1:
        st.plotly_chart(_one_chart(*sections[0]), use_container_width=True, config=_NO_BAR)
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(_one_chart(*sections[0]), use_container_width=True, config=_NO_BAR)
        with c2:
            st.plotly_chart(_one_chart(*sections[1]), use_container_width=True, config=_NO_BAR)


# ── Streamlit 主页面 ─────────────────────────────────────────

st.set_page_config(
    page_title="AIStock 图表调试",
    layout="wide",
    page_icon="🔬",
)

st.title("🔬 图表渲染调试页")
st.caption("使用硬编码 mock 数据，与 API / LLM / 缓存完全无关。验证渲染本身是否正常。")

if not PLOTLY_OK:
    st.stop()

tab1, tab2, tab3 = st.tabs(["📊 过往业绩 Dashboard", "🥧 收入结构横向条形图", "🔧 自定义 chart_data"])

with tab1:
    st.markdown("### Mock 数据（过往业绩）")
    with st.expander("查看 mock chart_data"):
        st.json(MOCK_PERFORMANCE)

    st.divider()
    render_performance_dashboard(MOCK_PERFORMANCE)

with tab2:
    st.markdown("### Mock 数据（主营收入结构）")
    with st.expander("查看 mock chart_data"):
        st.json(MOCK_REVENUE)

    st.divider()
    render_revenue_bars(MOCK_REVENUE)

with tab3:
    st.markdown("### 粘贴真实 chart_data 验证")
    st.caption("把 `test_chart_data.py` 输出的 chart_data JSON 粘贴到下方，验证图表是否渲染正常。")

    chart_type = st.radio("图表类型", ["过往业绩", "收入结构"], horizontal=True)
    raw = st.text_area("chart_data JSON", height=300,
                        placeholder='{"financial_bars": {"years": [...], ...}}')
    if st.button("渲染") and raw.strip():
        try:
            data = json.loads(raw)
            st.divider()
            if chart_type == "过往业绩":
                render_performance_dashboard(data)
            else:
                render_revenue_bars(data)
        except json.JSONDecodeError as e:
            st.error(f"JSON 解析失败: {e}")
        except Exception as e:
            import traceback
            st.error(f"渲染出错: {e}")
            st.code(traceback.format_exc())
