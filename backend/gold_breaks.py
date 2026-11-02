"""假突破記錄 —— 2026-11-02 加。

用戶要求：純記錄，唔改策略、唔落單、唔推送。
目的：累積「預警發出後 K 線收盤係真突破定假突破」嘅真實樣本，
      將來用數據（而唔係感覺）判斷「反彈係否可捕捉」。

流程：
  ① 預警發出（early_alert）→ 插入 pending 記錄
  ② 之後每次掃描 → 檢查該 K 線已收盤未
  ③ 收盤仍突破 = real（真信號）；彈返 = fake（假突破）
  ④ 順便記低收盤價，計反彈幅度
"""
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import config

log = logging.getLogger("tylove.breaks")


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_breaks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_ts    TEXT NOT NULL,
                direction   TEXT NOT NULL,
                level       REAL NOT NULL,
                price       REAL,
                close_price REAL,
                verdict     TEXT DEFAULT 'pending',
                move_after  REAL,
                created_at  TEXT,
                settled_at  TEXT,
                UNIQUE(alert_ts, direction)
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_gb_v ON gold_breaks(verdict)")


def record(info: dict) -> bool:
    """預警發出時呼叫 —— 記一筆 pending。重複同一支 K 線唔會插入。"""
    try:
        init_db()
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO gold_breaks"
                "(alert_ts,direction,level,price,created_at) VALUES(?,?,?,?,?)",
                (str(info.get("time")), str(info.get("direction")),
                 float(info.get("level") or 0), float(info.get("price") or 0),
                 datetime.now(ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d %H:%M:%S")))
        return True
    except Exception:  # noqa: BLE001
        log.exception("記錄假突破失敗")
        return False


def settle() -> dict:
    """檢查 pending 記錄，用已收盤 K 線判定真／假突破。"""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM gold_breaks WHERE verdict='pending'").fetchall()
    if not rows:
        return {"checked": 0, "real": 0, "fake": 0}

    try:
        import gold
        df = gold.add_indicators(gold._use_closed(gold.fetch()))
    except Exception:  # noqa: BLE001
        log.exception("假突破結算：取數據失敗")
        return {"checked": 0, "error": "no data"}

    real = fake = 0
    now = datetime.now(ZoneInfo(config.HK_TZ))
    with _conn() as c:
        for r in rows:
            try:
                ts = pd.Timestamp(r["alert_ts"]).tz_localize(config.HK_TZ) \
                    if pd.Timestamp(r["alert_ts"]).tz is None else pd.Timestamp(r["alert_ts"])
            except Exception:  # noqa: BLE001
                continue
            # 等 K 線收盤 + 2 分鐘寬限
            if now < (ts + pd.Timedelta(minutes=17)).to_pydatetime():
                continue
            hit = df[df.index >= ts]
            if hit.empty:
                continue
            close = float(hit.iloc[0]["Close"])
            lvl = float(r["level"])
            if r["direction"] == "long":
                ok = close > lvl
                move = close - lvl
            else:
                ok = close < lvl
                move = lvl - close
            verdict = "real" if ok else "fake"
            c.execute("UPDATE gold_breaks SET close_price=?, verdict=?, "
                      "move_after=?, settled_at=? WHERE id=?",
                      (round(close, 2), verdict, round(move, 2),
                       now.strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
            real += ok
            fake += (not ok)
    return {"checked": len(rows), "real": real, "fake": fake}


def stats(limit: int = 300) -> dict:
    """統計 + 明細 —— 給網頁顯示。"""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM gold_breaks ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
        agg = c.execute(
            "SELECT verdict, COUNT(*) n FROM gold_breaks "
            "WHERE verdict!='pending' GROUP BY verdict").fetchall()
    d = {r["verdict"]: r["n"] for r in agg}
    real, fake = d.get("real", 0), d.get("fake", 0)
    tot = real + fake
    mv = [abs(float(r["move_after"] or 0)) for r in rows
          if r["verdict"] == "fake"]
    return {
        "ok": True, "real": real, "fake": fake,
        "pending": sum(1 for r in rows if r["verdict"] == "pending"),
        "total": tot,
        "fake_pct": round(100 * fake / tot, 1) if tot else None,
        "avg_fake_move": round(sum(mv) / len(mv), 2) if mv else None,
        "recent": [{"ts": r["alert_ts"], "dir": r["direction"],
                    "level": r["level"], "verdict": r["verdict"],
                    "move": r["move_after"]} for r in rows[:60]],
    }
