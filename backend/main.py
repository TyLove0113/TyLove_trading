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

import schedule
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

import scanner
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


def job_close_reminder():
    try:
        scanner.closing_reminder()
    except Exception:  # noqa: BLE001
        log.exception("收市提醒失敗")


def setup_schedule():
    for t in SCAN_TIMES:
        t = t.strip()
        if t:
            schedule.every().day.at(t).do(job_open_scan)
    schedule.every(MONITOR_INTERVAL_MIN).minutes.do(job_monitor)
    schedule.every().day.at(CLOSE_REMINDER_TIME).do(job_close_reminder)
    log.info("排程設定完成：掃描 %s／每 %d 分鐘監控／%s 收市提醒",
             SCAN_TIMES, MONITOR_INTERVAL_MIN, CLOSE_REMINDER_TIME)


def scheduler_loop():
    setup_schedule()
    while True:
        try:
            schedule.run_pending()
        except Exception:  # noqa: BLE001
            log.exception("排程執行出錯")
        time.sleep(20)


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


@app.route("/api/positions", methods=["GET", "POST"])
def positions_api():
    from positions import close_position, open_position
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        try:
            pid = open_position(
                d["symbol"].upper(), d.get("name", d["symbol"].upper()),
                float(d["entry_price"]), float(d["qty"]), float(d["stop_loss"]),
                d.get("target1"), d.get("target2"), d.get("atr"), d.get("note", ""),
            )
            return jsonify({"ok": True, "id": pid})
        except Exception as exc:  # noqa: BLE001
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
