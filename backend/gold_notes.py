"""黃金交易日記 —— 文字筆記 + 截圖（2026-10-07 新增）。

用戶要求：可以輸入文字日記，同埋記錄低自己做嘅 screenshot。
之前所有交易相關嘅觀察都只係喺 Telegram 傾，冇入系統。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import config


def _now() -> str:
    return datetime.now(ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                trade_date  TEXT,
                body        TEXT,
                image_path  TEXT,
                source      TEXT DEFAULT 'web'
            )""")


def add(body: str = "", image_path: str = "", trade_date: str = None,
        source: str = "web") -> int:
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO gold_notes (created_at, trade_date, body, image_path, source) "
            "VALUES (?,?,?,?,?)",
            (_now(), trade_date or _now()[:10], (body or "").strip(), image_path, source))
        return cur.lastrowid


def all_notes(limit: int = 100) -> list:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM gold_notes ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]


def delete(nid: int) -> bool:
    init_db()
    with _conn() as c:
        return c.execute("DELETE FROM gold_notes WHERE id=?", (nid,)).rowcount > 0
