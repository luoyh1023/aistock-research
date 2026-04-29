"""
PeerAgent — 同行对比分析。

分析维度：行业内核心指标横向排名、竞争格局。
依赖：IndustryAgent 输出的 peer_codes 列表。
数据来源：全市场快照（PE/PB/ROE/营收增速等）。
"""

import hashlib
from datetime import datetime, date
from typing import Optional

from src.agents import data_fetcher
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位擅长横向比较分析的行业研究员。
基于提供的同行业公司关键指标，你的职责是：
1. 识别该公司在同行中的相对优势和劣势
2. 判断估值相对合理性（高估/低估/合理）
3. 指出竞争格局中的关键差异点

风格：数据导向、对比鲜明，有明确排名感。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下同行业公司对比数据，为 {name}（{code}）撰写「同行对比」分析。

## 同行业核心指标对比
{peers_table}

（注：目标公司 {code} 已在列表中标注 ★）

---
请按以下结构输出（Markdown格式，总字数控制在500字以内）：

## 同行对比

### 市值与估值排位
（该公司在同行中的市值排名、PE/PB水平对比，是高估还是低估，2条）

### 盈利能力排位
（ROE/净利率/毛利率与同行比较，排在前1/3还是后1/3，2条）

### 成长性排位
（营收增速、业绩增速与同行比较，2条）

### 综合竞争位置判断
（一句话：该公司在同行中属于龙头/中游/尾部，核心竞争优势是什么）

### 需要重点关注的差距
（与龙头公司相比，该公司最主要的差距在哪里，1-2条）
"""


def _build_peers_table(target_code: str, target_info: dict, peers_snapshot: list[dict]) -> str:
    """将全市场快照过滤出同行，构建对比表格。"""
    if not peers_snapshot:
        return "（同行数据获取失败）"

    rows = ["| 代码 | 名称 | 总市值(亿) | PE | PB | 换手率 | 60日涨跌 |"]
    rows.append("|------|------|-----------|----|----|--------|---------|")

    # 目标公司标注
    def fmt_row(r: dict, is_target: bool = False) -> str:
        code = str(r.get("code") or r.get("代码", ""))
        name = str(r.get("name") or r.get("名称", ""))[:6]
        mv = r.get("total_mv") or r.get("总市值", "")
        pe = r.get("pe_ttm") or r.get("市盈率-动态", "")
        pb = r.get("pb") or r.get("市净率", "")
        turn = r.get("turnover_rate") or r.get("换手率", "")
        chg60 = r.get("change_60d") or r.get("60日涨跌幅", "")
        # 格式化市值（元→亿）
        try:
            mv_yi = f"{float(mv)/1e8:.0f}" if mv else "N/A"
        except Exception:
            mv_yi = str(mv)[:8]
        mark = "★" if is_target else ""
        return f"| {mark}{code} | {mark}{name} | {mv_yi} | {pe} | {pb} | {turn} | {chg60} |"

    target_code_6 = str(target_code).zfill(6)
    added_target = False
    for r in peers_snapshot[:25]:
        code = str(r.get("code") or r.get("代码", ""))
        is_target = code == target_code_6
        if is_target:
            added_target = True
        rows.append(fmt_row(r, is_target))

    if not added_target:
        # 目标公司不在快照中，构造一行
        name = target_info.get("股票简称", target_code)
        rows.insert(1, f"| ★{target_code_6} | ★{name} | N/A | N/A | N/A | N/A | N/A |")

    return "\n".join(rows)


def _cache_key(code: str, model: str, data_ver: str) -> str:
    raw = f"peer|{code}|{model}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"peer_{code}_{h}"


class PeerAgent:
    def analyze(
        self,
        stock_code: str,
        peer_codes: list[str],
        model: str = "claude-sonnet",
        force_refresh: bool = False,
    ) -> ReportSection:
        print(f"[PeerAgent] 开始分析: {stock_code}，同行 {len(peer_codes)} 家")
        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="peer", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)

        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, data_ver)
        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[PeerAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        # 获取全市场快照，过滤同行
        try:
            snapshot = data_fetcher.fetch_screener_snapshot(force_refresh=force_refresh)
        except Exception:
            snapshot = []

        peer_set = set(peer_codes)
        peer_set.add(str(stock_code).zfill(6))
        peers_data = [
            r for r in snapshot
            if str(r.get("code") or r.get("代码", "")).zfill(6) in peer_set
        ]

        confidence = 1.0
        if not peers_data:
            confidence -= 0.5
        if len(peers_data) < 3:
            confidence -= 0.2

        peers_table = _build_peers_table(stock_code, info, peers_data)

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            peers_table=peers_table,
        )

        print(f"[PeerAgent] 调用 {model}...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        section = ReportSection(
            dimension="peer",
            content=result_content,
            confidence=round(confidence, 2),
            data_sources=["东财全市场快照"],
            generated_at=datetime.now().isoformat(),
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
        })
        print(f"[PeerAgent] 完成: {stock_code}")
        return section


def analyze(
    stock_code: str,
    peer_codes: list[str],
    model: str = "claude-sonnet",
    force_refresh: bool = False,
) -> ReportSection:
    return PeerAgent().analyze(stock_code, peer_codes=peer_codes, model=model,
                               force_refresh=force_refresh)
