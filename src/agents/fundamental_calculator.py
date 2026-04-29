"""
FundamentalCalculator — 纯代码计算层，不调用 LLM。
负责所有数值计算：DCF、估值分位、财务健康度、假设参数自动推导。
LLM 只接收计算结果，负责解读和报告撰写。
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np


# ── 行业 WACC 默认值 ─────────────────────────────────────────

_INDUSTRY_WACC = {
    # 消费/食品
    "白酒": 0.08, "食品饮料": 0.08, "消费": 0.09,
    # 科技/硬件
    "消费电子": 0.11, "半导体": 0.11, "通信设备": 0.10,
    "计算机": 0.10, "电子": 0.10,
    # 互联网/软件
    "互联网": 0.10, "软件": 0.10,
    # 汽车
    "汽车": 0.10, "新能源汽车": 0.11,
    # 医药
    "医药": 0.09, "生物医药": 0.10,
    # 金融
    "银行": 0.07, "保险": 0.08,
    # 地产/建筑
    "房地产": 0.09,
    # 制造
    "机械": 0.09, "化工": 0.09,
}

_MARKET_WACC_DEFAULT = {"A": 0.09, "HK": 0.10, "US": 0.10}


def _auto_wacc(market: str, industry: str = "") -> tuple[float, str]:
    """根据行业推断合理 WACC，返回 (wacc, 来源说明)"""
    for key, w in _INDUSTRY_WACC.items():
        if key in industry:
            return w, f"{industry}行业默认值 {w:.0%}"
    default = _MARKET_WACC_DEFAULT.get(market, 0.09)
    return default, f"{market}市场默认值 {default:.0%}"


# ── 假设参数 ────────────────────────────────────────────────

@dataclass
class AnalysisAssumptions:
    revenue_growth_rate: Optional[float] = None   # 营收增速，None = 系统自动计算
    wacc: Optional[float] = None                  # 加权平均资本成本，None = 自动
    terminal_growth_rate: float = 0.03            # 永续增长率（GDP 长增）
    pe_comparison_years: int = 5                  # PE 分位对比年数
    projection_years: int = 5                     # DCF 预测年数

    def to_dict(self) -> dict:
        return {
            "revenue_growth_rate": self.revenue_growth_rate,
            "wacc": self.wacc,
            "terminal_growth_rate": self.terminal_growth_rate,
            "pe_comparison_years": self.pe_comparison_years,
            "projection_years": self.projection_years,
        }

    def hash(self) -> str:
        """用于 cache key：假设参数变化 → 新 hash → 新分析"""
        content = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()[:6]


# ── 计算结果 ────────────────────────────────────────────────

@dataclass
class ValuationResult:
    # PE 分析
    pe_current: Optional[float] = None
    pe_percentile: Optional[float] = None
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pe_median: Optional[float] = None

    # PB 分析
    pb_current: Optional[float] = None
    pb_percentile: Optional[float] = None

    # DCF
    dcf_bear: Optional[float] = None
    dcf_base: Optional[float] = None
    dcf_bull: Optional[float] = None
    current_price: Optional[float] = None
    dcf_upside_pct: Optional[float] = None      # (dcf_base - current_price) / current_price

    # 综合估值分（0-100，越低越低估）
    valuation_score: Optional[float] = None

    # 假设参数（实际使用值，含来源说明）
    assumptions_used: dict = field(default_factory=dict)


@dataclass
class FinancialHealthResult:
    score: int = 100            # 0-100
    flags: list[str] = field(default_factory=list)      # 红色预警
    warnings: list[str] = field(default_factory=list)   # 黄色预警
    positives: list[str] = field(default_factory=list)  # 亮点


@dataclass
class CalculationBundle:
    """传递给 LLM 的完整计算结果包"""
    stock_code: str
    stock_name: str
    data_version: str                     # 财报期，如 "2025Q3"
    valuation: ValuationResult
    health: FinancialHealthResult
    key_financials: dict                  # 近5年核心财务数据（给 LLM 的参考）
    sensitivity: list[dict]              # 敏感性分析结果
    assumptions: AnalysisAssumptions


# ── 核心计算函数 ─────────────────────────────────────────────

def _safe(val, default=None) -> Optional[float]:
    try:
        return float(val) if val is not None else default
    except Exception:
        return default


def compute_revenue_cagr(revenue_list: list, years: int = 3) -> Optional[float]:
    """计算营收 CAGR（取最近 N 年）"""
    vals = [_safe(v) for v in revenue_list[:years + 1] if _safe(v) is not None]
    if len(vals) < 2:
        return None
    start, end = vals[-1], vals[0]
    n = len(vals) - 1
    if start <= 0:
        return None
    return (end / start) ** (1 / n) - 1


def compute_dcf(
    latest_revenue: float,
    growth_rate: float,
    net_margin: float,
    shares: float,
    wacc: float,
    terminal_growth: float,
    projection_years: int = 5,
) -> dict[str, Optional[float]]:
    """
    三情景 DCF（保守/基准/乐观）。
    growth_rate: 基准增速
    返回: {"bear": float, "base": float, "bull": float}，单位：元/股
    """
    if not all([latest_revenue, growth_rate, net_margin, shares, wacc]):
        return {"bear": None, "base": None, "bull": None}

    scenarios = {
        "bear": growth_rate * 0.6,
        "base": growth_rate,
        "bull": growth_rate * 1.4,
    }
    results = {}
    for name, g in scenarios.items():
        cash_flows = []
        rev = latest_revenue
        for _ in range(projection_years):
            rev *= (1 + g)
            fcf = rev * net_margin
            cash_flows.append(fcf)

        # 折现
        pv_fcfs = sum(cf / (1 + wacc) ** (i + 1) for i, cf in enumerate(cash_flows))

        # 终值（WACC 必须 > 永续增长率，否则无意义）
        if wacc <= terminal_growth:
            results[name] = None
            continue
        terminal_value = cash_flows[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
        pv_terminal = terminal_value / (1 + wacc) ** projection_years

        total_value = pv_fcfs + pv_terminal
        per_share = total_value / shares if shares > 0 else None
        results[name] = round(per_share, 2) if per_share else None

    return results


def compute_valuation_score(
    pe_percentile: Optional[float],
    pb_percentile: Optional[float],
    dcf_upside_pct: Optional[float],
) -> Optional[float]:
    """
    综合估值分 0-100：越低 = 越低估
    - PE 分位权重 30%
    - PB 分位权重 15%
    - DCF 溢价/折价权重 35%
    - 其余 20% 预留（PEG 等后续扩展）
    """
    score = 0.0
    weight_sum = 0.0

    if pe_percentile is not None:
        score += pe_percentile * 0.30
        weight_sum += 0.30

    if pb_percentile is not None:
        score += pb_percentile * 0.15
        weight_sum += 0.15

    if dcf_upside_pct is not None:
        # 上涨空间越大 → DCF 分越低（低估）
        # 映射：+50% 上涨空间 → 0分，-50% → 100分
        dcf_score = max(0, min(100, 50 - dcf_upside_pct * 100))
        score += dcf_score * 0.35
        weight_sum += 0.35

    if weight_sum == 0:
        return None
    return round(score / weight_sum, 1)


def check_financial_health_from_rows(rows: list[dict]) -> FinancialHealthResult:
    """
    检查财务健康度。接收已归一化的 parsed_rows（统一字段名）。
    支持 A 股和港股。
    """
    result = FinancialHealthResult()

    if not rows or len(rows) < 1:
        result.warnings.append("财务数据不足，无法完整评估")
        result.score = 60
        return result

    latest = rows[0]
    deductions = 0

    # 检查：ROE 趋势
    roes = [r["roe"] for r in rows if r.get("roe") is not None]
    if roes:
        if roes[0] > 20:
            result.positives.append(f"ROE {roes[0]:.1f}%，持续高位（近年均值 {sum(roes)/len(roes):.1f}%）")
        elif roes[0] < 8:
            result.warnings.append(f"ROE 偏低（{roes[0]:.1f}%），盈利能力存疑")
            deductions += 10

    # 检查：现金流质量
    eps = latest.get("eps")
    ocf = latest.get("ocf")
    if eps and ocf and eps > 0:
        ocf_eps_ratio = ocf / eps
        if ocf_eps_ratio >= 0.9:
            result.positives.append(f"每股经营现金流 / EPS = {ocf_eps_ratio:.2f}，现金质量好")
        elif ocf_eps_ratio < 0.5:
            result.flags.append(f"现金流质量差：每股经营现金流 / EPS = {ocf_eps_ratio:.2f}（< 0.5）")
            deductions += 20

    # 检查：负债率
    debt = latest.get("debt_ratio")
    if debt is not None:
        if debt < 40:
            result.positives.append(f"资产负债率 {debt:.1f}%，财务稳健")
        elif debt > 70:
            result.flags.append(f"资产负债率偏高（{debt:.1f}%），关注偿债风险")
            deductions += 15

    # 检查：营收增速持续性
    yoys = [r["revenue_yoy"] for r in rows if r.get("revenue_yoy") is not None]
    if len(yoys) >= 3:
        if all(y > 0 for y in yoys[:3]):
            result.positives.append(f"营收连续{len(yoys[:3])}年正增长")
        elif yoys[0] and yoys[0] < -10:
            result.warnings.append(f"最新营收同比下滑 {abs(yoys[0]):.1f}%")
            deductions += 10

    result.score = max(0, 100 - deductions)
    return result


def check_financial_health(financials: dict, market: str = "A") -> FinancialHealthResult:
    """向后兼容接口：接收原始 financials dict，内部转换后调用 check_financial_health_from_rows"""
    from src.data.akshare_client import parse_pct, parse_yi
    summary = financials.get("summary", [])
    if not summary:
        return check_financial_health_from_rows([])

    def _parse_a(row):
        return {
            "revenue": parse_yi(row.get("营业总收入")),
            "revenue_yoy": parse_pct(row.get("营业总收入同比增长率")),
            "roe": parse_pct(row.get("净资产收益率")),
            "debt_ratio": parse_pct(row.get("资产负债率")),
            "eps": _safe(str(row.get("基本每股收益", "")).replace("元", "")),
            "ocf": _safe(str(row.get("每股经营现金流", "")).replace("元", "")),
        }

    def _parse_hk(row):
        return {
            "revenue": _safe(row.get("OPERATE_INCOME")),
            "revenue_yoy": _safe(row.get("OPERATE_INCOME_YOY")),
            "roe": _safe(row.get("ROE_AVG")),
            "debt_ratio": _safe(row.get("DEBT_ASSET_RATIO")),
            "eps": _safe(row.get("BASIC_EPS")),
            "ocf": _safe(row.get("PER_NETCASH_OPERATE")),
        }

    parser = _parse_hk if market == "HK" else _parse_a
    rows = [parser(r) for r in summary[:5]]
    return check_financial_health_from_rows(rows)


def compute_sensitivity(
    dcf_base: Optional[float],
    base_growth: float,
    base_wacc: float,
    latest_revenue: float,
    net_margin: float,
    shares: float,
    terminal_growth: float,
) -> list[dict]:
    """计算关键假设的敏感性：增速 ±2% 和 WACC ±1% 对 DCF 的影响"""
    if not dcf_base:
        return []

    results = []
    for delta_g in [-0.04, -0.02, 0, 0.02, 0.04]:
        dcf = compute_dcf(latest_revenue, base_growth + delta_g, net_margin,
                          shares, base_wacc, terminal_growth)
        base_val = dcf.get("base")
        if base_val:
            change_pct = (base_val - dcf_base) / dcf_base * 100
            results.append({
                "type": "revenue_growth",
                "delta": f"{delta_g:+.0%}",
                "dcf_value": base_val,
                "change_pct": round(change_pct, 1),
            })

    return results


# ── 主计算入口 ───────────────────────────────────────────────

def calculate(
    stock_data: dict,
    assumptions: AnalysisAssumptions,
) -> CalculationBundle:
    """
    主入口：接收 DataFetcherAgent 的输出，计算所有指标。
    返回 CalculationBundle 供 LLM 使用。
    """
    code = stock_data.get("code", "")
    info = stock_data.get("info", {})
    name = info.get("股票简称", info.get("shortName", code))

    # ── 解析财务摘要数据 ──
    from src.data.akshare_client import parse_pct, parse_yi

    market = stock_data.get("market", "A")
    summary_rows = stock_data.get("financials", {}).get("summary", [])

    def _parse_row_a(row: dict) -> dict:
        """A 股：同花顺中文列名"""
        return {
            "year": str(row.get("报告期", ""))[:4],
            "revenue": parse_yi(row.get("营业总收入")),
            "revenue_yoy": parse_pct(row.get("营业总收入同比增长率")),
            "gross_margin": parse_pct(row.get("销售毛利率")),
            "net_margin": parse_pct(row.get("销售净利率")),
            "roe": parse_pct(row.get("净资产收益率")),
            "debt_ratio": parse_pct(row.get("资产负债率")),
            "eps": _safe(str(row.get("基本每股收益", "")).replace("元", "")),
            "bps": _safe(str(row.get("每股净资产", "")).replace("元", "")),
            "ocf": _safe(str(row.get("每股经营现金流", "")).replace("元", "")),
        }

    def _parse_row_hk(row: dict) -> dict:
        """港股：东财英文列名，数值已是 float（元/百分比）"""
        date_str = str(row.get("REPORT_DATE", ""))
        return {
            "year": date_str[:4],
            "revenue": _safe(row.get("OPERATE_INCOME")),          # 原始元，无需转换
            "revenue_yoy": _safe(row.get("OPERATE_INCOME_YOY")),  # 已是 float %
            "gross_margin": _safe(row.get("GROSS_PROFIT_RATIO")),
            "net_margin": _safe(row.get("NET_PROFIT_RATIO")),
            "roe": _safe(row.get("ROE_AVG")),
            "debt_ratio": _safe(row.get("DEBT_ASSET_RATIO")),
            "eps": _safe(row.get("BASIC_EPS")),
            "bps": _safe(row.get("BPS")),
            "ocf": _safe(row.get("PER_NETCASH_OPERATE")),
        }

    parse_row = _parse_row_hk if market == "HK" else _parse_row_a
    parsed_rows = [parse_row(r) for r in summary_rows[:5]]
    latest = parsed_rows[0] if parsed_rows else {}

    # ── 价格数据 ──
    price_records = stock_data.get("price", [])
    current_price = None
    if price_records:
        current_price = _safe(price_records[0].get("close"))

    # ── 估值分位 ──
    val_pct = stock_data.get("valuation_percentile", {})
    pe_info = val_pct.get("pe") or {}
    pb_info = val_pct.get("pb") or {}

    # ── 自动推导假设参数 ──
    revenues = [r["revenue"] for r in parsed_rows if r.get("revenue")]
    auto_growth = compute_revenue_cagr(revenues, years=3)
    effective_growth = assumptions.revenue_growth_rate if assumptions.revenue_growth_rate is not None else auto_growth

    industry = info.get("行业", info.get("industry", ""))
    auto_wacc, auto_wacc_src = _auto_wacc(market, industry)
    effective_wacc = assumptions.wacc if assumptions.wacc is not None else auto_wacc

    # ── DCF 计算 ──
    latest_revenue = revenues[0] if revenues else None
    net_margin_pct = latest.get("net_margin")
    net_margin = net_margin_pct / 100 if net_margin_pct else None

    # 股本估算：总市值 / 当前价格（yfinance 给的市值）
    total_mv = _safe(info.get("市值"))
    shares = total_mv / current_price if (total_mv and current_price) else None

    dcf_results = {}
    if all([latest_revenue, effective_growth, net_margin, shares]):
        dcf_results = compute_dcf(
            latest_revenue, effective_growth, net_margin, shares,
            effective_wacc, assumptions.terminal_growth_rate,
            assumptions.projection_years,
        )

    dcf_base = dcf_results.get("base")
    dcf_upside = None
    if dcf_base and current_price:
        dcf_upside = (dcf_base - current_price) / current_price

    # ── 综合估值分 ──
    val_score = compute_valuation_score(
        pe_info.get("percentile"), pb_info.get("percentile"), dcf_upside
    )

    valuation = ValuationResult(
        pe_current=pe_info.get("current"),
        pe_percentile=pe_info.get("percentile"),
        pe_min=pe_info.get("min"),
        pe_max=pe_info.get("max"),
        pe_median=pe_info.get("median"),
        pb_current=pb_info.get("current"),
        pb_percentile=pb_info.get("percentile"),
        dcf_bear=dcf_results.get("bear"),
        dcf_base=dcf_base,
        dcf_bull=dcf_results.get("bull"),
        current_price=current_price,
        dcf_upside_pct=round(dcf_upside * 100, 1) if dcf_upside else None,
        valuation_score=val_score,
        assumptions_used={
            "revenue_growth_rate": round(effective_growth, 4) if effective_growth else None,
            "revenue_growth_source": "用户自定义" if assumptions.revenue_growth_rate else f"近3年历史CAGR（{round(auto_growth*100, 1) if auto_growth else 'N/A'}%）",
            "wacc": effective_wacc,
            "wacc_source": "用户自定义" if assumptions.wacc else auto_wacc_src,
            "terminal_growth_rate": assumptions.terminal_growth_rate,
            "user_overrides": [k for k, v in assumptions.to_dict().items()
                               if v is not None and k not in ("terminal_growth_rate", "pe_comparison_years", "projection_years")],
        },
    )

    # ── 财务健康度（传入已归一化的 parsed_rows）──
    health = check_financial_health_from_rows(parsed_rows)

    # ── 敏感性分析 ──
    sensitivity = []
    if all([dcf_base, effective_growth, latest_revenue, net_margin, shares]):
        sensitivity = compute_sensitivity(
            dcf_base, effective_growth, effective_wacc,
            latest_revenue, net_margin, shares, assumptions.terminal_growth_rate,
        )

    # ── 关键财务摘要（给 LLM 的表格）──
    key_financials = {
        "years": [r["year"] for r in parsed_rows],
        "revenue_yi": [round(r["revenue"] / 1e8, 1) if r.get("revenue") else None for r in parsed_rows],
        "revenue_yoy_pct": [r.get("revenue_yoy") for r in parsed_rows],
        "gross_margin_pct": [r.get("gross_margin") for r in parsed_rows],
        "net_margin_pct": [r.get("net_margin") for r in parsed_rows],
        "roe_pct": [r.get("roe") for r in parsed_rows],
        "debt_ratio_pct": [r.get("debt_ratio") for r in parsed_rows],
        "eps": [r.get("eps") for r in parsed_rows],
        "ocf_per_share": [r.get("ocf") for r in parsed_rows],
    }

    # 数据版本（最新报告期）
    data_version = parsed_rows[0].get("year", "unknown") if parsed_rows else "unknown"

    return CalculationBundle(
        stock_code=code,
        stock_name=name,
        data_version=data_version,
        valuation=valuation,
        health=health,
        key_financials=key_financials,
        sensitivity=sensitivity,
        assumptions=assumptions,
    )
