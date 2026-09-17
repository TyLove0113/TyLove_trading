"""
可喺網頁即時修改嘅風控參數
--------------------------------------------------
優先次序：資料庫（網頁改）＞ .env ＞ 程式預設

改完即時生效、唔需要重新部署。原理：除咗更新 config 之外，仲會同步更新
所有已經 `from config import XXX` 載入咗副本嘅模組（例如 scanner、trade_plan），
否則佢哋手上仍然揸住開機時嗰個舊值。
"""
import datetime
import logging
import sqlite3
import sys
from pathlib import Path

import config

log = logging.getLogger("tylove.settings")

_OUR_MODULES = {
    "config", "scanner", "trade_plan", "scoring", "main", "positions",
    "notifier", "data_fetcher", "indicators", "news_sentiment", "settings_store",
    "gold",
}

SPEC = {
    "ACCOUNT_SIZE_HKD": {
        "type": "float", "label": "本金（港幣）", "unit": "HK$", "min": 1000, "max": 100000000,
        "hint": "你打算用幾多錢做短炒。建議注碼由此計算。"},
    "RISK_PER_TRADE_PCT": {
        "type": "float", "label": "每筆最大風險", "unit": "%", "min": 0.1, "max": 10,
        "hint": "建議 0.5–2%。一筆最多輸幾多 % 本金就要止蝕離場。"},
    "MAX_OPEN_POSITIONS": {
        "type": "int", "label": "最多同時持倉", "unit": "筆", "min": 1, "max": 20,
        "hint": "分散風險，建議 3–5 筆。"},
    "MAX_POSITION_PCT": {
        "type": "float", "label": "單一持倉上限", "unit": "%", "min": 1, "max": 100,
        "hint": "一注最多佔本金幾多 %。"},
    "DAILY_LOSS_LIMIT_PCT": {
        "type": "float", "label": "每日停損線", "unit": "%", "min": 0.5, "max": 20,
        "hint": "當日累積虧損到呢個數，程式會叫你停手。"},
    "ATR_STOP_MULT": {
        "type": "float", "label": "止蝕 = ATR ×", "unit": "倍", "min": 0.5, "max": 5,
        "hint": "越大越鬆，越唔易被震走，但每筆虧損越大。建議 1.5。"},
    "ATR_TARGET1_MULT": {
        "type": "float", "label": "目標一 = ATR ×", "unit": "倍", "min": 0.5, "max": 10,
        "hint": "到呢度減半倉。建議 2。"},
    "ATR_TARGET2_MULT": {
        "type": "float", "label": "目標二 = ATR ×", "unit": "倍", "min": 0.5, "max": 15,
        "hint": "到呢度清倉。建議 3。"},
    "ALERT_THRESHOLD": {
        "type": "int", "label": "入場門檻", "unit": "分", "min": 40, "max": 95,
        "hint": "回測顯示 80 分嘅最大回撤最細（-13.9% vs 70 分嘅 -20.5%）。"},
    "WATCH_THRESHOLD": {
        "type": "int", "label": "觀察門檻", "unit": "分", "min": 30, "max": 95,
        "hint": "低過入場門檻，只出觀察提示、唔出交易計劃。"},
    # ---------------- 黃金 XAU/USD ----------------
    "GOLD_MAX_TRADES_PER_DAY": {
        "type": "int", "label": "黃金每日最多訊號", "unit": "個", "min": 0, "max": 50,
        "hint": "0 = 不限（出幾多個都推送）。訊號越多越唔會 miss，但質素會下降；建議 3–5。"},
    "GOLD_STOP_ATR": {
        "type": "float", "label": "黃金止蝕 = ATR ×", "unit": "倍", "min": 0.5, "max": 5,
        "hint": "回測用 1.0。越大越鬆、越唔易被震走，但每筆虧損越大。"},
    "GOLD_TARGET_ATR": {
        "type": "float", "label": "黃金目標 = ATR ×", "unit": "倍", "min": 0.5, "max": 10,
        "hint": "回測用 3.0。目標 ÷ 止蝕 = 盈虧比。"},
}

# 喺任何覆蓋之前記低 .env / 程式預設值，用嚟做「回復預設」
_ENV_DEFAULTS = {k: getattr(config, k) for k in SPEC}


def _conn() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS settings "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)"
        )


def _cast(key: str, raw) -> float:
    spec = SPEC[key]
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{spec['label']} 要填數字")
    if spec["type"] == "int":
        v = int(round(v))
    if not (spec["min"] <= v <= spec["max"]):
        raise ValueError(f"{spec['label']} 要在 {spec['min']} 至 {spec['max']} 之間")
    return v


def load_overrides() -> dict:
    """由資料庫讀返你喺網頁改過嘅值。"""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    out = {}
    for r in rows:
        if r["key"] in SPEC:
            try:
                out[r["key"]] = _cast(r["key"], r["value"])
            except ValueError:
                log.warning("資料庫入面 %s 嘅值唔合法，已忽略", r["key"])
    return out


def _push(key: str, value) -> None:
    """更新 config，並同步更新所有已載入副本嘅模組。"""
    setattr(config, key, value)
    for name, mod in list(sys.modules.items()):
        if mod is None or name.split(".")[0] not in _OUR_MODULES:
            continue
        if getattr(mod, "__name__", "").split(".")[0] in _OUR_MODULES and hasattr(mod, key):
            try:
                setattr(mod, key, value)
            except (AttributeError, TypeError):
                pass


def apply() -> dict:
    """由資料庫載入並套用（冇記錄嘅就用 .env / 預設值）。"""
    saved = load_overrides()
    for k in SPEC:
        _push(k, saved.get(k, _ENV_DEFAULTS[k]))
    return all_values()


def save(payload: dict) -> dict:
    """儲存網頁提交嘅值。payload 帶 __reset__ 就清空所有覆蓋、回復預設。"""
    init_db()
    if payload.get("__reset__"):
        with _conn() as c:
            c.execute("DELETE FROM settings")
        log.info("風控參數已回復預設")
        return apply()

    clean = {}
    for k, raw in (payload or {}).items():
        if k not in SPEC or raw in (None, ""):
            continue
        clean[k] = _cast(k, raw)   # 唔合法會 raise ValueError，由 API 轉做 400

    if clean:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as c:
            for k, v in clean.items():
                c.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (k, str(v), ts),
                )
        log.info("風控參數已更新：%s", clean)
    return apply()


def all_values() -> dict:
    return {k: getattr(config, k) for k in SPEC}


def describe() -> dict:
    return {
        "spec": {
            k: {"type": v["type"], "label": v["label"], "unit": v["unit"],
                "hint": v["hint"], "min": v["min"], "max": v["max"]}
            for k, v in SPEC.items()
        },
        "values": all_values(),
        "defaults": dict(_ENV_DEFAULTS),
    }


# 匯入即套用一次
apply()
