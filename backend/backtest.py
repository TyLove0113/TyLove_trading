"""
回測引擎 —— 呢個係整個系統最重要嘅一塊
原版最大問題：所有參數（權重、門檻）都係拍腦袋，從未驗證過。
呢個引擎用真實歷史數據，逐日計分、逐日模擬入場出場，然後話你知：

  · 勝率 / 平均賺 / 平均蝕
  · 期望值（以 R 為單位）—— 正數先代表長期賺錢
  · 固定風險法（每筆風險 1% 本金）嘅資金曲線同最大回撤
  · 唔同門檻（60/70/80）分別係點

方法論：
  · 分數 >= 門檻 → 下一支開盤入場（唔用未來數據）
  · 同日先後觸及止蝕同目標 → 保守假設先中止蝕
  · 分批出場：目標一減半，目標二清倉
  · 同時最多 MAX_OPEN_POSITIONS 隻，同一隻股票唔會疊倉
  · 每筆交易風險固定 1% 本金 → 得出可比較嘅 R 值

用法：
    python backtest.py --sweep
    python backtest.py --years 3 --threshold 70
"""
import argparse
import logging
from collections import defaultdict

import pandas as pd

import data_fetcher as df_mod
import indicators
import scoring
from config import (ATR_STOP_MULT, ATR_TARGET1_MULT, ATR_TARGET2_MULT,
                    MAX_HOLD_DAYS, MAX_OPEN_POSITIONS, WATCHLIST)

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("tylove.backtest")

RISK_FRACTION = 0.01  # 每筆交易風險 = 本金 1%


def build_features(symbol: str, bench: pd.Series, years: int = 3) -> pd.DataFrame:
    """為每隻股票預先算好每日指標同分數。"""
    daily = df_mod.fetch_daily(symbol, period=f"{max(years, 1)}y")
    if len(daily) < 120:
        return pd.DataFrame()

    ind = indicators.compute(daily)
    bench_ret = (bench / bench.shift(20) - 1) * 100
    stock_ret = (ind["Close"] / ind["Close"].shift(20) - 1) * 100
    ind["RS_20D"] = (stock_ret - bench_ret.reindex(ind.index).ffill()).fillna(0.0)

    rows = []
    for i in range(60, len(ind)):
        r = ind.iloc[i]
        atr = r.get("ATR14")
        if pd.isna(atr) or atr <= 0:
            continue
        s = {
            "close": float(r["Close"]),
            "ma5": float(r.get("MA5", 0) or 0), "ma20": float(r.get("MA20", 0) or 0),
            "ma50": float(r.get("MA50", 0) or 0),
            "rsi": float(r.get("RSI14", 50) or 50), "atr": float(atr),
            "atr_pct": float(r.get("ATR_PCT", 0) or 0),
            "macd_hist": float(r.get("MACD_HIST", 0) or 0),
            "hist_prev": float(ind["MACD_HIST"].iloc[i - 1] or 0),
            "adx": float(r.get("ADX14", 0) or 0),
            "vol_ratio": float(r.get("VOL_RATIO", 1) or 1),
            "turnover_ma20": float(r.get("TURNOVER_MA20", 0) or 0),
            "volume": float(r["Volume"]),
            "ma20_slope": indicators.slope(ind["MA20"].iloc[: i + 1], 10),
            "rs_20d": float(r.get("RS_20D", 0) or 0),
        }
        tech = scoring.score_technical(s)
        rows.append({
            "date": ind.index[i], "open": float(r["Open"]), "high": float(r["High"]),
            "low": float(r["Low"]), "close": float(r["Close"]), "atr": float(atr),
            "score": tech["technical_score"],
        })
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def _simulate_one(fdf: pd.DataFrame, entry_date, sig_atr, entry_price,
                  stop_mult, t1_mult, t2_mult, max_hold):
    """模擬單筆交易，回傳 (結果 dict)。"""
    future = fdf[fdf.index > entry_date]
    if len(future) < 2 or entry_price <= 0:
        return None

    stop = entry_price - stop_mult * sig_atr
    t1 = entry_price + t1_mult * sig_atr
    t2 = entry_price + t2_mult * sig_atr
    initial_stop = stop

    half_done, realised, remaining = False, 0.0, 1.0
    exit_date, reason, held = future.index[min(max_hold, len(future) - 1)], "到期", 0

    for j in range(1, min(max_hold + 1, len(future))):
        bar = future.iloc[j]
        held = j
        low, high = float(bar["low"]), float(bar["high"])

        if low <= stop:                                  # 保守：先假設中停損
            realised += remaining * (stop - entry_price) / entry_price
            exit_date, reason, remaining = future.index[j], "止蝕", 0.0
            break
        if not half_done and high >= t1:
            realised += 0.5 * (t1 - entry_price) / entry_price
            remaining, half_done = 0.5, True
            stop = max(stop, entry_price)                # 止蝕推上成本價
        if high >= t2:
            realised += remaining * (t2 - entry_price) / entry_price
            exit_date, reason, remaining = future.index[j], "達標", 0.0
            break

    if remaining > 0:
        last = future.iloc[min(max_hold, len(future) - 1)]
        realised += remaining * (float(last["close"]) - entry_price) / entry_price
        exit_date = future.index[min(max_hold, len(future) - 1)]

    risk_pct = (entry_price - initial_stop) / entry_price
    return {
        "entry": round(entry_price, 3),
        "pct": realised * 100,
        "risk_pct": risk_pct * 100,
        "r_multiple": realised / risk_pct if risk_pct > 0 else 0.0,
        "days": held, "reason": reason,
        "entry_date": str(entry_date)[:10],
        "exit_date": str(exit_date)[:10],
        "exit_ts": exit_date,
    }


def simulate(features: dict, threshold: float,
             stop_mult=ATR_STOP_MULT, t1_mult=ATR_TARGET1_MULT,
             t2_mult=ATR_TARGET2_MULT, max_hold=MAX_HOLD_DAYS,
             max_concurrent=MAX_OPEN_POSITIONS) -> dict:
    """逐日推進，同一時間最多 max_concurrent 個倉、同股唔疊倉。"""
    events = defaultdict(list)
    for sym, fdf in features.items():
        for date, row in fdf.iterrows():
            if row["score"] >= threshold:
                events[date].append((sym, row))

    all_dates = sorted({d for f in features.values() for d in f.index})
    open_until, trades = {}, []   # symbol -> exit_date

    for date in all_dates:
        for sym in [s for s, d in open_until.items() if d <= date]:
            open_until.pop(sym, None)

        for sym, sig in events.get(date, []):
            if sym in open_until or len(open_until) >= max_concurrent:
                continue
            fdf = features[sym]
            future = fdf[fdf.index > date]
            if len(future) < 2:
                continue
            entry = float(future.iloc[0]["open"])
            res = _simulate_one(fdf, date, float(sig["atr"]), entry,
                                stop_mult, t1_mult, t2_mult, max_hold)
            if not res:
                continue
            res["symbol"] = sym
            res["score"] = float(sig["score"])
            trades.append(res)
            open_until[sym] = res["exit_ts"]

    if not trades:
        return {"count": 0, "trades": []}

    df = pd.DataFrame(trades).sort_values("entry_date").reset_index(drop=True)
    wins = df[df["r_multiple"] > 0]
    losses = df[df["r_multiple"] <= 0]

    # 固定風險法：每筆風險 1% 本金 → 資金曲線
    equity, peak, max_dd, curve = 1.0, 1.0, 0.0, []
    for r in df["r_multiple"]:
        equity *= (1 + RISK_FRACTION * r)
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity / peak - 1) * 100)
        curve.append(equity)

    return {
        "trades": trades, "count": len(df),
        "win_rate": round(len(wins) / len(df) * 100, 1),
        "avg_win_r": round(wins["r_multiple"].mean(), 2) if len(wins) else 0.0,
        "avg_loss_r": round(losses["r_multiple"].mean(), 2) if len(losses) else 0.0,
        "expectancy_r": round(df["r_multiple"].mean(), 3),
        "expectancy_pct": round(df["pct"].mean(), 3),
        "avg_days": round(df["days"].mean(), 1),
        "total_return_pct": round((equity - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "by_reason": df["reason"].value_counts().to_dict(),
    }


def report(res: dict, threshold: float) -> bool:
    print(f"\n【進場門檻 {threshold} 分】")
    print(f"  交易次數      : {res['count']}")
    print(f"  勝率          : {res['win_rate']}%")
    print(f"  平均賺 / 平均蝕: {res['avg_win_r']:+.2f}R / {res['avg_loss_r']:+.2f}R")
    print(f"  期望值        : {res['expectancy_r']:+.3f}R / 筆"
          f"  {'✅ 正期望' if res['expectancy_r'] > 0 else '❌ 負期望，唔可以實戰'}")
    print(f"  （即每筆平均賺蝕 {res['expectancy_pct']:+.3f}% 價格變動）")
    print(f"  平均持倉      : {res['avg_days']} 日")
    print(f"  資金曲線      : {res['total_return_pct']:+.1f}%（每筆風險 1% 本金）")
    print(f"  最大回撤      : {res['max_drawdown_pct']:.1f}%")
    print(f"  出場分佈      : {res['by_reason']}")
    return res["expectancy_r"] > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=70)
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    print(f"\n{'='*66}")
    print(f"  TyLove 回測｜{args.years} 年日線｜觀察名單 {len(WATCHLIST)} 隻")
    print(f"{'='*66}")

    bench_df = df_mod.fetch_benchmark(period=f"{args.years}y")
    if len(bench_df) == 0:
        print("❌ 拉唔到恒指數據，檢查網絡")
        return
    bench = bench_df["Close"]

    features = {}
    for sym in WATCHLIST:
        print(f"  計算 {sym} ...", end=" ", flush=True)
        fdf = build_features(sym, bench, args.years)
        if len(fdf):
            features[sym] = fdf
            print(f"{len(fdf)} 日")
        else:
            print("數據不足")

    if not features:
        print("❌ 無可用數據")
        return

    thresholds = [60, 70, 80] if args.sweep else [args.threshold]
    rows = []
    for th in thresholds:
        res = simulate(features, th)
        if res["count"] == 0:
            print(f"\n門檻 {th}：無交易")
            continue
        ok = report(res, th)
        rows.append((th, res, ok))

    print(f"\n{'='*66}")
    if args.sweep and rows:
        best = max(rows, key=lambda x: x[1]["expectancy_r"])
        print(f"  📌 最佳門檻：{best[0]} 分（期望值 {best[1]['expectancy_r']:+.3f}R/筆，"
              f"勝率 {best[1]['win_rate']}%，樣本 {best[1]['count']} 筆）")
        print(f"  喺 config.py 將 ALERT_THRESHOLD 設為 {best[0]}")
    print("\n  ⚠️ 未計交易成本：港股來回約 0.25–0.4%。")
    print("     期望值 < 0.15% / 筆 基本上無肉食。")
    print("  ⚠️ 樣本少於 100 筆嘅結果唔可靠，唔好當真。")
    print(f"{'='*66}\n")


if __name__ == "__main__":
    main()
