"""
ReportOrchestrator — 报告生成调度器。

按 Step 1 → Step 2 → Step 3 → Step 4 顺序调度各维度 Agent，
通过 Generator 流式 yield ReportSection，供前端逐节展示。

执行顺序（10 个维度）：
  Step 1（并行）：BusinessAgent + IndustryAgent
  Step 2（并行，依赖 Step 1）：PerformanceAgent + ShareholderAgent
  Step 3（并行）：CatalystAgent + ManagementAgent + ResearchAgent
  Step 4（并行）：ForecastAgent + KOLAgent
  最后：ReportSynthesizer（读取所有维度输出，生成综合结论）

注：⑦上下游 和 ⑧同行对比 已合并入 ⑥行业分析（IndustryAgent）。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator

from src.agents.business import BusinessAgent
from src.agents.industry import IndustryAgent
from src.agents.performance import PerformanceAgent
from src.agents.shareholder import ShareholderAgent
from src.agents.catalyst import CatalystAgent
from src.agents.management import ManagementAgent
from src.agents.research import ResearchAgent
from src.agents.forecast import ForecastAgent
from src.agents.kol import KOLAgent
from src.agents.report_synthesizer import ReportSynthesizer
from src.agents import data_fetcher
from src.models.report import ReportSection


class ReportOrchestrator:
    def run(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
        skip_research_pdf: bool = False,
        custom_kols: list[str] | None = None,
        debug_dims: set[str] | None = None,
    ) -> Generator[ReportSection, None, None]:
        """
        流式生成报告各节。每个维度完成后立即 yield，前端可逐节展示。

        debug_dims: 若传入，则只运行集合中的维度（自动 force_refresh），
                    其余维度直接跳过不 yield，适合快速调试单一维度。
        """
        code = stock_code.strip()
        all_sections: list[ReportSection] = []

        # 调试辅助：判断某维度是否需要运行
        def _active(dim: str) -> bool:
            return debug_dims is None or dim in debug_dims

        # 调试模式下对选中维度强制刷新
        def _fr(dim: str) -> bool:
            return force_refresh or (debug_dims is not None and dim in debug_dims)

        # 获取股票名称（供 Synthesizer 使用）
        stock_name = code
        try:
            info_data = data_fetcher.fetch(code, data_types=["info"], force_refresh=force_refresh)
            stock_name = info_data.get("info", {}).get("股票简称", code)
        except Exception:
            pass

        # ── Step 1：BusinessAgent + IndustryAgent（并行）────────
        def run_business():
            if not _active("business"):
                return None
            return BusinessAgent().analyze(code, model=model, force_refresh=_fr("business"))

        def run_industry():
            if not _active("industry"):
                return None
            return IndustryAgent().analyze(code, model=model, force_refresh=_fr("industry"))

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_business  = ex.submit(run_business)
            f_industry  = ex.submit(run_industry)

            for future in as_completed([f_business, f_industry]):
                section = future.result()
                if section is None:
                    continue
                all_sections.append(section)
                yield section

        # ── Step 2：Performance + Shareholder（并行）────────────
        def run_performance():
            if not _active("performance"):
                return None
            return PerformanceAgent().analyze(code, model=model, force_refresh=_fr("performance"))

        def run_shareholder():
            if not _active("shareholder"):
                return None
            return ShareholderAgent().analyze(code, model=model, force_refresh=_fr("shareholder"))

        with ThreadPoolExecutor(max_workers=2) as ex:
            futures_step2 = [
                ex.submit(run_performance),
                ex.submit(run_shareholder),
            ]
            for future in as_completed(futures_step2):
                section = future.result()
                if section is None:
                    continue
                all_sections.append(section)
                yield section

        # ── Step 3：Catalyst + Management + Research（并行）──────
        def run_catalyst():
            if not _active("catalyst"):
                return None
            return CatalystAgent().analyze(code, model=model, force_refresh=_fr("catalyst"))

        def run_management():
            if not _active("management"):
                return None
            return ManagementAgent().analyze(code, model=model, force_refresh=_fr("management"))

        def run_research():
            if not _active("research"):
                return None
            agent = ResearchAgent()
            return agent.analyze(
                code,
                model=model,
                force_refresh=_fr("research"),
                report_limit=6,
            )

        with ThreadPoolExecutor(max_workers=3) as ex:
            futures_step3 = [
                ex.submit(run_catalyst),
                ex.submit(run_management),
                ex.submit(run_research),
            ]
            for future in as_completed(futures_step3):
                section = future.result()
                if section is None:
                    continue
                all_sections.append(section)
                yield section

        # ── Step 4：Forecast + KOL（并行）────────────────────────
        def run_forecast():
            if not _active("forecast"):
                return None
            return ForecastAgent().analyze(code, model=model, force_refresh=_fr("forecast"))

        def run_kol():
            if not _active("kol"):
                return None
            return KOLAgent().analyze(code, model=model, force_refresh=_fr("kol"),
                                      custom_kols=custom_kols)

        with ThreadPoolExecutor(max_workers=2) as ex:
            futures_step4 = [
                ex.submit(run_forecast),
                ex.submit(run_kol),
            ]
            for future in as_completed(futures_step4):
                section = future.result()
                if section is None:
                    continue
                all_sections.append(section)
                yield section

        # ── 综合叙事层（调试模式下跳过）────────────────────────
        if debug_dims is not None and "synthesis" not in debug_dims:
            return

        synthesis = ReportSynthesizer().synthesize(
            stock_code=code,
            stock_name=stock_name,
            sections=all_sections,
            model=model,
            force_refresh=_fr("synthesis"),
        )
        all_sections.append(synthesis)
        yield synthesis
