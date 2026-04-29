"""
KOLAgent — 大牛分析。

分析维度：
  1. 默认选取 8 位 A 股知名投资人，或使用用户自定义名单
  2. 对每位 KOL，分析其投资风格是否与该股匹配
  3. 引用其公开表达过的立场或类似持仓逻辑
  4. 综合多位 KOL 观点给出共识与分歧

数据来源：
  - 内置 KOL 档案（src/data/kol_config.py）
  - 东财千股千评（市场情绪辅助数据）
  - LLM 知识库（公开资料、访谈、持仓报告）
"""

import hashlib
from datetime import datetime, date

import akshare as ak

from src.agents import data_fetcher
from src.data.kol_config import get_kols
from src.models.model_router import complete
from src.models.report import ReportSection
from src.utils import cache


SYSTEM_PROMPT = """你是一位熟悉 A 股知名投资人风格的研究员。
你的职责是：
1. 准确描述每位投资人的核心选股逻辑和偏好
2. 客观分析该股票是否符合他们的投资标准
3. 引用他们公开发表过的观点（访谈、雪球帖子、基金报告等），若无直接记录则根据其风格推断
4. 指出不同投资人之间的观点分歧

重要：区分"有据可查的公开观点"和"基于风格的推断"，两者都要标注清楚。
分析仅供参考，不构成投资建议。"""


ANALYSIS_PROMPT = """请基于以下信息，为 {name}（{code}）撰写「大牛分析」。

## 公司基本信息
{info_text}

## 市场情绪参考
{sentiment_text}

## 待分析的知名投资人
{kol_profiles}

---
请按以下结构输出（Markdown格式，总字数控制在600字以内）：

## 大牛分析

### 各位投资人观点

{kol_sections_template}

### 大牛观点综合

**共识点：**（多位大牛都认可的逻辑，1-2条）

**分歧点：**（不同投资人之间存在明显分歧的维度，1-2条）

**整体信号：**（一句话：主流大牛对该股偏正面/中性/存在明显分歧，最值得关注的1个观点是什么）
"""

KOL_SECTION_TEMPLATE = """#### {kol_name}（{affiliation}）
- **投资风格**：{style}
- **对本股判断**：（该股是否符合其选股标准？正面/中性/负面，并说明原因）
- **公开立场**：（有据可查的观点或持仓 / 基于风格的推断，请注明来源类型）"""


def _format_info(info: dict) -> str:
    keys = ["股票简称", "所属行业", "总市值", "上市时间"]
    lines = [f"- {k}：{v}" for k in keys if (v := info.get(k))]
    return "\n".join(lines) if lines else "（基本信息有限）"


def _fetch_sentiment(code: str) -> str:
    """从东财千股千评获取情绪分数"""
    try:
        df = ak.stock_comment_em()
        row = df[df["代码"] == code]
        if row.empty:
            return "（千股千评数据未找到）"
        r = row.iloc[0]
        return (
            f"- 综合得分：{r.get('综合得分', 'N/A'):.1f} / 100\n"
            f"- 目前排名：第 {r.get('目前排名', 'N/A')} 名\n"
            f"- 关注指数：{r.get('关注指数', 'N/A')}\n"
            f"- 机构参与度：{r.get('机构参与度', 'N/A')}"
        )
    except Exception as e:
        return f"（千股千评获取失败: {e}）"


def _build_kol_profiles(kols: list[dict]) -> str:
    lines = []
    for kol in kols:
        lines.append(f"### {kol['name']}（{kol['affiliation']}）")
        lines.append(f"- 投资风格：{kol['style']}")
        lines.append(f"- 核心选股标准：{kol['criteria']}")
        lines.append(f"- 已知立场/持仓：{kol['known_for']}")
        lines.append("")
    return "\n".join(lines)


def _build_kol_template(kols: list[dict]) -> str:
    return "\n\n".join(
        KOL_SECTION_TEMPLATE.format(
            kol_name=kol["name"],
            affiliation=kol["affiliation"],
            style=kol["style"],
        )
        for kol in kols
    )


def _cache_key(code: str, model: str, kol_names: str, data_ver: str) -> str:
    raw = f"kol|{code}|{model}|{kol_names}|{data_ver}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"kol_{code}_{h}"


class KOLAgent:
    def analyze(
        self,
        stock_code: str,
        model: str = "claude-sonnet",
        force_refresh: bool = False,
        custom_kols: list[str] | None = None,
    ) -> ReportSection:
        print(f"[KOLAgent] 开始分析: {stock_code}, 自定义KOL: {custom_kols}")

        # 暂时搁置，直接返回空节
        return ReportSection(dimension="kol", content="", confidence=0.0, error=None, chart_data={})

        kols = get_kols(custom_kols)
        kol_key = ",".join(k["name"] for k in kols)
        data_ver = date.today().strftime("%Y%m%d")
        ck = _cache_key(stock_code, model, kol_key, data_ver)

        if not force_refresh:
            cached = cache.get(ck)
            if cached:
                print(f"[KOLAgent] 命中缓存: {stock_code}")
                return ReportSection(**{k: v for k, v in cached.items() if not k.startswith("_")})

        try:
            stock_data = data_fetcher.fetch(
                stock_code,
                data_types=["info"],
                force_refresh=force_refresh,
            )
        except Exception as e:
            return ReportSection(dimension="kol", content="", confidence=0.0,
                                 error=f"数据获取失败: {e}")

        info = stock_data.get("info", {})
        stock_name = info.get("股票简称", stock_code)

        sentiment_text = _fetch_sentiment(stock_code)

        prompt = ANALYSIS_PROMPT.format(
            name=stock_name,
            code=stock_code,
            info_text=_format_info(info),
            sentiment_text=sentiment_text,
            kol_profiles=_build_kol_profiles(kols),
            kol_sections_template=_build_kol_template(kols),
        )

        print(f"[KOLAgent] 调用 {model}（{len(kols)} 位KOL）...")
        result_content = complete(prompt=prompt, system=SYSTEM_PROMPT, model=model).content

        using_custom = bool(custom_kols)
        section = ReportSection(
            dimension="kol",
            content=result_content,
            confidence=0.6,  # LLM知识为主，置信度中等
            data_sources=(
                ["用户自定义KOL", "LLM知识库", "东财千股千评"]
                if using_custom
                else ["雪球/Wind公认大牛名单", "LLM知识库", "东财千股千评"]
            ),
            generated_at=datetime.now().isoformat(),
        )
        cache.set(ck, {
            "dimension": section.dimension, "content": section.content,
            "confidence": section.confidence, "data_sources": section.data_sources,
            "generated_at": section.generated_at, "error": section.error,
        })
        print(f"[KOLAgent] 完成: {stock_code}")
        return section


def analyze(
    stock_code: str,
    model: str = "claude-sonnet",
    force_refresh: bool = False,
    custom_kols: list[str] | None = None,
) -> ReportSection:
    return KOLAgent().analyze(stock_code, model=model, force_refresh=force_refresh,
                               custom_kols=custom_kols)
