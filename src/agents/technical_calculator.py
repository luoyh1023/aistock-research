"""
技术面纯计算模块。
计算 RSI / MACD / 布林带 / KDJ / MA / 成交量指标，输出技术评分和关键支撑压力位。
不调用 LLM，不产生网络请求。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np


# ── 数据结构 ─────────────────────────────────────────────────

@dataclass
class IndicatorResult:
    value: Optional[float]
    signal: str          # 信号描述（中性 / 看多 / 看空）
    score: float         # -2 强看空 → +2 强看多


@dataclass
class TechnicalBundle:
    stock_code: str
    technical_score: int          # 0–100
    signal: str                   # 看多 / 中性 / 看空
    indicators: dict[str, dict]   # 各指标 value + signal
    key_levels: dict              # support / resistance
    patterns_raw: list[dict]      # 代码检测到的候选形态（供 LLM 解读）
    price_series: dict            # 近期 OHLCV 摘要（供 LLM 上下文）


# ── 指标计算 ─────────────────────────────────────────────────

def _safe(val) -> Optional[float]:
    """将 NaN/None/Series 转成 float 或 None。"""
    if val is None:
        return None
    if isinstance(val, pd.Series):
        val = val.iloc[-1] if not val.empty else None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _last(series: pd.Series) -> Optional[float]:
    s = series.dropna()
    return _safe(s.iloc[-1]) if not s.empty else None


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=length - 1, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(com=length - 1, min_periods=length).mean()
    rsi = pd.Series(np.where(loss == 0, 100.0, 100 - 100 / (1 + gain / loss.where(loss != 0, np.nan))), index=close.index)
    return rsi


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, min_periods=signal).mean()
    hist = dif - dea
    return dif, dea, hist


def _bbands(close: pd.Series, length=20, std=2):
    mid = close.rolling(length).mean()
    sigma = close.rolling(length).std(ddof=0)
    upper = mid + std * sigma
    lower = mid - std * sigma
    return upper, mid, lower


def _stoch_kdj(high: pd.Series, low: pd.Series, close: pd.Series, k=9, d=3, smooth_k=3) -> tuple:
    low_min  = low.rolling(k).min()
    high_max = high.rolling(k).max()
    denom = (high_max - low_min).replace(0, np.nan)
    rsv = (close - low_min) / denom * 100
    K = rsv.ewm(com=smooth_k - 1, min_periods=1).mean()
    D = K.ewm(com=d - 1, min_periods=1).mean()
    return K, D


def _sma(close: pd.Series, length: int) -> pd.Series:
    return close.rolling(length).mean()


def calc_rsi(close: pd.Series, length: int = 14) -> IndicatorResult:
    rsi_s = _rsi(close, length=length)
    val = _last(rsi_s)
    if val is None:
        return IndicatorResult(None, "数据不足", 0.0)
    if val >= 70:
        return IndicatorResult(val, "超买，注意回调风险", -1.5)
    if val >= 60:
        return IndicatorResult(val, "偏强，趋势向上", 1.0)
    if val >= 40:
        return IndicatorResult(val, "中性区间", 0.0)
    if val >= 30:
        return IndicatorResult(val, "偏弱，关注企稳", -1.0)
    return IndicatorResult(val, "超卖，可能反弹", 1.5)


def calc_macd(close: pd.Series) -> IndicatorResult:
    if len(close.dropna()) < 27:
        return IndicatorResult(None, "数据不足", 0.0)

    dif_s, dea_s, hist_s = _macd(close)
    dif  = _last(dif_s)
    dea  = _last(dea_s)
    hist = _last(hist_s)
    extra = {"dif": dif, "dea": dea, "histogram": hist}

    if dif is None or dea is None or hist is None:
        return IndicatorResult(None, "数据不足", 0.0)

    hist_prev = _safe(hist_s.dropna().iloc[-2]) if len(hist_s.dropna()) > 1 else None

    if dif > dea and hist > 0:
        if hist_prev is not None and hist_prev < 0:
            signal, score = "金叉刚形成，趋势转强", 2.0
        else:
            signal, score = "MACD 金叉，趋势向上", 1.5
    elif dif < dea and hist < 0:
        signal, score = "MACD 死叉，趋势向下", -1.5
    elif dif > 0 and dea > 0:
        signal, score = "零轴上方运行，多头", 1.0
    elif dif < 0 and dea < 0:
        signal, score = "零轴下方运行，空头", -1.0
    else:
        signal, score = "MACD 中性整理", 0.0

    result = IndicatorResult(hist, signal, score)
    result.__dict__.update(extra)
    return result


def calc_boll(close: pd.Series, length: int = 20) -> IndicatorResult:
    if len(close.dropna()) < length:
        return IndicatorResult(None, "数据不足", 0.0)

    upper_s, mid_s, lower_s = _bbands(close, length=length)
    upper = _last(upper_s)
    mid   = _last(mid_s)
    lower = _last(lower_s)
    price = _last(close)

    extra = {"upper": upper, "mid": mid, "lower": lower}

    if None in (upper, mid, lower, price):
        return IndicatorResult(None, "数据不足", 0.0)

    band_width = upper - lower
    if band_width == 0:
        return IndicatorResult(price, "布林带收窄", 0.0)

    pos = (price - lower) / band_width  # 0=下轨, 1=上轨

    if price >= upper:
        signal, score = "触及上轨，注意回调", -1.0
    elif price >= mid:
        signal, score = f"价格在中轨上方运行（位置 {pos:.0%}）", 1.0
    elif price >= lower:
        signal, score = f"价格在中轨下方运行（位置 {pos:.0%}）", -0.5
    else:
        signal, score = "触及下轨，可能反弹", 1.0

    result = IndicatorResult(price, signal, score)
    result.__dict__.update(extra)
    return result


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series) -> IndicatorResult:
    if len(close.dropna()) < 9:
        return IndicatorResult(None, "数据不足", 0.0)

    K_s, D_s = _stoch_kdj(high, low, close)
    k_val = _last(K_s)
    d_val = _last(D_s)

    extra = {"k": k_val, "d": d_val}

    if k_val is None or d_val is None:
        return IndicatorResult(None, "数据不足", 0.0)

    j_val = round(3 * k_val - 2 * d_val, 2)
    extra["j"] = j_val

    if k_val >= 80:
        signal, score = "超买区，注意顶部风险", -1.5
    elif k_val > d_val and k_val < 80:
        signal, score = "KDJ 多头排列，向上", 1.0
    elif k_val < d_val and k_val > 20:
        signal, score = "KDJ 空头排列，向下", -1.0
    elif k_val <= 20:
        signal, score = "超卖区，可能反弹", 1.5
    else:
        signal, score = "KDJ 中性", 0.0

    result = IndicatorResult(k_val, signal, score)
    result.__dict__.update(extra)
    return result


def calc_ma(close: pd.Series) -> IndicatorResult:
    lengths = [5, 10, 20, 60, 120, 250]
    mas = {}
    for n in lengths:
        if len(close) >= n:
            mas[f"MA{n}"] = _last(_sma(close, n))

    price = _last(close)
    if not mas or price is None:
        return IndicatorResult(None, "数据不足", 0.0)

    above = sum(1 for v in mas.values() if v and price > v)
    total = sum(1 for v in mas.values() if v)

    if total == 0:
        return IndicatorResult(price, "MA 数据不足", 0.0)

    ratio = above / total
    if ratio >= 0.8:
        signal, score = "均线多头排列，趋势强", 2.0
    elif ratio >= 0.6:
        signal, score = "价格在多条均线上方，偏多", 1.0
    elif ratio >= 0.4:
        signal, score = "均线交叉整理，中性", 0.0
    elif ratio >= 0.2:
        signal, score = "价格在多条均线下方，偏空", -1.0
    else:
        signal, score = "均线空头排列，趋势弱", -2.0

    result = IndicatorResult(price, signal, score)
    result.__dict__.update(mas)
    return result


def calc_volume(volume: pd.Series, close: pd.Series, window: int = 20) -> IndicatorResult:
    if len(volume) < window + 1:
        return IndicatorResult(None, "数据不足", 0.0)

    avg_vol = float(volume.iloc[-window-1:-1].mean())
    cur_vol = float(volume.iloc[-1])
    cur_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else cur_close

    ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
    up = cur_close >= prev_close

    if ratio >= 2.0 and up:
        signal, score = f"放量上涨（{ratio:.1f}x 均量），强势信号", 2.0
    elif ratio >= 1.5 and up:
        signal, score = f"温和放量上涨（{ratio:.1f}x）", 1.0
    elif ratio >= 2.0 and not up:
        signal, score = f"放量下跌（{ratio:.1f}x），注意出货", -2.0
    elif ratio < 0.5:
        signal, score = f"缩量（{ratio:.1f}x），观望情绪浓", 0.0
    else:
        signal, score = f"成交量正常（{ratio:.1f}x 均量）", 0.0

    return IndicatorResult(round(ratio, 2), signal, score)


# ── 支撑 / 压力位 ─────────────────────────────────────────────

def find_key_levels(df: pd.DataFrame, lookback: int = 60) -> dict:
    """基于近 N 日高低点聚类，找支撑和压力。"""
    recent = df.tail(lookback)
    price = float(df["close"].iloc[-1])

    highs = sorted(recent["high"].nlargest(5).tolist(), reverse=True)
    lows  = sorted(recent["low"].nsmallest(5).tolist())

    resistance = [round(h, 2) for h in highs if h > price][:2]
    support    = [round(l, 2) for l in lows  if l < price][:2]

    return {"support": support, "resistance": resistance}


# ── 形态识别（代码检测候选，LLM 解读） ───────────────────────

def detect_patterns_raw(df: pd.DataFrame) -> list[dict]:
    """
    代码识别候选形态，返回结构化描述供 LLM 解读。
    """
    patterns = []
    close = df["close"]
    volume = df["volume"]
    recent = df.tail(20)
    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else price

    # 均线金叉：MA5 上穿 MA20
    if len(close) >= 22:
        ma5_now  = float(_sma(close, 5).iloc[-1])
        ma20_now = float(_sma(close, 20).iloc[-1])
        ma5_prev  = float(_sma(close, 5).iloc[-2])
        ma20_prev = float(_sma(close, 20).iloc[-2])
        if ma5_prev <= ma20_prev and ma5_now > ma20_now:
            patterns.append({"pattern": "均线金叉（MA5上穿MA20）", "days_ago": 0, "type": "bullish"})
        elif ma5_prev >= ma20_prev and ma5_now < ma20_now:
            patterns.append({"pattern": "均线死叉（MA5下穿MA20）", "days_ago": 0, "type": "bearish"})

    # 放量突破：价格创 20 日新高 + 成交量 > 1.5x 均量
    if len(close) >= 20 and len(volume) >= 20:
        high20 = float(recent["high"].max())
        avg_vol = float(volume.iloc[-21:-1].mean())
        cur_vol = float(volume.iloc[-1])
        if price >= high20 and cur_vol >= 1.5 * avg_vol:
            patterns.append({"pattern": "放量突破20日高点", "days_ago": 0, "type": "bullish"})

    # 双底（近60日）：两个相近低点
    if len(close) >= 60:
        tail60 = df.tail(60)
        lows = tail60["low"]
        min1 = float(lows.nsmallest(1).iloc[0])
        min2 = float(lows[lows > min1 * 1.001].nsmallest(1).iloc[0]) if not lows[lows > min1 * 1.001].empty else None
        if min2 and abs(min1 - min2) / min1 < 0.03 and price > min1 * 1.05:
            patterns.append({"pattern": "潜在双底形态", "days_ago": None, "type": "bullish"})

    # 高位滞涨：近10日价格在高点附近但未突破
    if len(close) >= 30:
        high30 = float(df.tail(30)["high"].max())
        if price >= high30 * 0.97 and price < high30 and float(volume.iloc[-1]) < float(volume.iloc[-21:-1].mean()):
            patterns.append({"pattern": "高位缩量，注意压力", "days_ago": None, "type": "neutral"})

    return patterns


# ── 综合评分 ─────────────────────────────────────────────────

_WEIGHTS = {
    "macd":   0.25,
    "ma":     0.25,
    "rsi":    0.20,
    "kdj":    0.15,
    "volume": 0.15,
}


def compute_technical_score(scores: dict[str, float]) -> int:
    """
    加权平均各指标分数（-2~+2），映射到 0–100。
    """
    weighted = sum(_WEIGHTS.get(k, 0) * v for k, v in scores.items())
    total_w = sum(_WEIGHTS.get(k, 0) for k in scores)
    if total_w == 0:
        return 50
    normalized = weighted / total_w  # -2 ~ +2
    score_0_100 = round((normalized + 2) / 4 * 100)
    return max(0, min(100, score_0_100))


def _signal_label(score: int) -> str:
    if score >= 65:
        return "看多"
    if score >= 55:
        return "偏多"
    if score >= 45:
        return "中性"
    if score >= 35:
        return "偏空"
    return "看空"


# ── 主入口 ───────────────────────────────────────────────────

def calculate(price_data: list[dict], stock_code: str = "") -> TechnicalBundle:
    """
    接收 DataFetcherAgent 的 price 数据（records），返回 TechnicalBundle。
    """
    if not price_data:
        raise ValueError("price_data 为空，无法计算技术指标")

    df = pd.DataFrame(price_data)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["close"])
    df = df.tail(250)  # 最多用近 250 日

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    rsi    = calc_rsi(close)
    macd   = calc_macd(close)
    boll   = calc_boll(close)
    kdj    = calc_kdj(high, low, close)
    ma     = calc_ma(close)
    vol    = calc_volume(volume, close)

    raw_scores = {
        "macd":   macd.score,
        "ma":     ma.score,
        "rsi":    rsi.score,
        "kdj":    kdj.score,
        "volume": vol.score,
    }
    tech_score = compute_technical_score(raw_scores)
    signal = _signal_label(tech_score)

    def _to_dict(ind: IndicatorResult) -> dict:
        d = {"value": ind.value, "signal": ind.signal, "score": ind.score}
        for k, v in ind.__dict__.items():
            if k not in d:
                d[k] = v
        return d

    indicators = {
        f"RSI_14":     _to_dict(rsi),
        "MACD":        _to_dict(macd),
        "BOLL":        _to_dict(boll),
        "KDJ":         _to_dict(kdj),
        "MA":          _to_dict(ma),
        "VOLUME":      _to_dict(vol),
    }

    key_levels   = find_key_levels(df)
    patterns_raw = detect_patterns_raw(df)

    # 近30日 OHLCV 摘要（供 LLM prompt）
    recent30 = df.tail(30)[["date", "open", "high", "low", "close", "volume"]].copy()
    recent30["date"] = recent30["date"].dt.strftime("%Y-%m-%d")
    price_series = recent30.to_dict(orient="records")

    return TechnicalBundle(
        stock_code=stock_code,
        technical_score=tech_score,
        signal=signal,
        indicators=indicators,
        key_levels=key_levels,
        patterns_raw=patterns_raw,
        price_series=price_series,
    )
