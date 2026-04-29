"""
SupplyChainAgent — 上下游产业链分析。

分析维度：
  1. 产业链位置（上游原材料/中游制造/下游消费）
  2. 主要上游供应商及原材料依赖
  3. 主要下游客户及需求驱动
  4. 议价权判断（定价权强弱）
  5. 产业链景气传导（上下游景气如何影响公司）

数据来源：公司基本信息 + 行业数据 + LLM 知识（无直接供应链 API）。
"""

import hashlib
from datetime import datetime, date

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位专注于产业链研究的行业分析师。
你的职责是：
1. 准确定位公司在产业链中的位置（上/中/下游）
2. 分析上游成本传导：原材料价格波动如何影响公司毛利率
3. 分析下游需求驱动：终端客户结构和需求周期性
4. 判断议价权：公司是价格接受者还是价格制定者

请基于公司所在行业和主营业务，结合你的产业知识进行分析。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下信息，为 {name}（{code}）撰写「上下游产业链」分析。

## 公司基本信息
{info_text}

## 主营业务与行业背景
{business_context}

---
请按以下结构输出（Markdown格式，总字数控制在500字以内）：

## 上下游产业链

### 产业链定位
（公司处于产业链哪个环节，产业链整体结构是什么，1-2条）

### 上游依赖分析
（主要原材料/核心投入是什么，关键供应商类型，原材料价格波动对毛利率的影响敏感度，2-3条）

### 下游需求分析
（主要客户群体和终端需求，需求周期性强弱，下游景气对公司收入的传导路径，2-3条）

### 议价权判断
（公司对上游/下游的议价能力强弱，定价模式（成本加成/市场定价/长协定价），2条）

### 产业链景气信号
（当前上下游景气如何，对公司未来1-2年业绩的含义，1-2条）

### 产业链综合结论
（一句话：产业链位置给公司带来的核心优势或核心风险）
"""


def _format_info(info: dict) -> str:
    keys = ["股票简称", "所属行业", "总市值", "员工人数", "上市时间", "法人"]
    lines = [f"- {k}：{v}" for k in keys if (v := info.get(k))]
    return "\n".join(lines) if lines else "（基本信息有限）"


def _build_business_context(info: dict, industry_data: dict) -> str:
    lines = []
    if biz := info.get("主营业务") or info.get("经营范围"):
        lines.append(f"主营业务：{str(biz)[:300]}")
    if ind := info.get("所属行业"):
        lines.append(f"所属行业：{ind}")
    if peer_count := len((industry_data or {}).get("peers", [])):
        lines.append(f"同行公司数量：{peer_count} 家")
    if not lines:
        lines.append("（详细业务信息依赖 LLM 知识库）")
    return "\n".join(lines)


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"supply_chain|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"supply_chain_{code}_{h}"


class SupplyChainAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[SupplyChainAgent] 开始分析: {stock_code}")

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[SupplyChainAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "industry"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="supply_chain", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)
        industry_data = stock_data.get("industry", {})

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            info_text=_format_info(info),
            business_context=_build_business_context(info, industry_data),
        )

        print(f"[SupplyChainAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        section = ReportSection(
            dimension="supply_chain",
            content=result_content,
            confidence=0.7,
            data_sources=["公司基本信息", "LLM 产业知识"],
            generated_at=datetime.now().isoformat(),
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
        })
        print(f"[SupplyChainAgent] 完成: {stock_code}")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return SupplyChainAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
