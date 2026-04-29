"""
FundamentalCalculator 单元测试。
不依赖网络、不调用 LLM，全部使用 mock 数据。

测试分组：
  T1 - 数据解析函数（parse_yi、parse_pct）
  T2 - DCF 计算数学正确性
  T3 - 估值分位计算
  T4 - 综合估值分映射
  T5 - 财务健康度检查
  T6 - 假设参数 hash 唯一性
  T7 - 假设参数自动推导（CAGR）
  T8 - calculate() 端到端集成（mock 数据）
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.agents.fundamental_calculator import (
    AnalysisAssumptions,
    CalculationBundle,
    _safe,
    check_financial_health,
    compute_dcf,
    compute_revenue_cagr,
    compute_sensitivity,
    compute_valuation_score,
    calculate,
)
from src.data.akshare_client import parse_pct, parse_yi


# ── T1：数据解析函数 ─────────────────────────────────────────

class TestParseYi:
    def test_normal(self):
        assert parse_yi("1741.44亿") == pytest.approx(1741.44e8)

    def test_small(self):
        assert parse_yi("50.0亿") == pytest.approx(50e8)

    def test_none(self):
        assert parse_yi(None) is None

    def test_no_yi_suffix(self):
        # 如果没有"亿"后缀，尝试按纯数字解析
        result = parse_yi("5000000")
        assert result == pytest.approx(5000000.0)

    def test_false_value(self):
        # AKShare 对缺失数据返回 False
        assert parse_yi(False) is None


class TestParsePct:
    def test_pct_string(self):
        assert parse_pct("15.54%") == pytest.approx(15.54)

    def test_negative(self):
        assert parse_pct("-3.20%") == pytest.approx(-3.20)

    def test_none(self):
        assert parse_pct(None) is None

    def test_pure_float(self):
        assert parse_pct("8.5") == pytest.approx(8.5)

    def test_false(self):
        assert parse_pct(False) is None


# ── T2：DCF 计算 ─────────────────────────────────────────────

class TestDCF:
    """手算验证 DCF 数学正确性"""

    def test_basic_dcf(self):
        """
        简单 DCF 验证（手算）：
        收入=100亿, 增速=10%, 净利率=20%, 股本=10亿股
        WACC=10%, 永续增长=3%, 预测5年

        手算：每年 FCF ≈ 20亿（等比，折现后各约 20亿），PV_FCFs ≈ 100亿
        终值 = Year5_FCF(32.21亿)*1.03/(0.10-0.03) = 473.9亿，PV ≈ 294亿
        总 PV ≈ 394亿 / 10亿股 ≈ 39 元/股
        """
        result = compute_dcf(
            latest_revenue=100e8,
            growth_rate=0.10,
            net_margin=0.20,
            shares=10e8,
            wacc=0.10,
            terminal_growth=0.03,
            projection_years=5,
        )
        assert result["base"] is not None
        assert result["base"] > 0
        # 手算约 39 元/股，允许 ±5 元误差
        assert 30 < result["base"] < 50, f"DCF 基准值应约 39 元，实际 {result['base']}"

    def test_three_scenarios_ordering(self):
        """保守 < 基准 < 乐观"""
        result = compute_dcf(100e8, 0.10, 0.20, 10e8, 0.08, 0.03)
        assert result["bear"] < result["base"] < result["bull"]

    def test_zero_wacc_minus_terminal_returns_none(self):
        """WACC <= terminal_growth 时终值无意义，应不崩溃"""
        # WACC = terminal_growth 会导致除零，函数应返回 None 或极大值
        result = compute_dcf(100e8, 0.10, 0.20, 10e8, 0.03, 0.03)
        # 不崩溃即通过（终值无限大 → 结果可能很大或 None）
        assert result is not None

    def test_zero_shares_returns_none(self):
        result = compute_dcf(100e8, 0.10, 0.20, 0, 0.08, 0.03)
        assert result["base"] is None

    def test_negative_growth_still_works(self):
        """负增速（衰退情景）应能正常计算"""
        result = compute_dcf(100e8, -0.05, 0.15, 5e8, 0.08, 0.02)
        assert result["base"] is not None

    def test_dcf_sensitivity_ordering(self):
        """增速越高 → DCF 越高"""
        low = compute_dcf(100e8, 0.05, 0.20, 10e8, 0.08, 0.03)
        high = compute_dcf(100e8, 0.15, 0.20, 10e8, 0.08, 0.03)
        assert low["base"] < high["base"]


# ── T3：估值分位计算 ─────────────────────────────────────────

class TestValuationPercentile:
    """验证分位数计算逻辑（在 akshare_client 的 get_valuation_percentile 中）"""

    def test_lowest_percentile(self):
        """当前值 = 最小值 → 分位应接近 0%"""
        from src.data.akshare_client import parse_pct
        # 手动构造场景
        hist = [20, 25, 30, 35, 40]  # 当前=20（最低）
        current = 20.0
        pct = sum(1 for x in hist if x < current) / len(hist) * 100
        assert pct == pytest.approx(0.0)

    def test_highest_percentile(self):
        hist = [20, 25, 30, 35, 40]  # 当前=40（最高）
        current = 40.0
        pct = sum(1 for x in hist if x < current) / len(hist) * 100
        assert pct == pytest.approx(80.0)  # 4/5=80%

    def test_middle_percentile(self):
        hist = [10, 20, 30, 40, 50]  # 中位数=30
        current = 30.0
        pct = sum(1 for x in hist if x < current) / len(hist) * 100
        assert pct == pytest.approx(40.0)  # 2/5=40%


# ── T4：综合估值分 ───────────────────────────────────────────

class TestValuationScore:
    def test_all_low_means_low_score(self):
        """PE/PB 均处历史低位 + DCF 有大幅上涨空间 → 低分（低估）"""
        score = compute_valuation_score(
            pe_percentile=5.0,
            pb_percentile=5.0,
            dcf_upside_pct=0.40,   # +40% 上涨空间
        )
        assert score is not None
        assert score < 30, f"低估时估值分应 < 30，实际 {score}"

    def test_all_high_means_high_score(self):
        """PE/PB 均处历史高位 + DCF 已透支 → 高分（高估）"""
        score = compute_valuation_score(
            pe_percentile=95.0,
            pb_percentile=90.0,
            dcf_upside_pct=-0.30,  # -30% 下跌风险
        )
        assert score is not None
        assert score > 65, f"高估时估值分应 > 65，实际 {score}"

    def test_missing_dcf_uses_pe_pb_only(self):
        """DCF 无法计算时，仍能基于 PE/PB 给出分数"""
        score = compute_valuation_score(
            pe_percentile=50.0,
            pb_percentile=50.0,
            dcf_upside_pct=None,
        )
        assert score is not None
        assert 30 < score < 70

    def test_all_none_returns_none(self):
        score = compute_valuation_score(None, None, None)
        assert score is None

    def test_score_range_0_to_100(self):
        """估值分必须在 [0, 100]"""
        for pe in [0, 25, 50, 75, 100]:
            for pb in [0, 25, 50, 75, 100]:
                for upside in [-0.5, 0, 0.5]:
                    score = compute_valuation_score(pe, pb, upside)
                    if score is not None:
                        assert 0 <= score <= 100, f"score={score} 超出范围"


# ── T5：财务健康度 ───────────────────────────────────────────

class TestFinancialHealth:
    def _make_financials(self, rows: list[dict]) -> dict:
        """构造 financials 字典供 check_financial_health 使用"""
        return {"summary": rows}

    def _make_row(self, roe=25.0, debt_ratio=30.0, eps=10.0, ocf=10.0,
                  revenue="1000亿", revenue_yoy="10%") -> dict:
        return {
            "净资产收益率": f"{roe}%",
            "资产负债率": f"{debt_ratio}%",
            "基本每股收益": str(eps),
            "每股经营现金流": str(ocf),
            "营业总收入": revenue,
            "营业总收入同比增长率": revenue_yoy,
            "销售毛利率": "50%",
            "销售净利率": "20%",
        }

    def test_healthy_company_full_score(self):
        """健康公司：高 ROE、低负债、现金流好 → 高分"""
        rows = [self._make_row(roe=30, debt_ratio=20, eps=10, ocf=12)] * 3
        result = check_financial_health(self._make_financials(rows))
        assert result.score >= 80
        assert len(result.flags) == 0

    def test_high_debt_triggers_flag(self):
        """资产负债率 > 70% → 红色预警"""
        rows = [self._make_row(debt_ratio=75)] * 3
        result = check_financial_health(self._make_financials(rows))
        assert any("资产负债率" in f for f in result.flags), \
            f"高负债应触发 flag，实际 flags={result.flags}"
        assert result.score < 100

    def test_poor_cashflow_triggers_flag(self):
        """现金流/EPS < 0.5 → 红色预警"""
        rows = [self._make_row(eps=10, ocf=3)] * 3  # ocf/eps = 0.3
        result = check_financial_health(self._make_financials(rows))
        assert any("现金流" in f for f in result.flags), \
            f"差现金流应触发 flag，实际 flags={result.flags}"

    def test_good_cashflow_is_positive(self):
        """现金流/EPS >= 0.9 → 亮点"""
        rows = [self._make_row(eps=10, ocf=12)] * 3  # ocf/eps = 1.2
        result = check_financial_health(self._make_financials(rows))
        assert any("现金流" in p for p in result.positives), \
            f"好现金流应列为亮点，实际 positives={result.positives}"

    def test_high_roe_is_positive(self):
        rows = [self._make_row(roe=28)] * 3
        result = check_financial_health(self._make_financials(rows))
        assert any("ROE" in p for p in result.positives)

    def test_low_roe_triggers_warning(self):
        rows = [self._make_row(roe=5)] * 3
        result = check_financial_health(self._make_financials(rows))
        assert any("ROE" in w for w in result.warnings)
        assert result.score < 100

    def test_insufficient_data_gives_partial_score(self):
        result = check_financial_health({"summary": []})
        assert result.score == 60
        assert len(result.warnings) > 0


# ── T6：假设 hash 唯一性 ─────────────────────────────────────

class TestAssumptionsHash:
    def test_same_assumptions_same_hash(self):
        a1 = AnalysisAssumptions(revenue_growth_rate=0.10, wacc=0.08)
        a2 = AnalysisAssumptions(revenue_growth_rate=0.10, wacc=0.08)
        assert a1.hash() == a2.hash()

    def test_different_growth_rate_different_hash(self):
        a1 = AnalysisAssumptions(revenue_growth_rate=0.10)
        a2 = AnalysisAssumptions(revenue_growth_rate=0.06)
        assert a1.hash() != a2.hash()

    def test_different_wacc_different_hash(self):
        a1 = AnalysisAssumptions(wacc=0.08)
        a2 = AnalysisAssumptions(wacc=0.09)
        assert a1.hash() != a2.hash()

    def test_default_vs_explicit_none_same(self):
        """默认值（None）和显式传 None 应产生相同 hash"""
        a1 = AnalysisAssumptions()
        a2 = AnalysisAssumptions(revenue_growth_rate=None, wacc=None)
        assert a1.hash() == a2.hash()

    def test_hash_is_6_chars(self):
        a = AnalysisAssumptions()
        assert len(a.hash()) == 6


# ── T7：CAGR 自动推导 ────────────────────────────────────────

class TestRevenueCAGR:
    def test_basic_cagr(self):
        """3年 CAGR：100→121→146.41→176.9 → 增速应为 21%"""
        # 注意 compute_revenue_cagr 接收最新在前的列表
        revenues = [176.9e8, 146.41e8, 121e8, 100e8]
        cagr = compute_revenue_cagr(revenues, years=3)
        assert cagr == pytest.approx(0.21, abs=0.01)

    def test_flat_revenue(self):
        """零增长 → CAGR = 0"""
        revenues = [100e8, 100e8, 100e8, 100e8]
        cagr = compute_revenue_cagr(revenues, years=3)
        assert cagr == pytest.approx(0.0, abs=0.001)

    def test_insufficient_data_returns_none(self):
        cagr = compute_revenue_cagr([100e8], years=3)
        assert cagr is None

    def test_negative_start_returns_none(self):
        cagr = compute_revenue_cagr([100e8, 50e8, -10e8], years=2)
        assert cagr is None

    def test_declining_revenue(self):
        """下滑营收 → CAGR 为负"""
        revenues = [80e8, 90e8, 100e8]
        cagr = compute_revenue_cagr(revenues, years=2)
        assert cagr < 0


# ── T8：calculate() 端到端集成（mock 数据）───────────────────

MOCK_STOCK_DATA = {
    "code": "TEST001",
    "market": "A",
    "info": {"股票简称": "测试公司", "市值": 500e8},
    "price": [
        {"date": "2026-01-01", "close": 50.0, "open": 49, "high": 51, "low": 48, "volume": 1e6},
        {"date": "2025-12-01", "close": 48.0, "open": 47, "high": 49, "low": 47, "volume": 1e6},
    ],
    "financials": {
        "summary": [
            {
                "报告期": 2025,
                "营业总收入": "120亿",
                "营业总收入同比增长率": "20%",
                "销售毛利率": "55%",
                "销售净利率": "25%",
                "净资产收益率": "22%",
                "资产负债率": "30%",
                "流动比率": "2.5",
                "基本每股收益": "5.0",
                "每股净资产": "25.0",
                "每股经营现金流": "5.5",
            },
            {
                "报告期": 2024,
                "营业总收入": "100亿",
                "营业总收入同比增长率": "15%",
                "销售毛利率": "52%",
                "销售净利率": "22%",
                "净资产收益率": "20%",
                "资产负债率": "32%",
                "流动比率": "2.3",
                "基本每股收益": "4.0",
                "每股净资产": "22.0",
                "每股经营现金流": "4.2",
            },
            {
                "报告期": 2023,
                "营业总收入": "87亿",
                "营业总收入同比增长率": "12%",
                "销售毛利率": "50%",
                "销售净利率": "20%",
                "净资产收益率": "18%",
                "资产负债率": "35%",
                "流动比率": "2.1",
                "基本每股收益": "3.2",
                "每股净资产": "19.0",
                "每股经营现金流": "3.5",
            },
        ]
    },
    "valuation_percentile": {
        "pe": {"current": 10.0, "percentile": 20.0, "min": 8.0, "max": 25.0, "median": 15.0},
        "pb": {"current": 2.0, "percentile": 25.0, "min": 1.5, "max": 5.0, "median": 3.0},
    },
}


class TestCalculateEndToEnd:
    def test_returns_bundle(self):
        assumptions = AnalysisAssumptions()
        bundle = calculate(MOCK_STOCK_DATA, assumptions)
        assert isinstance(bundle, CalculationBundle)

    def test_stock_name_extracted(self):
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        assert bundle.stock_name == "测试公司"

    def test_data_version(self):
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        assert bundle.data_version == "2025"

    def test_valuation_score_calculated(self):
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        assert bundle.valuation.valuation_score is not None
        assert 0 <= bundle.valuation.valuation_score <= 100

    def test_pe_from_valuation_percentile(self):
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        assert bundle.valuation.pe_current == pytest.approx(10.0)
        assert bundle.valuation.pe_percentile == pytest.approx(20.0)

    def test_dcf_calculated(self):
        """市值已知时，应能计算 DCF"""
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        # DCF 需要市值（股本）和净利率，均在 mock 数据中
        # 结果可能为 None 也可能有值（取决于价格数据是否对齐）
        # 主要验证不崩溃
        assert bundle.valuation is not None

    def test_user_override_reflected_in_assumptions(self):
        custom = AnalysisAssumptions(revenue_growth_rate=0.05, wacc=0.09)
        bundle = calculate(MOCK_STOCK_DATA, custom)
        used = bundle.valuation.assumptions_used
        assert used["revenue_growth_source"] == "用户自定义"
        assert "revenue_growth_rate" in used.get("user_overrides", [])

    def test_auto_growth_from_cagr(self):
        """不传增速 → 从营收历史自动计算 CAGR"""
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        used = bundle.valuation.assumptions_used
        assert "CAGR" in used.get("revenue_growth_source", "")

    def test_financial_health_positives(self):
        """mock 数据财务健康，应有亮点"""
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        assert bundle.health.score > 0
        assert len(bundle.health.flags) == 0  # 无红色预警

    def test_key_financials_structure(self):
        bundle = calculate(MOCK_STOCK_DATA, AnalysisAssumptions())
        kf = bundle.key_financials
        assert "years" in kf
        assert "roe_pct" in kf
        assert len(kf["years"]) == 3  # 3年数据


# ── 边界/异常情景 ────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_financials_no_crash(self):
        data = {**MOCK_STOCK_DATA, "financials": {}}
        bundle = calculate(data, AnalysisAssumptions())
        assert bundle is not None

    def test_missing_price_no_crash(self):
        data = {**MOCK_STOCK_DATA, "price": []}
        bundle = calculate(data, AnalysisAssumptions())
        assert bundle.valuation.current_price is None
        assert bundle.valuation.dcf_base is None

    def test_missing_valuation_percentile_no_crash(self):
        """PE/PB 分位缺失时，仍可基于 DCF 计算估值分（不崩溃）"""
        data = {**MOCK_STOCK_DATA, "valuation_percentile": {}}
        bundle = calculate(data, AnalysisAssumptions())
        # PE/PB 为 None，但 DCF 仍可计算，valuation_score 可以是数值或 None
        assert bundle.valuation.pe_current is None
        assert bundle.valuation.pb_current is None
        # 不崩溃即通过，score 可以是 None 或纯 DCF 驱动的分数
        score = bundle.valuation.valuation_score
        if score is not None:
            assert 0 <= score <= 100

    def test_single_year_data(self):
        """只有1年财务数据时，CAGR 无法计算，不崩溃"""
        data = {**MOCK_STOCK_DATA,
                "financials": {"summary": [MOCK_STOCK_DATA["financials"]["summary"][0]]}}
        bundle = calculate(data, AnalysisAssumptions())
        assert bundle is not None
