"""Telegram 雙向：收圖 → OCR → 草稿 → 你確認 → 入日誌。

2026-10-03 新增。
之前 Telegram 完全單向（notifier.push 只發唔收），系統收唔到用戶
任何訊息 —— 用戶喺 Telegram 上載張圖，系統根本唔知。

而家：
  1. 你喺 Telegram 上載 MT4 截圖
  2. 系統 OCR 讀出所有成交（一張圖可以幾筆）
  3. 開草稿，回覆畀你核對（附「確認入帳」／「取消」Inline 按鈕）
  4. 你撳「確認」→ 寫入正式日誌（連截圖保存）

指令：
  /status  睇未確認草稿同記錄數
  /help    用法

安全：只接受你自己 chat_id 嘅訊息，其他人傳圖一律唔理。
"""
from __future__ import annotations

import logging
import time

import requests

import journal
import notifier
import ocr

log = logging.getLogger("tylove.telegram")

_API = "https://api.telegram.org/bot{token}/{method}"
_FILE_API = "https://api.telegram.org/file/bot{token}/{path}"

_MAX_PHOTO = 12 * 1024 * 1024      # 最大下載 12MB
_WEBHOOK_CLEARED = False
_POLL_TIMEOUT = 25                 # long polling 秒數


# ------------------------------------------------------------------ 底層
def _api(token: str, method: str, **payload):
    r = requests.post(_API.format(token=token, method=method),
                      json=payload, timeout=_POLL_TIMEOUT + 20)
    return r.json()


def _send(token: str, chat_id, text: str, keyboard=None) -> None:
    p = {
        "chat_id": chat_id,
        "text": text[:3900],
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if keyboard:
        p["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        _api(token, "sendMessage", **p)
    except Exception:  # noqa: BLE001
        log.exception("Telegram 回覆失敗")


def _dl_photo(token: str, file_id: str):
    """攞 Telegram 上嘅圖（回傳 bytes）。"""
    info = _api(token, "getFile", file_id=file_id)
    path = (info.get("result") or {}).get("file_path")
    if not path:
        log.warning("getFile 冇 file_path：%s", info)
        return None
    r = requests.get(_FILE_API.format(token=token, path=path), timeout=90)
    if r.status_code != 200:
        log.warning("下載圖片失敗 HTTP %s", r.status_code)
        return None
    if len(r.content) > _MAX_PHOTO:
        log.warning("圖片太大：%d bytes", len(r.content))
        return None
    return r.content


def _hk(s):
    """MT4 伺服器時間 → 香港時間（同網頁版共用同一個轉換點）。"""
    if not s:
        return None
    try:
        import main
        return main._hk_from_mt4(s)
    except Exception:  # noqa: BLE001
        return s


def _save_shot(data: bytes):
    """存低截圖，回傳檔名（同網頁版同一個目錄）。"""
    try:
        import main
        d = main._shot_dir()
    except Exception:  # noqa: BLE001
        d = "shots"
    import os
    from datetime import datetime
    os.makedirs(d, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-tg.jpg"
    with open(os.path.join(d, name), "wb") as f:
        f.write(data)
    return name


# ------------------------------------------------------------------ 開草稿
def make_drafts(trades, shot_name, source="Telegram 上載"):
    """將 OCR 讀到嘅成交開成草稿（同網頁版一致，唔會寫入正式記錄）。"""
    created, skipped = [], []
    for t in trades:
        tk = t.get("ticket")
        if tk and journal.deal_exists(tk):
            skipped.append(tk)
            continue
        d = {
            "direction": t.get("direction"),
            "actual_entry": t.get("actual_entry"),
            "actual_stop": t.get("actual_stop"),
            "signal_target": t.get("signal_target_hint"),
            "exit_price": t.get("exit_price"),
            "pnl_usd": t.get("profit"),
            "lot": t.get("lots"),
            "opened_at": _hk(t.get("opened_at")),
            "closed_at": _hk(t.get("closed_at")),
            "status": "draft",
            "data_source": "mt4",
            "shot": shot_name,
            "ticket": tk,
            "note": "MT4 成交 #%s（%s）｜MT4 %s → 香港 %s｜OCR 信心 %.0f%%" % (
                tk, source, t.get("opened_at") or "?", _hk(t.get("opened_at")) or "?",
                (t.get("confidence") or 0) * 100),
        }
        if not d.get("opened_at"):
            # 冇開倉時間就入唔到（DB 要求 NOT NULL）。
            # 唔好成個上載失敗，跳過呢筆就得。
            log.warning("跳過冇開倉時間嘅一筆：%s", tk)
            skipped.append(tk)
            continue
        try:
            tid = journal.add_trade(d)
        except Exception as exc:  # noqa: BLE001
            # 單筆壞資料唔應該令整張圖失敗（之前用戶見到嘅 IntegrityError）
            log.warning("跳過一筆入唔到嘅記錄 %s：%s", tk, exc)
            skipped.append(tk)
            continue
        if tid:
            if tk:
                try:
                    journal.mark_deal_journal(tk, tid)
                except Exception:  # noqa: BLE001
                    pass
            created.append(tid)
        else:
            skipped.append(tk)
    return created, skipped


def _fmt(trades, created, skipped) -> str:
    """草稿核對卡（Telegram Markdown）。"""
    out = ["🔍 *讀到 %d 筆成交*" % len(trades),
           "同你嘅 MT4 對一對（時間已轉香港）：", ""]
    for i, t in enumerate(trades[:10], 1):
        d = "🟢 做多" if t.get("direction") == "long" else "🔴 做空"
        pl = t.get("profit")
        pl_s = ("+%.2f" % pl) if (pl or 0) >= 0 else ("%.2f" % pl)
        out.append(
            "*%d.* %s　%s手\n"
            "　入 %s　止 %s　出 %s\n"
            "　盈虧 *%s*　香港 %s → %s"
            % (i, d, t.get("lots"), t.get("actual_entry"), t.get("actual_stop"),
               t.get("exit_price"), pl_s, _hk(t.get("opened_at")), _hk(t.get("closed_at"))))
    if len(trades) > 10:
        out.append("…仲有 %d 筆" % (len(trades) - 10))
    out.append("")
    if created:
        out.append("✅ 已開 *%d* 張草稿（未入正式記錄）" % len(created))
        out.append("揳下面嘅按鈕確認，或者去網頁改完先確認。")
    if skipped:
        out.append("⏭ 跳過 *%d* 筆（之前已經匯入過）" % len(skipped))
    if not created:
        out.append("如果想重新入，去網頁刪走舊記錄再上載。")
    return "\n".join(out)


def _keyboard(created):
    ids = ",".join(str(i) for i in created)
    return [[
        {"text": "✅ 確認入帳（%d 筆）" % len(created), "callback_data": "ok:" + ids},
        {"text": "❌ 全部取消", "callback_data": "no:" + ids},
    ]]


def _status() -> str:
    ds = journal.drafts()
    with journal._conn() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM gold_trades WHERE status!='draft'"
        ).fetchone()[0]
    with journal._conn() as c:
        rows = c.execute("SELECT pnl_usd FROM gold_trades "
                         "WHERE status!='draft' AND pnl_usd IS NOT NULL").fetchall()
    pnls = [r[0] for r in rows]
    win = [x for x in pnls if (x or 0) > 0]
    lose = [x for x in pnls if (x or 0) < 0]
    lines = ["📒 *交易日誌*", "正式記錄：*%d* 筆" % n,
             "未確認草稿：*%d* 張" % len(ds)]
    if pnls:
        lines += [
            "",
            "賺 *%d* 筆　蝕 *%d* 筆" % (len(win), len(lose)),
            "淨盈虧：*%+.2f* USD" % sum(x or 0 for x in pnls),
            "勝率：*%.0f%%*" % (100.0 * len(win) / len(pnls)),
            "_距 30 筆目標仲差 %d 筆_" % max(0, 30 - len(pnls)),
        ]
    if ds:
        lines.append("")
        for d in ds[:8]:
            lines.append("　#%s %s %s手　入%s　香港 %s" % (
                d.get("id"), d.get("direction"), d.get("lot"),
                d.get("actual_entry"), d.get("opened_at")))
        if len(ds) > 8:
            lines.append("　…仲有 %d 張" % (len(ds) - 8))
        lines += ["", "去網頁 /gold 核對同確認。"]
    return "\n".join(lines)


# ------------------------------------------------------------------ 處理
def _handle(token: str, my_chat, u: dict) -> None:
    # ① Inline 按鈕
    cq = u.get("callback_query")
    if cq:
        try:
            data = cq.get("data") or ""
            msg = cq.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(my_chat):
                return
            ids = [int(x) for x in data.split(":", 1)[1].split(",") if x.strip().isdigit()]
            if data.startswith("ok:"):
                n = sum(1 for i in ids if journal.approve(i))
                _send(token, my_chat, "✅ *%d 筆已入正式記錄*" % n)
            elif data.startswith("no:"):
                n = sum(1 for i in ids if journal.discard(i))
                _send(token, my_chat, "🗑 *%d 張草稿已取消*" % n)
            _api(token, "answerCallbackQuery", callback_query_id=cq.get("id"))
        except Exception:  # noqa: BLE001
            log.exception("按鈕處理失敗")
        return

    msg = u.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != str(my_chat):
        return                                  # 唔係你，唔理
    text = (msg.get("text") or "").strip()

    # ② 指令
    if text.startswith("/status"):
        _send(token, my_chat, _status())
        return
    if text.startswith("/pos"):
        import gold_positions as gp
        _send(token, my_chat, gp.fmt_list())
        return
    if text.startswith("/closed"):
        import gold_positions as gp
        n = gp.clear()
        _send(token, my_chat,
              "🧹 清走咗 %d 張持倉記錄。\n\n"
              "（唔係平倉，只係唔再監控。想再監控就再傳「交易」分頁截圖。）" % n)
        return
    if text.startswith("/close"):
        # 2026-10-07：用戶反映 Telegram 完全冇方法記錄平倉，只可以上網頁。
        #   /close              → 列出持倉叫你揀
        #   /close 2            → 平倉 #2（數值之後可以上網頁補）
        #   /close 2 4148.03 -27.70  → 平倉 + 平倉價 + 盈虧
        import gold_positions as gp
        import journal as J
        parts = text.split()
        if len(parts) < 2 or not parts[1].lstrip("#").isdigit():
            op = gp.all_open()
            if not op:
                _send(token, my_chat, "\U0001f4ed \u800c\u5bb6\u5187\u672a\u5e73\u5009\u6301\u5009\u3002")
            else:
                L = ["\U0001f534 *\u63c0\u908a\u5f35\u5e73\u5009\uff1f*\u3000\u6253 `/close \u7de8\u865f`\n"]
                for q in op:
                    L.append("#%s %s %s\u624b\u3000\u5165 %s\u3000\u6b62 %s" % (
                        q["id"],
                        "\U0001f7e2\u505a\u591a" if q.get("direction") == "long" else "\U0001f534\u505a\u7a7a",
                        q.get("lot"), q.get("entry"), q.get("stop")))
                L.append("\n\u60f3\u9806\u4fbf\u8a18\u6578\u503c\uff1a`/close \u7de8\u865f \u5e73\u5009\u50f9 \u76c8\u8667`")
                _send(token, my_chat, "\n".join(L))
            return
        pid = int(parts[1].lstrip("#"))
        with gp._conn() as c:
            row = c.execute("SELECT ticket FROM gold_positions WHERE id=?", (pid,)).fetchone()
        if not row:
            _send(token, my_chat, "\u274c \u6435\u5514\u5230 #%d\u3002" % pid)
            return
        ex = float(parts[2]) if len(parts) > 2 else None
        pnl = float(parts[3]) if len(parts) > 3 else None
        gp.close_by_ticket(row["ticket"] or str(pid), ex, pnl)
        out = "\u2705 #%d \u5df2\u7d93\u5e73\u5009\u3002\n\n\U0001f4d2 \u4ea4\u6613\u65e5\u8a8c\u800c\u5bb6\u6709 *%d* \u7b46\u6b63\u5f0f\u8a18\u9304\u3002" % (
            pid, J.stats().get("n_closed", 0))
        if ex is None:
            out += "\n\n\u26a0\ufe0f \u5187\u5e73\u5009\u50f9\u540c\u76c8\u8667\uff0c\u672a\u8a08\u5165\u76c8\u8667\u7d71\u8a08\u3002\n\u60f3\u88dc\u8fd4\u5c31\u4e0a\u7db2\u9801 /gold \u6539\u3002"
        _send(token, my_chat, out)
        return

    if text.startswith("/help") or text.startswith("/start"):
        _send(token, my_chat,
              "📸 *點用*\n"
              "1. MT4「歷史」分頁 → 記錄賺蝕（會開草稿等你確認）\n"
              "　 MT4「交易」分頁 → 記住持倉，等我幫你監控\n"
              "2. 直接傳張圖畀呢個對話\n"
              "3. 傳完我即刻覆你，你撳「✅ 確認入帳」\n\n"
              "/status 睇記錄同盈虧\n"
              "/pos 睇未平倉持倉\n"
              "/close 平倉（可以喺呢度直接做）\n"
              "　 /close 2　　　　　　　→ 平倉 #2\n"
              "　 /close 2 4148.03 -27.70　→ 連平倉價、盈虧一齊記\n"
              "（想改數值就上網頁 /gold 改完先確認）")
        return

    # ③ 圖片
    photos = msg.get("photo") or []
    doc = msg.get("document") or {}
    if not photos and (doc.get("mime_type") or "").startswith("image/"):
        photos = [{"file_id": doc.get("file_id"), "file_size": doc.get("file_size")}]
    if not photos:
        if text:
            _send(token, my_chat, "收到文字。想記錄成交就*傳 MT4 截圖*畀我，或者打 /help")
        return

    _send(token, my_chat, "🔍 解讀中…（第一次會慢少少）")
    try:
        fid = photos[-1].get("file_id")          # 最大尺寸排最後
        data = _dl_photo(token, fid)
        if not data:
            _send(token, my_chat, "❌ 攞唔到張圖，再傳一次試吓")
            return
        _d = ocr.diagnose(data)
        _eng = (ocr.status() or {}).get("engine") or "?"

        # 「交易」分頁 → 未平倉持倉 → 存起用嚟監控（E3）
        # 「歷史」分頁 → 已平倉成交 → 開草稿入日誌。兩個用途唔同。
        if _d.get("what") == "positions":
            import gold_positions as gp
            rows_p = ocr.extract_positions(data)
            for _r in rows_p:                   # MT4 時間 → 香港時間
                _r["opened_at"] = _hk(_r.get("opened_at"))
            res = gp.save_from_ocr(rows_p)
            lines = ["📌 *記住咗你嘅持倉*（%d 張）" % res["total"], ""]
            for pp in rows_p[:6]:
                dd = "🟢做多" if pp.get("direction") == "long" else "🔴做空"
                lines.append("%s　%s手　#%s"
                             % (dd, pp.get("lots"), pp.get("ticket") or "—"))
                lines.append("　入 %s　止 %s　標 %s" % (
                    pp.get("actual_entry"), pp.get("actual_stop") or "—",
                    pp.get("signal_target_hint") or "—"))
            lines += ["",
                      "我會喺黃金時段（15:00–01:00）每 15 分鐘對價，",
                      "接近止蝕／目標就即刻提你。",
                      "", "打 /pos 隨時睇持倉。"]
            _send(token, my_chat, "\n".join(lines))
            log.info("Telegram 持倉：新增 %d／更新 %d", res["added"], res["updated"])
            return

        trades = ocr.extract_trades(data)
        if not trades:
            _send(token, my_chat,
                  "⚠️ *讀唔到任何表格*\n\n請確認：\n"
                  "① 係 MT4「歷史」分頁（已平倉）或「交易」分頁（持倉）\n"
                  "② 成個表都入到鏡頭（唔好裁得太窄）\n"
                  "③ 見到「訂單／時間／類型／價格」呢幾欄\n"
                  "④ 上下兩邊嘅按鈕列唔好遮住表格\n\n"
                  "_（歷史 = 已平倉記錄；交易 = 手上持倉。兩個傳齊先完整。）_"
                  "\n\n_OCR 引擎：%s_" % _eng)
            log.info("Telegram OCR 讀唔到：%s（引擎 %s）", _d, _eng)
            return
        shot = _save_shot(data)
        created, skipped = make_drafts(trades, shot)
        kb = _keyboard(created) if created else None
        _send(token, my_chat, _fmt(trades, created, skipped), kb)
        log.info("Telegram OCR：讀到 %d 筆，開 %d 張草稿，跳過 %d",
                 len(trades), len(created), len(skipped))
    except Exception as e:  # noqa: BLE001
        log.exception("Telegram OCR 失敗")
        _send(token, my_chat, "❌ 出錯：%s" % type(e).__name__)


def poll_loop(channel: str = "gold") -> None:
    """長輪詢 Telegram。喺背景 thread 行。"""
    offset = None
    warned = False
    while True:
        try:
            token, chat_id = notifier._creds(channel)
            if not token or not chat_id:
                if not warned:
                    log.info("Telegram 收訊未啟用（%s 未設 token/chat_id）", channel)
                    warned = True
                time.sleep(60)
                continue
            warned = False
            # 如果之前設過 webhook，getUpdates 會回 409 而永遠收唔到訊息。
            # 我哋用長輪詢，所以要清走 webhook（每次啟動做一次就夠）。
            global _WEBHOOK_CLEARED
            if not _WEBHOOK_CLEARED:
                try:
                    _api(token, "deleteWebhook", drop_pending_updates=False)
                except Exception:  # noqa: BLE001
                    pass
                _WEBHOOK_CLEARED = True
            r = _api(token, "getUpdates",
                     timeout=_POLL_TIMEOUT, offset=offset,
                     allowed_updates=["message", "callback_query"])
            for u in (r.get("result") or []):
                offset = u.get("update_id", 0) + 1
                try:
                    _handle(token, chat_id, u)
                except Exception:  # noqa: BLE001
                    log.exception("處理 update 失敗")
        except Exception:  # noqa: BLE001
            log.exception("Telegram 輪詢失敗，10 秒後重試")
            time.sleep(10)
        time.sleep(0.4)
