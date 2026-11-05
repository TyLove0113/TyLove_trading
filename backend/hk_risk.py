"""港股相關性提示（2026-11-05）—— 純提示，唔改選股邏輯。

背景：用戶 63 隻觀察名單，實測有 12 對相關性 ≥0.70
（建行↔工行 0.89、紫金↔洛鉬 0.86、中芯↔華虹 0.80 等）。
如果同時買入高度相關嘅幾隻，風險會疊加 —— 但用戶可能以為已分散。

設計原則（用戶明確要求）：
  · 只出提示，唔做同業分組、唔改評分、唔擋訊號
  · 因為用戶要自己考慮本金同倉位
"""
import json
import logging
import os

log = logging.getLogger("hk_risk")

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hk_corr.json")
_cache = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_FILE, encoding="utf-8") as f:
            _cache = json.load(f)
    except Exception:  # noqa: BLE001
        log.warning("讀唔到 hk_corr.json，相關性提示停用")
        _cache = {}
    return _cache


def peers(symbol: str, min_r: float = 0.70) -> list:
    """回傳同呢隻股票高度相關嘅其他股票（按相關性由高到低）。"""
    d = _load()
    out = [p for p in (d.get("peers") or {}).get(str(symbol), [])
           if float(p.get("r") or 0) >= min_r]
    return out


def level(r: float) -> str:
    """相關性分級 —— 用嚟決定提示語氣。"""
    if r >= 0.85:
        return "極高"
    if r >= 0.78:
        return "高"
    return "偏高"


def hint(symbol: str, held: list = None, min_r: float = 0.70) -> str:
    """一句提示（冇高相關就回空字串）。

    held：用戶已持倉嘅代號清單 —— 如果有重疊，提示會更具體。
    """
    ps = peers(symbol, min_r)
    if not ps:
        return ""
    held = [str(x) for x in (held or [])]
    hit = [p for p in ps if p["with"] in held]
    if hit:
        p = hit[0]
        return ("⚠️ 同你已持有嘅 *%s %s* 相關性 *%.2f*（%s）"
                " —— 兩隻會同步升跌，等於同一風險做兩次"
                % (p["with"], p.get("name", ""), p["r"], level(p["r"])))
    p = ps[0]
    extra = ("　另外 %d 隻亦相關" % (len(ps) - 1)) if len(ps) > 1 else ""
    return ("⚠️ 同 *%s %s* 相關性 *%.2f*（%s）"
            " —— 如同一時間買兩隻，風險會疊加%s"
            % (p["with"], p.get("name", ""), p["r"], level(p["r"]), extra))


def summary() -> dict:
    """給網頁顯示嘅統計（唔涉及任何交易決策）。"""
    d = _load()
    if not d:
        return {"ok": False}
    pairs = []
    seen = set()
    for a, lst in (d.get("peers") or {}).items():
        for p in lst:
            key = tuple(sorted([a, p["with"]]))
            if key in seen:
                continue
            seen.add(key)
            pairs.append({"a": a, "b": p["with"], "b_name": p.get("name", ""),
                          "r": p["r"]})
    pairs.sort(key=lambda x: -x["r"])
    # 補上 A 的名字（前端要顯示兩邊）
    names = {}
    for a, lst in (d.get("peers") or {}).items():
        for p in lst:
            names[p["with"]] = p.get("name", "")
    for p in pairs:
        p["a_name"] = names.get(p["a"], "")
        p["b_name"] = p.get("b_name") or names.get(p["b"], "")
    meta = d.get("meta") or {}
    days = 0
    try:
        from datetime import date
        f = str(meta.get("from") or "")[:10]
        t = str(meta.get("to") or "")[:10]
        if f and t:
            y1, m1, d1 = map(int, f.split("-"))
            y2, m2, d2 = map(int, t.split("-"))
            days = (date(y2, m2, d2) - date(y1, m1, d1)).days
    except Exception:  # noqa: BLE001
        days = 0
    return {"ok": True, "meta": meta, "days": days,
            "avg": d.get("avg_corr"), "pairs_count": d.get("pairs_count"),
            "pairs": pairs}
