"""
主程式 — 排程 + 網頁儀表板
一個 process 搞掂：Flask 服務（Railway 需要）＋ 背景排程執行緒。
香港時間排程：開市前掃描、日內掃描、持倉監控、收市提醒。
"""
import logging
import json
import os
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

import config
import gold
import scanner
import settings_store
from config import (CLOSE_REMINDER_TIME, DASH_PASS, DASH_USER, HK_TZ,
                    MONITOR_INTERVAL_MIN, PORT, SCAN_TIMES, WATCHLIST)
from positions import init_db, list_closed, list_open, realised_stats, recent_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tylove.main")

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

app = Flask(__name__, template_folder="templates")
CORS(app)

_STATE = {"last_scan": None, "last_result": None, "last_positions": [], "running": False}
_LOCK = threading.Lock()


def now_hk() -> datetime:
    return datetime.now(ZoneInfo(HK_TZ)) if ZoneInfo else datetime.now()


# ------------------------------------------------------------------ 排程工作
def job_scan(label: str = "定時掃描"):
    if _STATE["running"]:
        log.info("上一次掃描未完，跳過今次")
        return
    _STATE["running"] = True
    try:
        log.info("開始掃描：%s", label)
        result = scanner.scan(scan_type=label)
        with _LOCK:
            _STATE["last_scan"] = now_hk().strftime("%Y-%m-%d %H:%M:%S")
            _STATE["last_result"] = result
        log.info("掃描完成，%d 個訊號", len(result["signals"]))
    except Exception as exc:  # noqa: BLE001
        log.exception("掃描失敗")
        try:
            import notifier
            notifier.push(notifier.fmt_error("定時掃描", str(exc)))
        except Exception:  # noqa: BLE001
            pass
    finally:
        _STATE["running"] = False


def job_monitor():
    try:
        recs = scanner.monitor_positions(push=True)
        with _LOCK:
            _STATE["last_positions"] = recs
    except Exception as exc:  # noqa: BLE001
        log.exception("持倉監控失敗")


def job_open_scan():
    """港股開市前掃描。休市日直接跳過（2026-10-03 加）。"""
    if config.SKIP_WEEKEND and not _is_trading_day(now_hk()):
        log.info("今日休市（%s），跳過港股掃描", now_hk().strftime("%A"))
        return
    job_scan("開市前掃描")


def job_heartbeat():
    """每日心跳 —— 報「系統正常／今日休市」，令你分得出靜同死。

    2026-10-03（星期六）用戶全日冇收到任何訊息，分唔到係市場休市
    定係系統掛咗。從此每日固定時間一定有一條。
    """
    try:
        t = now_hk()
        wd = ["一", "二", "三", "四", "五", "六", "日"][t.weekday()]
        trading = _is_trading_day(t)
        lines = [
            "🫀 *系統正常運作*",
            f"⏰ {t.strftime('%Y-%m-%d %H:%M')}（香港時間）",
            "",
            (f"📅 今日：星期{wd} — *正常交易日*" if trading
             else f"📅 今日：星期{wd} — *休市*（港股同黃金都唔開）"),
        ]
        if not trading:
            lines.append("   下次開市：黃金 週一 05:00／港股 週一 09:30")
        # 記錄統計（有錯都唔可以令心跳唔出）
        try:
            import journal
            nd = len(journal.drafts())
            with journal._conn() as c:
                n = c.execute(
                    "SELECT COUNT(*) FROM gold_trades WHERE status!='draft'"
                ).fetchone()[0]
            lines += ["", f"💰 記錄：正式 *{n}* 筆／草稿 *{nd}* 張"]
        except Exception:  # noqa: BLE001
            pass
        try:
            import gold as _g
            lines.append("📡 黃金數據源：*券商真實報價（MT4）*"
                         if _g._live() else "📡 黃金數據源：yfinance（延遲 10–20 分鐘）")
        except Exception:  # noqa: BLE001
            pass
        if not trading:
            lines += ["", "唔使理呢條訊息，佢只係話你知系統仍然活住。"]
        import notifier                    # 同其他 job 一樣，函數內匯入
        notifier.push("\n".join(lines), channel="gold")
        log.info("🫀 心跳已發送")
    except Exception:  # noqa: BLE001
        log.exception("心跳發送失敗")


def _is_trading_day(t) -> bool:
    """港股／黃金交易日（香港時間）。"""
    if t.weekday() == 6:                 # 星期日
        return False
    if t.weekday() == 5 and t.hour >= 5:  # 星期六 05:00 後
        return False
    return True


def job_gold_scan():
    """黃金 XAU/USD 分析（獨立於港股，用另一個 Telegram bot）。"""
    if not config.GOLD_ENABLED:
        return
    try:
        gold.scan(push=True)
    except Exception:  # noqa: BLE001
        log.exception("黃金掃描失敗")


def job_gold_early():
    """⚡ 黃金即時預警 —— 唔等 K 線收盤，價穿區間即推（快約 15 分鐘）。"""
    if not config.GOLD_ENABLED or not config.GOLD_EARLY_ALERT:
        return
    try:
        gold.early_alert(push=True)
    except Exception:  # noqa: BLE001
        log.exception("黃金即時預警失敗")


def job_close_reminder():
    try:
        scanner.closing_reminder()
    except Exception:  # noqa: BLE001
        log.exception("收市提醒失敗")


def _schedule_targets() -> list:
    """今日所有要觸發嘅時間點（香港時間 HH:MM）。"""
    out = [t.strip() for t in SCAN_TIMES if t.strip()]
    out.append(CLOSE_REMINDER_TIME.strip())
    if config.HEARTBEAT_TIME:
        out.append(config.HEARTBEAT_TIME)
    return out


def scheduler_loop():
    """背景排程 —— 一律用香港時間判斷，唔受容器時區影響。

    ⚠️ 舊版用 `schedule` 套件，佢跟容器嘅本地時間，
    而 Railway 容器係 UTC，所以「09:25」實際會喺香港時間 17:25 才跑。
    呢個版本自己計香港時間，喺任何主機都準。
    """
    log.info("⏰ 排程啟動｜掃描時段 %s（香港時間）｜每 %d 分鐘監控持倉｜%s 收市提醒",
             SCAN_TIMES, MONITOR_INTERVAL_MIN, CLOSE_REMINDER_TIME)
    log.info("⏰ 而家香港時間：%s（容器時間：%s）",
             now_hk().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%H:%M:%S"))
    last_day = None
    fired = set()
    last_monitor = 0.0
    last_gold = 0.0

    while True:
        try:
            t = now_hk()
            day = t.strftime("%Y-%m-%d")
            if day != last_day:
                last_day, fired = day, set()
                log.info("📅 新一日：%s（香港時間）", day)

            hhmm = t.strftime("%H:%M")
            if hhmm in _schedule_targets() and hhmm not in fired:
                fired.add(hhmm)
                if hhmm == config.HEARTBEAT_TIME:
                    threading.Thread(target=job_heartbeat, daemon=True).start()
                elif hhmm == CLOSE_REMINDER_TIME.strip():
                    threading.Thread(target=job_close_reminder, daemon=True).start()
                else:
                    threading.Thread(target=job_open_scan, daemon=True).start()

            # 黃金：活躍時段（香港 15:00–01:00）內每 N 分鐘檢查一次。
            # ⚠️ 唔可以綁港股時間 —— 港股掃描時段（09:25/10:30/11:30/14:00/15:50）
            # 大部分都唔喺黃金活躍時段，會被 _session_ok 過濾晒，一日最多得一次機會。
            if config.GOLD_ENABLED and config.GOLD_CHECK_MIN > 0:
                if t.hour >= 15 or t.hour < 1:
                    if time.time() - last_gold >= config.GOLD_CHECK_MIN * 60:
                        last_gold = time.time()
                        threading.Thread(target=job_gold_scan, daemon=True).start()
                        # ⚡ 即時預警：唔等 K 線收盤，價穿即推
                        if config.GOLD_EARLY_ALERT:
                            threading.Thread(target=job_gold_early, daemon=True).start()

            if time.time() - last_monitor >= MONITOR_INTERVAL_MIN * 60:
                last_monitor = time.time()
                threading.Thread(target=job_monitor, daemon=True).start()
        except Exception:  # noqa: BLE001
            log.exception("排程執行出錯")
        time.sleep(15)


# ------------------------------------------------------------------ 認證
def _authed() -> bool:
    if not DASH_USER:
        return True
    auth = request.authorization
    return bool(auth and auth.username == DASH_USER and auth.password == DASH_PASS)


@app.before_request
def _guard():
    if request.path.startswith("/api/health"):
        return None
    if not _authed():
        return ("需要登入", 401, {"WWW-Authenticate": 'Basic realm="TyLove"'})


# ------------------------------------------------------------------ 路由
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "time": now_hk().strftime("%Y-%m-%d %H:%M:%S"),
                    "running": _STATE["running"]})


@app.route("/api/state")
def state():
    with _LOCK:
        result = _STATE["last_result"]
        return jsonify({
            "last_scan": _STATE["last_scan"],
            "running": _STATE["running"],
            "signals": result["signals"] if result else [],
            "all": result["all"] if result else [],
            "vetoed": result["vetoed"] if result else [],
            "allow_new": result["allow_new"] if result else {},
            "positions": _STATE["last_positions"],
            "open_positions": list_open(),
            "stats": realised_stats(),
            "watchlist": WATCHLIST,
        })


@app.route("/api/scan", methods=["POST"])
def manual_scan():
    threading.Thread(target=job_scan, args=("手動掃描",), daemon=True).start()
    return jsonify({"ok": True, "message": "掃描已開始，約一至兩分鐘後更新"})


@app.route("/api/monitor", methods=["POST"])
def manual_monitor():
    recs = scanner.monitor_positions(push=False, force=True)
    with _LOCK:
        _STATE["last_positions"] = recs
    return jsonify({"ok": True, "positions": recs})


# ------------------------------------------------------------------ 黃金分頁
@app.route("/gold")
def gold_page():
    return render_template("gold.html")


@app.route("/api/gold")
def gold_state():
    """目前黃金狀態。呢個 endpoint 唔會推送 Telegram。"""
    try:
        g = gold.evaluate()
    except Exception as exc:  # noqa: BLE001
        log.exception("黃金分析失敗")
        return jsonify({"ok": False, "error": str(exc)}), 500
    g["signals"] = gold.recent(20)
    return jsonify(g)


@app.route("/api/gold/scan", methods=["POST"])
def gold_manual_scan():
    g = gold.scan(push=True)
    return jsonify({"ok": bool(g.get("ok")), "result": g})


@app.route("/api/gold/test-telegram", methods=["POST"])
def gold_test_telegram():
    """測試黃金 bot：直接問 Telegram，回報每一步實測結果。"""
    import notifier
    d = notifier.diagnose("gold")
    d["error"] = None if d.get("ok") else d.get("verdict")
    return jsonify(d)


@app.route("/api/hk/test-telegram", methods=["POST"])
def hk_test_telegram():
    """測試港股 bot（同一套診斷）。"""
    import notifier
    d = notifier.diagnose("hk")
    d["error"] = None if d.get("ok") else d.get("verdict")
    return jsonify(d)


def _feed_auth() -> bool:
    """檢查推送 token（未設定 GOLD_FEED_TOKEN 就唔檢查）。"""
    tok = config.GOLD_FEED_TOKEN
    if not tok:
        return True
    return request.headers.get("X-Feed-Token", "") == tok


@app.route("/api/gold/feed", methods=["GET"])
def gold_feed_status():
    """真實報價接收狀態 —— 畀網頁顯示「EA 連線中／斷線」。"""
    import feed
    st = feed.status()
    st["archive"] = feed.archive()
    return jsonify(st)


@app.route("/api/gold/deals", methods=["POST", "GET"])
def gold_deals():
    """POST：EA 推送成交記錄（桌面版讀券商歷史，手機落嘅單一樣讀到）。
    GET：睇收到嘅成交 + 有幾多張草稿等確認。

    收到嘅成交唔會直接入帳 —— 全部開 status='draft'，
    要用戶喺網頁核對完撳「確認」先成為正式日誌。
    """
    import deals
    if request.method == "GET":
        return jsonify({"ok": True, "deals": deals.recent(30),
                        "stats": deals.stats()})
    if not _feed_auth():
        return jsonify({"ok": False, "error": "token 唔啱"}), 403
    d = request.get_json(silent=True) or {}
    return jsonify(deals.ingest(d.get("deals") or []))


@app.route("/api/gold/journal/<int:tid>/approve", methods=["POST"])
def gold_journal_approve(tid):
    """確認草稿 → 正式入帳。用戶自己睇過先撳，系統唔會代你決定。"""
    import journal
    d = request.get_json(silent=True) or {}
    ok = journal.approve(tid, d)      # d 可以帶用戶改過嘅欄位
    return jsonify({"ok": ok})


@app.route("/api/gold/journal/<int:tid>/discard", methods=["POST"])
def gold_journal_discard(tid):
    """丟棄草稿 —— 入錯或者唔想記嘅，直接刪走，唔會污染統計。"""
    import journal
    return jsonify({"ok": journal.discard(tid)})


@app.route("/api/gold/tick", methods=["POST"])
def gold_feed_tick():
    """收 EA / Python 收集器推過嚟嘅即時報價。"""
    import feed
    if not _feed_auth():
        return jsonify({"ok": False, "error": "token 唔啱"}), 403
    d = request.get_json(silent=True) or {}
    try:
        r = feed.save_tick(
            symbol=str(d.get("symbol") or "XAUUSD"),
            bid=float(d["bid"]), ask=float(d["ask"]),
            source=str(d.get("source") or "mt4"),
            tick_ts=d.get("t"))
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"格式唔啱：{exc}"}), 400
    return jsonify({"ok": True, **r})


@app.route("/api/gold/bars", methods=["POST"])
def gold_feed_bars():
    """收 EA / Python 收集器推過嚟嘅 K 線（你券商嘅真實 OHLC）。

    收到之後，gold.fetch() 會自動改用呢批數據 —— 價位就同 MT4 完全一致。
    """
    import feed
    if not _feed_auth():
        return jsonify({"ok": False, "error": "token 唔啱"}), 403
    d = request.get_json(silent=True) or {}
    bars = d.get("bars") or []
    if not isinstance(bars, list) or not bars:
        return jsonify({"ok": False, "error": "冇 bars"}), 400
    n = feed.save_bars(
        symbol=str(d.get("symbol") or "XAUUSD"),
        interval=int(d.get("interval") or 15),
        bars=bars,
        source=str(d.get("source") or "mt4"))
    feed.prune_ticks()
    st = feed.status()
    log.info("收到券商 K 線 %d 支（來源 %s）→ %s",
             n, d.get("source"), st["hint"])
    return jsonify({"ok": True, "saved": n, "live": st["live"],
                    "bars_count": st["bars_count"], "hint": st["hint"]})


@app.route("/api/gold/journal", methods=["GET", "POST"])
def gold_journal():
    """黃金交易日誌 —— 記錄執行偏差、點差、跟足程度。"""
    import journal
    if request.method == "GET":
        return jsonify({"ok": True, "trades": journal.list_trades(),
                        "drafts": journal.drafts(),
                        "stats": journal.stats(),
                        "follow_options": journal.FOLLOW_OPTIONS})
    d = request.get_json(silent=True) or {}
    try:
        tid = journal.add_trade(d)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "id": tid, "trades": journal.list_trades(),
                    "stats": journal.stats()})


@app.route("/api/gold/journal/<int:tid>", methods=["POST", "DELETE"])
def gold_journal_one(tid):
    import journal
    if request.method == "DELETE":
        return jsonify({"ok": journal.delete_trade(tid)})
    d = request.get_json(silent=True) or {}
    return jsonify({"ok": journal.update_trade(tid, d),
                    "trades": journal.list_trades(), "stats": journal.stats()})


@app.route("/api/gold/journal/<int:tid>/close", methods=["POST"])
def gold_journal_close(tid):
    import journal
    d = request.get_json(silent=True) or {}
    try:
        ok = journal.close_trade(tid, d.get("exit_price"), d.get("pnl_usd"),
                                 d.get("note"), d.get("followed"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": ok, "trades": journal.list_trades(),
                    "stats": journal.stats()})



# ------------------------------------------------------------------ 截圖 OCR
def _shot_dir():
    import os
    base = os.path.dirname(os.path.abspath(config.DB_PATH))
    d = os.path.join(base, "shots")
    os.makedirs(d, exist_ok=True)
    return d


def _hk_from_mt4(s):
    """MT4 伺服器時間 → 香港時間。全系統唯一嘅轉換點。

    2026-10-01：之前轉換散喺 ocr.py 內部，結果「日期」做咗、「時間」又
    用原始值蓋返，出咗「備註話轉咗、實際冇轉」。而家只此一處。
    """
    if not s:
        return None
    try:
        from datetime import datetime, timedelta
        off = int(getattr(config, "MT4_TZ_OFFSET", 6))
        dt = datetime.strptime(str(s).replace("T", " ")[:16], "%Y-%m-%d %H:%M")
        return (dt + timedelta(hours=off)).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return str(s)


@app.route("/api/gold/ocr", methods=["POST"])
def gold_ocr():
    """上載 MT4 截圖 → OCR 讀出成交 → 每筆開一張草稿等你確認。

    2026-10-01 重寫：
      · 真實 MT4 手機「歷史」分頁係一張表，一版通常有幾筆成交
        → 一次過開多張草稿，唔再只讀一筆。
      · 全程 try/except，保證永遠回 JSON。之前 OCR 一爆就回 Flask 嘅
        HTML 錯誤頁，前端只見到 "Unexpected token '<'" —— 睇唔到真正原因。
    """
    import os
    import time
    import traceback

    import journal
    import ocr

    try:
        f = request.files.get("shot")
        if f is None:
            return jsonify({"ok": False, "error": "冇收到圖檔"}), 200
        data = f.read()
        if not data:
            return jsonify({"ok": False, "error": "圖檔係空"}), 200
        if len(data) > 12 * 1024 * 1024:
            return jsonify({"ok": False, "error": "圖太大（上限 12 MB）"}), 200

        # 存圖（連埋記錄保留，方便日後翻查）
        name = time.strftime("%Y%m%d-%H%M%S") + ".png"
        try:
            with open(os.path.join(_shot_dir(), name), "wb") as fh:
                fh.write(data)
        except OSError:
            app.logger.exception("存截圖失敗")
            name = None

        if not ocr.available():
            st = ocr.status()
            return jsonify({"ok": False, "shot": name, "created": [],
                            "error": "OCR 未就緒（引擎：%s）" % (st.get("engine"),),
                            "diag": st}), 200

        trades = ocr.extract_trades(data)

        if not trades:
            return jsonify({
                "ok": True, "created": [], "shot": name, "count": 0,
                "notes": ["讀唔到任何成交。確認截圖包含 MT4 嘅「歷史」分頁表格"
                          "（要有訂單號、時間、價格嗰幾欄），同埋唔好裁得太窄。"],
            }), 200

        created, skipped = [], []
        for t in trades:
            d = {
                "direction": t.get("direction"),
                "actual_entry": t.get("actual_entry"),
                "actual_stop": t.get("actual_stop"),
                "signal_target": t.get("signal_target_hint"),
                "exit_price": t.get("exit_price"),
                "pnl_usd": t.get("profit"),
                "lot": t.get("lots"),
                "opened_at": _hk_from_mt4(t.get("opened_at")),
                "closed_at": _hk_from_mt4(t.get("closed_at")),
                "note": "MT4 成交 #%s｜MT4 顯示 %s → 香港 %s｜OCR 信心 %.0f%%（%s）" % (
                    t.get("ticket"),
                    t.get("opened_at") or "?",
                    _hk_from_mt4(t.get("opened_at")) or "?",
                    (t.get("confidence") or 0) * 100,
                    ocr.status().get("engine")),
                "data_source": "mt4",
                "status": "draft",          # 草稿：唔計入統計，等用戶確認
                "shot": name,
                "ticket": t.get("ticket"),  # 去重用（刪咗記錄就可以重新匯入）
            }
            if journal.deal_exists(t.get("ticket")):
                skipped.append(t.get("ticket"))
                continue
            tid = journal.add_trade(d)
            if tid:
                journal.mark_deal_journal(t.get("ticket"), tid)
                created.append(tid)
            else:
                skipped.append(t.get("ticket"))
        return jsonify({
            "ok": True, "created": created, "skipped": skipped,
            "count": len(trades),
            "notes": (["%d 張草稿已開好，請逐張核對後確認入帳。" % len(created)]
                      if created else
                      ["呢 %d 筆之前已經匯入過，全部跳過。" % len(skipped)]),
        }), 200

    except Exception as e:  # noqa: BLE001
        app.logger.exception("OCR 失敗")
        import traceback
        return jsonify({
            "ok": False, "error": "%s: %s" % (type(e).__name__, e),
            "trace": traceback.format_exc()[-800:],
        }), 200


@app.route("/api/gold/shot/<path:name>", methods=["GET"])
def gold_shot(name):
    """睇返之前上載嘅截圖（限制喺 shots 目錄內）。"""
    import os
    from flask import send_from_directory
    d = os.path.realpath(_shot_dir())
    full = os.path.realpath(os.path.join(d, name))
    if not full.startswith(d):
        return jsonify({"ok": False, "error": "唔准"}), 403
    if not os.path.exists(full):
        return jsonify({"ok": False, "error": "冇呢張圖"}), 404
    return send_from_directory(d, os.path.basename(full))


@app.route("/api/gold/ocr-status", methods=["GET"])
def gold_ocr_status():
    import ocr
    return jsonify({"ok": True, **ocr.status()})


@app.route("/api/db-status", methods=["GET"])
def api_db_status():
    """資料庫係咪喺持久化位置 —— 網頁用嚟顯示警告橫幅。"""
    import backup
    return jsonify({"ok": True, **backup.db_status()})


@app.route("/api/backup", methods=["GET"])
def api_backup():
    """匯出所有記錄做 JSON 檔案下載。"""
    import backup
    from urllib.parse import quote
    data = backup.export_all()
    day = data["exported_at"][:10]
    ascii_name = f"TyLove_backup_{day}.json"
    pretty = f"TyLove備份_{day}.json"
    return Response(
        json.dumps(data, ensure_ascii=False, indent=1),
        mimetype="application/json; charset=utf-8",
        headers={
            # 2026-09-30：原本只用中文檔名，部分瀏覽器（尤其手機）
            # 會因為檔名含非 ASCII 字元而靜靜地唔下載，用戶只見到「冇反應」。
            # 所以同時畀 ASCII 檔名 + RFC 5987 編碼嘅中文名。
            "Content-Disposition":
                f"attachment; filename=\"{ascii_name}\"; "
                f"filename*=UTF-8''{quote(pretty)}",
        })


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """由備份 JSON 還原記錄。"""
    import backup
    d = request.get_json(silent=True)
    if d is None:
        f = request.files.get("file")
        if f is None:
            return jsonify({"ok": False, "error": "冇收到檔案"}), 400
        try:
            d = json.loads(f.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"讀唔到 JSON：{exc}"}), 400
    try:
        r = backup.import_all(d)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    log.info("還原備份：%s", r)
    return jsonify({"ok": True, **r, "status": backup.db_status()})

@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    """風控參數：網頁讀取／修改，改完即時生效。"""
    if request.method == "POST":
        payload = request.get_json(force=True) or {}
        try:
            settings_store.save(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            log.exception("儲存風控參數失敗")
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, **settings_store.describe()})
    return jsonify(settings_store.describe())


@app.route("/api/positions", methods=["GET", "POST"])
def positions_api():
    from positions import close_position, open_position
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        try:
            symbol = str(d["symbol"]).upper().strip()
            entry = float(d["entry_price"])
            qty = float(d["qty"])
            name = d.get("name") or symbol
            stop = float(d["stop_loss"]) if d.get("stop_loss") else None
            t1 = float(d["target1"]) if d.get("target1") else None
            t2 = float(d["target2"]) if d.get("target2") else None
            atr = None

            # 冇填止蝕／目標 → 自動用 ATR 幫你計（你只需要知自己幾錢入、買幾多股）
            if stop is None or t1 is None or t2 is None:
                try:
                    import data_fetcher as df_mod
                    import indicators
                    import trade_plan
                    daily = df_mod.fetch_daily(symbol)
                    if len(daily) >= 60:
                        snap = indicators.latest_snapshot(indicators.compute(daily))
                        atr = snap.get("atr")
                        if atr:
                            plan = trade_plan.build_plan(symbol, name, entry, atr, 100)
                            if plan.get("valid") is not False:
                                stop = stop if stop is not None else plan["stop_loss"]
                                t1 = t1 if t1 is not None else plan["target1"]
                                t2 = t2 if t2 is not None else plan["target2"]
                except Exception:  # noqa: BLE001
                    log.exception("自動計止蝕止賺失敗，改用你填嘅值")
            if stop is None:
                return jsonify({"ok": False, "error": "無法自動計算止蝕，請自己填止蝕價"}), 400

            pid = open_position(symbol, name, entry, qty, stop, t1, t2, atr, d.get("note", ""))
            log.info("新增持倉 #%s %s 入場 %.3f × %s 股，止蝕 %.3f", pid, symbol, entry, qty, stop)
            return jsonify({"ok": True, "id": pid, "stop_loss": stop,
                            "target1": t1, "target2": t2, "auto": atr is not None})
        except KeyError as exc:
            return jsonify({"ok": False, "error": f"缺少欄位 {exc}"}), 400
        except Exception as exc:  # noqa: BLE001
            log.exception("新增持倉失敗")
            return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"open": list_open(), "closed": list_closed(30), "stats": realised_stats()})


@app.route("/api/positions/<int:pid>/close", methods=["POST"])
def close_api(pid):
    from positions import close_position
    d = request.get_json(force=True) or {}
    close_position(pid, float(d["exit_price"]), d.get("reason", "手動平倉"))
    return jsonify({"ok": True})


@app.route("/api/signals")
def signals_api():
    return jsonify({"signals": recent_signals(60)})


_BG_STARTED = False


def _warn_if_not_persistent() -> None:
    """檢查資料庫係咪喺持久化嘅位置。

    呢個係最易蝕錢嘅技術問題：如果 DB 唔喺 Volume 上面，
    Railway 每次重新部署都會清空你所有持倉同交易紀錄。
    """
    import config as _cfg
    path = str(_cfg.DB_PATH)
    if path.startswith("/data") or os.getenv("DB_PERSIST_OK") == "1":
        log.info("資料庫位置：%s（持久化正常）", path)
        return
    log.warning(
        "⚠️  資料庫位置 = %s —— 唔喺 Railway Volume 掛載點上面，"
        "每次重新部署都會清空持倉同交易紀錄！"
        "解決方法：Railway → 服務 → Settings → Volumes 掛一個 volume 去 /data，"
        "再喺 Variables 加 DB_PATH=/data/tylove.db。", path)


def start_background():
    """啟動背景排程（只會啟動一次）。

    無論係 `python main.py` 定係 gunicorn 匯入（main:app / dashboard:app），
    都會自動起排程，唔會出現「網頁開得到但永遠唔掃描」嘅情況。
    """
    global _BG_STARTED
    if _BG_STARTED:
        return
    _BG_STARTED = True
    init_db()
    import journal; journal.init_db()
    _warn_if_not_persistent()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    # 開機先做一次掃描，確認設定正確
    threading.Thread(target=lambda: (time.sleep(5), job_scan("啟動掃描")), daemon=True).start()
    log.info("背景排程已啟動（掃描 %s／每 %d 分鐘監控／%s 收市提醒）",
             SCAN_TIMES, MONITOR_INTERVAL_MIN, CLOSE_REMINDER_TIME)


# 模組被匯入時即刻啟動排程 —— 呢句一定要放喺 if __name__ 之外
start_background()


if __name__ == "__main__":
    log.info("TyLove 啟動，port %s", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
