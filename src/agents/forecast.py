"""
ForecastAgent — 盈利预测分析。

分析维度：
  1. 分析师评级分布（买入/增持/中性/减持/卖出）
  2. 一致预期 EPS 多年趋势（2025-2028）
  3. 隐含 PE 估值（基于当前股价 × EPS 预测）
  4. 目标价区间（来自 ResearchAgent 的研报数据交叉验证）

数据来源：东财盈利预测、公司基本信息。
"""

import hashlib
from datetime import datetime, date

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位专注于盈利预测和估值的卖方分析师。
你的职责是：
1. 解读分析师一致预期：评级分布反映市场情绪，EPS 趋势反映增长预期
2. 用当前估值水平检验预期是否合理（隐含 PE 是否高估/低估）
3. 识别预期差：市场共识是否存在过度乐观或悲观的迹象

分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下数据，为 {name}（{code}）撰写「盈利预测」AI分析结论。
（注：逐机构预测表格已单独展示，此处只需写分析文字，不要重复列表格）

## 分析师评级分布（近6个月）
{rating_text}

## 一致预期区间（EPS + 净利润）
{consensus_text}

## 逐机构预测样本（最新{n}份）
{reports_sample}

---
请按以下结构输出（Markdown格式，总字数控制在400字以内）：

### 分析师评级概况
（买入/增持占比多少，市场情绪偏乐观/中性/悲观，覆盖广度，2条）

### 盈利趋势判断
（各年度净利润/EPS均值增速，增长加速/放缓/负增长，预测分歧度大小，2-3条）

### 预期差风险
（一致预期是否过于乐观或悲观，最值得关注的1-2个信号）

### 盈利预测小结
（一句话：当前分析师预期反映了什么，对估值有何含义）
"""


def _format_info(info: dict) -> str:
    keys = ["股票简称", "所属行业", "总市值", "上市时间"]
    lines = [f"- {k}：{v}" for k in keys if (v := info.get(k))]
    return "\n".join(lines) if lines else "（基本信息有限）"


def _format_ratings(ratings: dict, institution_count: int) -> str:
    if not ratings:
        return "（无评级数据）"
    total = sum(ratings.values()) or 1
    lines = [f"- 覆盖机构数：{institution_count} 家（近6月，每机构取最新一份研报）"]
    for label, count in ratings.items():
        if count > 0:
            pct = count / total * 100
            lines.append(f"- {label}：{count} 家（{pct:.0f}%）")
    return "\n".join(lines)


def _format_consensus(eps_range: dict, profit_range: dict) -> str:
    if not eps_range:
        return "（无一致预期数据）"
    rows = ["| 年度 | EPS均值(元) | EPS区间 | 净利润均值(亿) | 净利润区间 | 预测机构数 |"]
    rows.append("|------|------------|---------|-------------|-----------|----------|")
    for yr in sorted(eps_range.keys()):
        e = eps_range[yr]
        p = profit_range.get(yr, {})
        eps_avg = f"{e['avg']:.2f}" if e.get("avg") else "N/A"
        eps_rng = f"{e['min']:.2f}~{e['max']:.2f}" if e.get("min") else "N/A"
        np_avg  = f"{p['avg']:.1f}" if p.get("avg") else "N/A"
        np_rng  = f"{p['min']:.1f}~{p['max']:.1f}" if p.get("min") else "N/A"
        cnt = e.get("count", "")
        rows.append(f"| {yr} | {eps_avg} | {eps_rng} | {np_avg} | {np_rng} | {cnt} |")
    return "\n".join(rows)


def _format_reports_sample(reports: list[dict], n: int = 5) -> str:
    if not reports:
        return "（无逐机构预测数据）"
    rows = ["| 机构 | 研究员 | 日期 | 评级 | EPS（各年）|"]
    rows.append("|------|--------|------|------|-----------|")
    for r in reports[:n]:
        eps_str = "  ".join(f"{yr}:{v:.2f}" for yr, v in sorted(r["eps"].items()))
        rows.append(f"| {r['institution']} | {r['researcher']} | {r['date']} | {r['rating']} | {eps_str or 'N/A'} |")
    return "\n".join(rows)


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"forecast|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"forecast_{code}_{h}"


class ForecastAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[ForecastAgent] 开始分析: {stock_code}")

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[ForecastAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "forecast"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="forecast", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)
        forecast_data = stock_data.get("forecast", {})

        reports           = forecast_data.get("reports", [])
        ratings           = forecast_data.get("ratings", {})
        eps_range         = forecast_data.get("eps_range", {})
        profit_range      = forecast_data.get("profit_range", {})
        report_count      = forecast_data.get("report_count", 0)
        institution_count = forecast_data.get("institution_count", len(reports))

        confidence = 0.5
        if institution_count > 0:
            confidence += 0.3
        if len(eps_range) >= 2:
            confidence += 0.2

        sample_n = min(5, len(reports))
        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            rating_text=_format_ratings(ratings, institution_count),
            consensus_text=_format_consensus(eps_range, profit_range),
            reports_sample=_format_reports_sample(reports, sample_n),
            n=sample_n,
        )

        print(f"[ForecastAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        # chart_data：详细指标预测表 + 逐机构预测 + 一致预期区间
        chart_data = {
            "detail_table":     forecast_data.get("detail_table", {}),
            "reports":          reports,
            "ratings":          ratings,
            "eps_range":        eps_range,
            "profit_range":     profit_range,
            "report_count":     report_count,
            "institution_count": institution_count,
        }

        section = ReportSection(
            dimension="forecast",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=["东财研报逐机构预测", "同花顺一致预期区间"],
            generated_at=datetime.now().isoformat(),
            chart_data=chart_data,
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
            "chart_data": chart_data,
        })
        print(f"[ForecastAgent] 完成: {stock_code}")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return ForecastAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
