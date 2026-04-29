"""
FundamentalAgent 缓存行为测试。
验证：命中策略、假设变化触发新分析、季报过期检测。
使用 unittest.mock 隔离 LLM 和网络调用。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.agents.fundamental import (
    FundamentalAgent,
    _analysis_cache_key,
    get_analysis_history,
    is_data_stale,
)
from src.agents.fundamental_calculator import AnalysisAssumptions


# ── 缓存 key 生成规则 ────────────────────────────────────────

class TestCacheKeyGeneration:
    def test_same_params_same_key(self):
        a = AnalysisAssumptions(revenue_growth_rate=0.10)
        key1 = _analysis_cache_key("600519", "claude-sonnet", a, "2025")
        key2 = _analysis_cache_key("600519", "claude-sonnet", a, "2025")
        assert key1 == key2

    def test_different_model_different_key(self):
        a = AnalysisAssumptions()
        key1 = _analysis_cache_key("600519", "claude-sonnet", a, "2025")
        key2 = _analysis_cache_key("600519", "deepseek", a, "2025")
        assert key1 != key2

    def test_different_assumptions_different_key(self):
        a1 = AnalysisAssumptions(revenue_growth_rate=0.10)
        a2 = AnalysisAssumptions(revenue_growth_rate=0.06)
        key1 = _analysis_cache_key("600519", "claude-sonnet", a1, "2025")
        key2 = _analysis_cache_key("600519", "claude-sonnet", a2, "2025")
        assert key1 != key2

    def test_different_data_version_different_key(self):
        a = AnalysisAssumptions()
        key1 = _analysis_cache_key("600519", "claude-sonnet", a, "2025Q3")
        key2 = _analysis_cache_key("600519", "claude-sonnet", a, "2025Q4")
        assert key1 != key2

    def test_different_stock_different_key(self):
        a = AnalysisAssumptions()
        key1 = _analysis_cache_key("600519", "claude-sonnet", a, "2025")
        key2 = _analysis_cache_key("000858", "claude-sonnet", a, "2025")
        assert key1 != key2


# ── 季报过期检测 ─────────────────────────────────────────────

class TestDataStaleness:
    def test_old_data_is_stale(self):
        assert is_data_stale("2020") is True

    def test_current_year_is_not_stale(self):
        assert is_data_stale("2025") is False
        assert is_data_stale("2026") is False

    def test_last_year_is_not_stale(self):
        # 去年数据通常仍有效
        assert is_data_stale("2025") is False

    def test_invalid_version_no_crash(self):
        result = is_data_stale("unknown")
        assert isinstance(result, bool)


# ── LLM 调用次数验证 ─────────────────────────────────────────

class TestCacheBehavior:
    """
    通过 mock LLM 验证：
    - 第一次调用 → LLM 被调用 1 次
    - 相同参数第二次调用 → LLM 被调用 0 次（命中缓存）
    - 假设变化 → LLM 再次被调用
    """

    MOCK_LLM_RESULT = MagicMock(
        content="## 1. 投资建议\n【增持】基本面良好。\n## 2. 估值解读\n略。",
        input_tokens=500,
        output_tokens=200,
    )

    MOCK_STOCK_DATA = {
        "code": "TEST888",
        "market": "A",
        "info": {"股票简称": "缓存测试股", "市值": 100e8},
        "price": [{"date": "2025-12-01", "close": 20.0, "open": 19, "high": 21, "low": 18, "volume": 1e5}],
        "financials": {
            "summary": [
                {
                    "报告期": 2025,
                    "营业总收入": "50亿",
                    "营业总收入同比增长率": "15%",
                    "销售毛利率": "40%",
                    "销售净利率": "15%",
                    "净资产收益率": "18%",
                    "资产负债率": "35%",
                    "流动比率": "2.0",
                    "基本每股收益": "2.0",
                    "每股净资产": "12.0",
                    "每股经营现金流": "2.1",
                },
                {
                    "报告期": 2024,
                    "营业总收入": "43亿",
                    "营业总收入同比增长率": "10%",
                    "销售毛利率": "38%",
                    "销售净利率": "13%",
                    "净资产收益率": "15%",
                    "资产负债率": "37%",
                    "流动比率": "1.9",
                    "基本每股收益": "1.6",
                    "每股净资产": "11.0",
                    "每股经营现金流": "1.7",
                },
            ]
        },
        "valuation_percentile": {
            "pe": {"current": 10.0, "percentile": 30.0, "min": 8.0, "max": 20.0, "median": 13.0},
            "pb": {"current": 1.7, "percentile": 35.0, "min": 1.2, "max": 4.0, "median": 2.5},
        },
        "fetched_at": "2026-04-21T00:00:00",
    }

    @pytest.fixture(autouse=True)
    def clean_cache(self):
        """每个测试前清除 TEST888 的分析缓存"""
        from src.utils import cache as _cache
        import glob, os
        cache_dir = Path(__file__).parent.parent / "cache"
        for f in cache_dir.glob("*TEST888*"):
            f.unlink(missing_ok=True)
        yield
        for f in cache_dir.glob("*TEST888*"):
            f.unlink(missing_ok=True)

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_first_call_invokes_llm(self, mock_llm, mock_fetch):
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        result = agent.analyze("TEST888", model="claude-sonnet")

        assert mock_llm.call_count == 1
        assert result["cache_hit"] is False

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_second_call_hits_cache(self, mock_llm, mock_fetch):
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        agent.analyze("TEST888", model="claude-sonnet")      # 第一次
        mock_llm.reset_mock()

        result = agent.analyze("TEST888", model="claude-sonnet")  # 第二次
        assert mock_llm.call_count == 0, "第二次调用应命中缓存，不应调用 LLM"
        assert result["cache_hit"] is True

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_assumption_change_triggers_new_llm_call(self, mock_llm, mock_fetch):
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        agent.analyze("TEST888", model="claude-sonnet",
                      assumptions=AnalysisAssumptions(revenue_growth_rate=0.10))
        mock_llm.reset_mock()

        # 修改增速假设 → 新 hash → 应重新调用 LLM
        agent.analyze("TEST888", model="claude-sonnet",
                      assumptions=AnalysisAssumptions(revenue_growth_rate=0.05))
        assert mock_llm.call_count == 1, "假设变化应触发新的 LLM 调用"

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_different_model_triggers_new_llm_call(self, mock_llm, mock_fetch):
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        agent.analyze("TEST888", model="claude-sonnet")
        mock_llm.reset_mock()

        agent.analyze("TEST888", model="deepseek")
        assert mock_llm.call_count == 1, "不同模型应各自独立缓存"

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_force_refresh_bypasses_cache(self, mock_llm, mock_fetch):
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        agent.analyze("TEST888", model="claude-sonnet")
        mock_llm.reset_mock()

        result = agent.analyze("TEST888", model="claude-sonnet",
                               force_refresh_analysis=True)
        assert mock_llm.call_count == 1, "force_refresh_analysis 应绕过缓存"
        assert result["cache_hit"] is False

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_history_recorded_after_analysis(self, mock_llm, mock_fetch):
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        agent.analyze("TEST888", model="claude-sonnet")

        history = get_analysis_history("TEST888")
        assert len(history) >= 1
        assert history[0]["summary"]["model"] == "claude-sonnet"

    @patch("src.agents.fundamental.data_fetcher.fetch")
    @patch("src.agents.fundamental.complete")
    def test_output_structure(self, mock_llm, mock_fetch):
        """验证输出结构包含所有必要字段"""
        mock_fetch.return_value = self.MOCK_STOCK_DATA
        mock_llm.return_value = self.MOCK_LLM_RESULT

        agent = FundamentalAgent()
        result = agent.analyze("TEST888", model="claude-sonnet")

        required_keys = [
            "stock_code", "stock_name", "analysis_id", "data_version",
            "model_used", "analyzed_at", "cache_hit", "verdict",
            "valuation_detail", "assumptions_used", "financial_health",
            "sensitivity", "full_report",
        ]
        for key in required_keys:
            assert key in result, f"输出缺少必要字段: {key}"

        verdict_keys = ["valuation", "recommendation", "valuation_score"]
        for key in verdict_keys:
            assert key in result["verdict"], f"verdict 缺少字段: {key}"
