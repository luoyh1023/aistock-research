"""
ShareholderAgent — 股东分析。

展示顺序：
  1. 股东人数趋势（筹码集中度）
  2. 前十大股东（持股结构）
  3. 流通股前十大（含股东性质）
  4. 北向资金月度趋势
  [LLM 综合结论在最下方]

数据来源：东财/AKShare。
"""

import hashlib
from datetime import datetime, date

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位专注于股东结构分析的机构研究员。
基于已提供的量化数据，请综合分析：
1. 股东人数变化趋势 → 筹码集中/分散信号
2. 大股东持股稳定性
3. 机构（基金/社保/QFII）和北向资金的增减持方向
4. 综合判断：当前股东结构是偏健康还是需要警惕

注意：图表数据已单独展示，此处只写文字结论，不要重复表格。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下数据，为 {name}（{code}）撰写「股东分析」综合结论。
（图表和表格已单独展示，只写文字，不重复数据）

## 股东人数变化（近8期）
{holder_count_text}

## 前十大股东（最新期）
{top10_text}

## 流通股前十大（含机构性质）
{top10_free_text}

## 北向资金（近12个月月度）
{northbound_text}

---
请按以下结构输出（Markdown格式，总字数控制在350字以内）：

### 筹码集中度信号
（股东人数趋势方向，是集中还是分散，结合股价判断含义，1-2条）

### 机构与外资动向
（基金/QFII/社保持仓性质，北向资金近期净增减持方向，2条；无数据写"暂无相关数据"）

### 股东结构综合判断
（一句话：当前股东结构最值得关注的信号，看多/中性/谨慎）
"""


def _safe_str(v, default="—") -> str:
    if v is None or str(v).strip() in ("nan", "None", ""):
        return default
    return str(v)


def _fmt_holder_count(records: list[dict]) -> str:
    if not records:
        return "（无数据）"
    lines = []
    for r in records[:4]:
        date_v = _safe_str(r.get("股东户数统计截止日", ""))[:10]
        cnt    = _safe_str(r.get("股东户数-本次"))
        chg    = _safe_str(r.get("股东户数-增减比例"))
        avg_v  = _safe_str(r.get("户均持股市值"))
        lines.append(f"- {date_v}：{cnt}户，较上期{chg}，户均市值{avg_v}")
    return "\n".join(lines)


def _fmt_top10(records: list[dict]) -> str:
    if not records:
        return "（无数据）"
    rows = []
    for r in records[:10]:
        name  = str(r.get("股东名称", ""))[:18]
        qty   = _safe_str(r.get("持股数"))
        pct   = _safe_str(r.get("占总股本持股比例"))
        chg   = _safe_str(r.get("增减"))
        rows.append(f"- {name}：{qty}股，占{pct}%，{chg}")
    return "\n".join(rows)


def _fmt_top10_free(records: list[dict]) -> str:
    if not records:
        return "（无数据）"
    rows = []
    for r in records[:10]:
        name    = str(r.get("股东名称", ""))[:18]
        nature  = _safe_str(r.get("股东性质"), "其它")
        pct     = _safe_str(r.get("占总流通股本持股比例"))
        chg     = _safe_str(r.get("增减"))
        rows.append(f"- [{nature}] {name}：占流通股{pct}%，{chg}")
    return "\n".join(rows)


def _fmt_northbound(records: list[dict], data_date: str = "") -> str:
    if not records:
        return "（无北向数据，可能为非沪深港通标的）"
    # 检查数据时效性：滞后超过12个月则注明不具参考价值
    if data_date:
        try:
            from datetime import date
            lag_days = (date.today() - date.fromisoformat(data_date)).days
            if lag_days > 365:
                return f"（北向数据严重滞后，截至 {data_date}，距今约 {lag_days // 30} 个月，不具参考价值，请忽略此项）"
        except Exception:
            pass
    recent = records[-3:] if len(records) >= 3 else records
    lines = []
    for r in recent:
        month = _safe_str(r.get("月份"))
        qty   = r.get("持股数量")
        pct   = _safe_str(r.get("持股数量占A股百分比"))
        net   = r.get("当月净增持")
        net_str = f"+{net:,.0f}" if (net and net > 0) else (f"{net:,.0f}" if net else "—")
        lines.append(f"- {month}：持股{qty:,.0f}股（占A股{pct}%），当月净增持{net_str}股")
    return "\n".join(lines)


def _build_chart_data(shareholders: dict) -> dict:
    return {
        "holder_count":        shareholders.get("holder_count", []),
        "top10":               shareholders.get("top10", []),
        "top10_free":          shareholders.get("top10_free", []),
        "northbound":          shareholders.get("northbound", []),
        "northbound_note":     shareholders.get("northbound_note", ""),
        "northbound_data_date": shareholders.get("northbound_data_date", ""),
    }


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"shareholder|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"shareholder_{code}_{h}"


class ShareholderAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[ShareholderAgent] 开始分析: {stock_code}")

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[ShareholderAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "shareholders"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="shareholder", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)
        shareholders = stock_data.get("shareholders", {})
        if not isinstance(shareholders, dict):
            shareholders = {}

        holder_count = shareholders.get("holder_count", [])
        top10        = shareholders.get("top10", [])
        top10_free   = shareholders.get("top10_free", [])
        northbound   = shareholders.get("northbound", [])

        confidence = 0.4
        if holder_count: confidence += 0.2
        if top10:        confidence += 0.2
        if top10_free:   confidence += 0.1
        if northbound:   confidence += 0.1

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            holder_count_text=_fmt_holder_count(holder_count),
            top10_text=_fmt_top10(top10),
            top10_free_text=_fmt_top10_free(top10_free),
            northbound_text=_fmt_northbound(northbound, shareholders.get("northbound_data_date", "")),
        )

        print(f"[ShareholderAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        chart_data = _build_chart_data(shareholders)

        section = ReportSection(
            dimension="shareholder",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=["东财前十大股东", "东财流通股东", "东财股东人数", "沪深港通北向资金"],
            generated_at=datetime.now().isoformat(),
            chart_data=chart_data,
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
            "chart_data": chart_data,
        })
        print(f"[ShareholderAgent] 完成: {stock_code}")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return ShareholderAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
