"""MT4 截圖 OCR —— 自動讀出入場／平倉／手數／盈虧。

引擎：rapidocr-onnxruntime（純 pip，唔需要系統套件，15 MB）。
       冇裝就退去 pytesseract；兩個都冇時 available() = False，
       程式照跑，只係用唔到 OCR。

點解唔用固定裁切區域（2026-09-30 決定）：
  手機型號、字體大細、MT4 版本都會令座標飄移。rapidocr 會回傳每段
  文字嘅**位置**，所以可以用「搵到 Open 標籤 → 攞佢右邊／同一行嘅數字」，
  版面郁少少都唔會死。真係要精準時仍然可以用 OCR_REGIONS 覆寫。

安全設計：OCR 只會開「草稿」，唔會直接寫入正式記錄 —— 一定要你確認。
"""
from __future__ import annotations

import io
import json
import logging
import os
import re

log = logging.getLogger("tylove.ocr")

try:
    from PIL import Image, ImageOps
    _PIL = True
except Exception:  # noqa: BLE001
    _PIL = False

# 2026-10-01：原本錯誤被靜靜咁吞咗，用戶只見到「OCR 未啟用」但唔知原因。
# 而家記住真實錯誤，喺 status() 度顯示出嚟，方便診斷（尤其 Railway 部署）。
_ERR = {}

try:
    from rapidocr_onnxruntime import RapidOCR
    _RAPID = True
except Exception as e:  # noqa: BLE001
    _RAPID = False
    _ERR["rapidocr"] = f"{type(e).__name__}: {e}"

try:
    import pytesseract
    _TESS = True
except Exception as e:  # noqa: BLE001
    _TESS = False
    _ERR["pytesseract"] = f"{type(e).__name__}: {e}"

# tesseract 係「Python 包裝 + 系統執行檔」兩層，包裝裝好但執行檔唔喺度都會死。
_TESS_BIN = False
if _TESS:
    try:
        pytesseract.get_tesseract_version()
        _TESS_BIN = True
    except Exception as e:  # noqa: BLE001
        _ERR["tesseract_bin"] = f"{type(e).__name__}: {e}"


_rapid_engine = None


def _engine():
    global _rapid_engine
    if _rapid_engine is None and _RAPID:
        try:
            _rapid_engine = RapidOCR()
        except Exception:  # noqa: BLE001
            log.exception("rapidocr 初始化失敗")
            _rapid_engine = False
    return _rapid_engine or None


def available() -> bool:
    if _engine() is not None:
        return True
    if _PIL and _TESS and _TESS_BIN:
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def status() -> dict:
    """回報 OCR 狀態 + 真實錯誤，方便喺 Railway 診斷。"""
    eng = None
    if _engine() is not None:
        eng = "rapidocr"
    elif _TESS and _TESS_BIN:
        eng = "tesseract"
    ready = eng is not None
    if ready:
        hint = f"OCR 已就緒（{eng}）。"
    elif not _PIL:
        hint = "冇裝 Pillow，無法讀圖。"
    elif not _RAPID and not _TESS:
        hint = ("兩個引擎都匯入失敗。睇下面 errors 嘅確實原因 —— "
                "最常見係 cv2 缺 libGL.so.1（解決：喺 Railway 加 nixpacks.toml "
                "裝 libgl1，或者用 tesseract）。")
    else:
        hint = "引擎裝咗但啟動失敗，睇 errors。"
    return {
        "available": ready,
        "engine": eng,
        "pillow": _PIL,
        "ready": ready,
        "rapidocr_import": _RAPID,
        "tesseract_import": _TESS,
        "tesseract_binary": _TESS_BIN,
        "errors": _ERR,
        "hint": hint,
    }


# ------------------------------------------------------------------ 解析
# 標籤（全部細寫比對）→ 對應我哋嘅欄位；中英都收
LABELS = {
    "entry":  ["open", "open price", "entry", "入場", "開倉", "開倉價"],
    "exit":   ["close", "close price", "exit", "平倉", "出場", "平倉價"],
    "sl":     ["s/l", "sl", "stop loss", "止蝕", "停損"],
    "tp":     ["t/p", "tp", "take profit", "止賺", "止盈"],
    "profit": ["profit", "p/l", "盈虧", "盈利", "利潤"],
    "lots":   ["volume", "lots", "lot", "size", "手數", "數量", "成交量"],
}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _nums(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m))
        except ValueError:
            continue
    return out


def _cy(item) -> float:
    b = item[0]
    return sum(p[1] for p in b) / 4.0


def _cx(item) -> float:
    b = item[0]
    return sum(p[0] for p in b) / 4.0


def _row_of(item, items, tol: float = 22.0) -> list:
    """同一行嘅文字（垂直中心差唔多）。"""
    y = _cy(item)
    return [i for i in items if abs(_cy(i) - y) <= tol]


def _digits_score(t: str) -> float:
    """似唔似價位：4 位數 + 兩位小數（黃金大約 4000）。"""
    v = _nums(t)
    if not v:
        return 0.0
    x = abs(v[0])
    return 1.0 if 1000 <= x <= 20000 else 0.0


def _pick_after(label_item, items, field: str):
    """攞標籤右邊、同一行最似嘅數字。"""
    lx, ly = _cx(label_item), _cy(label_item)
    box = label_item[0]
    right_edge = max(p[0] for p in box)

    # ① 標籤自己就包住數字（例：「S/L4193.00」）
    own = _nums(label_item[1])
    if own:
        ok = [n for n in own if 500 <= abs(n) <= 20000]
        if field in ("sl", "tp", "entry", "exit") and ok:
            return ok[0]
        if field == "profit":
            return own[-1]
        if field == "lots":
            small = [n for n in own if 0.01 <= n <= 100 and n < 1]
            if small:
                return small[0]

    # ② 同一行、喺標籤右邊嘅文字
    cands = []
    for it in _row_of(label_item, items):
        if it is label_item:
            continue
        b = it[0]
        left = min(p[0] for p in b)
        if left < right_edge - 5:
            continue
        ns = _nums(it[1])
        if not ns:
            continue
        cands.append((left, ns, it))
    if not cands:
        # ③ 標籤喺上、數值喺下（常見於詳情面板）
        ly = _cy(label_item)
        box2 = label_item[0]
        x0 = min(p[0] for p in box2)
        x1 = max(p[0] for p in box2)
        below = []
        for it in items:
            if it is label_item:
                continue
            if _cy(it) <= ly + 5:
                continue
            if _cy(it) > ly + 90:          # 太遠，唔算同一組
                continue
            cx = _cx(it)
            if not (x0 - 40 <= cx <= x1 + 120):
                continue
            below.append((_cy(it), _nums(it[1])))
        below.sort(key=lambda x: x[0])
        for _y, ns in below:
            for n in ns:
                if field in ("entry", "exit", "sl", "tp") and 500 <= abs(n) <= 20000:
                    return n
                if field == "lots" and 0.01 <= n <= 100 and n < 1:
                    return n
                if field == "profit":
                    return n
        return None
    cands.sort(key=lambda x: x[0])
    for _left, ns, _it in cands:
        if field in ("entry", "exit", "sl", "tp"):
            for n in ns:
                if 500 <= abs(n) <= 20000:
                    return n
        elif field == "lots":
            for n in ns:
                if 0.01 <= n <= 100 and n < 1:
                    return n
        else:
            return ns[-1]
    return None


# ------------------------------------------------------------------ 主函數
def _boxes(im) -> list:
    """回傳 [(box, text, score), ...]"""
    eng = _engine()
    if eng is not None:
        res, _ = eng(im)
        return res or []
    # fallback：tesseract（冇位置，包成假 box）
    txt = pytesseract.image_to_string(ImageOps.autocontrast(ImageOps.grayscale(im)),
                                      config="--psm 6")
    out = []
    for i, line in enumerate((txt or "").splitlines()):
        if line.strip():
            out.append(([[0, i * 20], [999, i * 20], [999, i * 20 + 18], [0, i * 20 + 18]],
                        line.strip(), 0.5))
    return out


def extract(data: bytes) -> dict:
    """由 MT4 截圖讀出交易欄位。

    只回傳**讀到**嘅嘢；讀唔到就唔出現，唔會亂填。
    每個欄位附信心值，等草稿畫面可以標示「呢個要人手核對」。
    """
    if not available():
        return {"ok": False, "error": "OCR 未啟用", "fields": {}, "confidence": {}}
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.width < 700:                      # 太細嘅截圖先放大，幫 OCR
            f = 700 / im.width
            im = im.resize((700, int(im.height * f)), Image.LANCZOS)
        im = im.convert("RGB")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"讀唔到圖片：{e}", "fields": {}, "confidence": {}}

    items = _boxes(im)
    if not items:
        return {"ok": True, "fields": {}, "confidence": {},
                "notes": ["完全讀唔到文字 —— 截圖可能太矇或者太暗。"],
                "count": 0}

    fields: dict = {}
    conf: dict = {}
    notes: list[str] = []

    # 方向
    low_all = " ".join((t or "").lower() for _, t, _ in items)
    if "sell" in low_all or "short" in low_all:
        fields["direction"] = "short"
        conf["direction"] = 0.9
    elif "buy" in low_all or "long" in low_all:
        fields["direction"] = "long"
        conf["direction"] = 0.9

    # 逐個標籤搵值
    for field, keys in LABELS.items():
        if field in fields:
            continue
        for it in items:
            low = (it[1] or "").lower().replace(" ", "")
            if not any(k.replace(" ", "") in low for k in keys):
                continue
            v = _pick_after(it, items, field)
            if v is None:
                continue
            if field in ("entry", "exit", "sl", "tp") and not (500 <= abs(v) <= 20000):
                continue
            if field == "lots" and not (0.01 <= v <= 100):
                continue
            fields[field] = v
            conf[field] = round(float(it[2] or 0.5), 2)
            break

    # 手數冇標籤時：搵細過 1 嘅數字（0.01 / 0.02 / 0.05 …）
    if "lots" not in fields:
        for _b, t, s in items:
            for n in _nums(t):
                if 0.01 <= n <= 0.99:
                    fields["lots"] = n
                    conf["lots"] = round(float(s or 0.5), 2)
                    notes.append("手數係推測出嚟 —— 請核對。")
                    break
            if "lots" in fields:
                break

    return {"ok": True, "fields": fields, "confidence": conf, "notes": notes,
            "count": len(items),
            "texts": [t for _b, t, _s in items][:40]}
