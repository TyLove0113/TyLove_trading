"""真實報價接收層 —— 收你 MT4 / MT5 推送過嚟嘅券商報價。

【為咩要呢個模組】
yfinance 只有 COMEX 期貨（GC=F），同你 MT4 嘅現貨 XAUUSD 相差 US$20–35，
而且差距會漂移 —— 所以價位永遠對唔上，要靠「校正值」估。

呢個模組直接收你券商嘅 bid/ask / K 線，由源頭解決：
數據同落單用同一個 feed，價位 100% 對得上，唔需要校正。

【設計原則】
- 冇收到報價時自動回落到 yfinance，唔會令系統停擺。
- K 線用 (symbol, interval, ts) 做主鍵，EA 重推同一支唔會變重複。
- 只保留最近 N 支，避免 Volume 無限增長。
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import config

log = logging.getLogger("tylove.feed")

KEEP_BARS = config.KEEP_BARS   # 每個 (symbol, interval) 最多保留幾多支


def _conn():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS feed_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                bid REAL, ask REAL,
                source TEXT,
                tick_ts TEXT,
                recv_ts TEXT NOT NULL
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tick_recv ON feed_ticks(recv_ts)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS feed_bars (
                symbol TEXT NOT NULL,
                interval INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                o REAL, h REAL, l REAL, c REAL,
                source TEXT,
                recv_ts TEXT,
                PRIMARY KEY (symbol, interval, ts)
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bar_lookup ON feed_bars(symbol, interval, ts)")


def _now_hk() -> datetime:
    return datetime.now(ZoneInfo(config.HK_TZ))


# ------------------------------------------------------------------ 寫入
def save_tick(symbol: str, bid: float, ask: float, source: str = "mt4",
              tick_ts: str = None) -> dict:
    """收一個報價。"""
    init_db()
    now = _now_hk().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "INSERT INTO feed_ticks(symbol, bid, ask, source, tick_ts, recv_ts) "
            "VALUES(?,?,?,?,?,?)",
            (symbol, float(bid), float(ask), source, tick_ts, now))
    mid = (float(bid) + float(ask)) / 2
    return {"symbol": symbol, "bid": float(bid), "ask": float(ask),
            "mid": round(mid, 3), "spread": round(float(ask) - float(bid), 3),
            "recv_ts": now}


def save_bars(symbol: str, interval: int, bars: list, source: str = "mt4") -> int:
    """收一批 K 線並寫入（重複會覆蓋）。回傳寫入支數。"""
    if not bars:
        return 0
    init_db()
    now = _now_hk().isoformat(timespec="seconds")
    rows = []
    for b in bars:
        try:
            rows.append((symbol, int(interval), int(b["t"]),
                         float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]),
                         source, now))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    with _conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO feed_bars"
            "(symbol, interval, ts, o, h, l, c, source, recv_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        # 只保留最近 KEEP_BARS 支
        c.execute("""
            DELETE FROM feed_bars
             WHERE symbol = ? AND interval = ? AND ts NOT IN (
                SELECT ts FROM feed_bars WHERE symbol = ? AND interval = ?
                ORDER BY ts DESC LIMIT ?)""",
                  (symbol, int(interval), symbol, int(interval), KEEP_BARS))
    return len(rows)


def prune_ticks(keep: int = None):
    """只保留最近 N 個報價，避免資料庫無限變大。

    2026-09-30：原本 hardcode keep=5000 —— EA 每 5 秒推一個 tick，
    即係只保留 6.9 小時，長期真實數據永遠儲唔到。改用 config.KEEP_TICKS。
    """
    if keep is None:
        keep = config.KEEP_TICKS
    try:
        with _conn() as c:
            c.execute("""DELETE FROM feed_ticks WHERE id NOT IN
                         (SELECT id FROM feed_ticks ORDER BY id DESC LIMIT ?)""", (keep,))
    except sqlite3.Error:
        log.exception("清理報價失敗")


# ------------------------------------------------------------------ 讀取
def latest_tick() -> dict | None:
    try:
        init_db()
        with _conn() as c:
            r = c.execute(
                "SELECT * FROM feed_ticks ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None
    except sqlite3.Error:
        return None


def latest_bar_time() -> datetime | None:
    try:
        init_db()
        with _conn() as c:
            r = c.execute(
                "SELECT MAX(ts) AS ts FROM feed_bars").fetchone()
        if r and r["ts"]:
            return datetime.fromtimestamp(int(r["ts"]), ZoneInfo(config.HK_TZ))
    except sqlite3.Error:
        pass
    return None


def bars(interval: int = 15, limit: int = 800) -> pd.DataFrame | None:
    """由真實報價砌返一個 DataFrame（同 gold.fetch() 同一個格式）。"""
    try:
        init_db()
        with _conn() as c:
            rows = c.execute(
                "SELECT ts, o, h, l, c FROM feed_bars WHERE interval = ? "
                "ORDER BY ts DESC LIMIT ?", (int(interval), int(limit))).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(config.HK_TZ)
    df = df.set_index("dt")[["o", "h", "l", "c"]]
    df.columns = ["Open", "High", "Low", "Close"]
    return df.sort_index()


def bar_age_min() -> float | None:
    t = latest_bar_time()
    if t is None:
        return None
    return (_now_hk() - t).total_seconds() / 60


def is_live() -> bool:
    """真實報價係唔係新鮮到可以用？（K 線仍然夠新）"""
    if config.GOLD_FORCE_YFINANCE:
        return False
    age = bar_age_min()
    if age is None:
        return False
    return age <= config.GOLD_FEED_MAX_AGE_MIN


def status() -> dict:
    """畀網頁顯示：EA 連線狀態、最後報價幾時。"""
    tick = latest_tick()
    age = bar_age_min()
    tick_age = None
    if tick:
        try:
            tick_age = round((_now_hk() - datetime.fromisoformat(
                tick["recv_ts"])).total_seconds(), 1)
        except (ValueError, TypeError):
            tick_age = None
    n_bars = 0
    try:
        with _conn() as c:
            n_bars = c.execute("SELECT COUNT(*) AS n FROM feed_bars").fetchone()["n"]
    except sqlite3.Error:
        pass
    live = is_live()
    # 分辨三種情況，唔好一律顯示紅色 —— 咁樣先診斷得到
    if live:
        state = "ok"
    elif tick_age is not None and tick_age < 150:
        state = "bars_stale"      # EA 通（有報價），但 K 線推送停咗
    elif tick_age is not None:
        state = "stale"           # 有紀錄但都好舊
    else:
        state = "offline"         # 完全冇收過嘢

    if state == "ok":
        hint = "✅ 用緊你券商嘅真實報價 —— 價位同 MT4 完全一致"
    elif state == "bars_stale":
        hint = (f"⚠️ EA 有報價入嚟（{tick_age:.0f} 秒前），但 K 線已經 "
                f"{age:.0f} 分鐘冇更新。EA 應該係掛住但 K 線推送停咗 —— "
                f"試下重新拖 EA 落圖表，或者睇 EA 左上角有冇紅字。")
    elif state == "stale":
        hint = (f"⚠️ 最後收到係 {tick_age / 60:.0f} 分鐘前。"
                f"EA 可能已經停咗或者電腦休眠咗。")
    else:
        hint = ("⚠️ 完全未收過券商報價，暫時用 yfinance 期貨"
                "（價位有 US$20–35 偏差）。睇 zip 入面 tools/設定指示.md。")

    return {
        "live": live,
        "state": state,
        "source": "券商真實報價" if live else f"yfinance 期貨（{config.GOLD_SYMBOL}）",
        "tick": tick,
        "tick_age_sec": tick_age,
        "bar_age_min": round(age, 1) if age is not None else None,
        "bars_count": n_bars,
        "max_age_min": config.GOLD_FEED_MAX_AGE_MIN,
        "hint": hint,
    }

def archive() -> dict:
    """券商真實數據累積情況 —— 用嚟追蹤儲咗幾多可以回測嘅歷史。

    2026-09-30 新增。你嘅目標係累積真實 MT4 數據去取代 yfinance，
    所以需要一眼睇到「儲咗幾多日」。
    """
    try:
        init_db()
        with _conn() as c:
            b = c.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS t0, MAX(ts) AS t1 "
                "FROM feed_bars WHERE source='mt4'").fetchone()
            # feed_ticks 冇 ts 欄，時間存喺 recv_ts（TEXT）
            k = c.execute(
                "SELECT COUNT(*) AS n, MIN(recv_ts) AS t0, MAX(recv_ts) AS t1 "
                "FROM feed_ticks").fetchone()
    except sqlite3.Error:
        log.exception("讀取累積統計失敗")
        return {"ok": False}

    def span(row):
        """t0/t1 可能係 unix 秒（feed_bars）或者 ISO 字串（feed_ticks）。"""
        if not row or row["t0"] in (None, "") or row["t1"] in (None, ""):
            return 0.0
        a, b = row["t0"], row["t1"]
        try:
            if isinstance(a, str):
                a = datetime.fromisoformat(a.replace("Z", "+00:00")).timestamp()
                b = datetime.fromisoformat(b.replace("Z", "+00:00")).timestamp()
            return round((float(b) - float(a)) / 86400.0, 1)
        except Exception:  # noqa: BLE001
            return 0.0

    def fmt(v):
        """feed_bars 存 unix 秒；feed_ticks 存 ISO 字串。兩個都要處理。"""
        if not v:
            return None
        try:
            if isinstance(v, str):
                d = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=ZoneInfo("UTC"))
                return d.astimezone(
                    ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d %H:%M")
            return datetime.fromtimestamp(
                float(v), ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return None

    n_bars, n_ticks = (b["n"] or 0), (k["n"] or 0)
    bd, kd = span(b), span(k)
    return {
        "ok": True,
        "bars": n_bars,
        "bars_days": bd,
        "bars_from": fmt(b["t0"]),
        "ticks": n_ticks,
        "ticks_days": kd,
        "ticks_from": fmt(k["t0"]),
        "bars_limit": config.KEEP_BARS,
        "ticks_limit": config.KEEP_TICKS,
        # 有幾多日真實數據「已經夠做一次像樣嘅回測」
        "ready": bd >= 14,
        "progress_pct": min(100, round(bd / 14 * 100)) if bd else 0,
        "hint": (
            "✅ 已經儲夠 14 日真實券商數據 —— 可以用真價重跑回測，"
            "取代 yfinance 期貨。"
            if bd >= 14 else
            f"⏳ 儲到 {bd:.1f} / 14 日。要 14 日先夠做一次像樣嘅樣本外回測"
            f"（{max(0, 14 - bd):.0f} 日後達標）。"
            "呢段時間唔需要做任何嘢 —— EA 開住就會自動累積。"
            if n_bars else
            "⚠️ 仲未收到券商 K 線。確認 EA 掛緊、圖表左上角寫 v2.1。"
        ),
    }
