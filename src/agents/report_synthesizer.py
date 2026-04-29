"""
ReportSynthesizer — 综合叙事层。

读取所有维度的分析输出，生成统一的综合结论：
- 综合投资判断（一段话）
- 核心支撑逻辑（3条）
- 主要风险（2条）
- 关键待观察指标
"""

import hashlib
from datetime import datetime, date

from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位资深投资研究主管，负责审阅下属分析师的各维度报告后，出具最终综合判断。
你的职责是：
1. 综合所有维度（主营、行业、业绩、同行、股东、催化剂、管理层、研报、盈利预测、产业链、大牛观点）的分析要点
2. 判断多个维度是否指向一致的结论（或存在矛盾）
3. 生成一份有观点、有立场的最终判断

要求：
- 综合判断必须明确（看多/中性/看空/待观察），不能模棱两可
- 核心逻辑必须直接引用各维度分析中的具体数据点
- 风险必须是真实可能发生的，不是套话
- 全文控制在600字以内

分析仅供参考，不构成投资建议。"""


SYNTHESIS_PROMPT = """请综合以下各维度分析报告，为 {name}（{code}）生成最终综合结论。

{sections_text}

---
请按以下结构输出（Markdown格式）：

## 综合结论

### 投资判断
**【{name} — 综合评级：XX】**（看多/中性偏多/中性/中性偏空/看空，选一个）

（2-3句话综合判断：基于哪些核心事实，当前是否值得研究跟踪）

### 核心支撑逻辑
1. （最重要的正面因素，引用具体数据）
2. （第二重要的正面因素）
3. （第三正面因素）

### 主要风险
1. （最重要的风险，说明风险触发条件）
2. （第二风险）

### 关键待观察指标
- （如果XX指标出现YY变化，需要重新评估判断）
- （另一个关键跟踪指标）

### 研究结论
（一句话：这支股票适合什么类型的投资者，在什么条件下值得建仓/持有/规避）
"""


def _format_sections(sections: list[ReportSection]) -> str:
    parts = []
    for s in sections:
        if s.is_ok and s.dimension != "synthesis":
            parts.append(f"### {s.title}\n{s.content}\n")
    return "\n---\n".join(parts) if parts else "（暂无分析内容）"


def _cache_key(code: str, model: str, data_ver: str, section_hash: str) -> str:
    raw = f"synthesis|{code}|{model}|{data_ver}|{section_hash}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"synthesis_{code}_{h}"


def _sections_hash(sections: list[ReportSection]) -> str:
    content = "|".join(s.generated_at for s in sections if s.is_ok)
    return hashlib.md5(content.encode()).hexdigest()[:8]


class ReportSynthesizer:
    def synthesize(
        self,
        stock_code: str,
        stock_name: str,
        sections: list[ReportSection],
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[ReportSynthesizer] 开始综合分析: {stock_code}")

        valid_sections = [s for s in sections if s.is_ok and s.dimension != "synthesis"]
        if not valid_sections:
            return ReportSection(
                dimension="synthesis",
                content="",
                confidence=0.0,
                error="无有效分析维度，无法生成综合结论",
            )

        data_ver = date.today().strftime("%Y%m%d")
        sh = _sections_hash(valid_sections)
        ck = _cache_key(stock_code, model, data_ver, sh)

        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[ReportSynthesizer] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        sections_text = _format_sections(valid_sections)
        confidence = min(1.0, len(valid_sections) / 8.0)  # 8个以上维度达满分

        prompt = SYNTHESIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            sections_text=sections_text,
        )

        print(f"[ReportSynthesizer] 调用 {model}（综合 {len(valid_sections)} 个维度）...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        section = ReportSection(
            dimension="synthesis",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=[s.dimension for s in valid_sections],
            generated_at=datetime.now().isoformat(),
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
        })
        print(f"[ReportSynthesizer] 完成: {stock_code}")
        return section
