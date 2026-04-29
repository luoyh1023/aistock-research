"""
AKShare 数据客户端 — AIStock 核心数据层。
封装所有 AKShare / yfinance 调用，统一清洗格式后返回标准化数据。
其他 Agent 不得直接调用 AKShare，必须通过此模块。

支持的数据类型（data_type）：
  price             — 日/月 K 线（OHLCV）
  financials        — 财务三表（利润表/资产负债表/现金流量表）
  valuation         — PE、PB、股息率历史
  shareholders      — 前十大股东、机构持股、股东人数趋势
  industry          — 行业分类、同行业公司
  news              — 近期个股新闻
  screener          — 全市场基本面快照（供选股器用）
  business_overview — 主营构成（分产品/地区毛利率）+ 主营介绍（同花顺）
"""

import os
# 保留系统代理配置（东财 API 需要代理才能访问）
# yfinance 同样走系统代理，无需单独配置

import re
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
import yfinance as yf


# ── 工具函数 ─────────────────────────────────────────────────

def detect_market(code: str) -> str:
    code = code.strip()
    if code.startswith(("0", "6", "3", "8", "4")) and len(code) == 6:
        return "A"
    if len(code) == 5 or code.endswith(".HK"):
        return "HK"
    raise ValueError(f"无法识别的股票代码: {code}（A股6位，港股5位）")


def safe_float(val, default=None) -> Optional[float]:
    if val is None or val is False:
        return default
    try:
        s = str(val).strip().replace(",", "").replace("亿", "e8").replace("%", "")
        return float(s)
    except Exception:
        return default


def parse_yi(val) -> Optional[float]:
    """解析 '1741.44亿' → float（元）"""
    if val is None:
        return None
    m = re.match(r"([\d.]+)亿", str(val).strip())
    return float(m.group(1)) * 1e8 if m else safe_float(val)


def parse_pct(val) -> Optional[float]:
    """解析 '15.54%' → 15.54"""
    if val is None:
        return None
    try:
        return float(str(val).replace("%", "").strip())
    except Exception:
        return None


# ── 价格数据 ─────────────────────────────────────────────────

def fetch_price(code: str, market: str, period: str = "5y",
                interval: str = "1d") -> pd.DataFrame:
    """
    返回 K 线数据，列：date, open, high, low, close, volume
    period: yfinance 格式 ("1y", "3y", "5y" 等)
    interval: "1d" | "1wk" | "1mo"
    """
    if market == "HK":
        hk_code = code.replace(".HK", "").zfill(5)
        yf_symbol = f"{hk_code.lstrip('0')}.HK"
    elif code.startswith("6"):
        yf_symbol = f"{code}.SS"
    else:
        yf_symbol = f"{code}.SZ"

    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)
        df = df.reset_index()
        df = df.rename(columns={
            "Date": "date", "Datetime": "date",
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df[["date", "open", "high", "low", "close", "volume"]].copy()
    except Exception as e:
        print(f"[akshare_client] 价格拉取失败 {code}: {e}")
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


# ── A股财务数据 ──────────────────────────────────────────────

def _fetch_a_financial_summary(code: str) -> pd.DataFrame:
    """
    同花顺财务摘要（年度）— 与 AITeam fundamental-analyst 保持一致。
    列：报告期 营业总收入 营业总收入同比增长率 销售毛利率 销售净利率
        净资产收益率 资产负债率 流动比率 基本每股收益 每股净资产 每股经营现金流
    """
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
        df = df.sort_values("报告期", ascending=False).head(8)
        return df
    except Exception as e:
        print(f"[akshare_client] A股财务摘要失败 {code}: {e}")
        return pd.DataFrame()


def _fetch_a_income_statement(code: str) -> pd.DataFrame:
    """新浪财务 — 利润表（季度+年度）"""
    try:
        df = ak.stock_financial_report_sina(stock=code, symbol="利润表")
        return df
    except Exception as e:
        print(f"[akshare_client] 利润表失败 {code}: {e}")
        return pd.DataFrame()


def _fetch_a_balance_sheet(code: str) -> pd.DataFrame:
    try:
        df = ak.stock_financial_report_sina(stock=code, symbol="资产负债表")
        return df
    except Exception as e:
        print(f"[akshare_client] 资产负债表失败 {code}: {e}")
        return pd.DataFrame()


def _fetch_a_cash_flow(code: str) -> pd.DataFrame:
    try:
        df = ak.stock_financial_report_sina(stock=code, symbol="现金流量表")
        return df
    except Exception as e:
        print(f"[akshare_client] 现金流量表失败 {code}: {e}")
        return pd.DataFrame()


def fetch_a_financials(code: str) -> dict[str, pd.DataFrame]:
    """返回 {"summary": df, "income": df, "balance": df, "cashflow": df}"""
    return {
        "summary": _fetch_a_financial_summary(code),
        "income": _fetch_a_income_statement(code),
        "balance": _fetch_a_balance_sheet(code),
        "cashflow": _fetch_a_cash_flow(code),
    }


# ── 港股财务数据 ─────────────────────────────────────────────

def fetch_hk_financials(code: str) -> dict[str, pd.DataFrame]:
    """
    港股财务：东财综合指标（年度）。
    归一化输出与 A 股 summary 相同的中文列名，供 PerformanceAgent 等统一解析。
    原始列 OPERATE_INCOME 单位为港元（元），转换为 亿HKD 字符串（如 "4572.87亿"）。
    """
    hk_code = code.replace(".HK", "").zfill(5)
    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=hk_code, indicator="年度")
        df = df.sort_values("REPORT_DATE", ascending=False).head(8)

        def _pct(v):
            try:
                return f"{float(v):.2f}%"
            except Exception:
                return ""

        def _yi(v):
            """港元原始值（元）→ 亿 字符串，PerformanceAgent._parse_yi 可直接解析"""
            try:
                return f"{float(v) / 1e8:.2f}亿"
            except Exception:
                return ""

        norm = pd.DataFrame()
        norm["报告期"]              = df["REPORT_DATE"].astype(str).str[:10]
        norm["营业总收入"]          = df["OPERATE_INCOME"].apply(_yi)
        norm["营业总收入同比增长率"] = df.get("OPERATE_INCOME_YOY", pd.Series(dtype=float)).apply(_pct)
        norm["销售毛利率"]          = df.get("GROSS_PROFIT_RATIO", pd.Series(dtype=float)).apply(_pct)
        norm["销售净利率"]          = df.get("NET_PROFIT_RATIO",   pd.Series(dtype=float)).apply(_pct)
        norm["净资产收益率"]        = df.get("ROE_AVG",            pd.Series(dtype=float)).apply(_pct)
        norm["资产负债率"]          = df.get("DEBT_ASSET_RATIO",   pd.Series(dtype=float)).apply(_pct)
        norm["基本每股收益"]        = df.get("BASIC_EPS",          pd.Series(dtype=float)).apply(
            lambda v: str(round(float(v), 2)) if v == v else ""
        )
        # 归属母公司净利润（亿HKD）
        norm["归属母公司净利润"] = df.get("HOLDER_PROFIT", pd.Series(dtype=float)).apply(_yi)
        norm["货币单位"] = "HKD"

        return {"summary": norm}
    except Exception as e:
        print(f"[akshare_client] 港股财务失败 {code}: {e}")
        return {"summary": pd.DataFrame()}


def _fetch_hk_profile(code: str) -> dict:
    """
    内部函数：东财港股公司简介（含中文名、行业、公司介绍、董事长等）。
    供 fetch_hk_stock_info / fetch_hk_business_overview 共用，避免重复调用。
    """
    hk_code = code.replace(".HK", "").zfill(5)
    try:
        df = ak.stock_hk_company_profile_em(symbol=hk_code)
        if not df.empty:
            return df.iloc[0].to_dict()
    except Exception as e:
        print(f"[akshare_client] 港股公司简介失败 {code}: {e}")
    return {}


def fetch_hk_stock_info(code: str) -> dict:
    """
    港股基本信息：
    - 优先从东财 stock_hk_company_profile_em 获取中文名称、行业、公司介绍
    - 再从 yfinance 补充市值、PE、PB、当前价等
    """
    hk_code = code.replace(".HK", "").zfill(5)
    yf_symbol = f"{hk_code.lstrip('0')}.HK"

    result: dict = {"股票简称": code}

    # ── 东财公司简介（中文）────────────────────────────────────
    profile = _fetch_hk_profile(code)
    if profile:
        result.update({
            "股票简称":  profile.get("公司名称", code),
            "行业":      profile.get("所属行业", ""),
            "公司介绍":  profile.get("公司介绍", ""),
            "董事长":    profile.get("董事长", ""),
            "员工人数":  profile.get("员工人数"),
            "成立日期":  profile.get("公司成立日期", ""),
        })

    # ── yfinance：市值 / PE / PB ──────────────────────────────
    try:
        t   = yf.Ticker(yf_symbol)
        yfi = t.info
        cap = yfi.get("marketCap")
        if cap:
            cap_hkd = float(cap)
            if cap_hkd >= 1e12:
                result["总市值"] = f"{cap_hkd/1e12:.1f}万亿HKD"
            elif cap_hkd >= 1e8:
                result["总市值"] = f"{cap_hkd/1e8:.0f}亿HKD"
            else:
                result["总市值"] = f"{cap_hkd:.0f}HKD"
        result["PE"]      = round(float(yfi["trailingPE"]), 1) if yfi.get("trailingPE") else None
        result["PB"]      = round(float(yfi["priceToBook"]), 2) if yfi.get("priceToBook") else None
        result["当前价"]  = yfi.get("currentPrice")
        if not result.get("行业"):
            result["行业"] = yfi.get("industry", "")
        if not result.get("股票简称") or result["股票简称"] == code:
            result["股票简称"] = yfi.get("longName") or yfi.get("shortName", code)
    except Exception as e:
        print(f"[akshare_client] 港股yfinance信息失败 {code}: {e}")

    return result


def fetch_hk_business_overview(code: str) -> dict:
    """
    港股主营业务概述：从东财公司简介提取中文 business_intro，
    格式与 A 股 fetch_business_overview 兼容（供 BusinessAgent 使用）。
    """
    profile = _fetch_hk_profile(code)
    intro = str(profile.get("公司介绍") or "").strip()
    if not intro:
        return {}
    return {
        "main_business": intro,
        "business_intro": intro,
        "product": [],
    }


def fetch_hk_news(code: str) -> list[dict]:
    """
    港股新闻：来自 yfinance（英文为主，约10条）。
    返回格式与 A 股 fetch_news 兼容：list of {标题, 内容, 发布时间, 来源}。
    """
    hk_code  = code.replace(".HK", "").zfill(5)
    yf_symbol = f"{hk_code.lstrip('0')}.HK"
    result = []
    try:
        t    = yf.Ticker(yf_symbol)
        news = t.news or []
        for item in news[:20]:
            c = item.get("content", {})
            title   = c.get("title", "")
            summary = c.get("summary") or c.get("description", "")
            pub_date = c.get("pubDate", "")
            provider = c.get("provider", {}).get("displayName", "")
            if title:
                result.append({
                    "标题": title,
                    "内容": summary,
                    "发布时间": pub_date,
                    "来源": provider,
                })
    except Exception as e:
        print(f"[akshare_client] 港股新闻失败 {code}: {e}")
    return result


def fetch_hk_valuation(code: str) -> tuple[pd.DataFrame, dict]:
    """
    港股历史 PE/PB：用东财年度 EPS/BPS + yfinance 年末价格自建序列。
    返回 (历史 DataFrame, valuation_percentile dict)
    """
    hk_code = code.replace(".HK", "").zfill(5)
    yf_symbol = f"{hk_code.lstrip('0')}.HK"

    try:
        fin = ak.stock_financial_hk_analysis_indicator_em(symbol=hk_code, indicator="年度")
        fin = fin.sort_values("REPORT_DATE", ascending=False).head(10)
    except Exception as e:
        print(f"[akshare_client] 港股财务失败（用于估值计算）{code}: {e}")
        return pd.DataFrame(), {}

    price_df = fetch_price(code, "HK", period="10y", interval="1mo")

    rows = []
    for _, row in fin.iterrows():
        date_str = str(row.get("REPORT_DATE", ""))
        year = date_str[:4]
        eps = safe_float(row.get("BASIC_EPS"))
        bps_val = safe_float(row.get("BPS"))

        year_prices = (price_df[price_df["date"].dt.year == int(year)]
                       if not price_df.empty and year.isdigit() else pd.DataFrame())
        year_end_price = (float(year_prices.sort_values("date").iloc[-1]["close"])
                          if not year_prices.empty else None)

        pe = round(year_end_price / eps, 1) if (year_end_price and eps and eps > 0) else None
        pb = round(year_end_price / bps_val, 2) if (year_end_price and bps_val and bps_val > 0) else None
        rows.append({"year": year, "pe": pe, "pb": pb, "eps": eps, "bps": bps_val})

    val_df = pd.DataFrame(rows)

    def _percentile(metric: str, n: int = 5) -> Optional[dict]:
        if val_df.empty or metric not in val_df.columns:
            return None
        hist = val_df[metric].dropna().head(n)
        if len(hist) == 0:
            return None
        current = float(hist.iloc[0])
        pct = float((hist < current).sum() / len(hist) * 100)
        return {
            "current": round(current, 2),
            "percentile": round(pct, 1),
            "min": round(float(hist.min()), 2),
            "max": round(float(hist.max()), 2),
            "median": round(float(hist.median()), 2),
        }

    percentile = {"pe": _percentile("pe"), "pb": _percentile("pb")}
    return val_df, percentile


# ── 估值指标 ─────────────────────────────────────────────────

def fetch_valuation(code: str) -> pd.DataFrame:
    """
    A股估值指标：用同花顺财务摘要的每股收益 + yfinance 价格历史，计算历史 PE/PB。
    列：year, pe, pb（pb 从财务摘要中计算：年末价/每股净资产）
    """
    try:
        fin = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
        fin = fin.sort_values("报告期", ascending=False).head(10)
        price_df = fetch_price(code, "A", period="10y", interval="1mo")

        rows = []
        for _, row in fin.iterrows():
            year = str(int(row["报告期"]))
            eps = parse_pct(str(row.get("基本每股收益", "")).replace("元", "")) if "元" not in str(row.get("基本每股收益", "")) else safe_float(row.get("基本每股收益"))
            bps_val = safe_float(str(row.get("每股净资产", "")).replace("元", ""))

            # 年末价（12月份）
            year_prices = price_df[price_df["date"].dt.year == int(year)] if not price_df.empty else pd.DataFrame()
            year_end_price = float(year_prices.sort_values("date").iloc[-1]["close"]) if not year_prices.empty else None

            pe = round(year_end_price / eps, 1) if (year_end_price and eps and eps > 0) else None
            pb = round(year_end_price / bps_val, 2) if (year_end_price and bps_val and bps_val > 0) else None
            rows.append({"year": year, "pe": pe, "pb": pb, "eps": eps, "bps": bps_val})

        return pd.DataFrame(rows)
    except Exception as e:
        print(f"[akshare_client] 估值数据失败 {code}: {e}")
        return pd.DataFrame()


def get_valuation_percentile(code: str, metric: str = "pe",
                              years: int = 5) -> Optional[dict]:
    """
    计算当前 PE/PB 在历史 N 年中的分位数。
    返回: {"current": float, "percentile": float, "min": float, "max": float, "median": float}
    """
    df = fetch_valuation(code)
    if df.empty or metric not in df.columns:
        return None
    hist = df[metric].dropna().head(years)
    if len(hist) == 0:
        return None
    current = float(hist.iloc[0])
    pct = float((hist < current).sum() / len(hist) * 100)
    return {
        "current": round(current, 2),
        "percentile": round(pct, 1),
        "min": round(float(hist.min()), 2),
        "max": round(float(hist.max()), 2),
        "median": round(float(hist.median()), 2),
    }


def fetch_valuation_history(code: str, period: str = "近五年") -> dict:
    """
    从百度股市通获取个股 PE-TTM / PB 日频历史，用于过往业绩折线图。
    返回: {"pe_ttm": [{"date": "2021-01-01", "value": 56.7}, ...], "pb": [...]}
    """
    result: dict = {"pe_ttm": [], "pb": []}
    for indicator, key in [("市盈率(TTM)", "pe_ttm"), ("市净率", "pb")]:
        try:
            df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period=period)
            df = df.dropna()
            result[key] = df.rename(columns={"date": "date", "value": "value"}).to_dict(orient="records")
        except Exception as e:
            print(f"[akshare_client] 百度估值历史失败 {code} {indicator}: {e}")
    return result


# ── 股东结构 ─────────────────────────────────────────────────

def _latest_report_date() -> str:
    """返回最近一个季报日期（03-31/06-30/09-30/12-31），用于股东接口。"""
    today = datetime.now()
    quarters = [(3, 31), (6, 30), (9, 30), (12, 31)]
    for month, day in reversed(quarters):
        d = today.replace(month=month, day=day)
        if d <= today:
            return d.strftime("%Y%m%d")
    return (today.replace(year=today.year - 1, month=12, day=31)).strftime("%Y%m%d")


def fetch_shareholders(code: str) -> dict:
    """
    返回：
      holder_count — 股东人数历史（最近8期），含户数/增减/户均持股
      top10        — 前十大股东（含限售），含名称/持股数/占总股本/增减/变动率/股份类型
      top10_free   — 流通股前十大，含股东性质（证券投资基金/QFII/社保/其它）
      northbound   — 北向资金月度持仓（近12个月，每月最后一交易日），含持股数量/持股占比/当月净增持
    """
    result: dict = {
        "holder_count": [],
        "top10": [],
        "top10_free": [],
        "northbound": [],
    }
    market = detect_market(code)
    sym_prefixed = ("sh" if code.startswith("6") else "sz") + code

    # ── 1. 股东人数历史 ───────────────────────────────────────────
    try:
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        # 列：股东户数统计截止日 / 股东户数-本次 / 股东户数-上次 / 股东户数-增减 / 股东户数-增减比例 /
        #      户均持股市值 / 户均持股数量 / 总市值 / 总股本
        df = df.sort_values("股东户数统计截止日", ascending=False).head(8)
        result["holder_count"] = df.to_dict(orient="records")
    except Exception as e:
        print(f"[akshare_client] 股东人数失败 {code}: {e}")

    # ── 2. 前十大股东（含限售） ──────────────────────────────────
    report_date = _latest_report_date()
    for attempt_date in [report_date,
                         (datetime.strptime(report_date, "%Y%m%d") - pd.DateOffset(months=3)).strftime("%Y%m%d")]:
        try:
            df = ak.stock_gdfx_top_10_em(symbol=sym_prefixed, date=attempt_date)
            # 列：名次/股东名称/股份类型/持股数/占总股本持股比例/增减/变动比率
            result["top10"] = df.to_dict(orient="records")
            break
        except Exception as e:
            print(f"[akshare_client] 前十大股东失败 {code} date={attempt_date}: {e}")

    # ── 3. 流通股前十大（含股东性质） ───────────────────────────
    for attempt_date in [report_date,
                         (datetime.strptime(report_date, "%Y%m%d") - pd.DateOffset(months=3)).strftime("%Y%m%d")]:
        try:
            df = ak.stock_gdfx_free_top_10_em(symbol=sym_prefixed, date=attempt_date)
            # 列：名次/股东名称/股东性质/股份类型/持股数/占总流通股本持股比例/增减/变动比率
            result["top10_free"] = df.to_dict(orient="records")
            break
        except Exception as e:
            print(f"[akshare_client] 流通股前十大失败 {code} date={attempt_date}: {e}")

    # ── 4. 北向资金月度持仓（数据集最新日期往前12个月） ──────────
    if market == "A":
        try:
            df = ak.stock_hsgt_individual_em(symbol=code)
            if df.empty:
                result["northbound_note"] = "该股票无北向持仓记录（非沪深港通标的）"
            else:
                # 列：持股日期/当日收盘价/持股数量/持股市值/持股数量占A股百分比/今日增持股数/今日增持资金
                df["持股日期"] = pd.to_datetime(df["持股日期"])
                # 以数据集本身最新日期为基准，取近12个月（避免数据源滞后被全过滤）
                latest_date = df["持股日期"].max()
                cutoff = latest_date - pd.DateOffset(months=12)
                df = df[df["持股日期"] >= cutoff]
                result["northbound_data_date"] = str(latest_date.date())

                df["月份"] = df["持股日期"].dt.to_period("M")
                monthly = (df.sort_values("持股日期")
                             .groupby("月份", sort=True)
                             .last()
                             .reset_index())
                monthly["月份"] = monthly["月份"].astype(str)
                first_of_month = (df.sort_values("持股日期")
                                    .groupby("月份", sort=True)
                                    .first()
                                    .reset_index()[["月份", "持股数量"]]
                                    .rename(columns={"持股数量": "月初持股"}))
                first_of_month["月份"] = first_of_month["月份"].astype(str)
                monthly = monthly.merge(first_of_month, on="月份", how="left")
                monthly["当月净增持"] = monthly["持股数量"] - monthly["月初持股"]
                result["northbound"] = monthly[[
                    "月份", "当日收盘价", "持股数量", "持股市值",
                    "持股数量占A股百分比", "当月净增持"
                ]].to_dict(orient="records")
        except Exception as e:
            print(f"[akshare_client] 北向资金失败 {code}: {e}")
            result["northbound_note"] = f"北向资金数据获取失败: {e}"

    return result


# ── 行业数据 ─────────────────────────────────────────────────

def fetch_industry_fund_flow(industry_name: str) -> dict:
    """
    获取行业资金流向（同花顺），匹配行业名称返回净流入数据。
    ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    返回 {"今日": XX亿, "5日": XX亿, "10日": XX亿, "行业": name, "found": bool}
    """
    result: dict = {"行业": industry_name, "found": False}
    for indicator in ("今日", "5日", "10日"):
        result[indicator] = None
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df.empty:
            return result
        # 列名标准化
        cols = df.columns.tolist()
        name_col = next((c for c in cols if "名称" in c or "行业" in c), cols[0] if cols else None)
        if name_col is None:
            return result
        # 尝试按行业名称模糊匹配
        mask = df[name_col].str.contains(industry_name[:4], na=False)
        matched = df[mask]
        if matched.empty:
            # 全名匹配失败，返回全部排名作为参考
            return result
        row = matched.iloc[0]
        result["行业"] = str(row.get(name_col, industry_name))
        result["found"] = True
        # 今日净流入
        for field in cols:
            val = row.get(field)
            try:
                fval = float(val)
                if "今日" in field and "净" in field:
                    result["今日"] = round(fval / 1e8, 2)  # 转亿
                elif "5日" in field and "净" in field:
                    result["5日"] = round(fval / 1e8, 2)
                elif "10日" in field and "净" in field:
                    result["10日"] = round(fval / 1e8, 2)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print(f"[akshare_client] 行业资金流向获取失败 {industry_name}: {e}")
    return result


def fetch_industry_info(code: str) -> dict:
    """
    返回 {
        "industry": str,         # 所属行业
        "sector": str,           # 板块
        "peers": DataFrame,      # 同行业上市公司列表（含基本财务）
    }
    """
    result = {"industry": "", "sector": "", "peers": pd.DataFrame()}

    # 优先尝试东财个股信息（push2 API，在某些网络环境下不可用）
    try:
        info = ak.stock_individual_info_em(symbol=code)
        info_dict = info.set_index("item")["value"].to_dict()
        result["industry"] = info_dict.get("行业", "")
        result["sector"] = info_dict.get("板块", "")
    except Exception:
        pass

    # 降级：从东财业绩报表获取行业（更稳定，覆盖全市场）
    if not result["industry"]:
        try:
            yjbb = _fetch_yjbb_cached()
            row = yjbb[yjbb["股票代码"] == code]
            if not row.empty:
                result["industry"] = str(row.iloc[0].get("所处行业", ""))
        except Exception as e:
            print(f"[akshare_client] 行业降级获取失败 {code}: {e}")

    # 同行业公司：先试东财板块接口，降级用业绩报表过滤
    if result["industry"]:
        try:
            peers = ak.stock_board_industry_cons_em(symbol=result["industry"])
            result["peers"] = peers
        except Exception:
            # 降级：从 yjbb 里过滤同行业
            try:
                yjbb = _fetch_yjbb_cached()
                peers = yjbb[yjbb["所处行业"] == result["industry"]].copy()
                peers = peers.rename(columns={
                    "股票代码": "代码", "股票简称": "名称",
                    "每股收益": "EPS", "净资产收益率": "ROE",
                    "销售毛利率": "毛利率", "市盈率ttm": "市盈率",
                })
                result["peers"] = peers
            except Exception as e2:
                print(f"[akshare_client] 同行公司降级失败: {e2}")

    return result


# yjbb 全市场业绩报表（重量级，按日缓存）
_YJBB_CACHE: dict = {}

def _get_recent_report_date() -> str:
    """
    返回最近一个已充分披露的季报日期。
    A股披露截止日：Q1→4月30, Q2→8月31, Q3→10月31, Q4→次年4月30
    保守策略：每个季报截止日后 15 天才认为充分披露。
    """
    from datetime import date as _date
    today = _date.today()
    y, m, d = today.year, today.month, today.day

    if (m > 11) or (m == 11 and d >= 15):
        return f"{y}0930"        # Q3 已充分披露
    elif (m > 9) or (m == 9 and d >= 15):
        return f"{y}0630"        # Q2 已充分披露
    elif (m > 5) or (m == 5 and d >= 15):
        return f"{y}0331"        # Q1 已充分披露
    else:
        return f"{y - 1}0930"    # 去年 Q3（最保守的全量数据）

def _fetch_yjbb_cached() -> pd.DataFrame:
    date_key = _get_recent_report_date()
    if date_key in _YJBB_CACHE:
        return _YJBB_CACHE[date_key]
    print(f"[akshare_client] 拉取全市场业绩报表 {date_key}...")
    df = ak.stock_yjbb_em(date=date_key)
    _YJBB_CACHE[date_key] = df
    return df


# ── 新闻数据 ─────────────────────────────────────────────────

def fetch_news(code: str, limit: int = 50) -> list[dict]:
    """
    个股重大公告（重大事项 + 风险提示），作为催化剂分析的新闻来源。
    返回 list[dict]，每项含 title, type, date。
    """
    results = []
    for category in ("重大事项", "风险提示"):
        try:
            df = ak.stock_individual_notice_report(
                security=code, symbol=category
            )
            for _, row in df.iterrows():
                results.append({
                    "title": str(row.get("公告标题", "")),
                    "type":  str(row.get("公告类型", category)),
                    "date":  str(row.get("公告日期", ""))[:10],
                    "url":   str(row.get("网址", "")),
                })
        except Exception as e:
            print(f"[akshare_client] 公告拉取失败 {code} [{category}]: {e}")
    results.sort(key=lambda x: x["date"], reverse=True)
    return results[:limit]


def fetch_forecast(code: str, limit: int = 60) -> dict:
    """
    分析师逐机构预测（东财 reportapi，直接调用原始 API 以保留 researcher 字段）。

    返回 dict：
      reports      — 逐条研报 [{institution, researcher, date, rating, eps, net_profit}]
      ratings      — 近6月评级分布 {买入/增持/中性/减持/卖出: int}
      eps_range    — 各预测年度区间 {year: {min, avg, max, count}}
      profit_range — 各预测年度净利润区间（亿元）{year: {min, avg, max, count}}
      report_count — 总研报数
    """
    import json as _json
    from datetime import datetime as _dt

    result: dict = {
        "reports": [],
        "ratings": {"买入": 0, "增持": 0, "中性": 0, "减持": 0, "卖出": 0},
        "eps_range": {},
        "profit_range": {},
        "report_count": 0,
    }

    # ── 1. 逐机构研报明细（直调东财，保留 researcher 字段）──────
    try:
        import subprocess as _sp
        url = (
            f"https://reportapi.eastmoney.com/report/list"
            f"?code={code}&pageSize={limit}&pageNo=1&p=1"
            f"&beginTime=2024-01-01&endTime=2028-01-01"
            f"&rating=*&ratingChange=*&industryCode=*&industry=*&fields=&qType=0"
        )
        r = _sp.run(["curl", "-s", "--max-time", "10", url],
                    capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            raw = _json.loads(r.stdout)
            items = raw.get("data") or []
            result["report_count"] = raw.get("TotalRecord", len(items))
            cur_year = _dt.now().year
            year_keys = [
                ("predictThisYearEps",    str(cur_year)),
                ("predictNextYearEps",    str(cur_year + 1)),
                ("predictNextTwoYearEps", str(cur_year + 2)),
            ]
            seen: set[tuple] = set()
            for item in items:
                eps_by_year: dict[str, float] = {}
                for field, yr in year_keys:
                    v = item.get(field)
                    try:
                        eps_by_year[yr] = round(float(v), 2)
                    except Exception:
                        pass
                # 跳过无 EPS 数据的条目
                if not eps_by_year:
                    continue
                # 同一机构只保留最新一条（API 已按日期降序）
                key = item.get("orgSName", "")
                if key in seen:
                    continue
                seen.add(key)
                result["reports"].append({
                    "institution": item.get("orgSName", ""),
                    "researcher":  item.get("researcher", ""),
                    "date":        str(item.get("publishDate", ""))[:10],
                    "rating":      item.get("emRatingName", ""),
                    "title":       item.get("title", ""),
                    "eps":         eps_by_year,
                })
    except Exception as e:
        print(f"[akshare_client] 逐机构盈利预测失败 {code}: {e}")

    # ── 2. 评级分布 + 一致预期区间（同花顺）──────────────────────
    try:
        df_eps = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报每股收益")
        for _, row in df_eps.iterrows():
            yr = str(row.get("年度", ""))
            if not yr:
                continue
            result["eps_range"][yr] = {
                "min":   safe_float(row.get("最小值")),
                "avg":   safe_float(row.get("均值")),
                "max":   safe_float(row.get("最大值")),
                "count": int(row.get("预测机构数", 0) or 0),
            }
    except Exception as e:
        print(f"[akshare_client] 一致预期EPS失败 {code}: {e}")

    try:
        df_np = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报净利润")
        for _, row in df_np.iterrows():
            yr = str(row.get("年度", ""))
            if not yr:
                continue
            result["profit_range"][yr] = {
                "min":   safe_float(row.get("最小值")),
                "avg":   safe_float(row.get("均值")),
                "max":   safe_float(row.get("最大值")),
                "count": int(row.get("预测机构数", 0) or 0),
            }
    except Exception as e:
        print(f"[akshare_client] 一致预期净利润失败 {code}: {e}")

    # ── 3. 详细指标预测表（同花顺，含历史实际值+未来均值）──────────
    try:
        df_detail = ak.stock_profit_forecast_ths(symbol=code, indicator="业绩预测详表-详细指标预测")
        if not df_detail.empty:
            cols = df_detail.columns.tolist()
            rows_out = []
            for _, row in df_detail.iterrows():
                rows_out.append({c: str(row[c]) for c in cols})
            result["detail_table"] = {"columns": cols, "rows": rows_out}
    except Exception as e:
        print(f"[akshare_client] 详细指标预测失败 {code}: {e}")

    # ── 4. 评级分布（按机构数，去重后每机构只计一次）──────────────
    from datetime import date as _date, timedelta as _td
    cutoff = (_date.today() - _td(days=180)).isoformat()
    rating_map = {"买入": 0, "增持": 0, "中性": 0, "减持": 0, "卖出": 0}
    for rep in result["reports"]:
        if rep["date"] >= cutoff and rep["rating"] in rating_map:
            rating_map[rep["rating"]] += 1
    result["ratings"] = rating_map
    # institution_count：近6月有 EPS 预测的机构数（用于 LLM 展示）
    result["institution_count"] = sum(rating_map.values())

    return result


# ── 主营构成（公司概况用）───────────────────────────────────

def _ak_market_prefix(code: str) -> str:
    """返回东财接口所需的市场前缀：SH / SZ"""
    if code.startswith("6"):
        return f"SH{code}"
    return f"SZ{code}"


def _latest_annual_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    从 stock_zygc_em 返回的全量历史数据中，提取最新年报（12月31日）的行。
    回退策略：无年报则取最新报告期。
    """
    date_col = next((c for c in df.columns if "报告日期" in c or "日期" in c), None)
    if date_col is None:
        return df

    dates = df[date_col].astype(str)
    # 优先：最新一期年报（年末 12-31 或 1231）
    annual_mask = dates.str.endswith("12-31") | dates.str.endswith("1231")
    annual = df[annual_mask]
    if not annual.empty:
        latest = annual[date_col].max()
        return annual[annual[date_col] == latest].copy()
    # 回退：最新报告期
    latest = df[date_col].max()
    return df[df[date_col] == latest].copy()


def _normalize_pct(v) -> Optional[float]:
    """把 0.867695（小数）或 86.77（百分比）→ 86.77"""
    try:
        f = float(str(v).replace("%", "").strip())
        return round(f * 100, 2) if abs(f) <= 1 else round(f, 2)
    except Exception:
        return None


def _normalize_yi(v) -> Optional[float]:
    """把 146499906480.49（元）或 14.65（亿）→ 亿"""
    try:
        f = float(str(v).replace(",", "").replace("亿", "").strip())
        return round(f / 1e8, 2) if abs(f) > 1e6 else round(f, 2)
    except Exception:
        return None


def fetch_business_overview(code: str) -> dict:
    """
    公司概况数据（仅取最新年报）：
      revenue_breakdown — 主营构成（产品维度，含毛利率，pct 转为百分比）
      revenue_region    — 主营构成（地区维度）
      business_intro    — 主营介绍（同花顺）
    """
    result: dict = {
        "revenue_breakdown": [],
        "revenue_region": [],
        "business_intro": {},
    }

    # 1) 主营构成 — 东财 stock_zygc_em（SH/SZ前缀）
    ak_symbol = _ak_market_prefix(code)
    try:
        df = ak.stock_zygc_em(symbol=ak_symbol)
        if not df.empty:
            type_col = next((c for c in df.columns if "分类类型" in c or "类型" in c), None)
            if type_col:
                prod_df   = _latest_annual_rows(
                    df[df[type_col].str.contains("产品|业务|品种", na=False)]
                )
                region_df = _latest_annual_rows(
                    df[df[type_col].str.contains("地区|区域", na=False)]
                )
            else:
                prod_df   = _latest_annual_rows(df)
                region_df = pd.DataFrame()

            def _df_to_list(d: pd.DataFrame) -> list[dict]:
                """转换为 list[dict]，数值字段归一化（pct→%, revenue→亿, gm→%）"""
                out = []
                for _, row in d.iterrows():
                    entry: dict = {}
                    for col in d.columns:
                        val = row.get(col)
                        v_str = str(val) if val is not None else ""
                        if v_str in ("nan", "None", ""):
                            continue
                        # 归一化关键数值字段
                        if "比例" in col or col == "收入比例" or col == "成本比例" or col == "利润比例":
                            entry[col] = _normalize_pct(val)
                        elif "毛利率" in col:
                            entry[col] = _normalize_pct(val)
                        elif "收入" in col and "比例" not in col:
                            entry[col] = _normalize_yi(val)
                        elif "成本" in col and "比例" not in col:
                            entry[col] = _normalize_yi(val)
                        elif "利润" in col and "比例" not in col:
                            entry[col] = _normalize_yi(val)
                        else:
                            entry[col] = v_str
                    if entry:
                        out.append(entry)
                return out

            result["revenue_breakdown"] = _df_to_list(prod_df)
            result["revenue_region"]    = _df_to_list(region_df)
    except Exception as e:
        print(f"[akshare_client] 主营构成失败 {code}: {e}")

    # 2) 主营介绍 — 同花顺 stock_zyjs_ths（6位代码）
    try:
        df2 = ak.stock_zyjs_ths(symbol=code)
        if not df2.empty:
            row = df2.iloc[0]
            result["business_intro"] = {
                "主营业务": str(row.get("主营业务", "") or ""),
                "产品类型": str(row.get("产品类型", "") or ""),
                "产品名称": str(row.get("产品名称", "") or ""),
                "经营范围": str(row.get("经营范围", "") or ""),
            }
    except Exception as e:
        print(f"[akshare_client] 主营介绍失败 {code}: {e}")

    return result


# ── 全市场快照（选股器用）────────────────────────────────────

def fetch_screener_snapshot() -> pd.DataFrame:
    """
    全 A 股基本面快照。
    优先：东财实时行情（含PE/PB/换手率）；降级：yjbb 业绩报表（含ROE/毛利率/营收增速）。
    """
    # 优先：东财实时行情
    try:
        df = ak.stock_zh_a_spot_em()
        rename_map = {
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "成交量": "volume",
            "市盈率-动态": "pe_ttm", "市净率": "pb",
            "总市值": "total_mv", "流通市值": "float_mv",
            "换手率": "turnover_rate", "60日涨跌幅": "change_60d",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df
    except Exception as e:
        print(f"[akshare_client] 全市场快照失败，降级用业绩报表: {e}")

    # 降级：yjbb 业绩报表
    try:
        df = _fetch_yjbb_cached()
        df = df.rename(columns={
            "股票代码": "code", "股票简称": "name",
            "净资产收益率": "roe", "销售毛利率": "gross_margin",
            "营业总收入-同比增长": "revenue_yoy", "净利润-同比增长": "profit_yoy",
            "所处行业": "industry",
        })
        return df
    except Exception as e2:
        print(f"[akshare_client] 业绩报表快照失败: {e2}")
        return pd.DataFrame()


# ── 股票基本信息 ─────────────────────────────────────────────

def fetch_stock_info(code: str) -> dict:
    """返回股票名称、行业、上市日期等基本信息（东财接口，失败时多级降级）"""
    # 优先：东财个股信息（最完整）
    try:
        info = ak.stock_individual_info_em(symbol=code)
        return info.set_index("item")["value"].to_dict()
    except Exception as e:
        print(f"[akshare_client] 基本信息失败 {code}: {e}，尝试降级")

    # 降级1：从全市场代码名称表获取中文名 + yjbb 获取行业
    result: dict = {"股票简称": code}
    try:
        name_df = ak.stock_info_a_code_name()
        row = name_df[name_df["code"] == code]
        if not row.empty:
            result["股票简称"] = str(row.iloc[0]["name"])
    except Exception:
        pass

    try:
        yjbb = _fetch_yjbb_cached()
        row = yjbb[yjbb["股票代码"] == code]
        if not row.empty:
            r = row.iloc[0]
            result["行业"] = str(r.get("所处行业", ""))
            result["股票简称"] = result.get("股票简称") or str(r.get("股票简称", code))
    except Exception:
        pass

    # 降级2：yfinance（英文信息补充）
    if not result.get("行业"):
        try:
            yf_suffix = ".SS" if code.startswith("6") else ".SZ"
            t = yf.Ticker(f"{code}{yf_suffix}")
            info = t.info
            result.setdefault("股票简称", info.get("shortName", code))
            result["总市值"] = info.get("marketCap", "")
        except Exception:
            pass

    return result


# ── 公告数据 ─────────────────────────────────────────────────

def fetch_notices(code: str, days: int = 180) -> list[dict]:
    """
    获取个股公告（东财），返回列表，每条含 title/type/date/url。
    """
    try:
        from datetime import timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_individual_notice_report(
            security=code, symbol="全部", begin_date=start, end_date=end
        )
        df = df.rename(columns={
            "公告标题": "title", "公告类型": "type", "公告日期": "date", "网址": "url"
        })
        return df[["title", "type", "date", "url"]].to_dict(orient="records")
    except Exception as e:
        print(f"[akshare_client] 公告获取失败 {code}: {e}")
        return []


def fetch_board_members(code: str) -> list[dict]:
    """
    获取董事会/高管名单（东财 stock_board_member_em）。
    返回 list of dict，含姓名/职务/持股数量/年薪等字段。
    """
    try:
        df = ak.stock_board_member_em(symbol=code)
        if df.empty:
            return []
        return df.head(20).to_dict(orient="records")
    except Exception as e:
        print(f"[akshare_client] 董事会成员获取失败 {code}: {e}")
        return []


def fetch_pledge_ratio(code: str) -> list[dict]:
    """
    获取个股质押比例明细（东财），返回 list of dict。
    """
    try:
        df = ak.stock_gpzy_individual_pledge_ratio_detail_em(symbol=code)
        return df.head(20).to_dict(orient="records")
    except Exception as e:
        print(f"[akshare_client] 质押比例获取失败 {code}: {e}")
        return []


def fetch_shareholder_change(code: str, years: int = 2) -> list[dict]:
    """
    获取大股东增减持记录（同花顺）。
    - 按日期降序排列，只保留近 `years` 年内的记录
    - 无近期数据时返回空列表（由调用方显示"近N年内无增减持记录"）
    """
    try:
        df = ak.stock_shareholder_change_ths(symbol=code)
        if df.empty:
            return []

        # 打印实际列名，便于调试字段映射
        print(f"[akshare_client] shareholder_change 实际列名: {df.columns.tolist()}")

        # 统一列名：把各种可能的日期列名归一为 _date
        date_col = next(
            (c for c in df.columns if "日期" in c or "date" in c.lower()),
            None,
        )
        if date_col:
            df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.sort_values("_date", ascending=False)
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=years)
            df = df[df["_date"] >= cutoff]
        else:
            # 没有日期列时仍按原始顺序取前30条（兜底）
            df = df.head(30)

        return df.head(30).to_dict(orient="records")
    except Exception as e:
        print(f"[akshare_client] 增减持记录获取失败 {code}: {e}")
        return []


def fetch_concepts(code: str, max_boards: int = 200) -> list[str]:
    """
    获取个股所属概念板块（东财），反向查找策略，线程并行，30s 超时。
    返回概念名列表，最多 6 个。

    使用 subprocess curl 而非 Python requests：TUN 模式代理的自签 CA 在
    系统证书库中，但 Python urllib3 在 yfinance 初始化后与代理产生连接冲突。
    curl 通过系统 TLS 栈直接访问，稳定可靠。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import json as _json, math, subprocess, time

    # 东财概念板块：79.push2（列表）、29.push2（成分股）
    # Python requests 在 yfinance 初始化后与代理的 TLS 交互失败；
    # subprocess curl 继承 HTTPS_PROXY 环境变量，走代理路由，稳定可靠。
    _LIST_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
    _CONS_URL = "https://29.push2.eastmoney.com/api/qt/clist/get"
    _UT       = "bd1d9ddb04089700cf9c27f6f7426281"

    def _curl_get(url_base: str, params: dict) -> dict | None:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url_base}?{qs}"
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "10", url],
                capture_output=True, text=True,
            )
            if r.returncode != 0 or not r.stdout:
                return None
            return _json.loads(r.stdout)
        except Exception:
            return None

    # ── Step 1：获取所有概念板块（名称 + 代码）───────────────
    def _fetch_boards() -> list[dict]:
        params = {"pn": "1", "pz": "100", "po": "1", "np": "1",
                  "ut": _UT, "fltt": "2", "invt": "2", "fid": "f12",
                  "fs": "m:90+t:3+f:!50", "fields": "f12,f14"}
        boards = []
        try:
            resp = _curl_get(_LIST_URL, params)
            if not resp:
                return []
            data = resp["data"]
            total = data["total"]
            boards += [{"name": d["f14"], "code": d["f12"]} for d in data["diff"].values()]
            total_pages = math.ceil(total / 100)
            for pn in range(2, min(total_pages + 1, max_boards // 100 + 2)):
                params["pn"] = str(pn)
                d2 = _curl_get(_LIST_URL, params)
                if d2:
                    boards += [{"name": d["f14"], "code": d["f12"]} for d in d2["data"]["diff"].values()]
        except Exception as e:
            print(f"[akshare_client] 概念板块列表获取失败: {e}")
        return boards[:max_boards]

    # ── Step 2：查某个概念的成分股，返回股票代码集合 ──────────
    def _cons_codes(board_code: str) -> set[str]:
        params = {"pn": "1", "pz": "200", "po": "1", "np": "1",
                  "ut": _UT, "fltt": "2", "invt": "2", "fid": "f12",
                  "fs": f"b:{board_code}+f:!50", "fields": "f12"}
        try:
            resp = _curl_get(_CONS_URL, params)
            if not resp:
                return set()
            diff = resp["data"]["diff"]
            if isinstance(diff, dict):
                return {str(d["f12"]) for d in diff.values()}
            return {str(d["f12"]) for d in diff}
        except Exception:
            return set()

    boards = _fetch_boards()
    if not boards:
        return []

    # ── Step 3：并行反向查找 ──────────────────────────────────
    found: list[str] = []
    deadline = time.time() + 30.0

    def _check(board: dict) -> str | None:
        codes = _cons_codes(board["code"])
        if code in codes:
            return board["name"]
        return None

    with ThreadPoolExecutor(max_workers=5) as exc:
        futures = {exc.submit(_check, b): b for b in boards}
        for fut in as_completed(futures):
            if time.time() > deadline:
                break
            if res := fut.result():
                found.append(res)
                if len(found) >= 6:
                    break

    print(f"[akshare_client] 概念板块 {code}: {found}")
    return found


# ── 汇总入口 ─────────────────────────────────────────────────

def fetch_all(code: str, data_types: list[str] | None = None) -> dict:
    """
    统一拉取接口，data_types 为 None 时拉取全部。
    返回 dict，key 对应 data_types 中的值。
    """
    code = code.strip()
    market = detect_market(code)
    all_types = ["info", "price", "financials", "valuation", "shareholders", "industry",
                 "industry_fund_flow", "news",
                 "notices", "pledge", "shareholder_change", "board_members",
                 "forecast", "business_overview", "concepts"]
    types = data_types if data_types else all_types

    result: dict = {"code": code, "market": market}

    if "info" in types:
        if market == "A":
            result["info"] = fetch_stock_info(code)
        else:
            result["info"] = fetch_hk_stock_info(code)

    if "price" in types:
        result["price"] = fetch_price(code, market)

    if "financials" in types:
        result["financials"] = (fetch_a_financials(code) if market == "A"
                                else fetch_hk_financials(code))

    if "valuation" in types:
        if market == "A":
            result["valuation"] = fetch_valuation(code)
            result["valuation_percentile"] = {
                m: get_valuation_percentile(code, m)
                for m in ["pe", "pb"]
            }
            result["valuation_history"] = fetch_valuation_history(code)
        else:
            val_df, val_pct = fetch_hk_valuation(code)
            result["valuation"] = val_df.to_dict(orient="records") if not val_df.empty else []
            result["valuation_percentile"] = val_pct
            result["valuation_history"] = {}

    if "shareholders" in types and market == "A":
        result["shareholders"] = fetch_shareholders(code)

    if "industry" in types and market == "A":
        result["industry"] = fetch_industry_info(code)
        # industry_fund_flow depends on industry_name, fetch immediately after
        if "industry_fund_flow" in types:
            industry_name = result["industry"].get("industry", "") if isinstance(result.get("industry"), dict) else ""
            if industry_name:
                result["industry_fund_flow"] = fetch_industry_fund_flow(industry_name)
            else:
                result["industry_fund_flow"] = {"found": False}

    if "news" in types:
        result["news"] = fetch_news(code) if market == "A" else fetch_hk_news(code)

    if "forecast" in types and market == "A":
        result["forecast"] = fetch_forecast(code)

    if "notices" in types and market == "A":
        result["notices"] = fetch_notices(code)

    if "pledge" in types and market == "A":
        result["pledge"] = fetch_pledge_ratio(code)

    if "shareholder_change" in types and market == "A":
        result["shareholder_change"] = fetch_shareholder_change(code)

    if "board_members" in types and market == "A":
        result["board_members"] = fetch_board_members(code)

    if "business_overview" in types:
        result["business_overview"] = (
            fetch_business_overview(code) if market == "A"
            else fetch_hk_business_overview(code)
        )

    if "concepts" in types and market == "A":
        result["concepts"] = fetch_concepts(code)

    return result


if __name__ == "__main__":
    print("=== 测试：贵州茅台 600519 ===")
    data = fetch_all("600519", data_types=["info", "price", "valuation"])
    print(f"市场: {data['market']}")
    print(f"基本信息: {data.get('info', {})}")
    print(f"价格数据: {len(data.get('price', []))} 条")
    if "valuation_percentile" in data:
        print(f"PE 分位: {data['valuation_percentile']['pe']}")
