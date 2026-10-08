"""黃金未平倉持倉 + 監控（E3）。

2026-10-04 新增。

用戶嘅 MT4 有兩個分頁，兩者都重要：
  · 「歷史」分頁 → 已平倉成交 → 入 journal（gold_trades）＝ 賺蝕記錄
  · 「交易」分頁 → 未平倉持倉 → 入呢個表 ＝ 用嚟監控幾時接近止蝕／止賺

之前只做咗「歷史」嗰半，所以 E3（持倉監控）冇數據可用。
呢個模組補返另一半。
"""
from __future__ import annotations

import logging
import sqlite3

import config

log = logging.getLogger("tylove.positions")

# 距離止蝕／目標淨返幾多（相對原始風險）就通知
NEAR_FRAC = 0.35


def _now() -> str:
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(config.HK_TZ)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS gold_positions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket        TEXT,
                direction     TEXT NOT NULL,
                entry         REAL NOT NULL,
                stop          REAL,
                target        REAL,
                lot           REAL,
                opened_at     TEXT,
                symbol        TEXT DEFAULT 'XAUUSD',
                status        TEXT DEFAULT 'open',
                last_alert    TEXT,
                updated_at    TEXT
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_gp_open "
                  "ON gold_positions(status)")


def save_from_ocr(rows: list, source: str = "mt4") -> dict:
    """「交易」分頁讀到嘅持倉 → 存起。

    同一個 ticket 會更新而唔係重複開 —— 所以你可以隨時再傳一次截圖，
    止蝕／目標改咗都會跟到。
    """
    init_db()
    added = updated = 0
    for r in rows:
        entry = r.get("actual_entry")
        if not entry:
            continue
        tk = r.get("ticket")
        with _conn() as c:
            row = None
            if tk:
                row = c.execute(
                    "SELECT id FROM gold_positions "
                    "WHERE ticket=? AND status='open'", (tk,)).fetchone()
            if row:
                c.execute(
                    "UPDATE gold_positions SET entry=?, stop=?, target=?, lot=?, "
                    "direction=?, updated_at=? WHERE id=?",
                    (entry, r.get("actual_stop"), r.get("signal_target_hint"),
                     r.get("lots"), r.get("direction"), _now(), row["id"]))
                updated += 1
            else:
                c.execute(
                    "INSERT INTO gold_positions (ticket, direction, entry, stop, "
                    "target, lot, opened_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (tk, r.get("direction"), entry, r.get("actual_stop"),
                     r.get("signal_target_hint"), r.get("lots"),
                     r.get("opened_at"), _now()))
                added += 1
    return {"added": added, "updated": updated, "total": added + updated}


def all_open() -> list:
    init_db()
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM gold_positions WHERE status='open' ORDER BY id")]


def close(pid: int) -> None:
    with _conn() as c:
        c.execute("UPDATE gold_positions SET status='closed', updated_at=? "
                  "WHERE id=?", (_now(), pid))


def update(pid: int, d: dict) -> bool:
    """手動更正持倉數值（2026-10-07 新增）。

    用戶反映「系統有時計錯」—— OCR 讀交易分頁時，
    入場價／止蝕／目標可能讀歪。呢度容許逐筆手動改返。
    """
    init_db()
    fields, vals = [], []
    for k in ("direction", "entry", "stop", "target", "lot"):
        if k in d and d[k] not in (None, ""):
            fields.append(k + "=?")
            if k == "direction":
                vals.append(str(d[k]).strip().lower())
            else:
                try:
                    vals.append(float(d[k]))
                except (TypeError, ValueError):
                    continue
    if "opened_at" in d and d["opened_at"] not in (None, ""):
        fields.append("opened_at=?")
        vals.append(str(d["opened_at"]).replace("T", " ").strip()[:19])
    if not fields:
        return False
    fields.append("updated_at=?")
    vals.append(_now())
    vals.append(pid)
    with _conn() as c:
        cur = c.execute("UPDATE gold_positions SET " + ", ".join(fields) +
                        " WHERE id=?", vals)
        return cur.rowcount > 0


def close_by_ticket(ticket, exit_price=None, pnl_usd=None) -> bool:
    """由 ticket 自動平倉。

    2026-10-07 新增。用戶反映：MT4 明明已經平咗倉，但系統一世顯示
    「未平倉」，而且網頁／Telegram 都冇任何方法收尾。
    呢個函數喺日誌 OCR 讀到「歷史」分頁嘅已平倉交易時自動配對呼叫。
    """
    if not ticket:
        return False
    init_db()
    tk = str(ticket).strip()
    with _conn() as c:
        row = c.execute("SELECT id FROM gold_positions "
                        "WHERE ticket=? AND status='open'", (tk,)).fetchone()
        if not row and tk.isdigit():
            row = c.execute("SELECT id FROM gold_positions "
                            "WHERE ticket LIKE ? AND status='open'",
                            ("%" + tk + "%",)).fetchone()
        if not row:
            return False
        full = c.execute("SELECT * FROM gold_positions WHERE id=?",
                         (row["id"],)).fetchone()
        parts = []
        if exit_price is not None:
            parts.append("出 %s" % exit_price)
        if pnl_usd is not None:
            parts.append("盈虧 %s" % pnl_usd)
        note = "已平倉" + ("（" + "／".join(parts) + "）" if parts else "")
        c.execute("UPDATE gold_positions SET status='closed', last_alert=?, "
                  "updated_at=? WHERE id=?", (note, _now(), row["id"]))

    # ② 補入交易日誌（2026-10-07）。
    # 用戶反映：按住「已經平倉」之後，嗰筆交易喺統計上完全消失 ——
    # 唔計勝率、唔計盈虧、唔入交易日誌。持倉同交易日誌係兩張表，
    # 收尾持倉唔會自動入帳，所以呢度要補。
    # 但如果筆交易之前已經由 OCR 入過日誌，就唔可以再入一次（會重複計）。
    try:
        import journal as J
        if not J.deal_exists(tk):
            f = dict(full) if full else {}
            jid = J.add_trade({
                "opened_at": f.get("opened_at") or _now(),
                "closed_at": _now(),
                "direction": f.get("direction"),
                "actual_entry": f.get("entry"),
                "actual_stop": f.get("stop"),
                "signal_target": f.get("target"),
                "lot": f.get("lot"),
                "exit_price": exit_price,
                "pnl_usd": pnl_usd,
                "status": "closed",
                "data_source": "mt4",
                "ticket": tk,
                "followed": "yes",
                "note": "由未平倉持倉收尾時自動補入（" + note + "）",
            })
            J.mark_deal_journal(tk, jid)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("補入交易日誌失敗（持倉已收尾）")
    return True


def clear() -> int:
    """清走全部未平倉（例如你已經全部平晒倉）。"""
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM gold_positions WHERE status='open'"
                      ).fetchone()[0]
        c.execute("UPDATE gold_positions SET status='closed', updated_at=? "
                  "WHERE status='open'", (_now(),))
    return n


def _hit(p: dict, price: float) -> str | None:
    """現價係咪已經穿咗止蝕／目標。"""
    d, stop, tgt = p.get("direction"), p.get("stop"), p.get("target")
    if stop:
        if d == "long" and price <= stop:
            return "stop"
        if d == "short" and price >= stop:
            return "stop"
    if tgt:
        if d == "long" and price >= tgt:
            return "target"
        if d == "short" and price <= tgt:
            return "target"
    return None


def check(price: float) -> list[dict]:
    """用現價檢查所有持倉 → 回傳要通知嘅（同一種提示唔會重複發）。

    每張單只會記住最後發過嘅提示，狀態一變就會再通知。
    """
    out = []
    for p in all_open():
        entry = p.get("entry")
        if not entry:
            continue
        state = _hit(p, price)
        if not state:
            risk = abs(entry - (p.get("stop") or entry))
            if risk <= 0:
                continue
            to_stop = abs(price - (p.get("stop") or price))
            to_tgt = abs((p.get("target") or price) - price)
            if p.get("stop") and to_stop <= risk * NEAR_FRAC:
                state = "near_stop"
            elif p.get("target") and to_tgt <= risk * NEAR_FRAC:
                state = "near_target"
        if not state:
            continue
        if p.get("last_alert") == state:
            continue                      # 同一提示唔重複
        with _conn() as c:
            c.execute("UPDATE gold_positions SET last_alert=? WHERE id=?",
                      (state, p["id"]))
        out.append({"state": state, "pos": p, "price": price})
    return out


def _p2(v):
    """價格一律印兩位小數（2026-10-07：之前出過「4104.2001953125」）。"""
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return v if v else "—"


def fmt_alert(a: dict) -> str:
    """監控提示訊息。"""
    p, price, st = a["pos"], a["price"], a["state"]
    d = "🟢 做多" if p.get("direction") == "long" else "🔴 做空"
    head = {
        "stop":        "🚨 *已經到咗止蝕位*",
        "target":      "🎯 *已經到咗目標價*",
        "near_stop":   "⚠️ *接近止蝕位*",
        "near_target": "💰 *接近目標價 —— 可以考慮提早止賺*",
    }[st]
    lines = [head, ""]
    lines.append("%s　%s手　#%s" % (d, p.get("lot"), p.get("ticket") or "—"))
    lines.append("入場 %s　止蝕 %s　目標 %s" % (
        _p2(p.get("entry")), _p2(p.get("stop")) or "—", _p2(p.get("target")) or "—"))
    # 2026-10-07：現價之前用 %s 直接印浮點數，出過「4104.2001953125」。
    lines.append("現價 *%s*" % _p2(price))
    if st in ("stop", "near_stop"):
        lines.append("")
        lines.append("止蝕距離得返 *%.2f* 美元。你話過唔想我幫你落單 —— "
                     "所以呢個係提示，落唔落由你決定。" %
                     abs(price - (p.get("stop") or price)))
    if st in ("target", "near_target"):
        lines.append("")
        lines.append("目標距離得返 *%.2f* 美元。可以考慮提早止賺鎖利。" %
                     abs((p.get("target") or price) - price))
    lines.append("─" * 18)
    lines.append("回 /pos 睇全部持倉　·　/closed 清走全部")
    return "\n".join(lines)


def fmt_list() -> str:
    """持倉清單（Telegram /pos）。"""
    ps = all_open()
    if not ps:
        return ("📭 *而家冇未平倉持倉*\n\n"
                "想我幫你監控，就去 MT4「交易」分頁截圖傳畀我。\n"
                "（「歷史」分頁係已平倉記錄，用途唔同。）")
    lines = ["📌 *未平倉持倉（%d 張）*" % len(ps), ""]
    for p in ps:
        d = "🟢做多" if p.get("direction") == "long" else "🔴做空"
        lines.append("*#%s* %s　%s手" % (p.get("id"), d, p.get("lot")))
        lines.append("　入 %s　止 %s　標 %s" % (
            p.get("entry"), p.get("stop") or "—", p.get("target") or "—"))
        if p.get("opened_at"):
            lines.append("　香港 %s" % p["opened_at"])
    lines += ["", "我會喺黃金時段每 15 分鐘對價，接近止蝕／目標就提你。"]
    return "\n".join(lines)
