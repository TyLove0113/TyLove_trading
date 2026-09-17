"""
主程式 — 排程 + 網頁儀表板
一個 process 搞掂：Flask 服務（Railway 需要）＋ 背景排程執行緒。
香港時間排程：開市前掃描、日內掃描、持倉監控、收市提醒。
"""
import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request
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
    job_scan("開市前掃描")


def job_gold_scan():
    """黃金 XAU/USD 分析（獨立於港股，用另一個 Telegram bot）。"""
    if not config.GOLD_ENABLED:
        return
    try:
        gold.scan(push=True)
    except Exception:  # noqa: BLE001
        log.exception("黃金掃描失敗")


def job_close_reminder():
    try:
        scanner.closing_reminder()
    except Exception:  # noqa: BLE001
        log.exception("收市提醒失敗")


def _schedule_targets() -> list:
    """今日所有要觸發嘅時間點（香港時間 HH:MM）。"""
    out = [t.strip() for t in SCAN_TIMES if t.strip()]
    out.append(CLOSE_REMINDER_TIME.strip())
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
                if hhmm == CLOSE_REMINDER_TIME.strip():
                    threading.Thread(target=job_close_reminder, daemon=True).start()
                else:
                    threading.Thread(target=job_open_scan, daemon=True).start()
                    threading.Thread(target=job_gold_scan, daemon=True).start()

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
    """測試黃金專用 bot 通唔通。"""
    import notifier
    token, chat = notifier._creds("gold")
    missing = []
    if not token:
        missing.append("TELEGRAM_GOLD_BOT_TOKEN")
    if not chat:
        missing.append("TELEGRAM_GOLD_CHAT_ID")
    if missing:
        return jsonify({
            "ok": False,
            "token_set": bool(token),
            "chat_set": bool(chat),
            "chat_masked": (chat[:4] + "…" + chat[-3:]) if len(chat) > 8 else ("(空白)" if not chat else chat),
            "error": "Railway 讀唔到：" + "、".join(missing) +
                     "。檢查：①Variables 名稱要完全一致 ②加完要 Redeploy ③Chat ID 係數字"
                     "（例如 524897657），唔係 bot token 開頭嗰串。",
        })
    ok = notifier.push("✅ 黃金分析 bot 連線成功（TyLove）。\n呢個係測試訊息。", channel="gold")
    return jsonify({"ok": ok, "error": None if ok else "發送失敗，檢查 token / chat_id"})


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
