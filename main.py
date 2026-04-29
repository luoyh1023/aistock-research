"""
AIStock CLI 入口。

用法：
  python main.py fundamental <code> [options]   基本面分析
  python main.py technical <code> [options]     技术面分析
  python main.py fetch <code> [options]         拉取原始数据
  python main.py history <code>                 查看历史分析记录

示例：
  python main.py fundamental 600519
  python main.py fundamental 600519 --model deepseek --growth-rate 0.08
  python main.py fundamental 600519 --refresh-data --refresh-analysis
  python main.py technical 600519
  python main.py technical 600519 --model claude-sonnet --verbose
  python main.py fetch 600519 --types price,financials,valuation
  python main.py history 600519
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 加载 .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent))


# ── 子命令处理函数 ────────────────────────────────────────────

def cmd_fundamental(args):
    from src.agents.fundamental import analyze, get_analysis_history
    from src.agents.fundamental_calculator import AnalysisAssumptions

    assumptions = AnalysisAssumptions(
        revenue_growth_rate=args.growth_rate,
        wacc=args.wacc,
        terminal_growth_rate=args.terminal_growth,
        pe_comparison_years=args.pe_years,
    )

    result = analyze(
        stock_code=args.code,
        model=args.model,
        assumptions=assumptions,
        force_refresh_data=args.refresh_data,
        force_refresh_analysis=args.refresh_analysis,
    )

    _print_fundamental_result(result, verbose=args.verbose)


def cmd_technical(args):
    from src.agents.technical import TechnicalAgent

    agent = TechnicalAgent()
    result = agent.analyze(
        stock_code=args.code,
        model=args.model,
        force_refresh_data=args.refresh_data,
        force_refresh_analysis=args.refresh_analysis,
    )
    _print_technical_result(result, verbose=args.verbose)


def cmd_fetch(args):
    from src.agents.data_fetcher import fetch, fetch_screener_snapshot

    if args.code == "market":
        print("拉取全市场快照...")
        records = fetch_screener_snapshot(force_refresh=args.refresh)
        print(f"获取 {len(records)} 支股票数据")
        return

    types = args.types.split(",") if args.types else None
    data = fetch(args.code, data_types=types, force_refresh=args.refresh)

    print(f"\n股票: {args.code} | 市场: {data.get('market')}")
    print(f"拉取时间: {data.get('fetched_at', 'N/A')}")

    for key in ["info", "price", "financials", "valuation", "news", "shareholders"]:
        if key in data:
            val = data[key]
            if isinstance(val, list):
                print(f"  {key}: {len(val)} 条记录")
            elif isinstance(val, dict):
                sub_summary = {k: f"{len(v)} 条" if isinstance(v, list) else str(v)[:60]
                               for k, v in val.items()}
                print(f"  {key}: {sub_summary}")
            else:
                print(f"  {key}: {str(val)[:80]}")

    if args.verbose and "info" in data:
        print(f"\n基本信息:\n{json.dumps(data['info'], ensure_ascii=False, indent=2)}")


def cmd_history(args):
    from src.agents.fundamental import get_analysis_history

    history = get_analysis_history(args.code)
    if not history:
        print(f"{args.code} 暂无历史分析记录")
        return

    print(f"\n{args.code} 历史分析记录（共 {len(history)} 条）")
    print("-" * 70)
    for i, h in enumerate(history, 1):
        s = h.get("summary", {})
        user_overrides = "（含用户自定义假设）" if s.get("assumptions_hash") != "8850f5" else ""
        print(f"[{i}] {s.get('analyzed_at', '')[:16]} | 模型: {s.get('model', 'N/A'):<16} | "
              f"数据: {s.get('data_version', 'N/A')} | "
              f"评级: {s.get('rating', 'N/A')} | "
              f"估值分: {s.get('valuation_score', 'N/A')}{user_overrides}")
    print()


# ── 输出格式化 ───────────────────────────────────────────────

def _print_fundamental_result(result: dict, verbose: bool = False):
    verdict = result.get("verdict", {})
    vd = result.get("valuation_detail", {})
    health = result.get("financial_health", {})
    assumptions = result.get("assumptions_used", {})

    print()
    print("=" * 65)
    print(f"  {result.get('stock_name', result.get('stock_code'))}（{result.get('stock_code')}）基本面分析")
    print(f"  数据版本: {result.get('data_version')} | 模型: {result.get('model_used')}")
    if result.get("cache_hit"):
        print(f"  ✓ 分析结果来自缓存（{result.get('analyzed_at', '')[:16]}）")
    if result.get("stale_warning"):
        print(f"  ⚠ {result['stale_warning']}")
    print("=" * 65)

    # 核心结论
    print(f"\n  投资建议：{verdict.get('recommendation', 'N/A')}")
    print(f"  估值判断：{verdict.get('valuation', 'N/A')}")
    print(f"  综合估值分：{verdict.get('valuation_score', 'N/A')}/100（越低越低估）")

    # 估值数据
    print(f"\n  ── 估值数据 ──")
    if vd.get("current_price"):
        print(f"  当前价格：{vd['current_price']:.2f} 元")
    if vd.get("pe_current"):
        print(f"  PE：{vd['pe_current']:.1f}，历史 {vd.get('pe_percentile_5y', 'N/A')}% 分位 "
              f"（区间 {vd.get('pe_range', 'N/A')}）")
    if vd.get("pb_current"):
        print(f"  PB：{vd['pb_current']:.2f}，历史 {vd.get('pb_percentile_5y', 'N/A')}% 分位")
    if vd.get("dcf_base"):
        upside = vd.get('dcf_upside_pct', 0) or 0
        print(f"  DCF：保守 {vd.get('dcf_bear', 'N/A'):.0f} / "
              f"基准 {vd['dcf_base']:.0f} / "
              f"乐观 {vd.get('dcf_bull', 'N/A'):.0f} 元 "
              f"（基准上涨空间 {upside:+.1f}%）")

    # 假设参数
    print(f"\n  ── 分析假设 ──")
    g = assumptions.get("revenue_growth_rate")
    g_src = assumptions.get("revenue_growth_source", "")
    print(f"  营收增速：{g:.1%} [{g_src}]" if g else "  营收增速：N/A")
    print(f"  WACC：{assumptions.get('wacc', 'N/A'):.1%}  [{assumptions.get('wacc_source', '')}]"
          if assumptions.get('wacc') else "  WACC：N/A")
    overrides = assumptions.get("user_overrides", [])
    if overrides:
        print(f"  ⚠ 用户自定义参数：{', '.join(overrides)}")

    # 财务健康
    print(f"\n  ── 财务健康度：{health.get('score', 'N/A')}/100 ──")
    for p in health.get("positives", []):
        print(f"  ✓ {p}")
    for w in health.get("warnings", []):
        print(f"  ⚠ {w}")
    for f in health.get("flags", []):
        print(f"  ✗ {f}")

    # 历史对比
    hc = result.get("history_comparison")
    if hc and hc.get("previous_verdict"):
        print(f"\n  ── 与上次分析对比 ──")
        print(f"  上次评级：{hc['previous_verdict']} "
              f"（{hc.get('previous_analyzed_at', '')[:10]}，{hc.get('previous_data_version', '')}）")

    # 完整报告
    if verbose:
        print("\n" + "─" * 65)
        print(result.get("full_report", ""))
    else:
        print(f"\n  提示：加 --verbose 查看完整分析报告\n")


def _print_technical_result(result: dict, verbose: bool = False):
    print()
    print("=" * 65)
    print(f"  {result.get('stock_name', result.get('stock_code'))}（{result.get('stock_code')}）技术面分析")
    print(f"  数据截止: {result.get('data_version')} | 模型: {result.get('model_used')}")
    if result.get("cache_hit"):
        print(f"  ✓ 分析结果来自缓存（{result.get('analyzed_at', '')[:16]}）")
    print("=" * 65)

    print(f"\n  综合信号：{result.get('signal', 'N/A')}")
    print(f"  技术评分：{result.get('technical_score', 'N/A')}/100（越高越强势）")
    if result.get("current_price"):
        print(f"  当前价格：{result['current_price']:.2f}")

    levels = result.get("key_levels", {})
    support    = levels.get("support", [])
    resistance = levels.get("resistance", [])
    if support:
        print(f"  支撑位：{support}")
    if resistance:
        print(f"  压力位：{resistance}")

    print(f"\n  ── 各指标信号 ──")
    for name, ind in result.get("indicators", {}).items():
        sig = ind.get("signal", "")
        score = ind.get("score", 0)
        print(f"  {name:<10} 分={score:+.1f}  {sig}")

    patterns = result.get("patterns_detected", [])
    if patterns:
        print(f"\n  ── 形态识别 ──")
        for p in patterns:
            print(f"  {p['pattern']}（{p.get('type', '')}）")

    if verbose:
        print("\n" + "─" * 65)
        print(result.get("full_report", ""))
    else:
        print(f"\n  提示：加 --verbose 查看完整技术分析报告\n")


# ── 参数解析 ─────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AIStock — AI 驱动的股票分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # fundamental 子命令
    p_fund = sub.add_parser("fundamental", aliases=["f"], help="基本面分析")
    p_fund.add_argument("code", help="股票代码（A股6位 或 港股5位）")
    p_fund.add_argument("--model", "-m", default="claude-sonnet",
                        choices=["claude-sonnet", "claude-opus", "claude-haiku",
                                 "gpt-4o", "gpt-4o-mini", "deepseek"],
                        help="分析使用的模型（默认 claude-sonnet）")
    p_fund.add_argument("--growth-rate", "-g", type=float, default=None,
                        metavar="RATE", help="营收增速假设，如 0.08 表示 8%%（默认自动计算）")
    p_fund.add_argument("--wacc", "-w", type=float, default=None,
                        help="WACC 假设，如 0.09（默认 0.08）")
    p_fund.add_argument("--terminal-growth", type=float, default=0.03,
                        help="永续增长率（默认 0.03）")
    p_fund.add_argument("--pe-years", type=int, default=5,
                        help="PE 历史分位对比年数（默认 5）")
    p_fund.add_argument("--refresh-data", action="store_true",
                        help="强制刷新底层财务数据")
    p_fund.add_argument("--refresh-analysis", action="store_true",
                        help="强制重新运行 LLM 分析")
    p_fund.add_argument("--verbose", "-v", action="store_true",
                        help="显示完整分析报告")

    # technical 子命令
    p_tech = sub.add_parser("technical", aliases=["t"], help="技术面分析")
    p_tech.add_argument("code", help="股票代码")
    p_tech.add_argument("--model", "-m", default="claude-sonnet",
                        choices=["claude-sonnet", "claude-opus", "claude-haiku",
                                 "gpt-4o", "gpt-4o-mini", "deepseek"],
                        help="分析使用的模型（默认 claude-sonnet）")
    p_tech.add_argument("--refresh-data", action="store_true", help="强制刷新价格数据")
    p_tech.add_argument("--refresh-analysis", action="store_true", help="强制重新运行 LLM 分析")
    p_tech.add_argument("--verbose", "-v", action="store_true", help="显示完整技术分析报告")

    # fetch 子命令
    p_fetch = sub.add_parser("fetch", help="拉取原始数据")
    p_fetch.add_argument("code", help="股票代码，或 'market' 拉取全市场快照")
    p_fetch.add_argument("--types", "-t", default=None,
                         help="数据类型，逗号分隔（price,financials,valuation,shareholders,news,info）")
    p_fetch.add_argument("--refresh", "-r", action="store_true", help="强制刷新")
    p_fetch.add_argument("--verbose", "-v", action="store_true", help="显示详细数据")

    # history 子命令
    p_hist = sub.add_parser("history", aliases=["h"], help="查看历史分析记录")
    p_hist.add_argument("code", help="股票代码")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command in ("fundamental", "f"):
        cmd_fundamental(args)
    elif args.command in ("technical", "t"):
        cmd_technical(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command in ("history", "h"):
        cmd_history(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
