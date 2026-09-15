"""
持倉管理 — 用 SQLite 記住你每一筆倉，並同現價連埋
原版最大問題：journal 只係手動記帳，程式永遠唔會提你走。
新版：每次掃描都會對照現價，出「距離止蝕 / 目標仲有幾多」。
"""
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DB_PATH, HK_TZ

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

log = logging.getLogger("tylove.positions")


def _now() -> str:
    if ZoneInfo:
        return datetime.now(ZoneInfo(HK_TZ)).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT NOT NULL,
                name          TEXT,
                entry_price   REAL NOT NULL,
                qty           REAL NOT NULL,
                stop_loss     REAL NOT NULL,
                target1       REAL,
                target2       REAL,
                highest_price REAL,
                atr           REAL,
                opened_at     TEXT NOT NULL,
                closed_at     TEXT,
                exit_price    REAL,
                status        TEXT NOT NULL DEFAULT 'open',
                exit_reason   TEXT,
                note          TEXT
            );
            CREATE TABLE IF NOT EXISTS signals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT NOT NULL,
                name         TEXT,
                score        REAL,
                price        REAL,
                stop_loss    REAL,
                target1      REAL,
                target2      REAL,
                rr_ratio     REAL,
                veto         INTEGER DEFAULT 0,
                scanned_at   TEXT NOT NULL,
                outcome_pct  REAL,
                outcome_note TEXT
            );
            CREATE TABLE IF NOT EXISTS equity (
                day         TEXT PRIMARY KEY,
                start_value REAL,
                end_value   REAL,
                note        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_sig_time ON signals(scanned_at);
            """
        )


# ----------------------------------------------------------------- 持倉
def open_position(symbol, name, entry_price, qty, stop_loss,
                  target1=None, target2=None, atr=None, note="") -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (symbol,name,entry_price,qty,stop_loss,target1,target2,highest_price,atr,opened_at,status,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,'open',?)""",
            (symbol, name, entry_price, qty, stop_loss, target1, target2,
             entry_price, atr, _now(), note),
        )
        return int(cur.lastrowid)


def close_position(position_id: int, exit_price: float, reason: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE positions SET status='closed', closed_at=?, exit_price=?, exit_reason=? WHERE id=?",
            (_now(), exit_price, reason, position_id),
        )


def list_open() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY opened_at").fetchall()
    return [dict(r) for r in rows]


def list_closed(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_position_risk(position_id: int, stop_loss: float, highest_price: float) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE positions SET stop_loss=?, highest_price=MAX(COALESCE(highest_price,0),?) WHERE id=?",
            (stop_loss, highest_price, position_id),
        )


def realised_stats() -> dict:
    """已平倉統計 —— 用嚟睇你實際係咪賺錢。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status='closed' AND exit_price IS NOT NULL"
        ).fetchall()
    if not rows:
        return {"trades": 0, "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
                "expectancy_pct": 0.0, "total_pnl_hkd": 0.0}
    wins, losses, pnl = [], [], 0.0
    for r in rows:
        pct = (r["exit_price"] / r["entry_price"] - 1) * 100
        pnl += (r["exit_price"] - r["entry_price"]) * r["qty"]
        (wins if pct > 0 else losses).append(pct)
    n = len(rows)
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    return {"trades": n, "win_rate": round(wr, 1), "avg_win_pct": round(aw, 2),
            "avg_loss_pct": round(al, 2),
            "expectancy_pct": round(wr / 100 * aw + (1 - wr / 100) * al, 2),
            "total_pnl_hkd": round(pnl, 0)}


# ----------------------------------------------------------------- 訊號
def log_signal(sig: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO signals
               (symbol,name,score,price,stop_loss,target1,target2,rr_ratio,veto,scanned_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sig["symbol"], sig.get("name", ""), sig.get("total_score"), sig.get("price"),
             sig.get("stop_loss"), sig.get("target1"), sig.get("target2"),
             sig.get("rr_ratio"), 1 if sig.get("veto") else 0, _now()),
        )


def recent_signals(limit: int = 40) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── 自動初始化：確保首次執行時資料表一定存在（避免 no such table）──
_AUTO_INIT = True
init_db()
