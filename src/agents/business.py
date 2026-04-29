"""
BusinessAgent — 公司概况分析。

分析维度：公司定位、主营构成（产品/地区，含毛利率）、客户集中度、
         供应商依赖、竞争定位。
数据来源：东财主营构成（stock_zygc_em）、同花顺主营介绍（stock_zyjs_ths）、
         年报基本信息（info）、财务摘要（financials.summary）。

LLM 输出格式：HTML，利好信息用 <span class="bull">，利空用 <span class="bear">，
关键数字/结论用 <strong>。
"""

import hashlib
import re
from datetime import datetime, date

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位专业的股票研究分析师，擅长从公开数据中快速梳理公司的基本面全貌。
你的任务是基于提供的数据，为「公司概况」板块撰写结构化 HTML 内容。

风格要求：结论先行、数据支撑、言简意赅，适合有投资经验的散户快速阅读。
输出格式：严格按照下方模板输出 HTML，不要添加额外标签或解释。
特别要求：
- 利好信息用 <span class="bull">文字</span> 包裹（红色，A股红涨）
- 利空信息用 <span class="bear">文字</span> 包裹（绿色，A股绿跌）
- 重要数字和结论用 <strong>文字</strong> 包裹

分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下信息，为 {name}（{code}）生成「公司概况」HTML 内容。

## 基本信息
{info_text}

## 主营业务介绍
{biz_intro_text}

## 主营构成（按产品/业务，最近一期）
{revenue_product_text}

## 主营构成（按地区，最近一期）
{revenue_region_text}

## 近年财务摘要（从近到远）
{financials_text}

---
严格按以下 HTML 模板输出，直接输出内容块（无需 <!DOCTYPE>/<html> 标签）：

<!-- 1. 公司定位：1-2句精炼概括，说明公司是谁、做什么、处于产业链哪个环节 -->
<p class="biz-positioning">（公司定位，含 bull/bear/strong 标注）</p>

<!-- 2. 主营业务与核心产品：2-4条 bullet，具体说明产品线/服务/收入模式 -->
<h3>主营业务与核心产品</h3>
<ul>
  <li><strong>核心产品</strong>：...</li>
  <li><strong>收入模式</strong>：...</li>
  <!-- 可选第3-4条，如技术壁垒、产能特征等 -->
</ul>

<!-- 3. 三格风险评分卡：客户集中度 / 供应商依赖 / 竞争壁垒，每格20字内 -->
<!-- risk-level 只能是：低风险 / 中等风险 / 高风险（客户/供应商），或：弱壁垒 / 中等壁垒 / 强壁垒（竞争）-->
<!-- class 只能是：low（低风险/强壁垒）/ mid（中等）/ high（高风险/弱壁垒）-->
<div class="risk-grid">
  <div class="risk-card [low|mid|high]">
    <div class="risk-label">客户集中度</div>
    <div class="risk-level">低风险</div>
    <div class="risk-desc">（20字内描述依据）</div>
  </div>
  <div class="risk-card [low|mid|high]">
    <div class="risk-label">供应商依赖</div>
    <div class="risk-level">中等风险</div>
    <div class="risk-desc">（20字内描述依据）</div>
  </div>
  <div class="risk-card [low|mid|high]">
    <div class="risk-label">竞争壁垒</div>
    <div class="risk-level">强壁垒</div>
    <div class="risk-desc">（20字内描述依据）</div>
  </div>
</div>
"""


# ── 数据解析工具 ──────────────────────────────────────────────

def _parse_rev_to_yi(s) -> float | None:
    """把 '6.95亿' / '2782.38万' / 数字 → 亿元 float"""
    s = str(s).strip().replace(",", "")
    if "亿" in s:
        try:
            return float(s.replace("亿", ""))
        except Exception:
            return None
    if "万" in s:
        try:
            return round(float(s.replace("万", "")) / 10000, 4)
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None


def _parse_pct_to_float(s) -> float | None:
    """把 '28.84%' → 28.84"""
    try:
        return float(str(s).replace("%", "").strip())
    except Exception:
        return None


_MAX_BARS = 4  # 最多显示几根条形（超出的合并为「其他」）

# 地区关键词：用于语义归并
_DOMESTIC_KW = ["内销", "境内", "国内", "大陆", "华东", "华南", "华北",
                "华中", "华西", "西南", "西北", "东北", "中部", "南部", "北部"]
_OVERSEAS_KW = ["外销", "境外", "国外", "海外", "欧洲", "美洲", "亚洲",
                "非洲", "中东", "东南亚", "澳洲", "大洋洲", "美国", "日本",
                "韩国", "德国", "英国", "法国", "意大利"]


def _merge_group(group: list[dict], label: str) -> dict | None:
    """把一组条目合并成单条（收入占比相加，毛利率加权平均）。"""
    if not group:
        return None
    pct = round(sum(i.get("pct") or 0 for i in group), 2)
    rev = sum(i.get("revenue_yi") or 0 for i in group) or None
    total_rev_for_gm = sum(
        i.get("revenue_yi") or 0 for i in group if i.get("gm") is not None
    )
    gm = (
        round(
            sum((i.get("revenue_yi") or 0) * (i.get("gm") or 0)
                for i in group if i.get("gm") is not None)
            / total_rev_for_gm,
            2,
        )
        if total_rev_for_gm > 0
        else None
    )
    return {"name": label, "pct": pct, "revenue_yi": rev, "gm": gm}


def _aggregate_products(items: list[dict]) -> list[dict]:
    """
    产品聚合：取前 _MAX_BARS 条，其余合并为「其他」。
    items 已按 pct 降序排列。
    """
    if len(items) <= _MAX_BARS:
        return items
    top  = list(items[:_MAX_BARS])
    rest = items[_MAX_BARS:]
    other = _merge_group(rest, "其他")
    if other and other["pct"] > 0:
        top.append(other)
    return top


def _aggregate_regions(items: list[dict]) -> list[dict]:
    """
    地区聚合两步走：
    1. 过滤掉「其中-」「其中：」子条目（子类和父类重复计算）。
    2. 若仍 > _MAX_BARS，按境内/境外语义合并为两大类。
    """
    # Step 1：去掉 "其中" 子行
    filtered = [i for i in items if "其中" not in i.get("name", "")]

    if len(filtered) <= _MAX_BARS:
        return filtered

    # Step 2：按语义归并境内/境外
    domestic, overseas, others = [], [], []
    for item in filtered:
        name = item.get("name", "")
        if any(k in name for k in _DOMESTIC_KW):
            domestic.append(item)
        elif any(k in name for k in _OVERSEAS_KW):
            overseas.append(item)
        else:
            others.append(item)

    result = []
    if d := _merge_group(domestic, "境内"):
        result.append(d)
    if o := _merge_group(overseas, "境外"):
        result.append(o)
    # 剩余无法归类的保留原条目（通常极少）
    result.extend(others)
    return sorted(result, key=lambda x: x.get("pct") or 0, reverse=True)


def _build_chart_data(overview: dict) -> dict:
    """
    从 revenue_breakdown / revenue_region 提取横向条形图所需数据。
    akshare_client 已将 pct/gm 归一化为百分比（如 86.77），revenue 为亿元。
    输出经过语义聚合，最多 _MAX_BARS + 1（其他）条。
    """
    def _parse_items(items: list[dict]) -> list[dict]:
        out = []
        for item in items:
            name_col = next((c for c in item if c == "主营构成"), None)
            pct_col  = next((c for c in item if c == "收入比例"), None)
            rev_col  = next((c for c in item if c == "主营收入"), None)
            gm_col   = next((c for c in item if "毛利率" in c), None)

            if not name_col or not pct_col:
                continue

            name = str(item.get(name_col, ""))[:24]
            # 过滤噪音行
            if "补充" in name or name.strip() == "":
                continue

            pct = item.get(pct_col)
            if pct is None:
                continue
            try:
                pct = float(pct)
            except Exception:
                continue

            rev_yi = item.get(rev_col)
            try:
                rev_yi = float(rev_yi) if rev_yi is not None else None
            except Exception:
                rev_yi = None

            gm = item.get(gm_col)
            try:
                gm = float(gm) if gm is not None else None
            except Exception:
                gm = None

            out.append({"name": name, "revenue_yi": rev_yi, "pct": pct, "gm": gm})

        # 按收入占比降序
        out.sort(key=lambda x: x.get("pct") or 0, reverse=True)
        return out

    product_raw = _parse_items(overview.get("revenue_breakdown", []))
    region_raw  = _parse_items(overview.get("revenue_region", []))

    return {
        "revenue_pie": {
            "product": _aggregate_products(product_raw),
            "region":  _aggregate_regions(region_raw),
        }
    }


# ── 格式化函数（供 LLM Prompt 使用）──────────────────────────

def _format_info(info: dict) -> str:
    keys = ["股票简称", "所属行业", "行业", "总市值", "流通市值", "上市时间",
            "主营业务", "经营范围", "员工人数"]
    lines = []
    for k in keys:
        if v := info.get(k):
            lines.append(f"- {k}：{v}")
    return "\n".join(lines) if lines else "（基本信息获取失败）"


def _format_biz_intro(intro) -> str:
    if not intro:
        return "（未获取到主营介绍）"
    # 港股 business_intro 直接为字符串
    if isinstance(intro, str):
        return intro[:400] if intro.strip() else "（未获取到主营介绍）"
    lines = []
    for k in ["主营业务", "产品类型", "产品名称", "经营范围"]:
        v = str(intro.get(k, "")).strip()
        if v and v not in ("nan", "None"):
            lines.append(f"- {k}：{v[:200]}")
    return "\n".join(lines) if lines else "（未获取到主营介绍）"


def _format_revenue_breakdown(items: list[dict]) -> str:
    if not items:
        return "（未获取到主营构成数据）"
    sample = items[0]
    col_name    = next((c for c in sample if "主营构成" in c or ("分类" in c and "类型" not in c)), None)
    col_rev     = next((c for c in sample if "主营收入" in c and "比例" not in c), None)
    col_rev_pct = next((c for c in sample if "收入比例" in c or "收入占比" in c), None)
    col_gm      = next((c for c in sample if "毛利率" in c), None)
    col_profit_pct = next((c for c in sample if "利润比例" in c), None)

    headers = ["主营构成"]
    if col_rev:     headers.append("主营收入")
    if col_rev_pct: headers.append("收入占比")
    if col_gm:      headers.append("毛利率")
    if col_profit_pct: headers.append("利润占比")

    rows = ["| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |"]
    for item in items[:8]:
        name_val = str(item.get(col_name, "—"))[:20] if col_name else "—"
        row = [name_val]
        if col_rev:        row.append(item.get(col_rev, "—"))
        if col_rev_pct:    row.append(item.get(col_rev_pct, "—"))
        if col_gm:         row.append(item.get(col_gm, "—"))
        if col_profit_pct: row.append(item.get(col_profit_pct, "—"))
        rows.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(rows)


def _format_financials(records: list[dict]) -> str:
    if not records:
        return "（财务数据获取失败）"
    rows = ["| 报告期 | 营收 | 营收增速 | 毛利率 | 净利率 | ROE |",
            "|--------|-----|---------|--------|--------|-----|"]
    for r in records[:5]:
        period = str(r.get("报告期", ""))[:10]
        rows.append(
            f"| {period} | {r.get('营业总收入','N/A')} | "
            f"{r.get('营业总收入同比增长率','N/A')} | "
            f"{r.get('销售毛利率','N/A')} | "
            f"{r.get('销售净利率','N/A')} | "
            f"{r.get('净资产收益率','N/A')} |"
        )
    return "\n".join(rows)


# ── 缓存 ─────────────────────────────────────────────────────

def _cache_key(code: str, model: str) -> str:
    raw = f"business_overview|{code}|{model}|{date.today().strftime('%Y%m%d')}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"business_{code}_{h}"


# ── Agent ─────────────────────────────────────────────────────

class BusinessAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[BusinessAgent] 开始分析公司概况: {stock_code}")
        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "financials", "business_overview", "concepts"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="business", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)
        financials = stock_data.get("financials", {})
        summary_records = financials.get("summary", []) if isinstance(financials, dict) else []
        overview = stock_data.get("business_overview", {})

        ck = _cache_key(stock_code, model)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[BusinessAgent] 命中缓存: {stock_code}")
                return ReportSection(**cached)

        # 置信度
        confidence = 1.0
        if not overview.get("revenue_breakdown"):
            confidence -= 0.3
        if not overview.get("business_intro"):
            confidence -= 0.1
        if not summary_records:
            confidence -= 0.2

        # 图表数据（独立于 LLM）
        chart_data = _build_chart_data(overview)

        # 公司档案 chips（行业 / 市值 / 员工 / 上市时间）
        def _pick(*keys):
            for k in keys:
                v = str(info.get(k, "")).strip()
                if v and v not in ("nan", "None", ""):
                    return v
            return ""

        meta: dict[str, str] = {}
        if ind := _pick("所属行业", "行业"):
            meta["industry"] = ind
        if cap := _pick("总市值", "流通市值"):
            meta["market_cap"] = cap
        if emp := _pick("员工人数"):
            meta["employees"] = emp
        if lst := _pick("上市时间", "上市日期"):
            meta["listed_date"] = str(lst)[:10]

        # 降级兜底：从 yjbb 快照补充缺失的行业/市值
        if not meta.get("industry") or not meta.get("market_cap"):
            try:
                from src.data.akshare_client import _fetch_yjbb_cached
                yjbb = _fetch_yjbb_cached()
                row = yjbb[yjbb["股票代码"] == stock_code]
                if not row.empty:
                    r = row.iloc[0]
                    if not meta.get("industry"):
                        ind2 = str(r.get("所处行业", "")).strip()
                        if ind2 and ind2 not in ("nan", "None", ""):
                            meta["industry"] = ind2
                    if not meta.get("market_cap"):
                        cap2 = str(r.get("总市值", "")).strip()
                        if cap2 and cap2 not in ("nan", "None", ""):
                            # yjbb 总市值单位为元，转换为亿
                            try:
                                cap_yi = float(cap2) / 1e8
                                meta["market_cap"] = f"{cap_yi:.0f}亿"
                            except Exception:
                                meta["market_cap"] = cap2
            except Exception:
                pass

        chart_data["company_meta"] = meta
        chart_data["concept_tags"] = stock_data.get("concepts", [])[:6]

        # LLM
        if isinstance(summary_records, list):
            fin_text = _format_financials(summary_records)
        elif hasattr(summary_records, "to_dict"):
            fin_text = _format_financials(summary_records.to_dict(orient="records"))
        else:
            fin_text = "（财务数据格式异常）"

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            info_text=_format_info(info),
            biz_intro_text=_format_biz_intro(overview.get("business_intro", {})),
            revenue_product_text=_format_revenue_breakdown(overview.get("revenue_breakdown", [])),
            revenue_region_text=_format_revenue_breakdown(overview.get("revenue_region", [])),
            financials_text=fin_text,
        )

        print(f"[BusinessAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        # LLM 有时会用 ```html ... ``` 包裹输出，需要去掉
        result_content = re.sub(r'^```(?:html)?\s*\n?', '', result_content.strip())
        result_content = re.sub(r'\n?```\s*$', '', result_content.strip())
        result_content = result_content.strip()

        section = ReportSection(
            dimension="business",
            content=result_content,
            confidence=round(max(0.1, confidence), 2),
            data_sources=["东财主营构成(zygc)", "同花顺主营介绍(zyjs)", "东财个股信息", "同花顺财务摘要"],
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
        print(f"[BusinessAgent] 完成: {stock_code}")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return BusinessAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
