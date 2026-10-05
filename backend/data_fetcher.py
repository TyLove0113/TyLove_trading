"""
數據層 v2
修正原版三個問題：
  1. 只有日線 → 加入 15 分鐘 / 60 分鐘 K 線，短炒先睇得到今日走勢
  2. change_pct 用「昨收 vs 今收」→ 改用「即時價 vs 昨收」
  3. 無成交額過濾 → 提供 20 日平均成交額，做流動性篩查
"""
import logging
import time

import pandas as pd
import yfinance as yf

log = logging.getLogger("tylove.data")

_CACHE: dict = {}
_CACHE_TTL = 60  # 秒


def _cache_get(key):
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > _CACHE_TTL:
        return None
    return value


def _cache_set(key, value):
    _CACHE[key] = (time.time(), value)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns={"Stock Splits": "Splits"})
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].dropna(subset=["Close"])
    df = df[df["Volume"].fillna(0) >= 0]
    return df


def fetch(symbol: str, interval: str = "1d", period: str = "2y",
          retries: int = 3, use_cache: bool = True) -> pd.DataFrame:
    """拉 K 線。失敗會重試，唔會 raise，回傳空 DataFrame。"""
    key = f"{symbol}|{interval}|{period}"
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    last_err = None
    for attempt in range(retries):
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval,
                                           auto_adjust=False, raise_errors=False)
            df = _clean(df)
            if len(df) > 0:
                if use_cache:
                    _cache_set(key, df)
                return df
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(1.0 + attempt)

    log.warning("拉取 %s (%s) 失敗: %s", symbol, interval, last_err)
    return pd.DataFrame()


def fetch_daily(symbol: str, period: str = "2y") -> pd.DataFrame:
    return fetch(symbol, "1d", period)


def fetch_60m(symbol: str, period: str = "3mo") -> pd.DataFrame:
    return fetch(symbol, "60m", period)


def fetch_15m(symbol: str, period: str = "1mo") -> pd.DataFrame:
    return fetch(symbol, "15m", period)


def live_price(symbol: str) -> float | None:
    """即時價（盡量拉 1 分鐘 K 線最後一支；失敗就退回 15 分鐘／日線）。"""
    for interval, period in (("1m", "1d"), ("5m", "1d"), ("15m", "5d"), ("1d", "5d")):
        df = fetch(symbol, interval, period, retries=1)
        if len(df) > 0:
            return float(df["Close"].iloc[-1])
    return None


def fetch_benchmark(period: str = "2y") -> pd.DataFrame:
    return fetch_daily("^HSI", period)


def turnover_20d(df: pd.DataFrame) -> float:
    """20 日平均成交額（HKD）。"""
    if len(df) < 5:
        return 0.0
    to = (df["Close"] * df["Volume"]).tail(20)
    return float(to.mean())


def relative_strength(stock_df: pd.DataFrame, bench_df: pd.DataFrame, days: int = 20) -> float:
    """股票近 N 日回報 減 恒指近 N 日回報（百分點）。"""
    def ret(df):
        if df is None or len(df) < days + 1:
            return 0.0
        a = float(df["Close"].iloc[-1 - days])
        b = float(df["Close"].iloc[-1])
        return 0.0 if a == 0 else (b / a - 1.0) * 100.0

    return ret(stock_df) - ret(bench_df)
