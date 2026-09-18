"""
黃金 XAU/USD 日內分析模組
=========================================================
策略：Donchian 20 突破（同回測報告用嘅同一組規則）
  · 進場：M15 收盤突破前 20 支 K 線高位（做多）／低位（做空）
  · 止蝕：1.0 × ATR(14)
  · 目標：3.0 × ATR(14)
  · 每日最多 1 筆，日終平倉（即日鮮，唔持過夜）

⚠️ 誠實聲明（重要，唔可以刪）
呢組參數喺 60 日 M15 樣本表現幾好（利潤因子 2.17），
但放到兩年 H1 樣本外測試就跌到 **利潤因子 0.90（蝕錢）**。
即係話：呢個策略未證實有穩定優勢（edge）。所以本模組只作
「練習 + 記錄」用途，唔應該視為賺錢保證。

數據源：GC=F（COMEX 黃金期貨）。yfinance 冇 XAUUSD 現貨代號，
GC=F 同現貨走勢同步（正常價差 0–2 美元），足夠做技術分析。
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import config

log = logging.getLogger("tylove.gold")

DISCLAIMER = ("此策略未通過兩年樣本外驗證（利潤因子僅 0.90–1.06），"
              "只供練習與記錄，唔好視為賺錢保證。")


# ------------------------------------------------------------------ 資料庫
def _conn():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                price REAL, direction TEXT,
                entry REAL, stop REAL, target REAL,
                atr REAL, lot REAL, risk_usd REAL,
                reasons TEXT
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_gold_ts ON gold_signals(ts)")


def _store(g: dict):
    init_db()
    with _conn() as c:
        c.execute("INSERT INTO gold_signals"
                  "(ts,price,direction,entry,stop,target,atr,lot,risk_usd,reasons)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (g["time"], g["price"], g["direction"], g["entry"], g["stop"],
                   g["target"], g["atr"], g["lot"], g["risk_usd"],
                   "；".join(g.get("reasons", []))))


def recent(limit: int = 20) -> list:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM gold_signals WHERE direction IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _count_today() -> int:
    """今日已出訊號數（用香港時間計日，唔好跟容器 UTC）。"""
    init_db()
    today = datetime.now(ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d")
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) n FROM gold_signals WHERE ts LIKE ? AND direction IS NOT NULL",
            (today + "%",)).fetchone()
    return int(row["n"]) if row else 0


def _already_pushed(ts: str) -> bool:
    """同一支 K 線只推一次 —— 連續監控每 5 分鐘跑一次，冇呢個就會狂推。"""
    if not ts:
        return False
    init_db()
    with _conn() as c:
        row = c.execute("SELECT 1 FROM gold_signals WHERE ts = ? LIMIT 1", (ts,)).fetchone()
    return row is not None


def _use_closed(df: pd.DataFrame, minutes: int = 15) -> pd.DataFrame:
    """只用「已經收盤」嘅 K 線。

    未行完嘅最後一支 K 線，價位仲會變 —— 依賴佢會出假突破訊號
    （掃描嗰刻穿咗，收盤又跌返入去）。回測都係用收盤價，所以呢度保持一致。
    """
    if df is None or len(df) == 0:
        return df
    t = df.index[-1]
    try:
        bar_start = t.to_pydatetime()
    except AttributeError:
        bar_start = t
    tz = getattr(bar_start, "tzinfo", None)
    now = datetime.now(tz) if tz else datetime.now()
    if (now - bar_start).total_seconds() < minutes * 60:
        return df.iloc[:-1]
    return df


# ------------------------------------------------------------------ 指標
def fetch(interval: str = "15m", period: str = "60d") -> pd.DataFrame:
    df = yf.Ticker(config.GOLD_SYMBOL).history(
        period=period, interval=interval, auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"攞唔到 {config.GOLD_SYMBOL} 嘅數據")
    df = df[["Open", "High", "Low", "Close"]].dropna()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert(config.HK_TZ)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    h, l, c = d["High"], d["Low"], d["Close"]

    # ATR(14) —— Wilder 平滑
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # EMA / RSI
    d["ema20"] = c.ewm(span=20, adjust=False).mean()
    d["ema50"] = c.ewm(span=50, adjust=False).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, float("nan")))

    # Donchian 20（唔包含當前 K 線，避免自己突破自己）
    n = config.GOLD_BREAKOUT_BARS
    d["hh"] = h.rolling(n).max().shift(1)
    d["ll"] = l.rolling(n).min().shift(1)
    return d


# ------------------------------------------------------------------ 判斷
def _session_ok(t: datetime) -> bool:
    """倫敦 + 紐約活躍時段（香港時間 15:00 – 01:00）。"""
    return t.hour >= 15 or t.hour < 1


def evaluate(df: pd.DataFrame = None) -> dict:
    """分析最新一支 K 線，回傳完整訊號字典。"""
    if df is None:
        df = add_indicators(_use_closed(fetch()))

    last = df.iloc[-1]
    prev = df.iloc[-2]
    t = df.index[-1]

    price = float(last["Close"])
    atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
    atr_pct = round(atr / price * 100, 2) if price else 0.0

    out = {
        "ok": True,
        "time": t.strftime("%Y-%m-%d %H:%M"),
        "price": round(price, 2),
        "atr": round(atr, 2),
        "atr_pct": atr_pct,
        "has_signal": False,
        "direction": None,
        "reasons": [],
        "state": "",
        "disclaimer": DISCLAIMER,
        "lot_min": config.GOLD_LOT_MIN,
        "lot_max": config.GOLD_LOT_MAX,
        "oz_per_lot": config.GOLD_OZ_PER_LOT,
        "spread": config.GOLD_SPREAD_USD,
        "today_trades": _count_today(),
        "max_trades": config.GOLD_MAX_TRADES_PER_DAY,
        "session_ok": _session_ok(t.to_pydatetime()),
        "data_symbol": config.GOLD_SYMBOL,
        "spot_offset": config.GOLD_SPOT_OFFSET,
        "data_age_min": round(
            (datetime.now(ZoneInfo(config.HK_TZ)) - t.to_pydatetime()).total_seconds() / 60, 1),
    }

    # ---- 訊號判斷 ----
    broke_up = float(last["Close"]) > float(last["hh"]) if pd.notna(last["hh"]) else False
    broke_dn = float(last["Close"]) < float(last["ll"]) if pd.notna(last["ll"]) else False
    trend_up = float(last["ema20"]) > float(last["ema50"])
    trend_dn = float(last["ema20"]) < float(last["ema50"])
    rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0

    prev_up = float(prev["Close"]) > float(prev["hh"]) if pd.notna(prev["hh"]) else False
    prev_dn = float(prev["Close"]) < float(prev["ll"]) if pd.notna(prev["ll"]) else False

    reasons = [
        f"EMA20 {'高於' if trend_up else '低於'} EMA50（{'上升' if trend_up else '下降'}趨勢）",
        f"RSI(14) = {rsi:.1f}",
        f"前 20 支 K 線區間：{float(last['ll']):.2f} – {float(last['hh']):.2f}",
    ]
    if not out["session_ok"]:
        reasons.append("⚠️ 而家唔係倫敦／紐約活躍時段，流動性差、點差會擴闊")
    _lim = config.GOLD_MAX_TRADES_PER_DAY          # 0 = 不限
    if _lim > 0 and out["today_trades"] >= _lim:
        reasons.append(f"⚠️ 今日已有 {out['today_trades']} 筆訊號（上限 {_lim} 筆），唔再出")

    direction = None
    if broke_up and not prev_up:
        direction = "long"
    elif broke_dn and not prev_dn:
        direction = "short"

    blocked = (_lim > 0 and out["today_trades"] >= _lim) or not out["session_ok"]

    if direction is None:
        out["reasons"] = reasons
        out["state"] = (f"價位喺區間內（{float(last['ll']):.2f} – {float(last['hh']):.2f}）"
                        f"，等突破。今日已出 {out['today_trades']} 筆訊號。")
        return out

    if blocked:
        out["reasons"] = reasons + ["訊號出現，但因上面原因唔推送"]
        out["state"] = "有突破但被過濾（時段或每日上限）"
        out["direction"] = direction
        return out

    # ---- 組裝交易計劃 ----
    stop_dist = config.GOLD_STOP_ATR * atr
    tgt_dist = config.GOLD_TARGET_ATR * atr
    if direction == "long":
        stop, target = price - stop_dist, price + tgt_dist
    else:
        stop, target = price + stop_dist, price - tgt_dist

    rr = round(tgt_dist / stop_dist, 2) if stop_dist else 0

    # 手數：由你嘅 0.01–0.05 手 range 揀，用風險金額決定喺 range 內嘅位置
    lot = _pick_lot(stop_dist)
    oz = round(lot * config.GOLD_OZ_PER_LOT, 2)
    risk_usd = round(stop_dist * oz + config.GOLD_SPREAD_USD * oz, 2)

    out.update({
        "has_signal": True,
        "direction": direction,
        "entry": round(price, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "stop_dist": round(stop_dist, 2),
        "target_dist": round(tgt_dist, 2),
        "rr": rr,
        "lot": lot,
        "oz": oz,
        "risk_usd": risk_usd,
        "reasons": reasons + [
            f"{'升穿' if direction == 'long' else '跌穿'}前 20 支高位／低位（Donchian 突破）",
        ],
        "state": f"{'做多' if direction == 'long' else '做空'}訊號",
    })
    return out


def _pick_lot(stop_dist: float) -> float:
    """喺 0.01–0.05 手之間揀一個合理手數。

    止蝕距離越大 → 揀越細手數（控制每筆虧損）。
    呢個唔係「最優化」，只係一個簡單嘅風險控制規則。
    """
    lo, hi = config.GOLD_LOT_MIN, config.GOLD_LOT_MAX
    if stop_dist <= 0:
        return lo
    # 止蝕 5 美元內 → 用最大手數；40 美元以上 → 用最細手數
    if stop_dist <= 5:
        return hi
    if stop_dist >= 40:
        return lo
    frac = (40 - stop_dist) / 35          # 0 → 1
    step = round((lo + (hi - lo) * frac) * 100) / 100
    return max(lo, min(hi, step))


# ------------------------------------------------------------------ 主入口
def early_alert(push: bool = True, df: pd.DataFrame = None) -> dict:
    """⚡ 即時預警：價格一穿 20 支區間就推，唔等 K 線收盤。

    為咩要：正式訊號要等 K 線收盤（M15 = 最多遲 15 分鐘），再加掃描間隔，
    實測收到時已經遲 20–25 分鐘，對即市買賣完全唔達標。
    呢個預警用「未收盤」嘅最新支 K 線，所以可以即刻出。

    代價：價格可能彈返入區間（假突破）。所以同正式訊號分開標示。
    同一支 K 線、同一個方向只推一次。
    """
    if df is None:
        df = add_indicators(fetch())          # 故意保留未收盤嘅最新支
    if len(df) < 25:
        return {"ok": False, "reason": "數據不足"}
    last = df.iloc[-1]
    t = df.index[-1]
    chan_hi = float(df["High"].iloc[-21:-1].max())
    chan_lo = float(df["Low"].iloc[-21:-1].min())
    price = float(last["Close"])
    ts = t.strftime("%Y-%m-%d %H:%M")

    if price > chan_hi:
        direction, level = "long", chan_hi
    elif price < chan_lo:
        direction, level = "short", chan_lo
    else:
        return {"ok": True, "fired": False}

    key = f"{ts}|{direction}"
    init_db()
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS gold_alerts"
                  "(k TEXT PRIMARY KEY, ts TEXT, direction TEXT, price REAL)")
        if c.execute("SELECT 1 FROM gold_alerts WHERE k = ?", (key,)).fetchone():
            return {"ok": True, "fired": False, "reason": "呢支 K 線已預警過"}
        c.execute("INSERT INTO gold_alerts(k, ts, direction, price) VALUES(?,?,?,?)",
                  (key, ts, direction, price))

    info = {"direction": direction, "level": round(level, 2), "price": round(price, 2),
            "time": ts, "diff": round(price - level, 2),
            "spot_offset": config.GOLD_SPOT_OFFSET, "data_age_min": 0.0}
    if push:
        try:
            import notifier
            notifier.push(notifier.fmt_gold_early(info), channel="gold")
        except Exception:  # noqa: BLE001
            log.exception("黃金即時預警推送失敗")
    log.info("⚡ 黃金即時預警：%s 穿 %s（價 %s）", direction, round(level, 2), round(price, 2))
    return {"ok": True, "fired": True, **info}


def scan(push: bool = True) -> dict:
    """跑一次黃金分析。有訊號就存 DB + 推 Telegram。"""
    try:
        g = evaluate()
    except Exception as exc:  # noqa: BLE001
        log.exception("黃金分析失敗")
        return {"ok": False, "error": str(exc)}

    if g.get("has_signal"):
        if _already_pushed(g.get("time")):
            log.info("黃金：%s 呢支 K 線已經推過，唔重複推送", g.get("time"))
            return g
        try:
            _store(g)
            g["today_trades"] = _count_today()
        except Exception:  # noqa: BLE001
            log.exception("黃金訊號存檔失敗")
        if push:
            try:
                import notifier
                notifier.push(notifier.fmt_gold_signal(g), channel="gold")
            except Exception:  # noqa: BLE001
                log.exception("黃金 Telegram 推送失敗")
    log.info("黃金分析：%s｜現價 %s｜%s", g.get("state"), g.get("price"), g.get("time"))
    return g


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    print(json.dumps(scan(push=False), ensure_ascii=False, indent=2))
