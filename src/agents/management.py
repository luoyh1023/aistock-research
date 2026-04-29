"""
ManagementAgent — 管理层情况分析。

展示顺序：
  1. 董事会/高管名单（姓名·职务·持股数·年薪）
  2. 大股东/高管增减持（含量化影响度：占市值比 / 占流通股比）
  3. 股权质押情况
  [LLM 综合结论：管理层背景 + 战略匹配度 + 增减持信号 + 质押风险]

数据来源：东财/同花顺。
"""

import hashlib
import math
from datetime import datetime, date

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位专注于企业治理和管理层研究的分析师。
你的职责是综合评估管理层质量与战略执行力：
1. 管理层背景：核心高管来自哪类机构（华为系/阿里系/外资/国资/学术派等），团队稳定性
2. 战略匹配度：管理层背景是否与公司当前核心战略方向高度匹配
3. 行为信号：增减持的量化影响（占市值比/占流通股比），区分正常套现与警示性抛售
4. 质押风险：质押比例分级评估

注意：图表数据已单独展示，只写文字结论，不重复表格。减持不等于利空，需结合时点和占比判断。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下数据，为 {name}（{code}）撰写「管理层情况」综合结论。
（表格已单独展示，只写文字判断，不重复数据。战略匹配度分析不在此处，聚焦客观行为事实。）

## 董事会/高管名单（前10位）
{board_text}

## 大股东/高管增减持记录（近2年，含量化影响度）
{change_text}

## 股权质押情况
{pledge_text}

## 近期管理层相关公告
{notices_text}

---
请按以下结构输出（Markdown格式，总字数控制在400字以内）：

### 管理层背景
- 核心高管团队构成（董事长/CEO/总经理的职能分工）
- 若能从已知信息推断背景来源（华为系/阿里系/外资/国资/学术派），请标注；无法判断时写"背景信息有限"
- 持股规模评估（是否与公司深度绑定）

### 增减持信号
- 结合量化影响度（占市值比）判断：<0.1% 正常套现，0.1-0.5% 需关注，>0.5% 强信号
- 无记录时写"近2年内无显著增减持动作"

### 质押风险
（>50% 高风险 / 30-50% 需关注 / <30% 正常；无数据写"暂无质押记录"）

### 综合判断
（一句话：管理层行为信号偏正面/中性/偏负面，最值得关注的1个信号）
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


def _fmt_compact(v) -> str:
    """压缩大数字为万/亿。"""
    n = _to_float(v)
    if n is None:
        return _safe(v)
    if abs(n) >= 1e8:
        return f"{n/1e8:.2f}亿"
    if abs(n) >= 1e4:
        return f"{n/1e4:.0f}万"
    return f"{n:,.0f}"


def _calc_change_impact(records: list[dict], info: dict) -> list[dict]:
    """
    为每条增减持记录计算量化影响指标：
      - trade_value：交易金额（元），= 变动股数 × 变动均价
      - pct_of_mktcap：占总市值比（%）
      - pct_of_float：占流通股本比（%）
      - impact_level：影响等级（低/中/高）
    """
    mktcap_raw  = info.get("总市值")   # 可能是"1.2万亿"字符串或数值（元）
    float_raw   = info.get("流通股本")  # 股数（股）

    # 总市值统一转为元
    mktcap = None
    if mktcap_raw:
        s = str(mktcap_raw).replace(",", "").replace(" ", "")
        try:
            if "亿" in s:
                mktcap = float(s.replace("亿", "")) * 1e8
            elif "万" in s:
                mktcap = float(s.replace("万", "")) * 1e4
            else:
                mktcap = float(s)
        except ValueError:
            mktcap = None

    float_shares = _to_float(float_raw)

    enriched = []
    for r in records:
        rec = dict(r)
        import re as _re
        qty_raw = r.get("变动股数") or r.get("变动数量") or r.get("变动股份数量", "")
        qty_num = _re.sub(r"[^\d\.\-]", "", str(qty_raw)) if qty_raw else ""
        shares = _to_float(qty_num) if qty_num else None
        price  = _to_float(r.get("变动均价") or r.get("均价") or r.get("成交均价"))

        trade_value      = shares * price if (shares and price) else None
        pct_of_mktcap    = (trade_value / mktcap * 100) if (trade_value and mktcap) else None
        pct_of_float     = (abs(shares) / float_shares * 100) if (shares and float_shares) else None

        if pct_of_mktcap is not None:
            if pct_of_mktcap >= 0.5:
                level = "高"
            elif pct_of_mktcap >= 0.1:
                level = "中"
            else:
                level = "低"
        elif pct_of_float is not None:
            level = "高" if pct_of_float >= 1 else ("中" if pct_of_float >= 0.2 else "低")
        else:
            level = None

        rec["_trade_value"]   = trade_value
        rec["_pct_of_mktcap"] = pct_of_mktcap
        rec["_pct_of_float"]  = pct_of_float
        rec["_impact_level"]  = level
        enriched.append(rec)

    return enriched


def _fmt_board(records: list[dict]) -> str:
    if not records:
        return "（无数据）"
    lines = []
    for r in records[:10]:
        name  = _safe(r.get("姓名"))
        title = _safe(r.get("职务"))
        hold  = _fmt_compact(r.get("持股数"))
        sal   = _fmt_compact(r.get("年薪"))
        lines.append(f"- {name}（{title}）持股 {hold} 股，年薪 {sal}")
    return "\n".join(lines)


def _fmt_change_enriched(records: list[dict]) -> str:
    if not records:
        return "（近2年内无增减持记录）"
    import re as _re
    lines = []
    for r in records[:10]:
        dt    = _safe(r.get("变动截止日期") or r.get("公告日期") or r.get("变动日期") or r.get("_date", ""))[:10]
        name  = _safe(r.get("股东名称") or r.get("shareholder") or r.get("name", ""))
        typ   = _safe(r.get("变动类型") or r.get("变动方向") or r.get("type", ""))
        qty_raw = r.get("变动股数") or r.get("变动数量") or r.get("变动股份数量", "")
        if not typ and isinstance(qty_raw, str):
            typ = "增持" if "增" in qty_raw else ("减持" if "减" in qty_raw else "—")
        qty_num = _re.sub(r"[^\d\.\-]", "", str(qty_raw)) if qty_raw else ""
        qty   = _fmt_compact(float(qty_num)) if qty_num else _safe(qty_raw)
        ratio = _safe(r.get("变动比例") or r.get("持股变动比例", ""))
        level  = r.get("_impact_level")
        mktpct = r.get("_pct_of_mktcap")

        impact_str = ""
        if level:
            pct_str = f"，占市值 {mktpct:.3f}%" if mktpct else ""
            impact_str = f"【影响度:{level}{pct_str}】"

        lines.append(f"- {dt} {name} {typ} {qty}股（{ratio}）{impact_str}")
    return "\n".join(lines)


def _fmt_pledge(records: list[dict]) -> str:
    if not records:
        return "（无质押数据）"
    lines = []
    for r in records[:5]:
        name  = _safe(r.get("股东名称", ""))
        qty   = _fmt_compact(r.get("质押数量") or r.get("质押股数", ""))
        ratio = _safe(r.get("质押比例", ""))
        dt    = _safe(r.get("截止日期") or r.get("公告日期", ""))[:10]
        lines.append(f"- {name}：质押 {qty} 股，占比 {ratio}，截止 {dt}")
    return "\n".join(lines)


def _fmt_notices(notices: list[dict]) -> str:
    keywords = ["减持", "增持", "质押", "董事", "高管", "独立董事", "总裁", "CEO", "辞职", "任职", "股权激励"]
    filtered = [
        n for n in notices
        if any(kw in (n.get("title", "") + n.get("type", "")) for kw in keywords)
    ][:10]
    if not filtered:
        return "（无管理层相关公告）"
    return "\n".join(
        f"- [{str(n.get('date', ''))[:10]}] [{n.get('type', '')}] {n.get('title', '')}"
        for n in filtered
    )


def _fmt_strategy(biz: dict) -> str:
    """从 business_overview 提取主营业务和战略方向简述。"""
    intro = biz.get("main_business", "") or biz.get("business_intro", "")
    if intro and len(intro) > 300:
        intro = intro[:300] + "…"
    products = biz.get("product", [])
    top_products = "、".join(
        str(p.get("产品名称") or p.get("项目", ""))
        for p in products[:3] if p
    )
    parts = []
    if intro:
        parts.append(f"主营介绍：{intro}")
    if top_products:
        parts.append(f"核心产品/业务：{top_products}")
    return "\n".join(parts) if parts else "（无主营业务数据）"


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"management|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"management_{code}_{h}"


class ManagementAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[ManagementAgent] 开始分析: {stock_code}")

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[ManagementAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info", "notices", "pledge", "shareholder_change", "board_members"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="management", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info          = stock_data.get("info", {})
        stock_name    = info.get("股票简称", stock_code)
        board_members = stock_data.get("board_members", []) or []
        change_raw    = stock_data.get("shareholder_change", []) or []
        pledge_raw    = stock_data.get("pledge", []) or []
        notices_raw   = stock_data.get("notices", []) or []

        # 增减持量化影响
        change_enriched = _calc_change_impact(change_raw, info)

        confidence = 0.5
        if board_members:    confidence += 0.2
        if change_enriched:  confidence += 0.15
        if pledge_raw:       confidence += 0.1
        if notices_raw:      confidence += 0.05

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            board_text=_fmt_board(board_members),
            change_text=_fmt_change_enriched(change_enriched),
            pledge_text=_fmt_pledge(pledge_raw),
            notices_text=_fmt_notices(notices_raw),
        )

        print(f"[ManagementAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        chart_data = {
            "board_members":      board_members,
            "shareholder_change": change_enriched,
            "pledge":             pledge_raw,
        }

        section = ReportSection(
            dimension="management",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=["东财董事会成员", "同花顺增减持", "东财质押比例", "东财公告"],
            generated_at=datetime.now().isoformat(),
            chart_data=chart_data,
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
            "chart_data": chart_data,
        })
        print(f"[ManagementAgent] 完成: {stock_code}")
        return section


def analyze(stock_code: str, model: str = "claude-sonnet", force_refresh: bool = False) -> ReportSection:
    return ManagementAgent().analyze(stock_code, model=model, force_refresh=force_refresh)
