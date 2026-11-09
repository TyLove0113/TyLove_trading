"""每日觀察記錄 —— 2026-11-09 加。

對應用戶見到嘅 YouTube「七天觀察」練習：
  每日問一句「今日有明顯嘅方向嗎？」，記錄落嚟。

設計原則：
  · 純記錄 —— 唔影響訊號、唔影響落單、唔改策略。
  · 用 MT4 真實報價（feed）優先，冇才回落 yfinance。
  · 同一日只記一次（UPSERT），重跑唔會重複。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("gold_observe")
TZ = ZoneInfo(config.HK_TZ)

FLAT_EFF = 0.50      # 效率低於此 = 冇明顯方向


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_observe (
                date        TEXT PRIMARY KEY,
                o REAL, h REAL, l REAL, c REAL,
                rng REAL, move REAL, eff REAL,
                verdict     TEXT NOT NULL,
                bars        INTEGER,
                sig_count   INTEGER DEFAULT 0,
                source      TEXT,
                created_at  TEXT NOT NULL
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_obs_date ON gold_observe(date)")


def _bars(days_back: int = 60):
    """攞 M15 K 線：先試 MT4 真實報價，唔得才用 yfinance。"""
    import pandas as pd

    try:
        import feed
        d = feed.bars(15, max(2000, days_back * 100))
        if d is not None and len(d) >= 40:
            return d.rename(columns={"o": "Open", "h": "High",
                                     "l": "Low", "c": "Close"}), "mt4"
    except Exception:  # noqa: BLE001
        log.debug("讀 MT4 K 線失敗，回落 yfinance", exc_info=True)

    import yfinance as yf
    d = yf.download("GC=F", period=f"{max(days_back, 5)}d", interval="15m",
                    progress=False, auto_adjust=False)
    if d is None or len(d) == 0:
        return None, None
    if getattr(d.columns, "nlevels", 1) > 1:
        d.columns = d.columns.droplevel(1)
    d = d.dropna()
    d.index = d.index.tz_convert(config.HK_TZ)
    return d[["Open", "High", "Low", "Close"]], "yf"


def _sig_count(date_str: str) -> int:
    """該日（香港時間）有幾多個正式訊號。"""
    try:
        import gold
        gold.init_db()
        with gold._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) AS n FROM gold_signals WHERE substr(ts,1,10)=?",
                (date_str,)).fetchone()
        return int(r["n"] or 0)
    except Exception:  # noqa: BLE001
        return 0


def record_day(date_str: str | None = None, push: bool = False) -> dict:
    """記錄某一日嘅觀察（預設今日，香港時間）。"""
    init_db()
    if date_str is None:
        date_str = datetime.now(TZ).strftime("%Y-%m-%d")

    df, src = _bars(60)
    if df is None:
        return {"ok": False, "why": "冇 K 線數據"}

    d = df[df.index.strftime("%Y-%m-%d") == date_str]
    if len(d) < 20:
        return {"ok": False, "why": f"{date_str} 只有 {len(d)} 支 K 線，未夠"}

    o = float(d["Open"].iloc[0]); c_ = float(d["Close"].iloc[-1])
    h = float(d["High"].max());   l = float(d["Low"].min())
    rng = h - l
    move = abs(c_ - o)
    eff = (move / rng) if rng > 0 else 0.0
    verdict = "dir" if eff > FLAT_EFF else "flat"
    n_sig = _sig_count(date_str)

    with _conn() as con:
        con.execute(
            "INSERT INTO gold_observe (date,o,h,l,c,rng,move,eff,verdict,bars,"
            "sig_count,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET o=excluded.o,h=excluded.h,"
            "l=excluded.l,c=excluded.c,rng=excluded.rng,move=excluded.move,"
            "eff=excluded.eff,verdict=excluded.verdict,bars=excluded.bars,"
            "sig_count=excluded.sig_count,source=excluded.source",
            (date_str, round(o, 2), round(h, 2), round(l, 2), round(c_, 2),
             round(rng, 2), round(move, 2), round(eff, 3), verdict, len(d),
             n_sig, src, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")))

    out = {"ok": True, "date": date_str, "verdict": verdict,
           "eff": round(eff, 2), "rng": round(rng, 2), "move": round(move, 2),
           "bars": len(d), "sig_count": n_sig, "source": src,
           "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c_, 2)}

    if push:
        try:
            import notifier
            notifier.push(notifier.fmt_observe(out, stats(7)), channel="gold")
        except Exception:  # noqa: BLE001
            log.exception("觀察記錄推送失敗")
    return out


def backfill(days: int = 30) -> dict:
    """補回過去 N 日（用同一份 K 線，一次過）。"""
    init_db()
    df, src = _bars(days + 10)
    if df is None:
        return {"ok": False, "why": "冇 K 線數據"}
    have = {r["date"] for r in _conn().execute(
        "SELECT date FROM gold_observe").fetchall()}
    days_list = sorted(set(df.index.strftime("%Y-%m-%d")))[-days:]
    done = 0
    for ds in days_list:
        if ds in have:
            continue
        r = record_day(ds)
        if r.get("ok"):
            done += 1
    return {"ok": True, "filled": done, "total": len(days_list), "source": src}


def stats(days: int = 7) -> dict:
    """最近 N 日統計。"""
    init_db()
    with _conn() as c:
        rows = [dict(x) for x in c.execute(
            "SELECT * FROM gold_observe ORDER BY date DESC LIMIT ?",
            (int(days),)).fetchall()]
    if not rows:
        return {"days": 0, "dir": 0, "flat": 0, "dir_pct": None,
                "avg_rng": None, "avg_move": None, "sig_total": 0, "recent": []}
    n_dir = sum(1 for r in rows if r["verdict"] == "dir")
    return {
        "days": len(rows),
        "dir": n_dir,
        "flat": len(rows) - n_dir,
        "dir_pct": round(100 * n_dir / len(rows), 1),
        "avg_rng": round(sum(r["rng"] or 0 for r in rows) / len(rows), 2),
        "avg_move": round(sum(r["move"] or 0 for r in rows) / len(rows), 2),
        "sig_total": sum(r["sig_count"] or 0 for r in rows),
        "recent": rows,
    }


def all_rows(limit: int = 60) -> dict:
    init_db()
    with _conn() as c:
        rows = [dict(x) for x in c.execute(
            "SELECT * FROM gold_observe ORDER BY date DESC LIMIT ?",
            (int(limit),)).fetchall()]
    return {"ok": True, "rows": rows, "stats": stats(limit)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(backfill(30))
    print(stats(7))
