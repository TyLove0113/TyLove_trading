"""
技術指標庫 — 全部按業界標準算法實作（可用任何圖表軟件對照）
重點：
  · RSI 用 Wilder 平滑（同 TradingView / 富途 一致），唔係簡單平均
  · ATR 用 Wilder 平滑 —— 呢個係止蝕位嘅基礎
  · 全部指標同時回傳「最新值」同「方向」，方便評分
"""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    """標準 Wilder RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr_wilder(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """標準 Wilder ATR。"""
    return true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """趨勢強度 0-100，>25 代表有趨勢。"""
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1.0 / n, adjust=False, min_periods=n
    ).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1.0 / n, adjust=False, min_periods=n
    ).mean() / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().fillna(0.0)


def slope(series: pd.Series, n: int = 10) -> float:
    """近 n 支嘅平均斜率（以最後價值的百分比表示），用最小二乘。"""
    s = series.dropna().tail(n)
    if len(s) < 3:
        return 0.0
    x = np.arange(len(s), dtype=float)
    y = s.to_numpy(dtype=float)
    denom = ((x - x.mean()) ** 2).sum()
    if denom == 0 or y.mean() == 0:
        return 0.0
    b = ((x - x.mean()) * (y - y.mean())).sum() / denom
    return float(b / y.mean() * 100.0)  # 每支平均變動 %


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """一次過計好所有指標，掛上 DataFrame。"""
    out = df.copy()
    close = out["Close"]

    out["MA5"] = sma(close, 5)
    out["MA10"] = sma(close, 10)
    out["MA20"] = sma(close, 20)
    out["MA50"] = sma(close, 50)
    out["RSI14"] = rsi_wilder(close, 14)
    out["ATR14"] = atr_wilder(out, 14)
    out["ATR_PCT"] = out["ATR14"] / close * 100.0
    out["MACD"], out["MACD_SIG"], out["MACD_HIST"] = macd(close)
    out["BB_LOW"], out["BB_MID"], out["BB_UP"] = bollinger(close)
    out["ADX14"] = adx(out, 14)
    out["VOL_MA20"] = out["Volume"].rolling(20).mean()
    out["VOL_RATIO"] = out["Volume"] / out["VOL_MA20"].replace(0, np.nan)
    out["HIGH20"] = out["High"].rolling(20).max()
    out["LOW20"] = out["Low"].rolling(20).min()
    out["TURNOVER"] = close * out["Volume"]
    out["TURNOVER_MA20"] = out["TURNOVER"].rolling(20).mean()
    return out


def latest_snapshot(df: pd.DataFrame) -> dict:
    """抽出最後一支嘅指標數值，方便評分用。"""
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    def g(key, default=0.0):
        try:
            v = float(last.get(key, default))
            return default if (v is None or np.isnan(v)) else v
        except Exception:
            return default

    return {
        "close": float(last["Close"]),
        "open": float(last["Open"]),
        "high": float(last["High"]),
        "low": float(last["Low"]),
        "volume": float(last["Volume"]),
        "date": (df.index[-1].strftime("%Y-%m-%d %H:%M")
                  if hasattr(df.index[-1], "strftime") else str(df.index[-1])),
        "prev_close": float(prev["Close"]),
        "ma5": g("MA5"),
        "ma10": g("MA10"),
        "ma20": g("MA20"),
        "ma50": g("MA50"),
        "rsi": g("RSI14", 50.0),
        "atr": g("ATR14"),
        "atr_pct": g("ATR_PCT"),
        "macd": g("MACD"),
        "macd_sig": g("MACD_SIG"),
        "macd_hist": g("MACD_HIST"),
        "adx": g("ADX14"),
        "vol_ratio": g("VOL_RATIO", 1.0),
        "vol_ma20": g("VOL_MA20"),
        "bb_low": g("BB_LOW"),
        "bb_up": g("BB_UP"),
        "high20": g("HIGH20"),
        "low20": g("LOW20"),
        "turnover": g("TURNOVER"),
        "turnover_ma20": g("TURNOVER_MA20"),
        "ma20_slope": slope(df["MA20"], 10),
        "close_slope": slope(df["Close"], 10),
        "hist_prev": float(prev.get("MACD_HIST", 0.0) or 0.0),
        "rsi_prev": float(prev.get("RSI14", 50.0) or 50.0),
    }
