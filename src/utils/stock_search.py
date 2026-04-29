"""
stock_search — 股票名称搜索工具。

支持：
  - A 股中文名称模糊搜索（ak.stock_info_a_code_name，24 小时本地缓存）
  - 直接判断输入是否已经是合法股票代码

用法：
    from src.utils.stock_search import search_stocks, is_stock_code

    matches = search_stocks("茅台")
    # → [{"code": "600519", "name": "贵州茅台", "market": "A"}, ...]
"""

import re
import threading

import pandas as pd

from src.utils import cache

# 线程锁，防止并发时多次拉取全量列表
_lock = threading.Lock()
_df_cache: pd.DataFrame | None = None

_CACHE_KEY = "stock_list_a_v1"
_CACHE_TTL = 86400          # 24 小时
_MAX_RESULTS = 10


def _load_a_stocks() -> pd.DataFrame:
    """加载 A 股代码+名称表，优先内存 → Redis/文件缓存 → AKShare 实时拉取。"""
    global _df_cache

    # ① 内存命中（同进程内直接复用）
    if _df_cache is not None:
        return _df_cache

    with _lock:
        if _df_cache is not None:          # double-check after lock
            return _df_cache

        # ② 磁盘/Redis 缓存
        cached = cache.get(_CACHE_KEY)
        if cached:
            _df_cache = pd.DataFrame(cached)
            return _df_cache

        # ③ 远程拉取
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            # 清洗：去掉名称中的全角空格和半角空格（如"万  科Ａ"）
            df["name"] = df["name"].str.replace(r"\s+", "", regex=True)
            # 统一 code 为 6 位字符串
            df["code"] = df["code"].astype(str).str.zfill(6)
            cache.set(_CACHE_KEY, df.to_dict("records"), ttl_seconds=_CACHE_TTL)
            _df_cache = df
            return _df_cache
        except Exception as e:
            print(f"[stock_search] 加载 A 股列表失败: {e}")
            return pd.DataFrame(columns=["code", "name"])


def is_stock_code(query: str) -> bool:
    """
    判断输入是否为合法股票代码（不含中文时直接当代码处理）：
      - A 股：6 位纯数字
      - 港股：1~5 位纯数字，或 XXXXX.HK 格式
      - 纯英文字母（美股 ticker）
    """
    q = query.strip()
    # 纯数字：1-6 位全部认为是代码（1-5 位 → 港股；6 位 → A 股）
    if re.match(r"^\d{1,6}$", q):
        return True
    # XXXXX.HK 格式
    if re.match(r"^\d{1,5}\.HK$", q, re.IGNORECASE):
        return True
    # 纯英文字母（美股 ticker）
    if re.match(r"^[A-Za-z]{1,5}$", q):
        return True
    return False


def search_stocks(query: str, max_results: int = _MAX_RESULTS) -> list[dict]:
    """
    中文名称模糊搜索 A 股。

    匹配优先级：
      1. 名称以 query 开头（前缀匹配）
      2. 名称包含 query（模糊匹配）

    返回 list of {"code": str, "name": str, "market": "A"}，
    按优先级排序，最多 max_results 条。
    """
    query = query.strip()
    if not query:
        return []

    # 纯数字或合法代码 → 直接返回空，由调用方当代码处理
    if is_stock_code(query):
        return []

    df = _load_a_stocks()
    if df.empty:
        return []

    q = query.replace(" ", "").lower()

    # 名称列统一小写 + 去空格，用于搜索
    name_norm = df["name"].str.replace(r"\s+", "", regex=True).str.lower()

    mask_prefix = name_norm.str.startswith(q, na=False)
    mask_contain = name_norm.str.contains(q, na=False, case=False)

    results: list[dict] = []
    seen: set[str] = set()

    # 优先：前缀匹配
    for _, row in df[mask_prefix].iterrows():
        code = str(row["code"])
        if code not in seen:
            seen.add(code)
            results.append({
                "code":   code,
                "name":   str(row["name"]).strip(),
                "market": "A",
            })
        if len(results) >= max_results:
            return results

    # 次之：包含匹配（排除已加入的前缀结果）
    for _, row in df[mask_contain & ~mask_prefix].iterrows():
        code = str(row["code"])
        if code not in seen:
            seen.add(code)
            results.append({
                "code":   code,
                "name":   str(row["name"]).strip(),
                "market": "A",
            })
        if len(results) >= max_results:
            break

    return results


def resolve_input(raw: str) -> tuple[str | None, list[dict]]:
    """
    统一入口：把用户原始输入解析为 (resolved_code, candidates)。

    逻辑：
      1. 空输入 → (None, [])
      2. 是合法代码 → (normalize(raw), [])
      3. 中文/混合 → 搜索，返回 (None, candidates)
         - candidates 为空 → 用户需要检查输入
         - candidates 只有 1 条 → 可直接自动选中
         - candidates 多条 → 让用户选择
    """
    raw = raw.strip()
    if not raw:
        return None, []

    if is_stock_code(raw):
        return _normalize(raw), []

    candidates = search_stocks(raw)
    return None, candidates


def _normalize(code: str) -> str:
    """
    补零对齐：
    - A 股：6 位纯数字（开头 0/3/6/8）
    - 港股：5 位纯数字，或 XXXXX.HK 格式
    判断逻辑：5 位纯数字 → 港股；1-4 位数字 → 港股补零到 5 位；6 位 → A 股。
    """
    c = code.strip()
    # XXXXX.HK 格式
    if re.match(r"^\d{1,5}\.HK$", c, re.IGNORECASE):
        num = c.split(".")[0]
        return num.zfill(5)
    if c.isdigit():
        if len(c) <= 5:
            # 5 位及以下 → 港股，补零到 5 位
            return c.zfill(5)
        # 6 位 → A 股
        return c.zfill(6)
    return c
