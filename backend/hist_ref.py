"""歷史對照查表 —— 2026-11-02 加（用戶要求「歷史對照」功能）。

讀 hist_stats.json（22 年 M15 統計，由 tools/gen_hist.py 離線生成），
按當前訊號條件回傳歷史上同類情況嘅勝率。

⚠️ 呢個係**參考資訊**，唔會改變訊號。因為：
  · 統計顯示整體 27.4% 勝率、-0.042R（未扣成本）
  · 就算最強嘅組合（H1 同向）都只係 +0.025R
  · 扣你 0.41 點差之後仍然負數
所以佢嘅用途係「令你知道歷史上呢種情況成點」，唔係「叫你落單」。
"""
import json
import logging
import os
from functools import lru_cache

log = logging.getLogger("tylove.hist")

_PATH = os.path.join(os.path.dirname(__file__), "hist_stats.json")


@lru_cache(maxsize=1)
def _load():
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        log.warning("讀唔到 hist_stats.json —— 歷史對照停用")
        return {}


def _bucket_hour(h: int) -> str:
    if 4 <= h < 15:
        return "亞洲 04-14"
    if 15 <= h < 20:
        return "倫敦 15-19"
    if 20 <= h < 24:
        return "紐約 20-23"
    return "深夜 00-03"


def _bucket_atr(atr: float, price: float) -> str:
    if not price or price <= 0 or not atr:
        return "未知"
    ap = atr / price
    if ap < 0.0008:
        return "低波動"
    if ap < 0.0016:
        return "中波動"
    return "高波動"


def lookup(side: str, hour_hk: int, atr: float, price: float,
           h1_dir: str = None) -> dict:
    """回傳最匹配嘅歷史統計。

    side    : "long" / "short"
    hour_hk : 香港時間小時（0-23）
    atr     : 當前 ATR 值
    price   : 當前價（用嚟算 ATR%）
    h1_dir  : "long" / "short"（H1 方向 bias）
    """
    d = _load()
    if not d:
        return {}

    hb = _bucket_hour(hour_hk)
    ab = _bucket_atr(atr, price)
    h1rel = ("同向" if h1_dir == side else
             "反向" if h1_dir in ("long", "short") else "未知")

    # 由最精確 → 最寬鬆，揀第一個有足夠樣本（>= 50）嘅
    cands = [
        ("方向+H1", d.get("by_side_h1", {}).get("%s|%s" % (side, h1rel))),
        ("方向+時段", d.get("by_side_hour", {}).get("%s|%s" % (side, hb))),
        ("方向+波動", d.get("by_side_atr", {}).get("%s|%s" % (side, ab))),
        ("只按方向", d.get("by_side", {}).get(side)),
        ("全部歷史", d.get("overall")),
    ]

    picked, label = None, ""
    for name, v in cands:
        if v and v.get("n", 0) >= 50:
            picked, label = v, name
            break
    if not picked:
        return {}

    meta = d.get("meta") or {}
    return {
        "label": label,
        "n": picked["n"],
        "win": picked["win"],
        "avg": picked["avg"],
        "hour": hb,
        "atr": ab,
        "h1": h1rel,
        "years": "%s→%s" % (meta.get("from", "?"), meta.get("to", "?")),
    }


def describe(r: dict) -> str:
    """一句人話。"""
    if not r:
        return ""
    return ("歷史上同類情況（%s，%d 次）：勝率 %.1f%%、平均 %+.3fR（未扣點差）"
            % (r["label"], r["n"], r["win"], r["avg"]))


def summary() -> dict:
    """完整統計表 —— 給網頁顯示（2026-11-02 加）。"""
    d = _load()
    if not d:
        return {"ok": False, "error": "hist_stats.json 讀唔到"}
    return {
        "ok": True,
        "meta": d.get("meta"), "overall": d.get("overall"),
        "by_side": d.get("by_side"), "by_hour": d.get("by_hour"),
        "by_h1": d.get("by_h1"), "by_side_h1": d.get("by_side_h1"),
    }