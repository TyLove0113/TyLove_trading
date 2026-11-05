"""券商成交記錄 —— 由 MT4 EA 推送過嚟。

2026-09-30 新增（用戶選「方案 A」）。

重點：**唔會自動寫入日誌。** 收到嘅成交會開一張 status='draft' 嘅草稿，
用戶喺網頁核對完撳「確認」先正式入帳 —— 因為日誌係將來判斷策略有冇
優勢嘅唯一材料，唔可以畀錯資料污染。

MT4 嘅成交歷史存喺券商伺服器，桌面版會下載返，所以用手機落嘅單
桌面版 EA 一樣讀得到。
"""
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("tylove.deals")

DB_PATH = config.DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mt4_deals (
    ticket      INTEGER PRIMARY KEY,
    symbol      TEXT,
    direction   TEXT,
    lots        REAL,
    open_ts     INTEGER,
    open_price  REAL,
    close_ts    INTEGER,
    close_price REAL,
    sl          REAL,
    tp          REAL,
    profit      REAL,
    comment     TEXT,
    recv_at     TEXT NOT NULL,
    journal_id  INTEGER          -- 連去 gold_trades.id（NULL = 未確認）
)
"""


def _conn():
    import journal
    journal.init_db()
    return journal._conn()


def init_db() -> None:
    try:
        with _conn() as c:
            c.execute(_SCHEMA)
    except sqlite3.Error:
        log.exception("建立 mt4_deals 失敗")


def migrate_utc_to_hk() -> int:
    """一次性：把舊記錄嘅 UTC 時間轉成香港時間（2026-11-04）。

    只處理 data_source='mt4' 嘅記錄 —— 即 EA 推過嚟嘅，
    呢批一定係 UTC。手動輸入嘅唔會動。
    用 settings 表做記號，只跑一次。
    """
    import journal
    try:
        with journal._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS mig_flags("
                      "name TEXT PRIMARY KEY, done_at TEXT)")
            if c.execute("SELECT 1 FROM mig_flags WHERE name=?",
                         ("utc2hk_v1",)).fetchone():
                return 0
            rows = c.execute(
                "SELECT id, opened_at, closed_at FROM gold_trades "
                "WHERE data_source='mt4' AND opened_at IS NOT NULL").fetchall()
            n = 0
            for r in rows:
                def _pd(v):
                    """寬鬆解析：有秒／冇秒都食（2026-11-04）。"""
                    if not v:
                        return None
                    t = str(v).replace("T", " ").strip()[:19]
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                        try:
                            return datetime.strptime(t, fmt)
                        except ValueError:
                            continue
                    return None

                try:
                    o = _pd(r[1])
                    if o is None:
                        continue
                    no = (o + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                    nc = None
                    cl = _pd(r[2])
                    if cl is not None:
                        nc = (cl + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                    c.execute("UPDATE gold_trades SET opened_at=?, closed_at=? WHERE id=?",
                              (no, nc, r[0]))
                    n += 1
                except Exception:  # noqa: BLE001
                    continue
            c.execute("INSERT OR REPLACE INTO mig_flags(name, done_at) VALUES(?,?)",
                      ("utc2hk_v1", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            if n:
                log.info("已把 %d 筆 MT4 記錄由 UTC 轉成香港時間", n)
            return n
    except Exception:  # noqa: BLE001
        log.exception("時間遷移失敗")
        return 0


def _iso(ts) -> str:
    """MT4 時間戳 → 香港時間字串（2026-11-04 修）。

    原本直接輸出 UTC，令 MT4 顯示 09:21（券商伺服器 GMT+2）嘅單
    喺系統變成 07:21，用戶以為時間亂咗。而家統一轉成香港時間。
    """
    if not ts:
        return ""
    try:
        import config
        hk = ZoneInfo(config.HK_TZ)
    except Exception:  # noqa: BLE001
        hk = timezone.utc
    return datetime.fromtimestamp(int(ts), timezone.utc).astimezone(hk).strftime(
        "%Y-%m-%d %H:%M:%S")


def ingest(deals: list) -> dict:
    """收 EA 推過嚟嘅成交。新成交 → 開草稿日誌等確認。

    回傳 {"ok", "new", "draft_ids"}

    注意：一定要分兩段開連線。第一段淨係寫 mt4_deals 然後閂咗，
    第二段先呼叫 journal.add_trade —— 否則會 "database is locked"。
    """
    init_db()
    new_rows = []
    try:
        with _conn() as c:
            for d in deals or []:
                tk = d.get("ticket")
                if not tk:
                    continue
                if c.execute("SELECT 1 FROM mt4_deals WHERE ticket=?",
                             (int(tk),)).fetchone():
                    continue        # 已經收過（EA 每次推最後 N 筆，會重複）
                c.execute(
                    "INSERT INTO mt4_deals(ticket,symbol,direction,lots,open_ts,"
                    "open_price,close_ts,close_price,sl,tp,profit,comment,recv_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (int(tk), d.get("symbol"), d.get("direction"),
                     d.get("lots"), d.get("open_ts"), d.get("open_price"),
                     d.get("close_ts"), d.get("close_price"), d.get("sl"),
                     d.get("tp"), d.get("profit"), (d.get("comment") or "")[:80]))
                new_rows.append(d)
    except sqlite3.Error:
        log.exception("寫入成交記錄失敗")
        return {"ok": False, "error": "寫入失敗"}

    if not new_rows:
        return {"ok": True, "new": 0, "draft_ids": []}

    # 第二段：開草稿（用另一條連線，避免鎖死）
    import journal
    drafts = []
    for d in new_rows:
        try:
            jid = journal.add_trade(_to_journal(d))
            drafts.append(jid)
            with _conn() as c:
                c.execute("UPDATE mt4_deals SET journal_id=? WHERE ticket=?",
                          (jid, int(d["ticket"])))
        except sqlite3.Error:
            log.exception("開草稿失敗 ticket=%s", d.get("ticket"))

    log.info("收到 %d 筆新成交，開咗 %d 張草稿等確認", len(new_rows), len(drafts))
    return {"ok": True, "new": len(new_rows), "draft_ids": drafts}


def _to_journal(d: dict) -> dict:
    """將 MT4 成交轉成日誌欄位。全部係券商真實數字，零猜測。"""
    pnl = d.get("profit")
    return {
        "direction": "long" if d.get("direction") == "buy" else "short",
        "actual_entry": d.get("open_price"),
        "lot": d.get("lots"),
        "stop_loss": d.get("sl") or None,
        "take_profit": d.get("tp") or None,
        "exit_price": d.get("close_price"),
        "pnl_usd": pnl,
        "data_source": "mt4",
        "status": "draft",          # ← 關鍵：草稿，等你確認
        "note": "由 MT4 自動讀入，待確認",
        "entered_at": _iso(d.get("open_ts")),
        "closed_at": _iso(d.get("close_ts")),
    }


def recent(n: int = 20) -> list:
    """最近收到嘅成交記錄。"""
    try:
        init_db()
        with _conn() as c:
            rs = c.execute(
                "SELECT * FROM mt4_deals ORDER BY close_ts DESC LIMIT ?",
                (n,)).fetchall()
        return [dict(r) for r in rs]
    except sqlite3.Error:
        log.exception("讀取成交記錄失敗")
        return []


def stats() -> dict:
    try:
        init_db()
        with _conn() as c:
            r = c.execute(
                "SELECT COUNT(*) n, MIN(close_ts) t0, MAX(close_ts) t1, "
                "COALESCE(SUM(profit),0) pnl FROM mt4_deals").fetchone()
            pend = c.execute("SELECT COUNT(*) n FROM gold_trades WHERE status='draft'"
                             ).fetchone()
        return {"deals": r["n"] or 0, "pnl": round(r["pnl"] or 0, 2),
                "first": _iso(r["t0"]), "last": _iso(r["t1"]),
                "pending": pend["n"] or 0}
    except sqlite3.Error:
        log.exception("讀取成交統計失敗")
        return {"deals": 0, "pnl": 0, "pending": 0}
