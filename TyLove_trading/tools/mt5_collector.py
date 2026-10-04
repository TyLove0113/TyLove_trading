"""MT5 真實報價收集器 — 將你券商（AXI）嘅報價推送去 TyLove 系統。

═══════════════════════════════════════════════════════════════════
  行呢個之前要準備
═══════════════════════════════════════════════════════════════════
1. Windows 電腦（MetaTrader5 套件只支援 Windows 64-bit）
2. 安裝 MetaTrader 5 終端，登入你 AXI MT5 戶口，保持開住
3. 開一個 MT5 圖表揀 XAUUSD，等歷史下載完（圖表拉到有 300 支以上）
4. 裝套件：
       pip install MetaTrader5 requests
5. 改下面 API_BASE（同 Railway 環境變數 FEED_TOKEN 對應）

行：
       python mt5_collector.py

想試一次唔想長開：
       python mt5_collector.py --once

═══════════════════════════════════════════════════════════════════
  佢做啲咩
═══════════════════════════════════════════════════════════════════
· 每 5 秒推送一次 bid/ask（即時報價）
· 每 15 分鐘推送一次最近 300 支 M15 K 線（你券商嘅真實 OHLC）
· 收到之後，TyLove 系統就唔再用 yfinance 期貨，價位同你 MT4 完全一致

作者：TyLove
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    import requests
except ImportError as exc:
    print(f"❌ 缺少套件：{exc}")
    print("   請執行： pip install MetaTrader5 requests")
    sys.exit(1)

# ═════════════ 你只需要改呢兩行 ═════════════
API_BASE = "https://你的-app-名.up.railway.app"
FEED_TOKEN = ""          # 同 Railway 環境變數 GOLD_FEED_TOKEN 一樣；冇設定就留空
# ════════════════════════════════════════════

SYMBOL = "XAUUSD"
BAR_TF = mt5.TIMEFRAME_M15
BAR_MINUTES = 15
BAR_COUNT = 300
TICK_SECONDS = 5
PUSH_BARS_MIN = 15
HTTP_TIMEOUT = 8

_TF_SECONDS = BAR_MINUTES * 60


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def headers() -> dict:
    h = {"Content-Type": "application/json"}
    if FEED_TOKEN:
        h["X-Feed-Token"] = FEED_TOKEN
    return h


def detect_tz_offset() -> int:
    """MT5 嘅 K 線時間係「券商 server time」嘅 timestamp，唔係真 UTC。

    用當前未收盤 K 線嘅開始時間同真實時間對照，取最接近嘅週期倍數做偏移。
    唔咁做的話，系統嘅活躍時段判斷同「數據距今幾分鐘」都會錯。
    """
    rates = mt5.copy_rates_from_pos(SYMBOL, BAR_TF, 0, 1)
    if rates is None or len(rates) == 0:
        return 0
    bar_t = int(rates[0]["time"])
    now = int(time.time())
    return int(round((bar_t - now) / _TF_SECONDS)) * _TF_SECONDS


def push_bars(offset: int) -> bool:
    rates = mt5.copy_rates_from_pos(SYMBOL, BAR_TF, 0, BAR_COUNT)
    if rates is None or len(rates) == 0:
        log(f"⚠️ 攞唔到 K 線：{mt5.last_error()}")
        return False

    bars = [
        {
            "t": int(r["time"]) - offset,
            "o": float(r["open"]),
            "h": float(r["high"]),
            "l": float(r["low"]),
            "c": float(r["close"]),
        }
        for r in rates
    ]
    payload = {
        "symbol": SYMBOL,
        "interval": BAR_MINUTES,
        "source": "mt5",
        "bars": bars,
    }
    try:
        resp = requests.post(f"{API_BASE}/api/gold/bars", json=payload,
                             headers=headers(), timeout=HTTP_TIMEOUT)
        ok = resp.status_code == 200 and resp.json().get("ok")
        if ok:
            last_t = datetime.fromtimestamp(bars[-1]["t"], timezone.utc)
            log(f"✅ 已推 {len(bars)} 支 {SYMBOL} M{BAR_MINUTES} K 線"
                f"（最後一支 {last_t:%m-%d %H:%M} UTC）")
        else:
            log(f"⚠️ K 線推送被拒：HTTP {resp.status_code} {resp.text[:150]}")
        return ok
    except requests.RequestException as exc:
        log(f"⚠️ K 線推送失敗：{type(exc).__name__}: {exc}")
        return False


def push_tick(offset: int) -> bool:
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None or tick.bid <= 0:
        log("⚠️ 攞唔到即時報價（品種可能未加入 Market Watch）")
        return False
    payload = {
        "symbol": SYMBOL,
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "source": "mt5",
        "t": int(time.time()),
    }
    try:
        resp = requests.post(f"{API_BASE}/api/gold/tick", json=payload,
                             headers=headers(), timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            spread = round(tick.ask - tick.bid, 3)
            print(f"\r  報價 {tick.bid:.2f} / {tick.ask:.2f}  點差 {spread:.2f}   ",
                  end="", flush=True)
            return True
        log(f"⚠️ 報價推送被拒：HTTP {resp.status_code} {resp.text[:120]}")
        return False
    except requests.RequestException as exc:
        log(f"⚠️ 報價推送失敗：{type(exc).__name__}: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 → TyLove 真實報價收集器")
    ap.add_argument("--once", action="store_true", help="只推一次然後收工")
    args = ap.parse_args()

    if "你的-app" in API_BASE:
        print("❌ 你未改 API_BASE！請改成你 Railway app 嘅網址")
        return 1

    print("=" * 62)
    print("  MT5 真實報價收集器")
    print(f"  目標：{API_BASE}")
    print(f"  品種：{SYMBOL}    週期：M{BAR_MINUTES}")
    print("=" * 62)

    if not mt5.initialize():
        code, desc = mt5.last_error()
        print(f"❌ 連唔到 MetaTrader 5（錯誤 {code}：{desc}）")
        print("   檢查：① MT5 終端有冇開住並且登入咗？")
        print("         ② 係唔係 Windows？（MetaTrader5 套件只支援 Windows）")
        print("         ③ MT5 有冇喺「工具 → 選項 → 智能交易」開 API？")
        return 1

    info = mt5.account_info()
    if info is None:
        print("❌ 攞唔到戶口資料 —— MT5 可能未登入")
        mt5.shutdown()
        return 1
    print(f"✅ 已連線：{info.server}　戶口 {info.login}　"
          f"結餘 {info.balance:.2f} {info.currency}")

    # 確保品種喺 Market Watch
    if not mt5.symbol_info(SYMBOL):
        print(f"❌ MT5 搵唔到品種 {SYMBOL} —— 請喺 Market Watch 加入佢")
        mt5.shutdown()
        return 1
    mt5.symbol_select(SYMBOL, True)

    offset = detect_tz_offset()
    print(f"✅ 券商 server 同 UTC 相差 {offset / 3600.0} 小時（已自動修正）")

    if not push_bars(offset):
        print("⚠️ 第一次推 K 線失敗，會繼續試。");

    if args.once:
        push_tick(offset)
        print("\n--once 模式完成，收工。")
        mt5.shutdown()
        return 0

    print(f"\n開始推送：報價每 {TICK_SECONDS} 秒、K 線每 {PUSH_BARS_MIN} 分鐘")
    print("（按 Ctrl+C 收工）\n")

    last_bars = 0.0
    fails = 0
    try:
        while True:
            if push_tick(offset):
                fails = 0
            else:
                fails += 1

            now = time.time()
            if now - last_bars >= PUSH_BARS_MIN * 60:
                print()
                push_bars(offset)
                last_bars = now

            if fails == 10:
                print()
                log("⚠️ 連續 10 次推送失敗 —— 檢查 API_BASE 同網絡")

            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\n")
        log("收到 Ctrl+C，收工。")
    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
