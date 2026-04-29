"""
CatalystAgent — 最新催化剂分析（v2）。

数据来源：
  - 全类型公告（90天）：重大事项 / 增持 / 减持 / 股权激励 / 业绩预告 / 业绩快报
  - 大股东增减持明细（同花顺）
  - 业绩预告（分析师预期，供 LLM 判断超预期程度）
  - 概念主题（公司所在热门板块）

输出：
  - chart_data.catalysts — 结构化事件卡片列表
  - chart_data.watchpoints — 重点跟进节点
  - chart_data.environment — positive / neutral / negative
  - content — 空字符串（UI 完全由 chart_data 驱动）
"""

import hashlib
import json
import re
from datetime import datetime, date
from typing import Optional

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


# ── 公告类型过滤关键词 ─────────────────────────────────────────
# 东财公告 "type" 字段中包含这些关键词时纳入催化剂分析
NOTICE_INCLUDE_KEYWORDS = [
    "重大事项", "重大合同", "重大资产", "重组", "并购", "合并",
    "增持", "减持",
    "股权激励", "限制性股票", "股票期权",
    "业绩预告", "业绩快报", "盈利预警", "业绩修正",
    "战略合作", "合资", "子公司", "对外投资",
    "定向增发", "可转债", "再融资",
    "诉讼", "仲裁", "问询函", "监管", "立案",
]

NOTICE_EXCLUDE_KEYWORDS = [
    "年度报告", "半年度报告", "季度报告",   # 常规披露，价值低
    "股东大会", "董事会", "监事会",          # 常规会议
    "注销", "注册地址变更", "章程修订",
]

SYSTEM_PROMPT = """你是一位专注于事件驱动投资的研究分析师，擅长从公司公告和 insider 行为中
识别对股价有实质影响的催化剂与风险信号。

核心原则：
1. 每个事件必须判断：signal（positive/negative/neutral）和 importance（high/medium/low）
2. 增持 = 管理层/大股东看好信号；减持 = 信心下降警惕信号
3. 业绩预告超预期 = 强催化剂；低于预期 = 风险信号
4. "重点跟进节点"（watchpoints）必须是具体可验证的里程碑（有时间节点）
5. 你的输出必须是合法 JSON，不加任何额外文字或代码块标记

分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下数据，为 {name}（{code}）生成「最新催化剂」分析。

## 近期重大公告（90天内，共{notice_count}条，已过滤常规披露）
{notices_text}

## 大股东增减持记录（近1年）
{shareholder_text}

## 业绩预告/分析师预期
{forecast_text}

## 概念主题板块
{concepts_text}

---
请严格按以下 JSON 格式输出，不加任何额外内容、代码块或说明文字：

{{
  "catalysts": [
    {{
      "title": "事件标题（直接摘自公告，精简到20字以内）",
      "date": "YYYY-MM-DD",
      "category": "公告类型（如：重大事项/增持/减持/业绩预告等）",
      "signal": "positive 或 negative 或 neutral",
      "importance": "high 或 medium 或 low",
      "summary": "一句话点评：事件含义 + 对公司的影响（30字以内）"
    }}
  ],
  "watchpoints": [
    {{
      "timeline": "具体时间节点（如：Q2 2025/2025年6月/随时）",
      "event": "需要验证的具体事项（20字以内）",
      "rationale": "为什么这个节点重要（15字以内）"
    }}
  ],
  "environment": "positive 或 neutral 或 negative",
  "environment_summary": "一句话总结当前催化剂环境（30字以内）"
}}

要求：
- catalysts 列表按日期倒序，最多8条（优先选重要性高的）
- watchpoints 最多3条，必须具体可验证
- 若公告信息不足，importance 可设为 low，summary 表明"信息有限"
- signal/importance/environment 只能是给定的英文枚举值
"""


# ── 数据格式化函数 ────────────────────────────────────────────

def _filter_notices(notices: list[dict]) -> list[dict]:
    """过滤公告：保留与催化剂相关的类型，剔除常规披露。"""
    result = []
    for n in notices:
        t = str(n.get("type") or n.get("title") or "")
        title = str(n.get("title") or "")
        # 排除常规披露
        if any(kw in t or kw in title for kw in NOTICE_EXCLUDE_KEYWORDS):
            continue
        # 纳入关键催化剂类型
        if any(kw in t or kw in title for kw in NOTICE_INCLUDE_KEYWORDS):
            result.append(n)
    return result[:40]  # 最多 40 条给 LLM


def _fmt_notices(notices: list[dict]) -> str:
    if not notices:
        return "（近90天内无重大公告）"
    lines = []
    for n in notices[:30]:
        date_str = str(n.get("date") or "")[:10]
        title    = str(n.get("title") or "").strip()
        ntype    = str(n.get("type") or "").strip()
        if title:
            lines.append(f"- [{date_str}] 【{ntype}】{title}")
    return "\n".join(lines) if lines else "（近90天内无重大公告）"


def _fmt_shareholder_changes(changes: list[dict]) -> str:
    if not changes:
        return "（近1年内无大股东增减持记录）"
    lines = []
    for c in changes[:10]:
        # 字段名因 API 版本而异，做宽松适配
        name   = (c.get("股东名称") or c.get("变动人") or c.get("名称") or "")
        action = (c.get("变动类型") or c.get("变化类型") or c.get("方向") or "")
        pct    = (c.get("变动比例") or c.get("变化比例") or c.get("占比") or "")
        d      = str(c.get("变动截止日期") or c.get("变化日期") or c.get("日期") or "")[:10]
        if name:
            pct_str = f"，变动比例 {pct}" if pct else ""
            lines.append(f"- [{d}] {name} {action}{pct_str}")
    return "\n".join(lines) if lines else "（近1年内无大股东增减持记录）"


def _fmt_forecast(forecast: dict) -> str:
    if not forecast or not isinstance(forecast, dict):
        return "（无分析师预测数据）"
    lines = []
    count = forecast.get("report_count", 0)
    if count:
        lines.append(f"研报数量：{count} 份")
    ratings = forecast.get("ratings", {})
    buy = ratings.get("买入", 0) + ratings.get("增持", 0)
    sell = ratings.get("减持", 0) + ratings.get("卖出", 0)
    if buy or sell:
        lines.append(f"评级分布：买入/增持 {buy} 份，减持/卖出 {sell} 份")
    eps_range = forecast.get("eps_range", {})
    for yr, rng in list(eps_range.items())[:2]:
        if isinstance(rng, dict) and rng.get("avg"):
            lines.append(f"{yr}年 EPS 预测均值：{rng['avg']:.2f}元（{rng.get('count',0)}家机构）")
    return "\n".join(lines) if lines else "（无分析师预测数据）"


def _fmt_concepts(concepts: list[str]) -> str:
    if not concepts:
        return "（无概念板块数据）"
    return "、".join(concepts[:10])


# ── JSON 解析 ────────────────────────────────────────────────

def _parse_llm_json(raw: str) -> Optional[dict]:
    """从 LLM 输出中提取合法 JSON，容忍前后多余文字。"""
    # 尝试直接解析
    try:
        return json.loads(raw.strip())
    except Exception:
        pass
    # 提取第一个 {...} 块
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


def _validate_parsed(data: dict) -> dict:
    """对 LLM 解析结果做简单校验和修复。"""
    catalysts = data.get("catalysts", [])
    for c in catalysts:
        if c.get("signal") not in ("positive", "negative", "neutral"):
            c["signal"] = "neutral"
        if c.get("importance") not in ("high", "medium", "low"):
            c["importance"] = "medium"
    watchpoints = data.get("watchpoints", [])
    env = data.get("environment", "neutral")
    if env not in ("positive", "negative", "neutral"):
        env = "neutral"
    return {
        "catalysts":           catalysts[:8],
        "watchpoints":         watchpoints[:3],
        "environment":         env,
        "environment_summary": data.get("environment_summary", ""),
    }


# ── Cache key ────────────────────────────────────────────────

def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"catalyst_v2|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"catalyst_{code}_{h}"


# ── Agent ─────────────────────────────────────────────────────

class CatalystAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[CatalystAgent] 开始分析: {stock_code}")

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[CatalystAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items()
                                       if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "notices", "shareholder_change",
                             "forecast", "concepts"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="catalyst", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info          = stock_data.get("info", {})
        stock_name    = info.get("股票简称", stock_code)
        notices_raw   = stock_data.get("notices", []) or []
        shareholder   = stock_data.get("shareholder_change", []) or []
        forecast      = stock_data.get("forecast", {}) or {}
        concepts      = stock_data.get("concepts", []) or []

        # 过滤公告
        notices_filtered = _filter_notices(notices_raw)

        # 置信度
        confidence = 0.4
        if notices_filtered:  confidence += 0.3
        if shareholder:       confidence += 0.15
        if forecast.get("report_count", 0) > 0: confidence += 0.1
        if concepts:          confidence += 0.05

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            notice_count=len(notices_filtered),
            notices_text=_fmt_notices(notices_filtered),
            shareholder_text=_fmt_shareholder_changes(shareholder),
            forecast_text=_fmt_forecast(forecast),
            concepts_text=_fmt_concepts(concepts),
        )

        print(f"[CatalystAgent] 调用 {model}（公告{len(notices_filtered)}条）...")
        raw_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        # 解析结构化输出
        parsed = _parse_llm_json(raw_content)
        chart_data: dict = {}
        if parsed:
            chart_data = _validate_parsed(parsed)
        else:
            print(f"[CatalystAgent] JSON 解析失败，降级为空 chart_data")
            chart_data = {
                "catalysts": [], "watchpoints": [],
                "environment": "neutral", "environment_summary": "",
                "_raw": raw_content[:500],
            }

        section = ReportSection(
            dimension="catalyst",
            content="",           # UI 完全由 chart_data 驱动
            confidence=round(min(confidence, 1.0), 2),
            data_sources=["东财全类型公告", "同花顺增减持", "分析师预期", "概念主题"],
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
        print(f"[CatalystAgent] 完成: {stock_code}，催化剂{len(chart_data.get('catalysts',[]))}条，"
              f"跟进节点{len(chart_data.get('watchpoints',[]))}条")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return CatalystAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
