"""
KOL 大牛配置 — 默认知名投资人列表。

每位 KOL 包含：
  - name:       姓名
  - affiliation: 机构/身份
  - style:      投资风格标签
  - criteria:   核心选股标准（供 LLM 匹配分析）
  - known_for:  广为人知的持仓/观点

用户可通过 UI 输入自定义 KOL 名单，覆盖默认列表。
"""

DEFAULT_KOLS: list[dict] = [
    {
        "name": "段永平",
        "affiliation": "步步高创始人 / 个人投资者",
        "style": "价值投资 · 长期持有 · 集中持股",
        "criteria": "护城河极宽、生意模式简单易懂、管理层诚实有能力、合理价格买入、长期持有不动",
        "known_for": "重仓苹果、腾讯；强调'Stop doing list'；反对过度分散",
    },
    {
        "name": "张坤",
        "affiliation": "易方达基金 基金经理",
        "style": "消费白马 · 高ROE · 长期集中持有",
        "criteria": "高护城河消费类企业、ROE持续>20%、现金流强劲、品牌溢价、管理层优秀",
        "known_for": "长期重仓贵州茅台、洋河股份、五粮液、海天味业等消费白马",
    },
    {
        "name": "但斌",
        "affiliation": "东方港湾 创始人",
        "style": "时间玫瑰 · 极度长期持有 · 消费龙头",
        "criteria": "消费升级受益者、品牌护城河、行业龙头、可以持有10年以上",
        "known_for": "茅台多年重仓，'时间是朋友'理念，公开看好高端白酒长期价值",
    },
    {
        "name": "林园",
        "affiliation": "林园投资 创始人",
        "style": "白酒医药 · 消费独占 · 高毛利",
        "criteria": "毛利率极高（>60%）、独占性产品、消费品中的奢侈品属性、持续提价能力",
        "known_for": "长期持有贵州茅台、五粮液、片仔癀；强调'买独占性生意'",
    },
    {
        "name": "傅鹏博",
        "affiliation": "睿远基金 创始合伙人",
        "style": "成长价值平衡 · 制造业升级 · 消费",
        "criteria": "行业竞争格局清晰、公司有定价权或成本优势、管理层执行力强、估值合理",
        "known_for": "兴全合润基金历史业绩优异，持仓均衡覆盖消费制造科技",
    },
    {
        "name": "丘栋荣",
        "affiliation": "中庚基金 首席投资官",
        "style": "低估值价值 · 逆向投资 · 小盘成长",
        "criteria": "低估值（PE/PB分位偏低）、景气拐点、被市场忽视的中小市值企业",
        "known_for": "偏好低估值逆向机会，在市场热度高时往往回避热门赛道",
    },
    {
        "name": "朱少醒",
        "affiliation": "富国基金 基金经理",
        "style": "长期成长 · 均衡配置 · 消费科技",
        "criteria": "优质商业模式、行业空间大、管理层稳定、愿意持有3年以上",
        "known_for": "富国天惠精选成长基金长跑冠军，持仓风格均衡稳健",
    },
    {
        "name": "邱国鹭",
        "affiliation": "高毅资产 联合创始人",
        "style": "深度价值 · 行业专家 · 安全边际",
        "criteria": "低于内在价值买入、行业龙头、下行风险有限、催化剂明确",
        "known_for": "强调安全边际，擅长挖掘行业困境反转机会",
    },
]

# KOL 名称→配置的快查表（供按名字检索）
KOL_BY_NAME: dict[str, dict] = {kol["name"]: kol for kol in DEFAULT_KOLS}


def get_kols(custom_names: list[str] | None = None) -> list[dict]:
    """
    返回实际使用的 KOL 列表。
    - custom_names 为空 → 返回全部默认 KOL
    - custom_names 有值 → 先从内置库匹配，匹配不到则生成简要占位 profile
    """
    if not custom_names:
        return DEFAULT_KOLS

    result = []
    for name in custom_names:
        name = name.strip()
        if not name:
            continue
        if name in KOL_BY_NAME:
            result.append(KOL_BY_NAME[name])
        else:
            # 用户自定义、库中没有的 KOL → 提供基础结构，由 LLM 补充
            result.append({
                "name": name,
                "affiliation": "（用户自定义）",
                "style": "未知，请 LLM 根据公开信息判断",
                "criteria": "请根据该投资人公开资料分析其选股逻辑",
                "known_for": "请根据公开信息描述其知名持仓或投资观点",
            })
    return result if result else DEFAULT_KOLS
