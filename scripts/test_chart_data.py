"""
数据管道诊断脚本
用法：
    python scripts/test_chart_data.py            # 默认测试 600519
    python scripts/test_chart_data.py 301590     # 指定股票代码
    python scripts/test_chart_data.py 600519 --revenue  # 只看收入结构
    python scripts/test_chart_data.py 600519 --perf     # 只看财务趋势

输出：
    - akshare 原始数据的列名和前几行（确认字段名）
    - chart_data 的完整内容（JSON 格式）
    - 各字段的解析结果摘要
"""

import sys
import os
import json
from pathlib import Path

# 将项目根目录加入 PATH
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 加载 .env
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def subsection(title: str):
    print(f"\n── {title} ──")


def print_json(obj, indent=2):
    def default(o):
        return str(o)
    print(json.dumps(obj, ensure_ascii=False, indent=indent, default=default))


# ── 1. 测试 akshare 原始数据 ─────────────────────────────────

def test_raw_akshare(code: str):
    """直接测试 AKShare 接口，确认字段名和数据格式"""
    import akshare as ak

    section(f"AKShare 原始数据测试 — {code}")

    # A. 财务摘要（performance 用）
    subsection("stock_financial_abstract_ths（财务摘要）")
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
        print(f"  列数: {len(df.columns)}, 行数: {len(df)}")
        print(f"  列名: {list(df.columns)}")
        print(f"  前3行:")
        for _, row in df.head(3).iterrows():
            print(f"    {dict(row)}")
    except Exception as e:
        print(f"  ❌ 失败: {e}")

    # B. 主营构成（business 用）
    prefix = "SH" if code.startswith("6") else "SZ"
    ak_sym = f"{prefix}{code}"
    subsection(f"stock_zygc_em（主营构成）symbol={ak_sym}")
    try:
        df2 = ak.stock_zygc_em(symbol=ak_sym)
        print(f"  列数: {len(df2.columns)}, 行数: {len(df2)}")
        print(f"  列名: {list(df2.columns)}")
        print(f"  分类类型值: {df2.iloc[:,2].unique().tolist() if len(df2) > 0 else '(空)'}")
        print(f"  前5行:")
        for _, row in df2.head(5).iterrows():
            print(f"    {dict(row)}")
    except Exception as e:
        print(f"  ❌ 失败: {e}")

    # C. 主营介绍（business 用）
    subsection(f"stock_zyjs_ths（主营介绍）symbol={code}")
    try:
        df3 = ak.stock_zyjs_ths(symbol=code)
        print(f"  列名: {list(df3.columns)}")
        if not df3.empty:
            print(f"  内容: {dict(df3.iloc[0])}")
    except Exception as e:
        print(f"  ❌ 失败: {e}")


# ── 2. 测试 fetch_business_overview ──────────────────────────

def test_business_overview(code: str):
    from src.data.akshare_client import fetch_business_overview

    section(f"fetch_business_overview — {code}")
    result = fetch_business_overview(code)

    subsection("business_intro")
    print_json(result.get("business_intro", {}))

    subsection(f"revenue_breakdown（{len(result.get('revenue_breakdown', []))} 条）")
    for item in result.get("revenue_breakdown", [])[:5]:
        print(f"  {item}")

    subsection(f"revenue_region（{len(result.get('revenue_region', []))} 条）")
    for item in result.get("revenue_region", [])[:5]:
        print(f"  {item}")


# ── 3. 测试 BusinessAgent.chart_data ─────────────────────────

def test_business_chart_data(code: str):
    from src.agents.business import _build_chart_data, _parse_rev_to_yi, _parse_pct_to_float
    from src.data.akshare_client import fetch_business_overview

    section(f"BusinessAgent chart_data 解析 — {code}")
    overview = fetch_business_overview(code)
    chart_data = _build_chart_data(overview)

    subsection("revenue_pie.product")
    prod = chart_data.get("revenue_pie", {}).get("product", [])
    if prod:
        for item in prod:
            print(f"  {item}")
    else:
        print("  ⚠️  空！检查列名匹配")
        # 帮助调试：打印第一个 breakdown 条目的 key
        sample = (overview.get("revenue_breakdown") or [{}])[0]
        print(f"  breakdown[0] keys: {list(sample.keys())}")

    subsection("revenue_pie.region")
    reg = chart_data.get("revenue_pie", {}).get("region", [])
    if reg:
        for item in reg:
            print(f"  {item}")
    else:
        print("  ⚠️  空！")


# ── 4. 测试 PerformanceAgent.chart_data ──────────────────────

def test_performance_chart_data(code: str):
    from src.agents.performance import _build_chart_data, _parse_yi, _parse_pct, _parse_eps
    from src.data import akshare_client as ak_client

    section(f"PerformanceAgent chart_data 解析 — {code}")

    subsection("fetch_a_financials → summary")
    fin = ak_client.fetch_a_financials(code)
    summary = fin.get("summary")

    if summary is None or (hasattr(summary, "empty") and summary.empty):
        print("  ❌ 财务摘要为空！")
        return

    records = summary.to_dict(orient="records") if hasattr(summary, "to_dict") else summary
    print(f"  共 {len(records)} 条记录，字段：{list(records[0].keys()) if records else []}")

    subsection("近5条原始数据（关键字段）")
    for r in records[:5]:
        period = str(r.get("报告期", ""))[:10]
        rev    = r.get("营业总收入", "N/A")
        rev_g  = r.get("营业总收入同比增长率", "N/A")
        gm     = r.get("销售毛利率", "N/A")
        nm     = r.get("销售净利率", "N/A")
        roe    = r.get("净资产收益率", "N/A")
        debt   = r.get("资产负债率", "N/A")
        eps    = r.get("基本每股收益", "N/A")
        print(f"  {period} | 营收={rev!r:12} | 增速={rev_g!r:8} | 毛利={gm!r:8} | "
              f"净利={nm!r:8} | ROE={roe!r:8} | 负债={debt!r:8} | EPS={eps!r}")

    subsection("解析结果")
    for r in records[:5]:
        period = str(r.get("报告期", ""))[:4]
        rev_yi = _parse_yi(r.get("营业总收入", ""))
        gm     = _parse_pct(r.get("销售毛利率", ""))
        nm     = _parse_pct(r.get("销售净利率", ""))
        roe    = _parse_pct(r.get("净资产收益率", ""))
        debt   = _parse_pct(r.get("资产负债率", ""))
        eps    = _parse_eps(r.get("基本每股收益", ""))
        rev_g  = _parse_pct(r.get("营业总收入同比增长率", ""))
        ok_rev = "✅" if rev_yi is not None else "❌"
        ok_gm  = "✅" if gm is not None else "❌"
        ok_eps = "✅" if eps is not None else "❌"
        print(f"  {period} | 营收={ok_rev}{rev_yi} 亿 | 毛利={ok_gm}{gm}% | "
              f"净利={nm}% | ROE={roe}% | 负债={debt}% | EPS={ok_eps}{eps} | 增速={rev_g}%")

    subsection("最终 chart_data 结构")
    val_pct = {}  # 不测试估值，只看财务部分
    cd = _build_chart_data(records, val_pct)
    fb = cd.get("financial_bars", {})
    print(f"  years:          {fb.get('years')}")
    print(f"  revenue:        {fb.get('revenue')}")
    print(f"  revenue_growth: {fb.get('revenue_growth')}")
    print(f"  gross_margin:   {fb.get('gross_margin')}")
    print(f"  net_margin:     {fb.get('net_margin')}")
    print(f"  roe:            {fb.get('roe')}")
    print(f"  debt_ratio:     {fb.get('debt_ratio')}")
    print(f"  eps:            {fb.get('eps')}")

    any_empty = any(
        not fb.get(k) or all(v is None for v in fb.get(k, []))
        for k in ["years", "revenue", "gross_margin"]
    )
    if any_empty:
        print("\n  ⚠️  关键字段为空，图表将无法渲染！检查上面的解析结果。")
    else:
        print("\n  ✅ 数据结构正常，图表应能渲染。")


# ── 主入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    mode = sys.argv[2] if len(sys.argv) > 2 else "--all"

    print(f"\n🔍 诊断股票: {code}  模式: {mode}")

    if mode in ("--all", "--raw"):
        test_raw_akshare(code)

    if mode in ("--all", "--revenue"):
        test_business_overview(code)
        test_business_chart_data(code)

    if mode in ("--all", "--perf"):
        test_performance_chart_data(code)

    print(f"\n{'='*60}")
    print("  诊断完成")
    print(f"{'='*60}\n")
