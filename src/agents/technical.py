"""
TechnicalAgent — 技术面分析师。

缓存策略：
- 数据缓存：DataFetcherAgent 负责
- 分析缓存：按 {stock_code}_{model}_{data_version} 索引
             同一交易日数据不重复调用 LLM

用法：
    from src.agents.technical import TechnicalAgent
    agent = TechnicalAgent()
    result = agent.analyze("600519", model="claude-sonnet")
"""

import hashlib
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from src.agents.technical_calculator import TechnicalBundle, calculate
from src.agents import data_fetcher
from src.models.model_router import complete
from src.utils import cache


# ── Prompt ──────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位专业的股票技术面分析师，风格简洁、实战导向。
所有技术指标数值已由代码预先计算完毕，你的职责是：
1. 解读各指标信号的综合含义
2. 识别近期 K 线形态（代码已给出候选形态，请结合数据验证并描述）
3. 指出关键支撑和压力位的交易意义
4. 给出短期操作建议（多/空/观望）及理由

注意：你不需要重新计算任何数字，重点在于综合判断和实战解读。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下技术指标计算结果，对 {name} 撰写技术面分析报告。

## 技术指标计算结果

### 综合评分
- 技术评分：{tech_score}/100（越高越强）
- 综合信号：{signal}

### 各指标详情
{indicators_text}

### 关键价位
- 支撑位：{support}
- 压力位：{resistance}
- 当前价格：{current_price}

### 代码检测的候选形态
{patterns_text}

### 近30日 K 线数据（供形态验证）
{price_table}

---

请按以下结构输出分析报告（Markdown 格式）：

## 1. 技术面综合判断
【{signal}】（一句话核心结论）

## 2. 关键指标解读
分析 MACD / RSI / MA / KDJ / 成交量 的信号含义，重点讲有分歧或有亮点的指标。

## 3. 形态与价位分析
验证候选形态是否成立，说明关键支撑和压力位的技术依据。

## 4. 操作建议
短期建议（多/空/观望），入场条件、止损位和目标位（如可判断）。

## 5. 主要风险
1-2 条反转或止损风险。"""


# ── 缓存辅助 ─────────────────────────────────────────────────

_HISTORY_DIR = Path(__file__).parent.parent.parent / "cache" / "technical_history"
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(code: str, model: str, data_version: str) -> str:
    model_short = model.replace("claude-", "").replace("gpt-", "").replace("-", "")[:12]
    raw = f"{code}|{model_short}|{data_version}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"technical_{code}_{model_short}_{h}"


def _data_version(price_data: list[dict]) -> str:
    """用最新交易日作为数据版本。"""
    if not price_data:
        return date.today().strftime("%Y%m%d")
    try:
        dates = [r["date"][:10] for r in price_data if r.get("date")]
        return max(dates).replace("-", "")
    except Exception:
        return date.today().strftime("%Y%m%d")


def _save_history(code: str, summary: dict):
    path = _HISTORY_DIR / f"{code}.json"
    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text())
        except Exception:
            history = []
    history.insert(0, {"summary": summary, "saved_at": datetime.now().isoformat()})
    history = history[:20]  # 保留最近20条
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2))


def get_analysis_history(code: str) -> list[dict]:
    path = _HISTORY_DIR / f"{code}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


# ── 格式化 Prompt 内容 ────────────────────────────────────────

def _format_indicators(indicators: dict) -> str:
    lines = []
    for name, d in indicators.items():
        val = d.get("value")
        sig = d.get("signal", "")
        score = d.get("score", 0)
        extra = {k: v for k, v in d.items() if k not in ("value", "signal", "score")}
        extra_str = "  |  " + "  ".join(f"{k}={v}" for k, v in extra.items() if v is not None) if extra else ""
        val_str = f"{val:.2f}" if isinstance(val, float) else str(val)
        lines.append(f"- **{name}**: {val_str}（分数 {score:+.1f}）— {sig}{extra_str}")
    return "\n".join(lines)


def _format_patterns(patterns: list[dict]) -> str:
    if not patterns:
        return "- 暂未检测到明显形态"
    return "\n".join(
        f"- {p['pattern']}（{p.get('type', '')}）" for p in patterns
    )


def _format_price_table(price_series: list[dict]) -> str:
    if not price_series:
        return "无数据"
    header = "日期 | 开 | 高 | 低 | 收 | 量"
    rows = [header, "---|---|---|---|---|---"]
    for r in price_series[-15:]:  # 最近15日
        rows.append(
            f"{r.get('date','')} | {r.get('open','')} | {r.get('high','')} | "
            f"{r.get('low','')} | {r.get('close','')} | {r.get('volume','')}"
        )
    return "\n".join(rows)


# ── LLM 调用 ─────────────────────────────────────────────────

def _call_llm(bundle: TechnicalBundle, stock_name: str, model: str) -> str:
    current_price = bundle.price_series[-1].get("close") if bundle.price_series else "N/A"

    prompt = ANALYSIS_PROMPT.format(
        name=stock_name,
        tech_score=bundle.technical_score,
        signal=bundle.signal,
        indicators_text=_format_indicators(bundle.indicators),
        support=bundle.key_levels.get("support", []),
        resistance=bundle.key_levels.get("resistance", []),
        current_price=current_price,
        patterns_text=_format_patterns(bundle.patterns_raw),
        price_table=_format_price_table(bundle.price_series),
    )

    result = complete(
        prompt=prompt,
        system=SYSTEM_PROMPT,
        model=model,
    )
    return result.content


# ── Agent 主类 ────────────────────────────────────────────────

class TechnicalAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh_data: bool = False,
        force_refresh_analysis: bool = False,
    ) -> dict:
        print(f"[TechnicalAgent] 获取数据: {stock_code}")
        stock_data = data_fetcher.fetch(
            stock_code,
            data_types=["info", "price"],
            force_refresh=force_refresh_data,
        )

        price_data = stock_data.get("price", [])
        if not price_data:
            raise ValueError(f"获取价格数据失败: {stock_code}")

        stock_name = stock_data.get("info", {}).get("股票简称", stock_code)
        market = stock_data.get("market", "A")

        data_ver = _data_version(price_data)
        ck = _cache_key(stock_code, model, data_ver)

        if not force_refresh_analysis:
            cached = cache.get(ck)
            if cached:
                print(f"[TechnicalAgent] 命中缓存: {stock_code}")
                cached["cache_hit"] = True
                return cached

        print(f"[TechnicalAgent] 计算技术指标...")
        bundle = calculate(price_data, stock_code=stock_code)

        print(f"[TechnicalAgent] 调用 {model} 进行分析...")
        full_report = _call_llm(bundle, stock_name, model)

        current_price = float(bundle.price_series[-1]["close"]) if bundle.price_series else None

        result = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": market,
            "data_version": data_ver,
            "model_used": model,
            "analyzed_at": datetime.now().isoformat(),
            "cache_hit": False,
            "technical_score": bundle.technical_score,
            "signal": bundle.signal,
            "current_price": current_price,
            "indicators": bundle.indicators,
            "key_levels": bundle.key_levels,
            "patterns_detected": bundle.patterns_raw,
            "full_report": full_report,
        }

        cache.set(ck, result)
        print(f"[TechnicalAgent] 完成。评分: {bundle.technical_score}，信号: {bundle.signal}")

        _save_history(stock_code, {
            "analyzed_at": result["analyzed_at"],
            "model": model,
            "data_version": data_ver,
            "technical_score": bundle.technical_score,
            "signal": bundle.signal,
        })

        return result


# ── 便捷函数 ─────────────────────────────────────────────────

def analyze(
    stock_code: str,
    model: str = "claude-sonnet",
    force_refresh_data: bool = False,
    force_refresh_analysis: bool = False,
) -> dict:
    return TechnicalAgent().analyze(
        stock_code,
        model=model,
        force_refresh_data=force_refresh_data,
        force_refresh_analysis=force_refresh_analysis,
    )
