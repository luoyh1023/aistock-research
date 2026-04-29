"""
FundamentalAgent — 基本面分析师。
设计方向：分析工具（Direction B）——透明假设、用户可调、结果可追溯。

缓存策略：
- 数据缓存：DataFetcherAgent 负责（3个月 + 季报节点失效）
- 分析缓存：按 {stock_code}_{model}_{assumptions_hash}_{data_version} 索引
             假设不变 + 数据未更新 → 直接返回缓存
             数据更新后 → 旧缓存保留，新分析另存，支持历史对比

用法：
    from src.agents.fundamental import FundamentalAgent
    agent = FundamentalAgent()
    result = agent.analyze("600519", model="claude-sonnet")
    # 调整假设
    from src.agents.fundamental_calculator import AnalysisAssumptions
    result = agent.analyze("600519", model="claude-sonnet",
                           assumptions=AnalysisAssumptions(revenue_growth_rate=0.06))
"""

import json
from datetime import datetime
from typing import Optional

from src.agents.fundamental_calculator import (
    AnalysisAssumptions,
    CalculationBundle,
    calculate,
)
from src.agents import data_fetcher
from src.models.model_router import complete
from src.utils import cache


# ── Prompt ──────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位专业的股票基本面分析师，风格客观、数据驱动。
你的分析基于已经计算好的量化结果（DCF、PE分位、财务健康度等），你的职责是：
1. 解读这些数字的含义和背后的业务逻辑
2. 识别财务数据中的异常模式和趋势
3. 生成清晰、有观点的分析报告
4. 明确指出哪些结论强依赖于假设参数，哪些是数据硬事实

注意：所有数字已由代码预计算，你不需要做数学运算。你的任务是解释和判断。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下量化分析结果，对 {name} 撰写基本面分析报告。

## 量化计算结果

### 股票基本信息
- 代码：{code}
- 数据版本：{data_version}（基于该财报期数据）
- 当前价格：{current_price}

### 估值分析
- 当前 PE：{pe_current}，处于近{pe_years}年历史 {pe_percentile}% 分位
  （历史区间：{pe_min} ~ {pe_max}，中位数 {pe_median}）
- 当前 PB：{pb_current}，处于历史 {pb_percentile}% 分位
- DCF 估值（三情景）：
  - 保守：{dcf_bear} 元
  - 基准：{dcf_base} 元（当前价格 {dcf_upside_pct}% 空间）
  - 乐观：{dcf_bull} 元
- 综合估值分：{valuation_score}/100（越低越低估，<35低估，>65高估）

### 本次分析使用的核心假设
{assumptions_text}

### 近年关键财务数据
{financials_table}

### 财务健康度：{health_score}/100
红色预警：{flags}
黄色预警：{warnings}
亮点：{positives}

### 假设敏感性（营收增速变化对 DCF 基准值的影响）
{sensitivity_table}

---

请按以下结构输出分析报告（Markdown）：

## 1. 投资建议
明确给出：【买入 / 增持 / 中性 / 减持 / 卖出】
用一句话说明核心理由，直接基于上方数据。

## 2. 估值解读
- PE/PB 分位说明了什么？结合历史背景解释
- DCF 结论是否可信？指出哪个假设最关键
- 如果假设发生变化，结论会怎样改变（参考敏感性分析）

## 3. 财务质量判断
- 营收和利润趋势反映什么业务逻辑？
- 财务健康度亮点和预警说明了什么？
- 现金流质量评估

## 4. 核心风险
列举 2-3 个最关键的风险，每条要说明风险来自哪个数据

## 5. 假设透明度说明
明确指出：本次分析结论中，哪些判断依赖于假设（不确定的），哪些是基于历史数据的硬事实。

要求：有观点，不要只复述数字。对异常数据要有解释。"""


# ── 缓存工具 ─────────────────────────────────────────────────

def _analysis_cache_key(code: str, model: str, assumptions: AnalysisAssumptions,
                         data_version: str) -> str:
    model_short = model.replace("claude-", "").replace("gpt-", "").replace("-", "")[:8]
    return f"analysis_{code}_{model_short}_{assumptions.hash()}_{data_version}"


def _history_index_key(code: str) -> str:
    return f"analysis_history_{code}"


def _save_to_history(code: str, analysis_id: str, summary: dict) -> None:
    """将分析摘要写入该股票的历史索引"""
    key = _history_index_key(code)
    history = cache.get(key) or []
    history.insert(0, {"id": analysis_id, "summary": summary})
    history = history[:20]  # 最多保留20条历史
    cache.set(key, history, ttl_seconds=365 * 24 * 3600)


def get_analysis_history(code: str) -> list[dict]:
    """获取某股票的历史分析记录摘要"""
    return cache.get(_history_index_key(code)) or []


# ── 季报节点检测 ─────────────────────────────────────────────

def _get_report_season(dt: datetime) -> str:
    """返回当前时间对应的财报季：Q1/Q2/Q3/Q4"""
    m = dt.month
    if m <= 4:
        return f"{dt.year}Q1"
    if m <= 8:
        return f"{dt.year}Q2"
    if m <= 10:
        return f"{dt.year}Q3"
    return f"{dt.year}Q4"


def is_data_stale(data_version: str) -> bool:
    """
    判断缓存数据是否因新财报发布而过期。
    财报季节点：4月底（Q1）、8月底（Q2）、10月底（Q3）、次年3月底（Q4）
    """
    now = datetime.now()
    current_season = _get_report_season(now)
    # 如果数据版本的年份早于当前财报季对应年份，认为过期
    try:
        data_year = int(data_version[:4])
        current_year = now.year
        if data_year < current_year - 1:
            return True
    except Exception:
        pass
    return False


# ── Prompt 构建 ──────────────────────────────────────────────

def _build_financials_table(key_financials: dict) -> str:
    years = key_financials.get("years", [])
    if not years:
        return "（财务数据不可用）"

    lines = [f"{'指标':<12}" + "".join(f"{y:<10}" for y in years)]
    lines.append("-" * (12 + 10 * len(years)))

    def row(label, vals, fmt=".1f", suffix=""):
        cells = []
        for v in vals:
            if v is None:
                cells.append("N/A")
            else:
                try:
                    cells.append(f"{v:{fmt}}{suffix}")
                except Exception:
                    cells.append(str(v))
        return f"{label:<12}" + "".join(f"{c:<10}" for c in cells)

    lines.append(row("营收(亿)", key_financials.get("revenue_yi", []), ".1f"))
    lines.append(row("营收增速", key_financials.get("revenue_yoy_pct", []), ".1f", "%"))
    lines.append(row("毛利率", key_financials.get("gross_margin_pct", []), ".1f", "%"))
    lines.append(row("净利率", key_financials.get("net_margin_pct", []), ".1f", "%"))
    lines.append(row("ROE", key_financials.get("roe_pct", []), ".1f", "%"))
    lines.append(row("负债率", key_financials.get("debt_ratio_pct", []), ".1f", "%"))
    lines.append(row("EPS(元)", key_financials.get("eps", []), ".2f"))
    lines.append(row("每股CFO(元)", key_financials.get("ocf_per_share", []), ".2f"))

    return "\n".join(lines)


def _build_assumptions_text(assumptions_used: dict) -> str:
    lines = []
    for k, v in assumptions_used.items():
        if k == "user_overrides":
            continue
        if isinstance(v, float) and k.endswith("_rate"):
            lines.append(f"- {k}: {v:.1%}")
        else:
            lines.append(f"- {k}: {v}")
    overrides = assumptions_used.get("user_overrides", [])
    if overrides:
        lines.append(f"⚠️ 用户自定义参数：{', '.join(overrides)}")
    else:
        lines.append("✓ 全部使用系统自动计算值")
    return "\n".join(lines)


def _build_sensitivity_table(sensitivity: list[dict]) -> str:
    if not sensitivity:
        return "（DCF 数据不足，无法计算敏感性）"
    lines = ["营收增速变化 | DCF基准值 | 较基准变化"]
    lines.append("-----------|---------|----------")
    for row in sensitivity:
        lines.append(
            f"{row['delta']:>10} | {row['dcf_value']:>8.0f} | {row['change_pct']:>+.1f}%"
        )
    return "\n".join(lines)


def _build_prompt(bundle: CalculationBundle) -> str:
    v = bundle.valuation
    h = bundle.health

    def fmt(val, suffix="", decimals=1):
        if val is None:
            return "N/A"
        return f"{val:.{decimals}f}{suffix}"

    return ANALYSIS_PROMPT.format(
        name=bundle.stock_name,
        code=bundle.stock_code,
        data_version=bundle.data_version,
        current_price=fmt(v.current_price, " 元"),
        pe_current=fmt(v.pe_current),
        pe_years=bundle.assumptions.pe_comparison_years,
        pe_percentile=fmt(v.pe_percentile),
        pe_min=fmt(v.pe_min),
        pe_max=fmt(v.pe_max),
        pe_median=fmt(v.pe_median),
        pb_current=fmt(v.pb_current, decimals=2),
        pb_percentile=fmt(v.pb_percentile),
        dcf_bear=fmt(v.dcf_bear, " 元", 0) if v.dcf_bear else "N/A",
        dcf_base=fmt(v.dcf_base, " 元", 0) if v.dcf_base else "N/A",
        dcf_bull=fmt(v.dcf_bull, " 元", 0) if v.dcf_bull else "N/A",
        dcf_upside_pct=fmt(v.dcf_upside_pct, "%") if v.dcf_upside_pct else "N/A",
        valuation_score=fmt(v.valuation_score) if v.valuation_score else "N/A",
        assumptions_text=_build_assumptions_text(v.assumptions_used),
        financials_table=_build_financials_table(bundle.key_financials),
        health_score=h.score,
        flags=("无" if not h.flags else "\n".join(f"  - {f}" for f in h.flags)),
        warnings=("无" if not h.warnings else "\n".join(f"  - {w}" for w in h.warnings)),
        positives=("无" if not h.positives else "\n".join(f"  - {p}" for p in h.positives)),
        sensitivity_table=_build_sensitivity_table(bundle.sensitivity),
    )


# ── 主 Agent ────────────────────────────────────────────────

class FundamentalAgent:

    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        assumptions: Optional[AnalysisAssumptions] = None,
        force_refresh_data: bool = False,
        force_refresh_analysis: bool = False,
    ) -> dict:
        """
        主入口。

        Args:
            stock_code: 股票代码（A股6位或港股5位）
            model: 使用的模型（claude-sonnet/claude-opus/deepseek 等）
            assumptions: 分析假设参数，None = 全部自动计算
            force_refresh_data: 强制刷新底层财务数据
            force_refresh_analysis: 强制重新运行 LLM 分析（即使缓存命中）

        Returns:
            完整分析结果 dict，包含 full_report、valuation_detail、history_comparison 等
        """
        if assumptions is None:
            assumptions = AnalysisAssumptions()

        # Step 1: 获取数据
        print(f"[FundamentalAgent] 获取数据: {stock_code}")
        stock_data = data_fetcher.fetch(
            stock_code,
            data_types=["info", "price", "financials", "valuation"],
            force_refresh=force_refresh_data,
        )

        # Step 2: 代码计算
        print(f"[FundamentalAgent] 计算估值指标...")
        bundle = calculate(stock_data, assumptions)

        # Step 3: 检查分析缓存
        cache_key = _analysis_cache_key(
            stock_code, model, assumptions, bundle.data_version
        )

        if not force_refresh_analysis:
            cached_analysis = cache.get(cache_key)
            if cached_analysis:
                print(f"[FundamentalAgent] 命中分析缓存: {cache_key}")
                cached_analysis["cache_hit"] = True

                # 检查数据是否已过期（新财报发布）
                if is_data_stale(bundle.data_version):
                    cached_analysis["stale_warning"] = (
                        f"注意：{bundle.data_version} 财报数据可能已有新版本，"
                        f"建议调用 force_refresh_data=True 更新"
                    )
                return cached_analysis

        # Step 4: LLM 分析
        print(f"[FundamentalAgent] 调用 {model} 进行分析...")
        prompt = _build_prompt(bundle)
        result = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model, temperature=0.2)

        # Step 5: 提取投资建议
        rating = "未知"
        for keyword in ["买入", "增持", "中性", "减持", "卖出"]:
            if keyword in result.content[:300]:
                rating = keyword
                break

        # Step 6: 获取历史分析（用于对比）
        history = get_analysis_history(stock_code)
        prev = history[0] if history else None
        history_comparison = None
        if prev:
            prev_summary = prev.get("summary", {})
            history_comparison = {
                "previous_analysis_id": prev.get("id"),
                "previous_verdict": prev_summary.get("rating"),
                "previous_analyzed_at": prev_summary.get("analyzed_at"),
                "previous_data_version": prev_summary.get("data_version"),
            }

        # Step 7: 组装完整结果
        analyzed_at = datetime.now().isoformat()
        analysis_id = cache_key.replace("analysis_", "")

        output = {
            "stock_code": stock_code,
            "stock_name": bundle.stock_name,
            "analysis_id": analysis_id,
            "data_version": bundle.data_version,
            "model_used": model,
            "analyzed_at": analyzed_at,
            "cache_hit": False,

            "verdict": {
                "valuation": _score_to_label(bundle.valuation.valuation_score),
                "recommendation": rating,
                "valuation_score": bundle.valuation.valuation_score,
                "confidence_basis": bundle.health.positives[:3],
            },

            "valuation_detail": {
                "pe_current": bundle.valuation.pe_current,
                "pe_percentile_5y": bundle.valuation.pe_percentile,
                "pe_range": f"{bundle.valuation.pe_min} ~ {bundle.valuation.pe_max}",
                "pb_current": bundle.valuation.pb_current,
                "pb_percentile_5y": bundle.valuation.pb_percentile,
                "dcf_bear": bundle.valuation.dcf_bear,
                "dcf_base": bundle.valuation.dcf_base,
                "dcf_bull": bundle.valuation.dcf_bull,
                "current_price": bundle.valuation.current_price,
                "dcf_upside_pct": bundle.valuation.dcf_upside_pct,
            },

            "assumptions_used": bundle.valuation.assumptions_used,

            "financial_health": {
                "score": bundle.health.score,
                "flags": bundle.health.flags,
                "warnings": bundle.health.warnings,
                "positives": bundle.health.positives,
            },

            "sensitivity": bundle.sensitivity,

            "full_report": result.content,
            "tokens_used": (result.input_tokens + result.output_tokens
                            if result.input_tokens > 0 else -1),

            "history_comparison": history_comparison,
        }

        # Step 8: 缓存分析结果（永久，按需失效）
        cache.set(cache_key, output, ttl_seconds=365 * 24 * 3600)

        # Step 9: 写入历史索引
        _save_to_history(stock_code, analysis_id, {
            "rating": rating,
            "valuation_score": bundle.valuation.valuation_score,
            "data_version": bundle.data_version,
            "model": model,
            "analyzed_at": analyzed_at,
            "assumptions_hash": assumptions.hash(),
        })

        print(f"[FundamentalAgent] 完成。评级: {rating}，估值分: {bundle.valuation.valuation_score}")
        return output


def _score_to_label(score: Optional[float]) -> str:
    if score is None:
        return "未知"
    if score < 30:
        return "明显低估"
    if score < 45:
        return "合理偏低估"
    if score < 55:
        return "合理"
    if score < 70:
        return "合理偏高估"
    return "明显高估"


# 模块级单例
_agent = FundamentalAgent()


def analyze(
    stock_code: str,
    model: str = "claude-sonnet",
    assumptions: Optional[AnalysisAssumptions] = None,
    force_refresh_data: bool = False,
    force_refresh_analysis: bool = False,
) -> dict:
    return _agent.analyze(stock_code, model, assumptions,
                          force_refresh_data, force_refresh_analysis)


if __name__ == "__main__":
    import sys

    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    model = sys.argv[2] if len(sys.argv) > 2 else "claude-sonnet"

    print(f"\n=== FundamentalAgent 测试：{code} ({model}) ===\n")
    result = analyze(code, model=model)

    print(f"股票: {result['stock_name']}")
    print(f"数据版本: {result['data_version']}")
    print(f"估值判断: {result['verdict']['valuation']}")
    print(f"投资建议: {result['verdict']['recommendation']}")
    print(f"估值分: {result['verdict']['valuation_score']}")
    print(f"缓存命中: {result['cache_hit']}")
    print(f"\n估值详情: {json.dumps(result['valuation_detail'], ensure_ascii=False, indent=2)}")
    print(f"\n--- 分析报告 ---\n{result['full_report']}")
