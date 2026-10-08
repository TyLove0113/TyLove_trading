"""黃金交易日記 —— 文字筆記 + 多張截圖（2026-10-07 新增、2026-10-08 改多圖）。

用戶要求：可以輸入文字日記，同埋記錄低自己做嘅 screenshot。
之前所有交易相關嘅觀察都只係喺 Telegram 傾，冇入系統。
2026-10-08：用戶反映一張圖唔夠用（M15／H1／MT4 各一張），改為支援多張。
"""
from __future__ import annotations

import json
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


def _migrate(c) -> None:
    """為舊資料庫補 images 欄位（SQLite 唔支援 ADD COLUMN IF NOT EXISTS）。"""
    cols = {r[1] for r in c.execute("PRAGMA table_info(gold_notes)")}
    if "images" not in cols:
        c.execute("ALTER TABLE gold_notes ADD COLUMN images TEXT")
        # 舊記錄：把單張 image_path 搬入 images（用 Python 做，唔倚賴 JSON1 擴充）
        rows = c.execute("SELECT id, image_path FROM gold_notes "
                         "WHERE image_path IS NOT NULL AND image_path<>''").fetchall()
        for r in rows:
            c.execute("UPDATE gold_notes SET images=? WHERE id=?",
                      (json.dumps([r[1]], ensure_ascii=False), r[0]))


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                trade_date  TEXT,
                body        TEXT,
                image_path  TEXT,
                images      TEXT,
                source      TEXT DEFAULT 'web'
            )""")
        _migrate(c)


def add(body: str = "", images=None, trade_date: str = None,
        source: str = "web") -> int:
    """寫一篇日記。images 可以係單一字串，或者一串檔名（多張圖）。"""
    if images is None:
        imgs: list = []
    elif isinstance(images, str):
        imgs = [images] if images else []
    else:
        imgs = [x for x in images if x]
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO gold_notes (created_at, trade_date, body, image_path, "
            "images, source) VALUES (?,?,?,?,?,?)",
            (_now(), trade_date or _now()[:10], (body or "").strip(),
             imgs[0] if imgs else "", json.dumps(imgs, ensure_ascii=False), source))
        return cur.lastrowid


def all_notes(limit: int = 100) -> list:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM gold_notes ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["images"] = json.loads(d.get("images") or "[]")
        except (TypeError, ValueError):
            d["images"] = []
        # 舊記錄兼容：如果 images 空空但 image_path 有值
        if not d["images"] and d.get("image_path"):
            d["images"] = [d["image_path"]]
        out.append(d)
    return out


def delete(nid: int) -> bool:
    init_db()
    with _conn() as c:
        return c.execute("DELETE FROM gold_notes WHERE id=?", (nid,)).rowcount > 0
