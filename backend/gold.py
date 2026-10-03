"""
黃金 XAU/USD 日內分析模組
=========================================================
策略：Donchian 20 突破（同回測報告用嘅同一組規則）
  · 進場：M15 收盤突破前 20 支 K 線高位（做多）／低位（做空）
  · 止蝕：1.0 × ATR(14)　目標 3.0 × ATR(14)（盈虧比 1:3）
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
def _live() -> bool:
    """而家係唔係用緊券商嘅真實報價？"""
    if config.GOLD_FORCE_YFINANCE:
        return False
    try:
        import feed
        return feed.is_live()
    except Exception:  # noqa: BLE001
        return False


def effective_offset() -> float:
    """校正值：用真實券商報價時係 0（唔需要校正）；用期貨時才要。"""
    return 0.0 if _live() else config.GOLD_SPOT_OFFSET


def fetch(interval: str = "15m", period: str = "60d") -> pd.DataFrame:
    """攞 K 線。優先用人嘅真實券商報價，冇就用 yfinance 期貨。

    呢個就係整個「價位對唔上」問題嘅根治點：
    券商報價 = 你落單嗰個價，唔需要估校正值。
    """
    if interval == "15m":
        try:
            import feed
            live = feed.bars(15)
            if live is not None and len(live) >= 25 and _live():
                log.info("用緊券商真實報價（%d 支）", len(live))
                return live
        except Exception:  # noqa: BLE001
            log.exception("讀券商報價失敗，回落 yfinance")

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
    """倫敦 + 紐約活躍時段（香港時間 15:00 – 01:00）。

    2026-10-03 加週末判斷：舊版冇睇星期幾，星期六日照樣掃描。
    黃金週五 17:00 ET 收市 = 香港週六 05:00，直到週一 05:00 才重開。
    """
    if config.SKIP_WEEKEND:
        if t.weekday() == 6:                    # 星期日全日休市
            return False
        if t.weekday() == 5 and t.hour >= 5:    # 星期六 05:00 後收市
            return False
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
        "spot_offset": effective_offset(),
        "live_feed": _live(),
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

    # 顯示用嘅價一律扣校正，同入場／止蝕／目標保持一致
    # （用券商真實報價時 effective_offset() = 0，即係原價顯示）
    _off = effective_offset()
    _tag = "（已校正）" if _off else ""
    reasons = [
        f"EMA20 {'高於' if trend_up else '低於'} EMA50（{'上升' if trend_up else '下降'}趨勢）",
        f"RSI(14) = {rsi:.1f}",
        f"前 20 支 K 線區間：{float(last['ll']) - _off:.2f} – {float(last['hh']) - _off:.2f}{_tag}",
    ]
    if not out["session_ok"]:
        reasons.append("⚠️ 而家唔係倫敦／紐約活躍時段，流動性差、點差會擴闊")
    _lim = config.GOLD_MAX_TRADES_PER_DAY          # 0 = 不限
    if _lim > 0 and out["today_trades"] >= _lim:
        reasons.append(f"⚠️ 今日已有 {out['today_trades']} 筆訊號（上限 {_lim} 筆），唔再出")

    # ── B. 最少穿透門檻（2026-10-02 新增）──
    # 舊版「Close > 20 支高位」就當突破，穿 $0.01 都算 —— 假訊號主要來源。
    _atr_v = float(last["atr"]) if pd.notna(last.get("atr")) else 0.0
    _need = _min_break_gap(_atr_v)
    if _need > 0:
        if broke_up and (float(last["Close"]) - float(last["hh"])) < _need:
            broke_up = False
            reasons.append("穿透 %.2f 未夠門檻 %.2f，唔當突破" % (
                float(last["Close"]) - float(last["hh"]), _need))
        if broke_dn and (float(last["ll"]) - float(last["Close"])) < _need:
            broke_dn = False
            reasons.append("穿透 %.2f 未夠門檻 %.2f，唔當突破" % (
                float(last["ll"]) - float(last["Close"]), _need))

    direction = None
    if broke_up and not prev_up:
        direction = "long"
    elif broke_dn and not prev_dn:
        direction = "short"

    # ① 趨勢過濾：跌勢唔做多、升勢唔做空。
    # 之前 trend_up / trend_dn 計完之後完全冇用過，只係塞入 reasons 當理由顯示，
    # 結果會出「下降趨勢 + 叫你做多」嘅自相矛盾訊號（2026-09-21 嗰單就係咁輸）。
    trend_conflict = bool(direction and (
        (direction == "long" and trend_dn) or (direction == "short" and trend_up)))

    # ② 追高／追低：入場價離突破位幾遠（以 ATR 計）。
    # 買喺垂直爆升嘅頂部係典型假突破陷阱。
    chase_atr = 0.0
    if direction is not None and atr:
        lvl = float(last["hh"]) if direction == "long" else float(last["ll"])
        gap = (price - lvl) if direction == "long" else (lvl - price)
        chase_atr = round(gap / atr, 2)

    blocked = (_lim > 0 and out["today_trades"] >= _lim) or not out["session_ok"]
    if config.GOLD_TREND_FILTER and trend_conflict:
        blocked = True
        reasons.append(
            "⚠️ 逆勢突破："
            + ("EMA20 低於 EMA50（下降趨勢）" if direction == "long"
               else "EMA20 高於 EMA50（上升趨勢）")
            + "，趨勢過濾擋咗呢個" + ("做多" if direction == "long" else "做空"))

    # ── C. 每日方向偏見（2026-10-02 新增）──
    _ok, _bias = _bias_ok(direction)
    out["daily_bias"] = _bias
    if not _ok:
        blocked = True
        reasons.append(
            "⚠️ 逆當日方向偏見：今日只做%s（H1 EMA 趨勢），呢個%s訊號擋咗" % (
                "升" if _bias == "long" else "跌",
                "做多" if direction == "long" else "做空"))

    out["trend_conflict"] = trend_conflict
    out["chase_atr"] = chase_atr
    out["chase_warn"] = bool(chase_atr > config.GOLD_MAX_CHASE_ATR)

    if direction is None:
        out["reasons"] = reasons
        out["state"] = (f"價位喺區間內（{float(last['ll']) - _off:.2f} – "
                        f"{float(last['hh']) - _off:.2f}{_tag}）"
                        f"，等突破。今日已出 {out['today_trades']} 筆訊號。")
        return out

    if blocked:
        out["reasons"] = reasons + ["訊號出現，但因上面原因唔推送"]
        if config.GOLD_TREND_FILTER and trend_conflict:
            out["state"] = "有突破但被趨勢過濾擋咗（逆勢唔做）"
        else:
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
    eq = config.GOLD_EQUITY_USD
    rp = config.GOLD_RISK_PCT
    lot = _pick_lot(stop_dist, eq, rp)
    oz = round(lot * config.GOLD_OZ_PER_LOT, 2)
    risk_usd = round((stop_dist + config.GOLD_SPREAD_USD) * oz, 2)
    risk_pct_actual = round(risk_usd / eq * 100, 2) if eq else 0.0
    # 你本金細（US$500）+ 最細手數 0.01（= 1 盎司），風險完全由止蝕距離決定。
    # 1.0×ATR 之下：ATR 7（正常）風險約 1.5%；ATR 15（大波動）約 3.1%。
    # 所以警報線定喺 3%：只有真正大波動日先響，嗰陣風險已經超標。
    risk_warn = bool(eq and risk_pct_actual > max(rp * 1.5, 3.0))

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
        "equity": eq,
        "risk_pct_target": rp,
        "risk_pct_actual": risk_pct_actual,
        "risk_warn": risk_warn,
        "risk_usd": risk_usd,
        "reasons": reasons + [
            f"{'升穿' if direction == 'long' else '跌穿'}前 20 支高位／低位（Donchian 突破）",
        ],
        "state": f"{'做多' if direction == 'long' else '做空'}訊號",
    })
    return out


def _pick_lot(stop_dist: float, equity: float, risk_pct: float) -> float:
    """按「本金 × 風險% ÷ 止蝕距離」計手數。

    2026-09 修正：舊版只睇止蝕距離（5 美元內就俾 0.05 手），完全唔理本金，
    結果 US$500 戶口都會被建議 0.05 手 —— 每筆風險 7.2% 本金，
    連輸 5 次就蒸發 37%。而 1:3 策略勝率得 25–35%，連輸 5 次係正常事。

    新規則：手數 = (本金 × 風險%) ÷ 止蝕距離 ÷ 每手盎司，
    向下取到 0.01（唔好超風險），再夾喺你嘅最低／最高手數之間。
    """
    lo, hi = config.GOLD_LOT_MIN, config.GOLD_LOT_MAX
    if stop_dist <= 0 or equity <= 0 or risk_pct <= 0:
        return lo
    risk_usd = equity * risk_pct / 100.0
    oz_need = risk_usd / stop_dist
    lot = oz_need / config.GOLD_OZ_PER_LOT
    lot = int(lot * 100) / 100          # 向下取，確保唔超過風險目標
    return max(lo, min(hi, round(lot, 2)))


# ------------------------------------------------------------------ 主入口
def daily_bias(refresh: bool = False):
    """當日方向偏見：每日只做一個方向（升／跌），當日固定不變。

    2026-10-02 起因：18:33 出「向下突破」→ 跟咗輸 $53.20；同日 20:45
    系統自己反手出「做多」。日內方向反覆 = 兩邊都輸。

    用 H1 圖 EMA20 vs EMA50 決定，每日只計一次並寫入 DB。
    想停用：Railway 加 GOLD_DAILY_BIAS=0
    """
    if not getattr(config, "GOLD_DAILY_BIAS", 0):
        return None
    today = datetime.now(ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d")
    try:
        init_db()
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS gold_bias"
                      "(day TEXT PRIMARY KEY, bias TEXT, note TEXT, ts TEXT)")
            if not refresh:
                row = c.execute("SELECT bias FROM gold_bias WHERE day=?", (today,)).fetchone()
                if row:
                    return row[0]
        h1 = add_indicators(fetch(interval="1h", period="60d"))
        if len(h1) < 55:
            return None
        last = h1.iloc[-2]          # 用已收盤嘅 H1 支，唔用未收盤
        bias = "long" if float(last["ema20"]) >= float(last["ema50"]) else "short"
        note = "H1 EMA20 %.2f %s EMA50 %.2f" % (
            float(last["ema20"]), "≥" if bias == "long" else "<", float(last["ema50"]))
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO gold_bias(day,bias,note,ts) VALUES(?,?,?,?)",
                      (today, bias, note,
                       datetime.now(ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d %H:%M")))
        log.info("📅 當日方向偏見：%s（%s）", bias, note)
        return bias
    except Exception:  # noqa: BLE001
        log.exception("計當日方向偏見失敗")
        return None


def _min_break_gap(atr: float) -> float:
    """最少穿透門檻（美元）。0 = 唔設門檻。"""
    try:
        pct = float(getattr(config, "GOLD_MIN_BREAK_ATR", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0.0
    return pct * atr if (pct > 0 and atr and atr > 0) else 0.0


def _bias_ok(direction: str):
    """回 (係唔係可以出, 今日偏見)。偏見未定或停用 → 一律放行。"""
    b = daily_bias()
    if not b or not direction:
        return True, b
    return (direction == b), b


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

    # ── B. 最少穿透門檻（2026-10-02 新增）──
    _atr = float(last["atr"]) if pd.notna(last.get("atr")) else 0.0
    _need = _min_break_gap(_atr)
    if _need > 0 and abs(price - level) < _need:
        return {"ok": True, "fired": False,
                "reason": "穿透 %.2f 未夠門檻 %.2f（ATR 嘅 %.0f%%）" % (
                    abs(price - level), _need, config.GOLD_MIN_BREAK_ATR * 100)}

    # ── C. 每日方向偏見（2026-10-02 新增）──
    _ok, _b = _bias_ok(direction)
    if not _ok:
        return {"ok": True, "fired": False,
                "reason": "逆當日方向偏見（今日只做%s）" % ("升" if _b == "long" else "跌")}

    key = f"{ts}|{direction}"
    init_db()
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS gold_alerts"
                  "(k TEXT PRIMARY KEY, ts TEXT, direction TEXT, price REAL)")
        if c.execute("SELECT 1 FROM gold_alerts WHERE k = ?", (key,)).fetchone():
            return {"ok": True, "fired": False, "reason": "呢支 K 線已預警過"}
        c.execute("INSERT INTO gold_alerts(k, ts, direction, price) VALUES(?,?,?,?)",
                  (key, ts, direction, price))

    now_hk = datetime.now(ZoneInfo(config.HK_TZ))
    info = {"direction": direction, "level": round(level, 2), "price": round(price, 2),
            "time": ts, "diff": round(price - level, 2),
            # 2026-09-29：新增真正嘅偵測時刻。
            # 舊版只顯示 K 線開盤時間（例如 19:00），但偵測係喺 19:12 發生，
            # 用家睇到「19:00」就以為遲咗 12 分鐘 —— 其實個通知係即時嘅。
            "detected": now_hk.strftime("%Y-%m-%d %H:%M:%S"),
            "bar_window": (t + pd.Timedelta(minutes=15)).strftime("%H:%M"),
            "live_feed": _live(),
            "daily_bias": _b,
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
