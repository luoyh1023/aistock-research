"""
DataFetcherAgent — AIStock 数据层统一入口。
封装 akshare_client，加缓存、日志，供其他 Agent 调用。
其他 Agent 不得直接调用 akshare_client，必须通过此模块。

用法：
    from src.agents.data_fetcher import DataFetcherAgent
    agent = DataFetcherAgent()
    data = agent.fetch("600519", data_types=["price", "financials", "valuation"])
"""

import json
from datetime import datetime
from typing import Optional

import pandas as pd

from src.data import akshare_client as ak_client
from src.utils import cache


class DataFetcherAgent:
    """
    统一数据拉取入口。
    - 自动检测市场（A/HK）
    - 15 分钟内的相同请求命中缓存
    - 返回可序列化的 dict（DataFrame 转换为 records 格式）
    """

    CACHE_TTL = 900  # 15 分钟

    def fetch(
        self,
        stock_code: str,
        data_types: Optional[list[str]] = None,
        force_refresh: bool = False,
    ) -> dict:
        """
        主入口。

        data_types 可选值：
            "info"         — 股票基本信息（名称、行业、上市日期等）
            "price"        — 日 K 线（近 5 年）
            "financials"   — 财务报表（利润表/资产负债表/现金流量表）
            "valuation"    — PE/PB 历史及当前分位
            "shareholders" — 前十大股东、机构持股、股东人数趋势
            "industry"          — 所属行业及同行业公司
            "news"              — 近期个股新闻（最多 50 条）
            "business_overview" — 主营构成（产品/地区，含毛利率）+ 主营介绍
        """
        code = stock_code.strip()
        types_key = "_".join(sorted(data_types)) if data_types else "all"
        cache_key = cache.make_key(code, types_key)

        if not force_refresh:
            cached = cache.get(cache_key)
            if cached:
                print(f"[DataFetcherAgent] 命中缓存: {cache_key}")
                return cached

        print(f"[DataFetcherAgent] 拉取数据: {code}, 类型: {data_types or 'all'}")
        raw = ak_client.fetch_all(code, data_types)
        result = self._serialize(raw)
        result["fetched_at"] = datetime.now().isoformat()

        cache.set(cache_key, result, ttl_seconds=self.CACHE_TTL)
        return result

    def fetch_screener_snapshot(self, force_refresh: bool = False) -> list[dict]:
        """全市场基本面快照，供 StockScreenerAgent 使用。每日盘后缓存 8 小时。"""
        cache_key = cache.make_key("market", "screener")
        if not force_refresh:
            cached = cache.get(cache_key)
            if cached:
                return cached

        print("[DataFetcherAgent] 拉取全市场快照...")
        df = ak_client.fetch_screener_snapshot()
        records = df.to_dict(orient="records") if not df.empty else []
        cache.set(cache_key, records, ttl_seconds=28800)  # 8 小时
        return records

    def _serialize(self, data: dict) -> dict:
        """将 dict 中的 DataFrame 转为 records list，便于 JSON 序列化"""
        result = {}
        for key, val in data.items():
            if isinstance(val, pd.DataFrame):
                result[key] = val.to_dict(orient="records")
            elif isinstance(val, dict):
                result[key] = self._serialize(val)
            else:
                result[key] = val
        return result


# 模块级单例
_agent = DataFetcherAgent()


def fetch(stock_code: str, data_types: Optional[list[str]] = None,
          force_refresh: bool = False) -> dict:
    """便捷函数，其他 Agent 直接 import 此函数使用"""
    return _agent.fetch(stock_code, data_types, force_refresh)


def fetch_screener_snapshot(force_refresh: bool = False) -> list[dict]:
    return _agent.fetch_screener_snapshot(force_refresh)


def fetch_industry_fund_flow(industry_name: str) -> dict:
    """获取行业资金流向（当日/5日/10日净流入），供 IndustryAgent 使用。"""
    cache_key = cache.make_key("fund_flow", industry_name)
    cached = cache.get(cache_key)
    if cached:
        return cached
    result = ak_client.fetch_industry_fund_flow(industry_name)
    cache.set(cache_key, result, ttl_seconds=3600)  # 1 小时缓存
    return result


if __name__ == "__main__":
    print("=== DataFetcherAgent 测试 ===")
    data = fetch("600519", data_types=["info", "price", "valuation"])
    print(f"股票信息: {data.get('info', {}).get('股票简称', 'N/A')}")
    print(f"价格记录数: {len(data.get('price', []))}")
    pe_pct = data.get("valuation_percentile", {}).get("pe")
    if pe_pct:
        print(f"当前 PE: {pe_pct['current']}，历史 5 年分位: {pe_pct['percentile']}%")
    print("fetched_at:", data.get("fetched_at"))
