"""虛擬落單（Paper Trading）—— 2026-10-22 新增。

為咩要呢個
----------
用戶親手落單，一個月大約 10 幾筆。要用統計判斷策略有冇優勢，
最少要 30–50 筆，即係要等幾個月。所以改為「系統自動虛擬落單」：
每個訊號都開一張虛擬倉，用真實後續價格自動判定輸贏，
唔需要用戶做任何事，亦絕對唔會落真單。

重要限制（必須向用戶講清楚）
--------------------------
1. 虛擬倉假設「一定成交喺訊號價」，真實會有滑點 → 結果會比真實樂觀。
2. 虛擬倉假設「止蝕一定平到」，真實滑價會蝕多啲。
3. 同一支 K 線內同時觸及止蝕同目標，一律當「輸」（保守）。
所以：虛擬結果用嚟驗證「策略邏輯有冇優勢」，唔係預測你會賺幾多。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("gold_paper")
TZ = ZoneInfo(config.HK_TZ)
MAX_BARS = 80          # 20 小時內未觸發就作廢


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_paper (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_ts   TEXT NOT NULL UNIQUE,
                direction   TEXT NOT NULL,
                entry       REAL NOT NULL,
                entry_raw   REAL NOT NULL,
                stop        REAL NOT NULL,
                target      REAL NOT NULL,
                atr         REAL NOT NULL,
                lot         REAL,
                risk_usd    REAL,
                strategy    TEXT DEFAULT 'A',
                status      TEXT NOT NULL DEFAULT 'open',
                exit_ts     TEXT,
                exit_price  REAL,
                pnl_atr     REAL,
                pnl_usd     REAL,
                created_at  TEXT NOT NULL
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_gp_status ON gold_paper(status)")


def open_from_signal(g: dict) -> dict:
    """每個新訊號開一張虛擬倉。同一支 K 線只開一次（signal_ts 唯一）。"""
    if not g.get("has_signal") or not g.get("entry"):
        return {"opened": False, "why": "no signal"}
    init_db()
    off = float(g.get("spot_offset") or 0)
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO gold_paper (signal_ts,direction,entry,entry_raw,stop,target,"
                "atr,lot,risk_usd,strategy,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(g.get("time")), g.get("direction"), float(g["entry"]),
                 float(g["entry"]) + off, float(g["stop"]) + off, float(g["target"]) + off,
                 float(g.get("atr") or 0), g.get("lot"), g.get("risk_usd"),
                 str(g.get("strategy") or "A"), _now()))
            log.info("虛擬倉已開：%s %s @ %.2f", g.get("time"), g.get("direction"), g["entry"])
            return {"opened": True}
    except sqlite3.IntegrityError:
        return {"opened": False, "why": "already opened"}


def settle(df=None) -> dict:
    """用後續 15 分鐘 K 線判定每張未平倉虛擬倉嘅輸贏。"""
    init_db()
    if df is None:
        import yfinance as yf
        df = yf.download("GC=F", period="10d", interval="15m",
                         progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return {"settled": 0, "why": "no data"}
    if getattr(df.columns, "nlevels", 1) > 1:
        df.columns = df.columns.droplevel(1)
    df = df.dropna()

    idx = list(df.index)
    hi, lo = df["High"].values, df["Low"].values
    done, still = 0, 0
    with _conn() as c:
        rows = c.execute("SELECT * FROM gold_paper WHERE status='open'").fetchall()
        for r in rows:
            ts = str(r["signal_ts"])[:16]
            pos = None
            for k, t in enumerate(idx):
                if t.tz_convert(TZ).strftime("%Y-%m-%d %H:%M") >= ts:
                    pos = k
                    break
            if pos is None:
                still += 1
                continue
            side = 1 if r["direction"] == "long" else -1
            res = None
            for j in range(pos + 1, min(pos + 1 + MAX_BARS, len(idx))):
                if side == 1:
                    if lo[j] <= r["stop"]:
                        res = ("loss", r["stop"], idx[j]); break
                    if hi[j] >= r["target"]:
                        res = ("win", r["target"], idx[j]); break
                else:
                    if hi[j] >= r["stop"]:
                        res = ("loss", r["stop"], idx[j]); break
                    if lo[j] <= r["target"]:
                        res = ("win", r["target"], idx[j]); break
            if res is None:
                still += 1
                continue
            status, price, when = res
            pnl_atr = (side * (price - r["entry_raw"]) / r["atr"]) if r["atr"] else 0.0
            oz = 100.0 * float(r["lot"] or 0)
            c.execute(
                "UPDATE gold_paper SET status=?, exit_ts=?, exit_price=?, "
                "pnl_atr=?, pnl_usd=? WHERE id=?",
                (status, when.tz_convert(TZ).strftime("%Y-%m-%d %H:%M"),
                 round(price, 2), round(pnl_atr, 3), round(pnl_atr * r["atr"] * oz, 2),
                 r["id"]))
            done += 1
        c.commit()
    log.info("虛擬倉結算：完成 %d，未觸發 %d", done, still)
    return {"settled": done, "open": still}


def stats() -> dict:
    init_db()
    with _conn() as c:
        rows = [dict(x) for x in c.execute(
            "SELECT * FROM gold_paper ORDER BY signal_ts").fetchall()]
    closed = [r for r in rows if r["status"] in ("win", "loss")]
    n = len(closed)
    if not n:
        return {"open": len(rows) - n, "closed": 0, "win_rate": None,
                "expectancy_atr": None, "total_atr": None, "by_strategy": {}}
    w = sum(1 for r in closed if r["status"] == "win")
    tot = sum(r["pnl_atr"] or 0 for r in closed)
    by = {}
    for r in closed:
        k = r.get("strategy") or "A"
        d = by.setdefault(k, {"n": 0, "win": 0, "atr": 0.0})
        d["n"] += 1
        d["win"] += 1 if r["status"] == "win" else 0
        d["atr"] += r["pnl_atr"] or 0
    for d in by.values():
        d["win_rate"] = round(100 * d["win"] / d["n"], 1)
        d["atr"] = round(d["atr"], 2)
    return {"open": len(rows) - n, "closed": n,
            "win_rate": round(100 * w / n, 1),
            "expectancy_atr": round(tot / n, 3),
            "total_atr": round(tot, 2), "by_strategy": by}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
