"""
统一报告节结构 — 所有维度 Agent 的输出格式。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# 维度顺序（报告展示顺序）
DIMENSION_ORDER = [
    "business",
    "industry",
    "performance",
    "peer",
    "shareholder",
    "forecast",
    "management",
    "research",
    "catalyst",
    "supply_chain",
    "kol",
    "synthesis",
]

DIMENSION_TITLES = {
    "business":     "公司概况",
    "industry":     "行业分析",
    "performance":  "过往业绩",
    "peer":         "同行对比",
    "shareholder":  "股东分析",
    "forecast":     "盈利预测",
    "management":   "管理层情况",
    "research":     "研究报告",
    "catalyst":     "最新催化剂",
    "supply_chain": "上下游情况",
    "kol":          "大牛分析",
    "synthesis":    "综合结论",
}


@dataclass
class ReportSection:
    dimension: str
    content: str                          # HTML/Markdown 正文（支持 unsafe HTML）
    confidence: float = 1.0              # 0-1，数据完整度
    data_sources: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None
    chart_data: Optional[dict] = None    # 图表原始数据（由渲染层负责绘制）

    @property
    def title(self) -> str:
        return DIMENSION_TITLES.get(self.dimension, self.dimension)

    @property
    def is_ok(self) -> bool:
        return self.error is None and bool(self.content)

    def confidence_label(self) -> str:
        if self.confidence >= 0.8:
            return ""
        if self.confidence >= 0.5:
            return "⚠️ 数据部分缺失，结论仅供参考"
        return "⚠️ 数据不足，本节仅作参考"
