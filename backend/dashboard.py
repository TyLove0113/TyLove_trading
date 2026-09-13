from flask import Flask, jsonify, render_template
from flask_cors import CORS
from data_fetcher import get_all_stocks
from scoring import score_all_stocks
import pytz
from datetime import datetime
import os
import threading
import time

app    = Flask(__name__)
CORS(app)
HK_TZ  = pytz.timezone("Asia/Hong_Kong")

# ── 快取 ──────────────────────────────────
_cache = {
    "results":    [],
    "updated_at": "未有數據",
    "loading":    False,
}

def refresh_cache():
    """背景更新數據，唔阻塞網頁請求"""
    if _cache["loading"]:
        return
    _cache["loading"] = True
    try:
        print("🔄 更新 Dashboard 數據...")
        stocks  = get_all_stocks()
        results = score_all_stocks(stocks)

        output = []
        for r in results:
            output.append({
                "symbol":     r["symbol"],
                "price":      r["price"],
                "change_pct": r["change_pct"],
                "total":      r["total"],
                "grade":      r["grade"],
                "action":     r["action"],
                "technical":  r["technical"],
                "risk":       r["risk"],
                "news": {
                    "score":     r.get("news", {}).get("score", 15),
                    "label":     r.get("news", {}).get("label", "中性"),
                    "headlines": r.get("news", {}).get("headlines", []),
                    "summary":   r.get("news", {}).get("summary", ""),
                },
            })

        _cache["results"]    = output
        _cache["updated_at"] = datetime.now(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")
        print(f"✅ Dashboard 數據更新完成：{_cache['updated_at']}")

    except Exception as e:
        print(f"❌ Dashboard 數據更新失敗: {e}")
    finally:
        _cache["loading"] = False


def background_loop():
    """每 5 分鐘自動更新一次"""
    # 啟動時先更新一次
    refresh_cache()
    while True:
        time.sleep(300)
        refresh_cache()


# 啟動背景更新
_bg_thread = threading.Thread(target=background_loop, daemon=True)
_bg_thread.start()


# ── 路由 ──────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analysis")
def api_analysis():
    """返回快取數據，即時響應"""
    return jsonify({
        "results":    _cache["results"],
        "updated_at": _cache["updated_at"],
        "count":      len(_cache["results"]),
        "loading":    _cache["loading"],
    })


@app.route("/api/refresh")
def api_refresh():
    """手動觸發更新（背景執行）"""
    if not _cache["loading"]:
        t = threading.Thread(target=refresh_cache, daemon=True)
        t.start()
        return jsonify({"status": "updating"})
    return jsonify({"status": "already_updating"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)