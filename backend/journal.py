# ─────────────────────────────────────────
# journal.py — 交易日誌
# 用 SQLite 本地存儲，部署到 Railway 後持久化
# ─────────────────────────────────────────

import sqlite3
import os
from datetime import datetime
import pytz

HK_TZ   = pytz.timezone("Asia/Hong_Kong")
DB_PATH = os.path.join(os.path.dirname(__file__), "journal.db")


def init_db():
    """初始化數據庫"""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            score       INTEGER,
            direction   TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price  REAL,
            qty         INTEGER DEFAULT 1000,
            pnl         REAL,
            pnl_pct     REAL,
            result      TEXT,
            note        TEXT,
            status      TEXT DEFAULT 'open'
        )
    """)
    conn.commit()
    conn.close()


def add_trade(symbol, score, direction, entry_price, qty=1000, note=""):
    """新增一筆模擬交易"""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    now  = datetime.now(HK_TZ).strftime("%Y-%m-%d %H:%M")
    c.execute("""
        INSERT INTO trades
        (created_at, symbol, score, direction, entry_price, qty, note, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
    """, (now, symbol, score, direction, entry_price, qty, note))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id, exit_price):
    """平倉一筆交易，計算盈虧"""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    c.execute("SELECT entry_price, qty, direction FROM trades WHERE id=?", (trade_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        return None

    entry_price, qty, direction = row

    if direction == "買入":
        pnl     = (exit_price - entry_price) * qty
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        pnl     = (entry_price - exit_price) * qty
        pnl_pct = (entry_price - exit_price) / entry_price * 100

    result = "贏" if pnl > 0 else ("輸" if pnl < 0 else "平")

    c.execute("""
        UPDATE trades
        SET exit_price=?, pnl=?, pnl_pct=?, result=?, status='closed'
        WHERE id=?
    """, (exit_price, round(pnl, 2), round(pnl_pct, 2), result, trade_id))
    conn.commit()
    conn.close()
    return {"pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "result": result}


def get_all_trades():
    """取得所有交易記錄"""
    conn   = sqlite3.connect(DB_PATH)
    c      = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY id DESC")
    rows   = c.fetchall()
    conn.close()

    cols = ["id","created_at","symbol","score","direction",
            "entry_price","exit_price","qty","pnl","pnl_pct",
            "result","note","status"]
    return [dict(zip(cols, row)) for row in rows]


def get_stats():
    """計算統計數據"""
    trades = get_all_trades()
    closed = [t for t in trades if t["status"] == "closed"]

    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0,
            "avg_win": 0, "avg_loss": 0,
        }

    wins   = [t for t in closed if t["result"] == "贏"]
    losses = [t for t in closed if t["result"] == "輸"]

    total_pnl = sum(t["pnl"] for t in closed if t["pnl"])
    avg_win   = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss  = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0

    return {
        "total":    len(closed),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win":  round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


# 初始化
init_db()