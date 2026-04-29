"""
ResearchAgent — 研报聚合分析（v2）。

核心价值：
  从卖方研报中提取对投资决策真正有用的信息：
  1. 行业认知 — 趋势/空间/竞争格局的分析师判断
  2. 公司战略 — 发展方向/重点布局/护城河
  3. 经营管理 — 管理层能力/执行力/盈利质量评价
  4. 主要分歧 — 分析师最不一致的核心问题
  5. 价格参考 — 目标价区间（折叠，仅供参考）

数据来源：
  - 东财研报列表 + PDF 正文（自动获取）
  - 用户上传 PDF（持久化存储）
  - 可对指定子集进行分析

输出：
  - chart_data.reports          — 研报库列表（含 source 字段区分来源）
  - chart_data.industry_view    — 行业认知
  - chart_data.strategy_view    — 公司战略
  - chart_data.management_view  — 经营管理
  - chart_data.key_disagreements— 主要分歧
  - chart_data.price_ref        — 价格参考 {range, avg, note}
  - chart_data.stock_code       — 股票代码（供 UI 交互用）
  - content                     — 空字符串（UI 完全由 chart_data 驱动）
"""

import hashlib
import json
import re
from datetime import datetime, date
from typing import Optional

from src.data.report_parser import fetch_research_reports, load_uploaded_reports, fetch_all_reports
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位独立基本面研究员，善于从卖方研报中过滤噪音，提取对投资决策真正有用的信息。

核心原则：
1. 目标价和评级仅为参考，不是研报的核心价值
2. 研报最有价值的部分是：对行业趋势的判断、对公司战略的解读、对管理层执行力的评估
3. 提炼"分析师为什么这样判断"比"分析师得出什么结论"更重要
4. 如果多篇研报在某点有明显分歧，必须明确指出，不要模糊带过
5. 你的输出必须是合法 JSON，不加任何额外文字或代码块标记

⚠️ JSON 格式严格要求：
- 字符串值内部绝对禁止使用 ASCII 双引号 " — 改用中文引号「」或单引号''代替
- 例如：错误写法 "战略是"全面2C"模式"；正确写法 "战略是「全面2C」模式"
- 所有字段值必须是合法的 JSON 字符串，不得有未转义的控制字符

分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下 {count} 份关于 {name}（{code}）的研报，提取核心信息。

## 研报列表
{metadata_table}

## 盈利预测（EPS 汇总）
{eps_table}

## 研报正文摘录
{report_excerpts}

---
请严格按以下 JSON 格式输出，不加任何额外内容：

{{
  "industry_view": "行业认知：聚焦行业趋势/市场空间/竞争格局，100-200字，要有具体数据支撑",
  "strategy_view": "公司战略：聚焦公司发展方向/核心布局/护城河来源，100-200字",
  "management_view": "经营管理：聚焦管理层能力/执行力/盈利质量/财务健康度，80-150字",
  "key_disagreements": "主要分歧：分析师间最不一致的核心判断点，50-100字；若观点一致写'主流观点较为一致，分歧不显著'",
  "price_ref": {{
    "range": "目标价区间，如 42-58元；若无数据写 '暂无'",
    "avg": 50.0,
    "note": "评级分布，如 买入×4 增持×2"
  }},
  "report_count": {count},
  "has_pdf_text": {has_pdf}
}}

要求：
- 每个字段必须输出，不能省略
- industry_view / strategy_view / management_view 必须有实质内容，不能泛泛而谈
- 如果研报正文不足，基于标题和盈利预测推断，并在对应字段末尾注明"（基于元数据推断）"
- price_ref.avg 若无法计算填 null
"""


# ── 数据格式化 ─────────────────────────────────────────────────

def _build_metadata_table(reports: list[dict]) -> str:
    rows = ["| 日期 | 机构 | 评级 | 标题 |"]
    rows.append("|------|------|------|------|")
    for r in reports:
        src = "📎" if r.get("source") == "uploaded" else "🤖"
        title = r["title"][:28] + "…" if len(r["title"]) > 28 else r["title"]
        rows.append(f"| {r['date']} | {src}{r['institution']} | {r['rating'] or '—'} | {title} |")
    return "\n".join(rows)


def _build_eps_table(reports: list[dict]) -> str:
    years = sorted({yr for r in reports for yr in r.get("eps_forecast", {})})
    if not years:
        return "（无盈利预测数据）"
    rows = ["| 机构 | " + " | ".join(f"{y}E" for y in years) + " |"]
    rows.append("|------| " + " | ".join(["---"] * len(years)) + " |")
    for r in reports:
        eps_f = r.get("eps_forecast", {})
        cells = []
        for yr in years:
            d = eps_f.get(yr, {})
            eps = d.get("eps")
            pe  = d.get("pe")
            if eps:
                cells.append(f"EPS:{eps:.2f}" + (f" PE:{pe:.1f}" if pe else ""))
            else:
                cells.append("—")
        rows.append(f"| {r['institution']} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _build_excerpts(reports: list[dict], max_chars: int = 2000) -> str:
    parts = []
    budget = max_chars
    for r in reports:
        text = r.get("text", "").strip()
        if not text:
            continue
        excerpt = text[:min(500, budget)]
        src = "📎用户上传" if r.get("source") == "uploaded" else "🤖自动获取"
        parts.append(f"**[{r['institution']} · {r['date']} · {src}]**\n{excerpt}")
        budget -= len(excerpt)
        if budget <= 0:
            break
    return "\n\n".join(parts) if parts else "（PDF 正文未获取，仅基于元数据分析）"


def _rating_summary(reports: list[dict]) -> str:
    from collections import Counter
    counts = Counter(r["rating"] for r in reports if r.get("rating"))
    if not counts:
        return "暂无评级数据"
    return "、".join(f"{k}×{v}" for k, v in counts.most_common())


def _price_ref(reports: list[dict], rating_summary: str) -> dict:
    """从 EPS + 当前 PE 估算目标价区间（简化：若有 EPS 预测则计算）。"""
    # 先尝试从报告正文中找目标价数字（简化提取）
    target_prices = []
    for r in reports:
        text = r.get("text", "")
        # 简单正则：目标价 XX 元
        for m in re.finditer(r"目标[价格]{1,2}[为是：:]*\s*(\d+\.?\d*)\s*元", text):
            try:
                target_prices.append(float(m.group(1)))
            except Exception:
                pass

    if len(target_prices) >= 2:
        target_prices.sort()
        rng = f"{target_prices[0]:.0f}–{target_prices[-1]:.0f}元"
        avg = sum(target_prices) / len(target_prices)
    elif len(target_prices) == 1:
        rng = f"约{target_prices[0]:.0f}元"
        avg = target_prices[0]
    else:
        rng = "暂无"
        avg = None

    return {"range": rng, "avg": avg, "note": rating_summary}


# ── JSON 解析 ─────────────────────────────────────────────────

def _parse_llm_json(raw: str) -> Optional[dict]:
    # 1. 剥离 markdown 代码块标记
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

    # 2. 尝试直接解析
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 3. 用正则提取最外层 { ... }
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass

    # 4. 尝试修复：把字符串值内的 ASCII 双引号转义
    try:
        fixed = _fix_unescaped_quotes(cleaned)
        return json.loads(fixed)
    except Exception:
        pass

    # 5. 逐字段正则提取（最终兜底，对格式最宽松）
    result = _extract_fields_by_regex(cleaned or raw)
    if result:
        print(f"[ResearchAgent] JSON 解析通过逐字段正则兜底成功")
        return result

    print(f"[ResearchAgent] JSON 解析彻底失败，原始输出前300字:\n{raw[:300]}")
    return None


def _fix_unescaped_quotes(text: str) -> str:
    """
    修复 JSON 字符串值内部未转义的 ASCII 双引号。
    策略：在非字段分隔位置的 " 前加反斜杠。
    """
    # 简单替换：把形如 ："...内容"内...词"... 的内部引号转义
    # 用状态机逐字符处理
    result = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and in_string:
            result.append(c)
            i += 1
            if i < len(text):
                result.append(text[i])
            i += 1
            continue
        if c == '"':
            if not in_string:
                in_string = True
                result.append(c)
            else:
                # 判断是否是字段结束的引号（后面紧跟 : 或 , 或 } 或换行）
                rest = text[i+1:i+5].lstrip()
                if rest and rest[0] in (':', ',', '}', '\n', '\r'):
                    in_string = False
                    result.append(c)
                else:
                    # 内部未转义引号 → 转义
                    result.append('\\"')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _extract_fields_by_regex(text: str) -> Optional[dict]:
    """
    当 JSON 解析完全失败时，用正则逐字段提取。
    利用字段名作为分隔符，对未转义引号具有容忍性。
    """
    # 已知的所有字段名（按顺序）
    str_fields = ["industry_view", "strategy_view", "management_view", "key_disagreements"]
    anchors = str_fields + ["price_ref", "report_count", "has_pdf_text"]

    result: dict = {}

    for i, field in enumerate(str_fields):
        # 构造截止锚点（下一个字段名）
        next_anchors = anchors[i+1:]
        stop = "|".join(rf'"{a}"' for a in next_anchors)
        # 匹配 "field": "任意内容（直到下一个字段）"
        pat = rf'"{field}"\s*:\s*"(.*?)(?=\s*(?:,\s*)?(?:{stop}))'
        m = re.search(pat, text, re.DOTALL)
        if m:
            val = m.group(1).rstrip('",\n\r ')
            result[field] = val

    # price_ref 块
    pr_m = re.search(r'"price_ref"\s*:\s*\{([^}]+)\}', text, re.DOTALL)
    if pr_m:
        pr_block = '{' + pr_m.group(1) + '}'
        try:
            result["price_ref"] = json.loads(pr_block)
        except Exception:
            # 提取子字段
            rng = re.search(r'"range"\s*:\s*"([^"]*)"', pr_block)
            avg = re.search(r'"avg"\s*:\s*([0-9.]+|null)', pr_block)
            note = re.search(r'"note"\s*:\s*"([^"]*)"', pr_block)
            result["price_ref"] = {
                "range": rng.group(1) if rng else "暂无",
                "avg":   float(avg.group(1)) if avg and avg.group(1) != "null" else None,
                "note":  note.group(1) if note else "",
            }

    # 简单数值字段
    rc = re.search(r'"report_count"\s*:\s*(\d+)', text)
    hp = re.search(r'"has_pdf_text"\s*:\s*(true|false)', text)
    if rc:
        result["report_count"] = int(rc.group(1))
    if hp:
        result["has_pdf_text"] = hp.group(1) == "true"

    return result if len(result) >= 2 else None


def _validate_parsed(data: dict, reports: list[dict]) -> dict:
    """校验并补全 LLM 解析结果。"""
    return {
        "industry_view":      data.get("industry_view", "（未能提取）"),
        "strategy_view":      data.get("strategy_view", "（未能提取）"),
        "management_view":    data.get("management_view", "（未能提取）"),
        "key_disagreements":  data.get("key_disagreements", "（未能提取）"),
        "price_ref":          data.get("price_ref", {"range": "暂无", "avg": None, "note": ""}),
        "report_count":       data.get("report_count", len(reports)),
        "has_pdf_text":       data.get("has_pdf_text", any(r.get("text") for r in reports)),
    }


# ── Cache key ─────────────────────────────────────────────────

def _cache_key(code: str, model: str, data_ver: str, report_ids: str = "") -> str:
    raw = f"research_v2|{code}|{model}|{data_ver}|{report_ids}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"research_{code}_{h}"


def _report_ids(reports: list[dict]) -> str:
    """生成研报集合的标识字符串（用于 cache key）。"""
    parts = sorted(f"{r['institution']}_{r['date']}" for r in reports)
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:8]


# ── Agent ─────────────────────────────────────────────────────

class ResearchAgent:

    def _run_llm(
        self,
        stock_code: str,
        stock_name: str,
        reports: list[dict],
        model: str,
    ) -> dict:
        """调用 LLM，返回解析后的 chart_data 字段（不含 reports）。"""
        has_pdf = any(r.get("text") for r in reports)
        rating_sum = _rating_summary(reports)
        prompt = ANALYSIS_PROMPT.format(
            count=len(reports),
            name=stock_name,
            code=stock_code,
            metadata_table=_build_metadata_table(reports),
            eps_table=_build_eps_table(reports),
            report_excerpts=_build_excerpts(reports),
            has_pdf=str(has_pdf).lower(),
        )
        print(f"[ResearchAgent] 调用 {model}（{len(reports)} 份研报，PDF正文: {has_pdf}）...")
        raw = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content
        parsed = _parse_llm_json(raw)
        if parsed:
            result = _validate_parsed(parsed, reports)
        else:
            print("[ResearchAgent] JSON 解析失败，使用降级数据")
            result = {
                "industry_view":     "（JSON 解析失败，请重试）",
                "strategy_view":     "（JSON 解析失败，请重试）",
                "management_view":   "（JSON 解析失败，请重试）",
                "key_disagreements": "（JSON 解析失败，请重试）",
                "price_ref":         _price_ref(reports, rating_sum),
                "report_count":      len(reports),
                "has_pdf_text":      has_pdf,
            }
        # 补充价格参考（从正文提取，LLM 可能没提取到）
        if result["price_ref"].get("range") in ("暂无", "", None):
            result["price_ref"] = _price_ref(reports, rating_sum)
        return result

    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
        report_limit: int = 6,
    ) -> ReportSection:
        print(f"[ResearchAgent] 开始分析: {stock_code}")

        # 加载所有研报（自动 + 已上传）
        all_reports = fetch_all_reports(stock_code, limit=report_limit)
        auto_reports     = all_reports["auto"]
        uploaded_reports = all_reports["uploaded"]
        reports = auto_reports + uploaded_reports

        # 若无任何研报
        if not reports:
            return ReportSection(
                dimension="research",
                content="",
                confidence=0.0,
                error="未获取到研报数据，可手动上传 PDF",
                chart_data={"stock_code": stock_code, "reports": [], "uploaded": []},
            )

        data_ver    = date.today().strftime("%Y%m%d")
        report_sig  = _report_ids(reports)
        ck          = _cache_key(stock_code, model, data_ver, report_sig)

        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[ResearchAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items()
                                       if not k.startswith("_")})

        # 置信度：有PDF正文的比例
        pdf_count  = sum(1 for r in reports if r.get("text"))
        confidence = round(0.5 + 0.5 * (pdf_count / len(reports)), 2)

        # 获取股票名称
        stock_name = stock_code
        try:
            from src.agents import data_fetcher
            info = data_fetcher.fetch(stock_code, data_types=["info"])
            stock_name = info.get("info", {}).get("股票简称", stock_code)
        except Exception:
            pass

        # LLM 分析
        llm_result = self._run_llm(stock_code, stock_name, reports, model)

        # 构建研报库列表（供 UI 展示，不含正文）
        report_list = [
            {
                "date":        r["date"],
                "institution": r["institution"],
                "title":       r["title"],
                "rating":      r.get("rating", ""),
                "eps_forecast": r.get("eps_forecast", {}),
                "source":      r.get("source", "auto"),
                "filename":    r.get("filename", ""),
            }
            for r in reports
        ]

        chart_data = {
            "stock_code": stock_code,
            "reports":    report_list,
            **llm_result,
        }

        section = ReportSection(
            dimension="research",
            content="",        # UI 完全由 chart_data 驱动
            confidence=confidence,
            data_sources=(
                ["东财研报列表", "研报PDF正文"] if pdf_count > 0 else ["东财研报列表"]
            ) + (["用户上传研报"] if uploaded_reports else []),
            generated_at=datetime.now().isoformat(),
            chart_data=chart_data,
        )
        cache.set(ck, {
            "dimension":    section.dimension,
            "content":      section.content,
            "confidence":   section.confidence,
            "data_sources": section.data_sources,
            "generated_at": section.generated_at,
            "error":        section.error,
            "chart_data":   chart_data,
        })
        print(f"[ResearchAgent] 完成: {stock_code}，{len(reports)} 份研报（{pdf_count} 份含正文）")
        return section

    def analyze_selected(
        self,
        stock_code: str,
        selected_reports: list[dict],
        model: str = "claude-sonnet",
    ) -> dict:
        """
        对指定研报子集进行 LLM 分析，返回 chart_data 字段（不含 reports 列表）。
        不走缓存（on-demand 触发）。
        """
        if not selected_reports:
            return {}
        stock_name = stock_code
        try:
            from src.agents import data_fetcher
            info = data_fetcher.fetch(stock_code, data_types=["info"])
            stock_name = info.get("info", {}).get("股票简称", stock_code)
        except Exception:
            pass
        return self._run_llm(stock_code, stock_name, selected_reports, model)


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return ResearchAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
