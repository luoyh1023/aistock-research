"""
IndustryAgent — 行业分析（合并 ⑦上下游 + ⑧同行对比）。

展示顺序：
  A. 产业链位置与战略门槛（LLM）
  B. 市场空间量化（LLM）
  C. 同行财务对比表（数据驱动）
  D. 行业资金流向（数据，可选）
  [LLM 综合判断：战略门槛 + 市场空间 + 投资时点]

数据来源：东财行业分类 / AKShare 行业资金流。
"""

import hashlib
import math
from datetime import datetime, date
from typing import Optional

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位产业研究分析师，专注挖掘公司的长期战略价值与成长弹性。
分析框架：
1. 产业链定位 — 聚焦"钱从哪流向哪、谁有定价权"；若供应商或客户存在集中度≥30%的情况，须点名具体公司
2. 战略壁垒 — 用可量化技术指标（效率/精度/成本/认证数量）与同行对比，禁止只写泛泛的标签（如"认证壁垒"）
3. 市场空间 — 按核心产品线拆分 TAM；同等重要的是判断"第二增长曲线"——核心技术能否迁移至相邻市场
4. 风险抵御力 — 对每个竞争风险，评估公司具体防御能力并给出置信度（高/中/低）

简单行业（食品饮料/银行/零售等，产业链节点少于3层）省略产业链分层，直接从壁垒开始。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下数据，为 {name}（{code}）撰写「行业分析」。

## 公司基本信息
- 所属行业：{industry}
- 板块：{sector}
- 总市值：{market_cap}

## 主营业务结构
{biz_summary}

## 同行业主要公司（前15家）
{peers_summary}

---
请严格按以下五节输出（Markdown格式，总字数控制在650字以内）：

### 产业链位置与战略门槛
- 产业链分层：[上游关键供应商，集中度≥30%时请点名] → [中游，★本公司所在环节] → [下游客户，集中度≥30%时请点名] → [终端]
  （简单行业省略此行，直接写壁垒）
- 核心壁垒：具体说明 **2 条**，每条必须用可量化指标支撑（如：转换效率 XX% vs 行业均值 YY%；与竞品的具体对比；国内/国际认证数量）
- 战略门槛评级：**高 / 中 / 低**

### 市场空间量化（现有业务）
- 按核心产品线拆分 TAM：
  - [产品/业务 A]：XX 亿（估算依据）
  - [产品/业务 B]：XX 亿（估算依据）
  - 合计 TAM：XX 亿
- 渗透率场景（公司份额假设 X%，依据：XX）：
  - 保守（X% 渗透率）→ 公司可获 XX 亿营收
  - 中性（X% 渗透率）→ 公司可获 XX 亿营收
  - 乐观（X% 渗透率）→ 公司可获 XX 亿营收

### 第二增长曲线判断
- 核心技术底座：[当前最关键的技术能力，1句]
- 迁移潜力：列出 1-2 个相邻市场，具体说明技术迁移的可行性与主要障碍
- 评级：**有明确路径 / 待验证 / 暂不具备** — [关键判断依据，1句]

### 竞争格局与风险抵御力
- 行业集中度：CR3/CR5 水平，目标公司位置（龙头 / 主要玩家 / 跟随者）
- 竞争风险评估（格式：[风险] → [公司的具体防御能力] → 抵御概率：高/中/低）：
  - 风险 1 → 防御说明 → 抵御概率：XX
  - 风险 2 → 防御说明 → 抵御概率：XX

### 综合判断
- 战略门槛：[高/中/低] — [一句核心理由]
- 成长空间：[大/中/小] — [现有业务空间 + 第二曲线潜力合并判断]
- 投资时点：[有投资价值/需等待验证/暂不具备] — [1个关键前提]
"""


def _safe(v, default="—") -> str:
    if v is None or str(v).strip() in ("nan", "None", ""):
        return default
    return str(v)


def _to_float(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fmt_mktcap(v) -> str:
    n = _to_float(v)
    if n is None:
        return _safe(v)
    if abs(n) >= 1e12:
        return f"{n/1e12:.1f}万亿"
    if abs(n) >= 1e8:
        return f"{n/1e8:.0f}亿"
    if abs(n) >= 1e4:
        return f"{n/1e4:.0f}万"
    return f"{n:,.0f}"


def _fmt_ratio(v, places=1) -> str:
    n = _to_float(v)
    if n is None:
        return "—"
    return f"{n:.{places}f}%"


def _fmt_pe(v) -> str:
    n = _to_float(v)
    if n is None:
        return "—"
    if n <= 0:
        return "亏损"
    return f"{n:.1f}x"


def _build_peer_table(
    peers: list[dict],
    target_code: str,
    screener: list[dict],
) -> list[dict]:
    """
    构建同行对比表数据。
    peers: 来自 industry["peers"]。
      - 东财 stock_board_industry_cons_em 返回：代码/名称/最新价/涨跌幅/换手率/PE/PB/总市值
      - yjbb 降级返回：代码/名称/EPS/ROE/毛利率/营业总收入-同比增长/净利润-同比增长
    screener: 全市场快照（含 code/pe_ttm/pb/total_mv/change_pct），网络不通时为空。
    返回 list[dict] 含 is_target 标记，字段自适应可用数据。
    """
    # 构建 screener 查找字典
    sc_by_code: dict[str, dict] = {}
    for row in screener:
        c = str(row.get("code") or row.get("代码") or "").strip()
        if c:
            sc_by_code[c] = row

    result = []
    seen = set()

    for r in peers[:15]:
        code = str(r.get("代码") or r.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)

        sc = sc_by_code.get(code, {})

        # ── 市值（东财实时 or screener）──────────────────────
        mv_raw = (r.get("总市值") or r.get("流通市值") or
                  sc.get("total_mv") or sc.get("总市值"))
        mv_str = _fmt_mktcap(mv_raw)

        # ── PE（东财 or screener）────────────────────────────
        pe_raw = (r.get("市盈率-动态") or r.get("PE") or r.get("市盈率") or
                  sc.get("pe_ttm") or sc.get("PE"))
        pe_str = _fmt_pe(pe_raw)

        # ── ROE（yjbb 降级常见）──────────────────────────────
        roe_raw = r.get("ROE") or r.get("净资产收益率") or sc.get("roe")
        n_roe = _to_float(roe_raw)
        roe_str = f"{n_roe:.1f}%" if n_roe is not None else "—"

        # ── 毛利率（yjbb 降级常见）──────────────────────────
        gm_raw = r.get("毛利率") or r.get("销售毛利率") or sc.get("gross_margin")
        n_gm = _to_float(gm_raw)
        gm_str = f"{n_gm:.1f}%" if n_gm is not None else "—"

        # ── 营收增速（yjbb 降级）─────────────────────────────
        rev_raw = (r.get("营业总收入-同比增长") or r.get("营收增速") or
                   sc.get("revenue_yoy"))
        n_rev = _to_float(rev_raw)
        if n_rev is not None:
            rev_str = f"{n_rev:+.1f}%"
            rev_pos = n_rev > 0
        else:
            rev_str = "—"
            rev_pos = None

        # ── 净利润增速（yjbb 降级）──────────────────────────
        np_raw = (r.get("净利润-同比增长") or r.get("净利增速") or
                  sc.get("profit_yoy"))
        n_np = _to_float(np_raw)
        if n_np is not None:
            np_str = f"{n_np:+.1f}%"
            np_pos = n_np > 0
        else:
            np_str = "—"
            np_pos = None

        # ── 涨跌幅（东财实时 or screener）───────────────────
        chg_raw = r.get("涨跌幅") or sc.get("change_pct")
        n_chg = _to_float(chg_raw)
        if n_chg is not None:
            chg_str = f"{n_chg:+.2f}%"
            chg_pos = n_chg > 0
        else:
            chg_str = "—"
            chg_pos = None

        result.append({
            "code":      code,
            "name":      _safe(r.get("名称") or r.get("name")),
            "mv":        mv_str,
            "pe":        pe_str,
            "roe":       roe_str,
            "gm":        gm_str,
            "rev_yoy":   rev_str,
            "rev_pos":   rev_pos,
            "np_yoy":    np_str,
            "np_pos":    np_pos,
            "chg":       chg_str,
            "chg_pos":   chg_pos,
            "is_target": code == target_code,
        })

    # 确保目标公司出现在列表中（若 peers 里没有）
    if target_code not in seen:
        sc = sc_by_code.get(target_code, {})
        if sc:
            n_chg = _to_float(sc.get("change_pct"))
            n_rev = _to_float(sc.get("revenue_yoy"))
            result.insert(0, {
                "code":      target_code,
                "name":      _safe(sc.get("name") or sc.get("名称")),
                "mv":        _fmt_mktcap(sc.get("total_mv") or sc.get("总市值")),
                "pe":        _fmt_pe(sc.get("pe_ttm") or sc.get("PE")),
                "roe":       f"{_to_float(sc.get('roe')):.1f}%" if _to_float(sc.get('roe')) else "—",
                "gm":        f"{_to_float(sc.get('gross_margin')):.1f}%" if _to_float(sc.get('gross_margin')) else "—",
                "rev_yoy":   f"{n_rev:+.1f}%" if n_rev is not None else "—",
                "rev_pos":   (n_rev > 0) if n_rev is not None else None,
                "np_yoy":    "—",
                "chg":       f"{n_chg:+.2f}%" if n_chg is not None else "—",
                "chg_pos":   (n_chg > 0) if n_chg is not None else None,
                "is_target": True,
            })

    return result


def _fmt_biz_summary(biz: dict) -> str:
    """从 business_overview 提取主营业务简述供 LLM 参考。"""
    # business_intro 可能是 str 或 dict（含 主营业务/产品类型/经营范围 等字段）
    raw_intro = biz.get("main_business") or biz.get("business_intro") or ""
    if isinstance(raw_intro, dict):
        # 优先取主营业务字段，否则拼接所有值
        intro = (
            raw_intro.get("主营业务") or
            raw_intro.get("业务介绍") or
            "；".join(str(v) for v in raw_intro.values() if v)
        )
    else:
        intro = str(raw_intro)
    if intro and len(intro) > 300:
        intro = intro[:300] + "…"

    products = biz.get("product", []) or biz.get("revenue_breakdown", []) or []

    parts = []
    if intro:
        parts.append(intro)

    # 产品/业务线结构（供 LLM 按产品线拆分 TAM）
    if products and isinstance(products[0], dict):
        prod_lines = []
        for p in products[:6]:
            name = (p.get("产品名称") or p.get("项目") or
                    p.get("产品类型") or p.get("分部名称") or "")
            rev_pct  = _to_float(p.get("营收占比") or p.get("主营构成比例") or p.get("占比"))
            gm_pct   = _to_float(p.get("毛利率") or p.get("产品毛利率"))
            if not name:
                continue
            line = str(name)
            if rev_pct is not None:
                line += f"（营收占比 {rev_pct:.1f}%"
                if gm_pct is not None:
                    line += f"，毛利率 {gm_pct:.1f}%"
                line += "）"
            prod_lines.append(line)
        if prod_lines:
            parts.append("主要产品/业务线：\n" + "\n".join(f"  - {l}" for l in prod_lines))

    return "\n".join(parts) if parts else "（无主营业务数据）"


def _fmt_peers_summary(peers: list[dict]) -> str:
    if not peers:
        return "（同行数据获取失败）"
    lines = [f"| 代码 | 名称 | 总市值 | PE | ROE | 毛利率 | 营收增速 |",
             "|------|------|--------|-----|-----|--------|----------|"]
    for r in peers[:15]:
        code = _safe(r.get("代码") or r.get("code"))
        name = _safe(r.get("名称") or r.get("name"))
        mv   = _fmt_mktcap(r.get("总市值") or r.get("流通市值"))
        pe   = _fmt_pe(r.get("市盈率-动态") or r.get("PE") or r.get("市盈率"))
        roe  = _fmt_ratio(r.get("ROE") or r.get("净资产收益率"))
        gm   = _fmt_ratio(r.get("毛利率") or r.get("销售毛利率"))
        n_rev = _to_float(r.get("营业总收入-同比增长") or r.get("营收增速"))
        rev  = f"{n_rev:+.1f}%" if n_rev is not None else "—"
        lines.append(f"| {code} | {name} | {mv} | {pe} | {roe} | {gm} | {rev} |")
    return "\n".join(lines)


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"industry_v3|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"industry_{code}_{h}"


class IndustryAgent:
    """
    行业分析 Agent（合并上下游 + 同行对比）。
    analyze() 返回 ReportSection。
    chart_data 包含：
      - peer_table: list[dict]  # 同行对比表
      - fund_flow: dict          # 行业资金流向
      - industry_name: str
    """

    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[IndustryAgent] 开始分析: {stock_code}")

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[IndustryAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "industry", "business_overview"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="industry", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info         = stock_data.get("info", {})
        stock_name   = info.get("股票简称", stock_code)
        market_cap   = info.get("总市值", "N/A")
        biz          = stock_data.get("business_overview", {}) or {}
        industry_data = stock_data.get("industry", {}) or {}

        industry_name = industry_data.get("industry", "") if isinstance(industry_data, dict) else ""
        sector        = industry_data.get("sector", "") if isinstance(industry_data, dict) else ""
        peers_raw     = industry_data.get("peers", []) if isinstance(industry_data, dict) else []
        if not isinstance(peers_raw, list):
            # DataFrame 已被 serialized 为 records
            peers_raw = []

        # 获取全市场快照（用于同行对比表）
        try:
            screener = data_fetcher.fetch_screener_snapshot(force_refresh=False)
        except Exception:
            screener = []

        # 获取行业资金流向
        fund_flow: dict = {"found": False}
        if industry_name:
            try:
                fund_flow = data_fetcher.fetch_industry_fund_flow(industry_name)
            except Exception as e:
                print(f"[IndustryAgent] 行业资金流向获取失败: {e}")

        # 构建同行对比表
        peer_table = _build_peer_table(peers_raw, stock_code, screener)

        # 置信度
        confidence = 0.5
        if industry_name:   confidence += 0.2
        if peers_raw:       confidence += 0.15
        if biz:             confidence += 0.1
        if fund_flow.get("found"): confidence += 0.05

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            industry=industry_name or "未知",
            sector=sector or "未知",
            market_cap=market_cap,
            biz_summary=_fmt_biz_summary(biz),
            peers_summary=_fmt_peers_summary(peers_raw),
        )

        print(f"[IndustryAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        chart_data = {
            "peer_table":    peer_table,
            "fund_flow":     fund_flow,
            "industry_name": industry_name,
        }

        section = ReportSection(
            dimension="industry",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=["东财行业分类", "东财行业成份股", "同花顺行业资金流向"],
            generated_at=datetime.now().isoformat(),
            chart_data=chart_data,
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
            "chart_data": chart_data,
        })
        print(f"[IndustryAgent] 完成: {stock_code}，行业={industry_name}，同行={len(peer_table)}家")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return IndustryAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
