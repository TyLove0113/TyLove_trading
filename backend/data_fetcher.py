# ─────────────────────────────────────────
# data_fetcher.py — 拉取港股數據（修復版）
# ─────────────────────────────────────────

import yfinance as yf
import pandas as pd
import datetime
from datetime import datetime as dt
import config


def get_stock_data(symbol: str) -> dict:
    """
    拉取單隻股票數據
    自動處理收市後 nan 問題
    """
    try:
        ticker = yf.Ticker(symbol)

        # 用明確日期範圍避免快取問題
        end   = dt.now()
        start = end - datetime.timedelta(days=60)
        hist  = ticker.history(start=start, end=end, interval="1d")

        if hist.empty:
            print(f"⚠️ 無數據：{symbol}")
            return None

        # 過濾掉 Close 係 nan 嘅行
        hist = hist.dropna(subset=["Close"])

        if len(hist) < 2:
            print(f"⚠️ 數據不足：{symbol}")
            return None

        # 最新有效數據
        latest = hist.iloc[-1]
        prev   = hist.iloc[-2]

        current_price = round(float(latest["Close"]), 3)
        prev_price    = round(float(prev["Close"]), 3)
        change_pct    = round(
            (current_price - prev_price) / prev_price * 100, 2
        )
        volume = int(latest["Volume"]) if not pd.isna(latest["Volume"]) else 0

        # 技術指標
        closes = hist["Close"].dropna()
        rsi    = calculate_rsi(closes)
        ma5    = round(float(closes.tail(5).mean()), 3)
        ma20   = round(float(closes.tail(20).mean()), 3)

        # 數據日期
        data_date = latest.name
        if hasattr(data_date, "date"):
            data_date = data_date.date()

        return {
            "symbol":      symbol,
            "price":       current_price,
            "change_pct":  change_pct,
            "volume":      volume,
            "rsi":         rsi,
            "ma5":         ma5,
            "ma20":        ma20,
            "above_ma5":   current_price > ma5,
            "above_ma20":  current_price > ma20,
            "data_date":   str(data_date),
            "timestamp":   dt.now().strftime("%H:%M:%S"),
        }

    except Exception as e:
        print(f"❌ 錯誤 {symbol}: {e}")
        return None


def calculate_rsi(closes: pd.Series, period: int = 14) -> float:
    """計算 RSI"""
    if len(closes) < period + 1:
        return 50.0

    delta    = closes.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = -delta.where(delta < 0, 0.0)
    avg_gain = gain.tail(period).mean()
    avg_loss = loss.tail(period).mean()

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def get_all_stocks() -> list[dict]:
    """拉取 Watchlist 所有股票數據"""
    print(f"\n📡 開始拉取數據 ({dt.now().strftime('%H:%M:%S')})")
    print(f"共 {len(config.HK_STOCKS)} 隻股票...\n")

    results = []
    for symbol in config.HK_STOCKS:
        data = get_stock_data(symbol)
        if data:
            results.append(data)
            arrow = "▲" if data["change_pct"] >= 0 else "▼"
            print(f"  {symbol:<12} {data['price']:>8.3f}  "
                  f"{arrow}{abs(data['change_pct']):>5.2f}%  "
                  f"RSI:{data['rsi']:>6.1f}  "
                  f"({data['data_date']})")

    print(f"\n✅ 完成：{len(results)}/{len(config.HK_STOCKS)} 隻")
    return results


# ── 測試用 ────────────────────────────────
if __name__ == "__main__":
    stocks = get_all_stocks()

    if stocks:
        print("\n數據摘要：")
        gainers = [s for s in stocks if s["change_pct"] > 0]
        losers  = [s for s in stocks if s["change_pct"] < 0]
        best    = max(stocks, key=lambda x: x["change_pct"])
        worst   = min(stocks, key=lambda x: x["change_pct"])
        print(f"  上漲：{len(gainers)} 隻")
        print(f"  下跌：{len(losers)} 隻")
        print(f"  最強：{best['symbol']} {best['change_pct']:+.2f}%")
        print(f"  最弱：{worst['symbol']} {worst['change_pct']:+.2f}%")
        print(f"\n  數據來自：{stocks[0]['data_date']}")