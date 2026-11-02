#!/usr/bin/env python3
"""TyLove — yfinance 多時框收集器（2026-11-01 新增）

用途：你暫時未有 desktop 長期跑 MT4，所以用 yfinance 補 M1/M5/M15/H1/H4
五個時框嘅數據返你系統，令伺服器可以做多時框 cross-check。

用法：
    python3 yf_collector.py              # 跑一次
    python3 yf_collector.py --loop 300   # 每 5 分鐘推一次

環境變數（同 mt5_collector.py 一致）：
    API_BASE   例 https://tylovetrading-production.up.railway.app
    FEED_TOKEN 你嘅 feed token

⚠️ 誠實限制（一定要知）：
  1. yfinance 嘅黃金係 GC=F **期貨**，唔係你 MT4 嘅現貨，兩者有基差
     （你日誌顯示 4–9 美元），所以呢批數據適合「多時框方向 cross-check」，
     唔適合做精確入場價。
  2. 1 分鐘數據 yfinance 只提供最近 7 日；5/15/30 分鐘 60 日；
     1 小時 730 日；4 小時冇原生，由 1 小時合成。
  3. 長期歷史仍然要靠你 MT4 匯出 / HistData。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:5000").rstrip("/")
FEED_TOKEN = os.getenv("FEED_TOKEN", "")
SYMBOL = os.getenv("YF_SYMBOL", "GC=F")
SEND_SYMBOL = os.getenv("YF_SEND_SYMBOL", "XAUUSD")
HTTP_TIMEOUT = 30

# (yfinance interval, 推送用嘅分鐘數, 回溯期, 要唔要 resample)
FRAMES = [
    ("1m",  1,   "7d",   None),
    ("5m",  5,   "60d",  None),
    ("15m", 15,  "60d",  None),
    ("30m", 30,  "60d",  None),
    ("60m", 60,  "730d", None),
    ("60m", 240, "730d", "4h"),
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def headers() -> dict:
    h = {"Content-Type": "application/json"}
    if FEED_TOKEN:
        h["X-Feed-Token"] = FEED_TOKEN
    return h


def resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """4 小時 K 線由 1 小時合成（yfinance 冇 4h）。"""
    out = df.resample("4h").agg({"Open": "first", "High": "max",
                                 "Low": "min", "Close": "last"}).dropna()
    return out


def push_frame(interval: str, minutes: int, period: str, rs) -> bool:
    try:
        import yfinance as yf
        df = yf.download(SYMBOL, period=period, interval=interval,
                         progress=False, auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ {interval}/{period} 下載失敗：{type(exc).__name__}: {exc}")
        return False

    if df is None or df.empty:
        log(f"⚠️ {interval}/{period} 冇數據")
        return False

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    if rs == "4h":
        df = resample_4h(df)

    bars = []
    for ts, row in df.iterrows():
        try:
            t = int(ts.timestamp())
            o, h, lo, c = (float(row["Open"]), float(row["High"]),
                           float(row["Low"]), float(row["Close"]))
        except (ValueError, TypeError, KeyError):
            continue
        if not all(v == v for v in (o, h, lo, c)):   # NaN 檢查
            continue
        bars.append({"t": t, "o": round(o, 2), "h": round(h, 2),
                     "l": round(lo, 2), "c": round(c, 2)})

    if not bars:
        log(f"⚠️ {minutes} 分鐘冇有效 K 線")
        return False

    payload = {"symbol": SEND_SYMBOL, "interval": minutes,
               "source": "yf", "bars": bars}
    tag = rs or f"{minutes}m"
    try:
        r = requests.post(f"{API_BASE}/api/gold/bars", json=payload,
                          headers=headers(), timeout=HTTP_TIMEOUT)
        ok = r.status_code == 200 and (r.json() or {}).get("ok")
        if ok:
            last = datetime.fromtimestamp(bars[-1]["t"], timezone.utc)
            log(f"✅ {tag:>4} 已推 {len(bars):5d} 支（最後 {last:%m-%d %H:%M} UTC）")
        else:
            log(f"⚠️ {tag:>4} 被拒：HTTP {r.status_code} {r.text[:120]}")
        return ok
    except requests.RequestException as exc:
        log(f"⚠️ {tag} 推送失敗：{type(exc).__name__}: {exc}")
        return False


def run_once() -> int:
    ok = 0
    for interval, minutes, period, rs in FRAMES:
        if push_frame(interval, minutes, period, rs):
            ok += 1
    log(f"── 完成：{ok}/{len(FRAMES)} 個時框成功 ──")
    return ok


def main() -> None:
    loop = 0
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        loop = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 300

    log(f"API={API_BASE} | 符號={SYMBOL} → 推送為 {SEND_SYMBOL}")
    if loop <= 0:
        run_once()
        return

    log(f"每 {loop} 秒推一次（Ctrl+C 停止）")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            log("已停止")
            break
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️ 本輪出錯：{type(exc).__name__}: {exc}")
        time.sleep(loop)


if __name__ == "__main__":
    main()
