"""資料備份 / 還原。

為咩要：Railway 容器嘅檔案系統係臨時嘅 —— 如果 SQLite 唔喺 Volume
上面，每次 git push 重新部署都會清空所有持倉同交易記錄。
實測你已經中過一次（所有 gold + 股票記錄消失）。

呢個模組提供匯出 / 匯入，令你可以自己保存一份，隨時還原。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

import config

# 要備份嘅表（sqlite_master 攞唔到嘅嘢例如 index 唔需要）
SKIP = {"sqlite_sequence"}


# 自動收集嘅價格數據（唔係用戶嘅交易記錄）
_FEED_TABLES = {"feed_ticks", "feed_bars"}


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(str(config.DB_PATH)) or ".", exist_ok=True)
    c = sqlite3.connect(str(config.DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def db_status() -> dict:
    """資料庫係咪喺持久化位置。"""
    path = str(config.DB_PATH)
    persistent = path.startswith("/data") or os.getenv("DB_PERSIST_OK") == "1"
    size = os.path.getsize(path) if os.path.exists(path) else 0
    counts = {}
    try:
        with _conn() as c:
            for t in _tables(c):
                counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception:  # noqa: BLE001
        pass
    total = sum(counts.values())
    # 2026-09-30：原本只報 total_rows，但佢包埋 feed_ticks / feed_bars
    # 嘅報價數據 —— 用戶見到「555 筆」會以為係自己嘅交易記錄，
    # 其實大部分係自動收集嘅價格。所以分開兩類報。
    feed_rows = sum(n for t, n in counts.items() if t in _FEED_TABLES)
    record_rows = sum(n for t, n in counts.items() if t not in _FEED_TABLES)
    return {
        "path": path,
        "persistent": persistent,
        "size_kb": round(size / 1024, 1),
        "counts": counts,
        "total_rows": total,
        "record_rows": record_rows,
        "feed_rows": feed_rows,
        "records": {t: n for t, n in counts.items() if t not in _FEED_TABLES},
        "hint": ("✅ 資料庫喺 Volume 上面，重新部署唔會清空"
                 if persistent else
                 "🚨 資料庫唔喺 Volume 上面！每次更新程式都會清空所有記錄。"
                 "去 Railway → 服務 → Settings → Volumes 掛一個去 /data，"
                 "再喺 Variables 加 DB_PATH=/data/tylove.db。"
                 "未搞好之前，記得每次落完單都撳「匯出備份」。"),
    }


def _tables(c: sqlite3.Connection) -> list[str]:
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [r[0] for r in rows if r[0] not in SKIP]


def export_all() -> dict:
    """匯出所有表做 JSON。"""
    out = {"app": "TyLove Trading", "exported_at": datetime.now().isoformat(timespec="seconds"),
           "tables": {}}
    with _conn() as c:
        for t in _tables(c):
            rows = c.execute(f"SELECT * FROM {t}").fetchall()
            out["tables"][t] = {
                "columns": list(rows[0].keys()) if rows else [],
                "rows": [dict(r) for r in rows],
                "count": len(rows),
            }
    out["total_rows"] = sum(v["count"] for v in out["tables"].values())
    return out


def import_all(data: dict) -> dict:
    """由 JSON 還原。

    係「合併」而唔係「取代」：備份入面有嘅記錄會寫入／覆蓋，
    你目前有而備份冇嘅記錄會保留。所以還原唔會整走你之後入嘅嘢。
    """
    if not isinstance(data, dict) or "tables" not in data:
        raise ValueError("檔案格式唔啱 —— 應該係本系統匯出嘅備份 JSON")
    added: dict[str, int] = {}
    with _conn() as c:
        existing = set(_tables(c))
        for t, blob in data["tables"].items():
            if t not in existing:
                added[t] = 0
                continue
            cols = blob.get("columns") or []
            rows = blob.get("rows") or []
            if not cols or not rows:
                added[t] = 0
                continue
            ph = ",".join("?" * len(cols))
            sql = (f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) "
                   f"VALUES ({ph})")
            n = 0
            for r in rows:
                try:
                    c.execute(sql, [r.get(k) for k in cols])
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
            added[t] = n
    return {"restored": added, "total": sum(added.values())}
