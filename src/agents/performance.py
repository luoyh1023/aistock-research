"""
PerformanceAgent — 过往业绩分析。

分析维度：营收/利润/毛利率/ROE/EPS 多年趋势、估值分位。
数据来源：财务摘要（近5年）、PE/PB 历史分位。

LLM 输出格式：HTML，利好信息用 <span class="bull">，利空用 <span class="bear">。
渲染顺序：先图表（由 chart_data 驱动），再文字结论（LLM content）。
"""

import hashlib
from datetime import datetime, date
from typing import Optional

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位专业的财务分析师，擅长从历史财务数据中识别经营质量与趋势。
基于已提供的量化数据，你的职责是：
1. 识别营收/利润的增长质量（速度、稳定性、含金量）
2. 发现财务数据中的异常模式（毛利率下滑、ROE恶化等）
3. 给出简洁的业绩质量判断

风格：数据导向、结论明确，避免模糊表述。不需要重新计算数字。
输出格式：HTML（不是 Markdown）。
特别要求：
- 利好信息（增长加速、毛利提升、ROE改善、估值低位等）用 <span class="bull">文字</span> 包裹
- 利空信息（增速放缓、毛利下滑、ROE恶化、估值高位等）用 <span class="bear">文字</span> 包裹
- 重要数字和结论用 <strong>文字</strong> 包裹
- 注意：图表和数据表格已单独展示，此处只需撰写文字结论，无需重复列出数字表格

分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下财务数据，为 {name}（{code}）撰写「过往业绩」文字结论。
（注：详细数据表格和图表已单独展示，此处只需写分析文字，不要重复列表格）

## 近年财务摘要（近到远）
{financials_table}

## 估值历史分位
- 当前 PE：{pe_current}，处于近5年 {pe_pct}% 分位（区间：{pe_min}~{pe_max}）
- 当前 PB：{pb_current}，处于近5年 {pb_pct}% 分位

---
请按以下结构输出 HTML（无需 <!DOCTYPE> 或 <html> 标签，直接输出内容块，总字数控制在400字以内）：

<h3>营收与利润趋势</h3>
<p>（近5年增速节奏，是否稳定，有无明显拐点；增速加快用 bull，放缓/下滑用 bear，2-3条）</p>

<h3>盈利能力分析</h3>
<p>（毛利率/净利率/ROE走势；改善用 bull，恶化用 bear，2-3条）</p>

<h3>财务质量信号</h3>
<p>（现金流含金量、资产负债率变化；异常信号用 bear，健康指标用 bull，2条）</p>

<h3>估值历史水位</h3>
<p>（当前PE/PB处于历史什么位置；低估值分位用 bull，高分位用 bear，1-2条）</p>

<h3>业绩小结</h3>
<p>（一句话综合判断，用 <strong> 包裹核心结论）</p>
"""


# ── 数据解析工具 ──────────────────────────────────────────────

def _parse_yi(s) -> Optional[float]:
    """把 '1741.44亿' / '2782.38万' / 数字 → 亿元 float"""
    s = str(s).strip().replace(",", "")
    if "亿" in s:
        try:
            return round(float(s.replace("亿", "")), 2)
        except Exception:
            return None
    if "万" in s:
        try:
            return round(float(s.replace("万", "")) / 10000, 4)
        except Exception:
            return None
    try:
        f = float(s)
        return f if abs(f) < 1e6 else round(f / 1e8, 2)
    except Exception:
        return None


def _parse_pct(s) -> Optional[float]:
    """把 '15.54%' → 15.54"""
    try:
        return float(str(s).replace("%", "").strip())
    except Exception:
        return None


def _parse_eps(s) -> Optional[float]:
    """把 '2.10元' / '2.10' → 2.10"""
    try:
        return round(float(str(s).replace("元", "").strip()), 2)
    except Exception:
        return None


def _build_chart_data(records: list[dict], val_pct: dict,
                      val_history: dict | None = None) -> dict:
    """
    从财务摘要（最多5年）提取图表所需全量数据，时间从旧→新排列。
    chart_data 结构：
      financial_bars: {years, revenue, net_profit, revenue_growth,
                       gross_margin, net_margin, roe, debt_ratio, eps}
      valuation_percentile: {pe: {...}, pb: {...}}
    """
    years, revenue, revenue_growth = [], [], []
    net_profit, gross_margin, net_margin = [], [], []
    roe, debt_ratio, eps = [], [], []

    for r in reversed(records[:5]):
        period = str(r.get("报告期", ""))
        year = period[:4]
        if not year.isdigit():
            continue
        years.append(year)

        rev_yi = _parse_yi(r.get("营业总收入", ""))
        revenue.append(rev_yi)

        revenue_growth.append(_parse_pct(r.get("营业总收入同比增长率", "")))

        gm = _parse_pct(r.get("销售毛利率", ""))
        nm = _parse_pct(r.get("销售净利率", ""))
        gross_margin.append(gm)
        net_margin.append(nm)

        if rev_yi is not None and nm is not None:
            net_profit.append(round(rev_yi * nm / 100, 2))
        else:
            net_profit.append(None)

        roe.append(_parse_pct(r.get("净资产收益率", "")))
        debt_ratio.append(_parse_pct(r.get("资产负债率", "")))
        eps.append(_parse_eps(r.get("基本每股收益", "")))

    return {
        "financial_bars": {
            "years": years,
            "revenue": revenue,
            "revenue_growth": revenue_growth,
            "net_profit": net_profit,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "roe": roe,
            "debt_ratio": debt_ratio,
            "eps": eps,
        },
        "valuation_percentile": val_pct or {},
        "valuation_history": val_history or {},
    }


# ── 格式化函数（供 LLM Prompt 使用）──────────────────────────

def _format_financials(records: list[dict]) -> str:
    if not records:
        return "（财务数据获取失败）"
    rows = [
        "| 报告期 | 营收 | 营收增速 | 毛利率 | 净利率 | ROE | 资产负债率 | EPS |",
        "|--------|-----|---------|--------|--------|-----|-----------|-----|",
    ]
    for r in records[:5]:
        period = str(r.get("报告期", ""))[:10]
        rows.append(
            f"| {period} | {r.get('营业总收入','N/A')} | "
            f"{r.get('营业总收入同比增长率','N/A')} | "
            f"{r.get('销售毛利率','N/A')} | "
            f"{r.get('销售净利率','N/A')} | "
            f"{r.get('净资产收益率','N/A')} | "
            f"{r.get('资产负债率','N/A')} | "
            f"{r.get('基本每股收益','N/A')} |"
        )
    return "\n".join(rows)


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"performance|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"performance_{code}_{h}"


# ── Agent ─────────────────────────────────────────────────────

class PerformanceAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[PerformanceAgent] 开始分析: {stock_code}")
        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "financials", "valuation"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="performance", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)
        financials = stock_data.get("financials", {})
        summary_records = financials.get("summary", []) if isinstance(financials, dict) else []
        val_pct = stock_data.get("valuation_percentile", {})

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[PerformanceAgent] 命中缓存: {stock_code}")
                return ReportSection(**cached)

        confidence = 1.0
        if not summary_records:
            confidence -= 0.4
        if not val_pct:
            confidence -= 0.2

        # 标准化记录列表
        records_list = (
            summary_records if isinstance(summary_records, list)
            else summary_records.to_dict(orient="records")
            if hasattr(summary_records, "to_dict") else []
        )

        # 图表数据（不依赖 LLM）
        val_history = stock_data.get("valuation_history", {})
        chart_data = _build_chart_data(records_list, val_pct, val_history)

        pe_data = (val_pct or {}).get("pe") or {}
        pb_data = (val_pct or {}).get("pb") or {}

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            financials_table=_format_financials(records_list),
            pe_current=pe_data.get("current", "N/A"),
            pe_pct=pe_data.get("percentile", "N/A"),
            pe_min=pe_data.get("min", "N/A"),
            pe_max=pe_data.get("max", "N/A"),
            pb_current=pb_data.get("current", "N/A"),
            pb_pct=pb_data.get("percentile", "N/A"),
        )

        print(f"[PerformanceAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        section = ReportSection(
            dimension="performance",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=["同花顺财务摘要", "历史PE/PB分位"],
            generated_at=datetime.now().isoformat(),
            chart_data=chart_data,
        )
        cache.set(ck, {
            "dimension": section.dimension,
            "content": section.content,
            "confidence": section.confidence,
            "data_sources": section.data_sources,
            "generated_at": section.generated_at,
            "error": section.error,
            "chart_data": section.chart_data,
        })
        print(f"[PerformanceAgent] 完成: {stock_code}")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return PerformanceAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
