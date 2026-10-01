"""黃金交易日誌 —— 記錄「執行偏差」，唔止記錄賺蝕。

點解要記呢三樣：
  signal_entry vs actual_entry  → 量度你自己嘅執行偏差（滑價）
  spread_at_entry              → 真實交易成本，將來重跑回測要用
  followed                     → 你係咪真係跟足訊號（早入／遲入／改止蝕）

賺蝕只係結果；呢三樣先係你將來 debug「點解策略唔 work」嘅材料。
"""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import config

FOLLOW_OPTIONS = {
    "yes":         "✅ 完全跟足",
    "early":       "⏩ 早入（未到價就入）",
    "late":        "🐢 遲入（遲咗先入）",
    "chase":       "🚨 追高／追低",
    "changed_sl":  "✋ 改過止蝕",
    "early_exit":  "🚪 提早平倉",
    "partial":     "➗ 只做部分手數",
    "manual":      "🖐 完全自己判斷",
}


def _now() -> str:
    return datetime.now(ZoneInfo(config.HK_TZ)).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at       TEXT NOT NULL,
                closed_at       TEXT,
                direction       TEXT NOT NULL,
                signal_entry    REAL,
                actual_entry    REAL NOT NULL,
                signal_stop     REAL,
                signal_target   REAL,
                actual_stop     REAL,
                lot             REAL,
                spread_at_entry REAL,
                exit_price      REAL,
                pnl_usd         REAL,
                followed        TEXT NOT NULL DEFAULT 'yes',
                note            TEXT,
                status          TEXT NOT NULL DEFAULT 'open',
    data_source     TEXT,         -- mt4 = 券商真實報價 / yfin = 期貨延遲
                shot            TEXT,         -- 截圖檔名（shots/ 目錄）
                ticket          TEXT          -- MT4 訂單號（截圖匯入用嚟去重）

            )""")
        _migrate(c)
        c.execute("CREATE INDEX IF NOT EXISTS ix_gt_status ON gold_trades(status)")
        c.execute("""CREATE TABLE IF NOT EXISTS ocr_imported (
                ticket TEXT PRIMARY KEY,
                journal_id INTEGER,
                imported_at TEXT NOT NULL
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_gt_opened ON gold_trades(opened_at)")
def _f(v):
    """安全轉 float；空白或 None 回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None



def _migrate(c) -> None:
    """為舊資料庫補新欄位（SQLite 唔支援 ADD COLUMN IF NOT EXISTS）。"""
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(gold_trades)")}
        if "data_source" not in cols:
            c.execute("ALTER TABLE gold_trades ADD COLUMN data_source TEXT")
            log.info("已為 gold_trades 加入 data_source 欄位")
        if "shot" not in cols:
            c.execute("ALTER TABLE gold_trades ADD COLUMN shot TEXT")
            log.info("已為 gold_trades 加入 shot 欄位")
        if "ticket" not in cols:
            c.execute("ALTER TABLE gold_trades ADD COLUMN ticket TEXT")
            log.info("已為 gold_trades 加入 ticket 欄位")
    except sqlite3.Error:
        log.exception("遷移失敗")


def add_trade(d: dict) -> int:
    """新增一筆交易記錄。actual_entry 同 direction 係必填。"""
    direction = (d.get("direction") or "").strip().lower()
    if direction not in ("long", "short"):
        raise ValueError("direction 一定要係 long 或 short")
    actual = _f(d.get("actual_entry"))
    if actual is None:
        raise ValueError("一定要填實際入場價")

    followed = (d.get("followed") or "yes").strip()
    if followed not in FOLLOW_OPTIONS:
        followed = "yes"

    with _conn() as c:
        cur = c.execute(
            # 2026-09-30：status 原本寫死 'open'。改為可傳入 ——
            # 由 MT4 自動讀入嘅成交係 'draft'（草稿），要用戶確認先入帳；
            # 已平倉嘅成交會連埋 closed_at / exit_price / pnl_usd 一齊寫。
            """INSERT INTO gold_trades
               (opened_at, direction, signal_entry, actual_entry,
                signal_stop, signal_target, actual_stop, lot,
                spread_at_entry, followed, note, status, data_source,
                closed_at, exit_price, pnl_usd, shot, ticket)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ((d.get("opened_at") or d.get("entered_at") or "").strip() or _now(),
             direction,
             _f(d.get("signal_entry")), actual,
             _f(d.get("signal_stop")), _f(d.get("signal_target")),
             _f(d.get("actual_stop")), _f(d.get("lot")),
             _f(d.get("spread_at_entry")), followed,
             (d.get("note") or "").strip() or None,
             (d.get("status") or "open").strip(),
             (d.get("data_source") or "").strip() or None,
             (d.get("closed_at") or "").strip() or None,
             _f(d.get("exit_price")),
             (None if d.get("pnl_usd") in (None, "") else float(d.get("pnl_usd"))),
             (d.get("shot") or "").strip() or None,
             (str(d.get("ticket")).strip() or None) if d.get("ticket") else None))
        return int(cur.lastrowid)


def update_trade(tid: int, d: dict) -> bool:
    """改一筆記錄（未平倉都可以改止蝕／備註）。"""
    fields, vals = [], []
    for k in ("actual_entry", "actual_stop", "signal_entry", "signal_stop",
              "signal_target", "lot", "spread_at_entry", "opened_at"):
        if k in d:
            fields.append(f"{k}=?")
            vals.append(_f(d[k]))
    if "followed" in d and d["followed"] in FOLLOW_OPTIONS:
        fields.append("followed=?")
        vals.append(d["followed"])
    if "note" in d:
        fields.append("note=?")
        vals.append((d["note"] or "").strip() or None)
    if "direction" in d and str(d["direction"]).lower() in ("long", "short"):
        fields.append("direction=?")
        vals.append(str(d["direction"]).lower())
    if not fields:
        return False
    vals.append(tid)
    with _conn() as c:
        cur = c.execute(f"UPDATE gold_trades SET {', '.join(fields)} WHERE id=?", vals)
        return cur.rowcount > 0


def close_trade(tid: int, exit_price, pnl_usd=None, note=None,
                followed=None) -> bool:
    """平倉。pnl 唔填就自動由入場價、出場價、手數、方向計出嚟。"""
    ex = _f(exit_price)
    if ex is None:
        raise ValueError("一定要填出場價")
    with _conn() as c:
        row = c.execute("SELECT * FROM gold_trades WHERE id=?", (tid,)).fetchone()
        if row is None:
            return False
        pnl = _f(pnl_usd)
        if pnl is None:
            lot = row["lot"] or 0.0
            oz = lot * config.GOLD_OZ_PER_LOT
            diff = (ex - row["actual_entry"]) if row["direction"] == "long" \
                   else (row["actual_entry"] - ex)
            pnl = round(diff * oz - config.GOLD_SPREAD_USD * oz, 2)
        sets = ["closed_at=?", "exit_price=?", "pnl_usd=?", "status='closed'"]
        vals = [_now(), ex, pnl]
        if note is not None:
            sets.append("note=?")
            vals.append((note or "").strip() or None)
        if followed and followed in FOLLOW_OPTIONS:
            sets.append("followed=?")
            vals.append(followed)
        vals.append(tid)
        c.execute(f"UPDATE gold_trades SET {', '.join(sets)} WHERE id=?", vals)
        return True


def delete_trade(tid: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM gold_trades WHERE id=?", (tid,)).rowcount > 0
def list_trades(limit: int = 200, include_draft: bool = False) -> list[dict]:
    """正式記錄，最新喺前。附帶計好嘅執行偏差。

    2026-09-30：預設唔包 status='draft'。草稿係由 MT4 自動讀入、
    未經用戶確認嘅，唔應該混入正式記錄（會污染統計同你嘅判斷）。
    草稿另外用 drafts() 取。
    """
    with _conn() as c:
        if include_draft:
            rows = c.execute(
                "SELECT * FROM gold_trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM gold_trades WHERE status<>'draft' "
                "ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["slip_price"] = _slip_price(r)
        d["slip_usd"] = _slip_usd(r)
        d["followed_label"] = FOLLOW_OPTIONS.get(d.get("followed"), d.get("followed"))
        out.append(d)
    return out


def _slip_price(r) -> float | None:
    """訊號價 vs 你實際入場價嘅價差（每盎司）。正數 = 對你不利。

    ⚠️ 如果訊號當時用期貨價、而你入現貨，呢個數會包含 US$20–35 嘅
    基準差 —— 嗰個唔係你嘅執行問題。用咗券商真實報價之後就冇呢個問題。
    """
    sig = r["signal_entry"]
    if sig is None or not r["actual_entry"]:
        return None
    d = (r["actual_entry"] - sig) if r["direction"] == "long" \
        else (sig - r["actual_entry"])
    return round(d, 2)


def _slip_usd(r) -> float | None:
    """同上，但換成美元（乘手數）。正數 = 對你不利（買貴咗／沽平咗）。"""
    p = _slip_price(r)
    if p is None:
        return None
    oz = (r["lot"] or 0.0) * config.GOLD_OZ_PER_LOT
    if not oz:
        return None
    return round(p * oz, 2)


def stats() -> dict:
    """統計。⚠️ 少過 30 筆唔好當結論 —— 統計上分唔開好彩同本事。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM gold_trades WHERE status='closed' ORDER BY id"
        ).fetchall()
        n_open = c.execute(
            "SELECT COUNT(*) AS n FROM gold_trades WHERE status='open'"
        ).fetchone()["n"]

    n = len(rows)
    s = {"n_closed": n, "n_open": n_open, "enough_sample": n >= 30}
    if n == 0:
        s.update({k: None for k in
                  ("win_rate", "total_pnl", "avg_win", "avg_loss",
                   "profit_factor", "expectancy", "avg_slip_usd",
                   "avg_spread_usd", "max_drawdown")})
        s["follow_breakdown"] = {}
        s["verdict"] = "仲未有已平倉記錄。"
        return s

    pnls = [r["pnl_usd"] for r in rows if r["pnl_usd"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_w = round(sum(wins), 2)
    gross_l = round(abs(sum(losses)), 2)

    slips = [x for x in (_slip_usd(r) for r in rows) if x is not None]
    spreads = [r["spread_at_entry"] for r in rows if r["spread_at_entry"] is not None]

    fb: dict[str, int] = {}
    for r in rows:
        k = r["followed"] or "yes"
        fb[k] = fb.get(k, 0) + 1

    # 最大回撤（用累積盈虧曲線）
    run, peak, dd = 0.0, 0.0, 0.0
    for p in pnls:
        run += p
        peak = max(peak, run)
        dd = max(dd, peak - run)

    s.update({
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(gross_w / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_l / len(losses), 2) if losses else 0.0,
        "gross_win": gross_w,
        "gross_loss": gross_l,
        "profit_factor": (round(gross_w / gross_l, 2) if gross_l else None),
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "avg_slip_usd": round(sum(slips) / len(slips), 2) if slips else None,
        "avg_spread_usd": round(sum(spreads) / len(spreads), 2) if spreads else None,
        "max_drawdown": round(dd, 2),
        "follow_breakdown": {FOLLOW_OPTIONS.get(k, k): v for k, v in fb.items()},
    })

    pf = s["profit_factor"]
    if n < 30:
        s["verdict"] = (f"只有 {n} 筆 —— 統計上太少，"
                        f"就算賺錢都證明唔到有優勢。目標 100 筆。")
    elif pf and pf >= 1.2:
        s["verdict"] = f"利潤因子 {pf} —— 初步睇有優勢，繼續觀察。"
    else:
        s["verdict"] = (f"利潤因子 {pf} —— 大約打和或更差。"
                        f"加埋點差同滑價實際係蝕。")
    return s


# ------------------------------------------------------------------ 草稿流程
# 2026-09-30 新增。由 MT4 自動讀入嘅成交一律係 'draft'，
# 用戶喺網頁核對完撳「確認」先變正式記錄 —— 因為日誌係將來判斷策略
# 有冇優勢嘅唯一材料，唔可以畀未核對嘅資料污染。
_EDITABLE = {
    "direction", "actual_entry", "actual_stop", "lot", "exit_price",
    "pnl_usd", "note", "opened_at", "closed_at", "signal_entry",
    "signal_stop", "signal_target", "followed", "spread_at_entry",
}
_TEXT_COLS = {"direction", "note", "opened_at", "closed_at", "followed"}


def drafts() -> list:
    """未確認嘅草稿。"""
    try:
        init_db()
        with _conn() as c:
            rs = c.execute("SELECT * FROM gold_trades WHERE status='draft' "
                           "ORDER BY id DESC").fetchall()
        return [dict(r) for r in rs]
    except sqlite3.Error:
        log.exception("讀取草稿失敗")
        return []


def approve(tid: int, edits: dict | None = None) -> bool:
    """確認草稿。可以順便套用用戶改過嘅欄位。"""
    try:
        init_db()
        with _conn() as c:
            r = c.execute("SELECT * FROM gold_trades WHERE id=?", (tid,)).fetchone()
            if not r or r["status"] != "draft":
                return False
            if edits:
                sets, vals = [], []
                for k, v in edits.items():
                    if k not in _EDITABLE:
                        continue
                    sets.append(k + "=?")
                    vals.append((str(v).strip() or None) if k in _TEXT_COLS
                                else _f(v))
                if sets:
                    vals.append(tid)
                    c.execute("UPDATE gold_trades SET " + ",".join(sets) +
                              " WHERE id=?", vals)
            # 已經有平倉價／盈虧 → 直接就係已完結嘅交易
            r2 = c.execute("SELECT exit_price, pnl_usd FROM gold_trades "
                           "WHERE id=?", (tid,)).fetchone()
            done = bool(r2["exit_price"] is not None or r2["pnl_usd"] is not None)
            c.execute("UPDATE gold_trades SET status=? WHERE id=?",
                      ("closed" if done else "open", tid))
        return True
    except sqlite3.Error:
        log.exception("確認草稿失敗")
        return False


def discard(tid: int) -> bool:
    """丟棄草稿。只可以丟棄 draft —— 正式記錄唔畀誤刪。"""
    try:
        init_db()
        with _conn() as c:
            r = c.execute("SELECT status FROM gold_trades WHERE id=?",
                          (tid,)).fetchone()
            if not r or r["status"] != "draft":
                return False
            c.execute("DELETE FROM gold_trades WHERE id=?", (tid,))
        return True
    except sqlite3.Error:
        log.exception("丟棄草稿失敗")
        return False


# ---------------------------------------------------------------- 匯入去重
def deal_exists(ticket) -> bool:
    """呢張 MT4 成交之前匯入過未？

    2026-10-01 修正：原本查獨立嘅 ocr_imported 表 —— 用戶刪走日誌記錄
    之後，去重表仲留住張飛，所以點都話「已匯入過」，冇得重新入。
    改為查 gold_trades 本身：**記錄冇咗 = 可以重新匯入**。
    """
    if not ticket:
        return False
    try:
        init_db()
        with _conn() as c:
            return c.execute("SELECT 1 FROM gold_trades WHERE ticket=?",
                             (str(ticket),)).fetchone() is not None
    except sqlite3.Error:
        log.exception("查成交去重失敗")
        return False


def mark_deal_journal(ticket, journal_id) -> None:
    """保留介面；去重而家靠 gold_trades.ticket，唔需要另寫表。"""
    return None


def forget_deal(ticket) -> None:
    """草稿被丟棄時，釋放個 ticket，等你可以重新匯入。"""
    if not ticket:
        return
    try:
        init_db()
        with _conn() as c:
            c.execute("DELETE FROM ocr_imported WHERE ticket=?", (str(ticket),))
    except sqlite3.Error:
        log.exception("釋放成交去重失敗")
