"""截圖 OCR —— 讀 MT4 手機「歷史」分頁嘅成交表。

2026-10-01 重寫。原因：舊版假設截圖上面有 "Open"／"Close"／"Profit"
呢啲標籤，但真實 MT4 手機版係一張表：

  訂單        時間          類型  大小  交易品種  價格      S/L       T/P
  1628080512  2026.09.21 13:49 buy  0.05  XAUUSD  4367.08  4359.26  4377.69
  ...（後面再接：時間、價格、庫存費、利潤）

完全冇標籤。所以改為「按 y 分行、按 x 排欄、按樣式分類」。

一張截圖通常有幾筆成交 —— extract_trades() 回傳 list。

引擎：rapidocr（主）→ tesseract（後備）。兩個都係「文字 + 位置」，
所以歸一化成同一種 words 格式再解析。
"""
from __future__ import annotations

import config
import io
import logging
import os
import re

log = logging.getLogger("tylove.ocr")

# ------------------------------------------------------------------ 引擎
try:
    from PIL import Image
    _PIL = True
except Exception:  # noqa: BLE001
    _PIL = False

# ── 系統庫預載（2026-10-04）────────────────────────────────────
# 症狀：libxcb.so.1 明明存在，但 import cv2 報
#   "ImportError: libxcb.so.1: cannot open shared object file"
# 根因：Nixpacks 嘅 Python 由 Nix 提供，佢嘅動態載入器搜尋路徑
#   唔包含 /usr/lib/x86_64-linux-gnu —— 所以 apt 裝好嘅庫搵唔到。
#   呢個解釋得通「檔案 ✅ 但載入器話搵唔到」呢個矛盾。
# 解法：用【絕對路徑】ctypes 預載成條依賴鏈（RTLD_GLOBAL），
#   之後 cv2 再 dlopen 同名庫就會命中已載入嘅版本，唔使再搜尋。
_SYSLIB_DIRS = ("/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu",
                "/usr/lib64", "/usr/lib", "/lib", "/usr/local/lib")
_SYSLIB_CHAIN = (
    "libmd.so.0", "libbsd.so.0", "libXau.so.6", "libXdmcp.so.6",
    "libxcb.so.1", "libX11.so.6", "libXext.so.6", "libXrender.so.1",
    "libGL.so.1", "libgomp.so.1",
)
_SYSLIB_LOADED = set()
_SYSLIB_NOTE = ""


def _preload_syslibs() -> str:
    """預載系統庫鏈。回傳簡短結果，放喺 OCR 狀態供排查。"""
    global _SYSLIB_NOTE
    if _SYSLIB_NOTE:
        return _SYSLIB_NOTE
    import ctypes
    got, miss = [], []
    for name in _SYSLIB_CHAIN:
        found = False
        for d in _SYSLIB_DIRS:
            fp = os.path.join(d, name)
            if not os.path.exists(fp):
                continue
            found = True
            try:
                ctypes.CDLL(fp, mode=ctypes.RTLD_GLOBAL)
                _SYSLIB_LOADED.add(name)
                got.append(name)
            except OSError:
                miss.append(name)
            break
        if not found:
            miss.append(name)
    _SYSLIB_NOTE = "預載 %d 個成功／%d 個失敗%s" % (
        len(got), len(miss),
        ("，失敗：" + "、".join(miss)) if miss else "")
    return _SYSLIB_NOTE


_preload_syslibs()


_RAPID, _RAPID_ERR = None, None
try:
    from rapidocr_onnxruntime import RapidOCR
    _RAPID_ERR = None
except Exception as e:  # noqa: BLE001
    _RAPID_ERR = f"{type(e).__name__}: {e}"

_TESS = False
try:
    import pytesseract
    _TESS = True
except Exception:  # noqa: BLE001
    pass

_TESS_BIN_ERR = None
if _TESS:
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:  # noqa: BLE001
        _TESS_BIN_ERR = f"{type(e).__name__}: {e}"

_rapid_obj = None


def _rapid():
    """rapidocr 實例（第一次用先載入模型，之後重用）。"""
    global _rapid_obj, _RAPID
    if _rapid_obj is not None:
        return _rapid_obj
    if _RAPID_ERR and _RAPID is None:
        return None
    try:
        _rapid_obj = RapidOCR()
        _RAPID = True
        return _rapid_obj
    except Exception as e:  # noqa: BLE001
        log.exception("rapidocr 載入失敗")
        globals()["_RAPID_ERR"] = f"{type(e).__name__}: {e}"
        return None


def _tess_ok() -> bool:
    return bool(_TESS and not _TESS_BIN_ERR)


def available() -> bool:
    return _PIL and (_rapid() is not None or _tess_ok())


def engine() -> str | None:
    if not _PIL:
        return None
    if _rapid() is not None:
        return "rapidocr"
    if _tess_ok():
        return "tesseract"
    return None


def _syslib() -> dict:
    """診斷用：三個關鍵系統庫喺唔喺度。

    2026-10-04 加。之前只知「rapidocr import 失敗」，
    但分唔清係「套件冇裝到」定「裝咗但載入器搵唔到」——
    兩者修法完全唔同，所以要分開報。
    """
    import os
    dirs = ("/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu", "/usr/lib64",
            "/usr/lib", "/lib", "/usr/local/lib", "/opt/venv/lib")
    out = {}
    for f in ("libGL.so.1", "libxcb.so.1", "libgomp.so.1"):
        out[f] = any(os.path.exists(os.path.join(d, f)) for d in dirs)
    return out


def status() -> dict:
    eng = engine()
    errs = {}
    if _RAPID_ERR:
        errs["rapidocr"] = _RAPID_ERR
    if _TESS_BIN_ERR:
        errs["tesseract_bin"] = _TESS_BIN_ERR
    if not _PIL:
        errs["pillow"] = "Pillow 未安裝"
    return {
        "syslib": _syslib(),
        "preload": _preload_syslibs(),
        "available": eng is not None,
        "engine": eng,
        "pillow": _PIL,
        "rapidocr_import": _RAPID_ERR is None,
        "tesseract_import": _TESS,
        "tesseract_binary": _tess_ok(),
        "errors": errs,
        "hint": ("OCR 未就緒" if eng is None else f"OCR 已就緒（{eng}）"),
    }


# ------------------------------------------------------------------ 讀字
def _cap_size(img, max_w: int = 2000):
    """限制影像闊度。手機截圖原圖可到 3000+ px，
    （a）tesseract 太闊反而易食字，（b）處理大圖用多幾倍記憶體
    —— 之前容器每次上載圖都重啟，呢個係嫌疑之一。
    2026-10-04 加。"""
    try:
        if img.width > max_w:
            r = max_w / float(img.width)
            return img.resize((max_w, max(1, int(img.height * r))))
    except Exception:  # noqa: BLE001
        pass
    return img


def _words(img):
    img = _cap_size(img)
    """回傳 [(x, y, text, conf)]，兩個引擎都歸一化成同一格式。"""
    import numpy as np
    r = _rapid()
    out = []
    if r is not None:
        res, _ = r(np.array(img))
        for box, txt, score in (res or []):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((min(xs), min(ys), (txt or "").strip(), float(score)))
        return out
    if _tess_ok():
        import pytesseract
        d = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT,
                                      config="--psm 6")
        for i, t in enumerate(d["text"]):
            t = (t or "").strip()
            if not t:
                continue
            conf = float(d["conf"][i]) / 100.0
            if conf <= 0:
                continue
            out.append((float(d["left"][i]), float(d["top"][i]), t, conf))
    return out


# ------------------------------------------------------------------ 樣式
_RE_TICKET = re.compile(r"^\d{9,10}$")
_RE_DATE = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_RE_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
_RE_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
_TYPE = {"buy": "long", "sell": "short", "買": "long", "賣": "short"}


def _is_date(t: str) -> bool:
    return bool(_RE_DATE.search(t))


def _num(t: str):
    s = t.replace(" ", "").replace(",", "").replace("−", "-")
    if not _RE_NUM.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _kind(t: str) -> str:
    """判斷一段文字係咩欄位。"""
    s = t.strip()
    low = s.lower().replace(" ", "")
    if low in _TYPE or low in ("buy", "sell"):
        return "type"
    if _RE_TICKET.match(s):
        return "ticket"
    if _is_date(s):
        return "date"
    if _RE_TIME.fullmatch(s):
        return "time"
    n = _num(s)
    if n is None:
        if re.fullmatch(r"[A-Za-z]{3,10}", s):
            return "symbol"
        return "other"
    if abs(n) < 1 and n != 0:
        return "lots"
    if abs(n) >= 100:
        return "price"
    return "small"


def _norm_dt(txt: str) -> str | None:
    """'2026.09.21 13:49' → '2026-09-21 13:49'（只統一格式，唔改時區）。"""
    d = _RE_DATE.search(txt)
    if not d:
        return None
    y, mo, da = d.group(1), d.group(2).zfill(2), d.group(3).zfill(2)
    t = _RE_TIME.search(txt)
    hh = f"{t.group(1).zfill(2)}:{t.group(2)}" if t else "00:00"
    return f"{y}-{mo}-{da} {hh}"


def _mt4_raw(txt: str) -> str | None:
    """只將 MT4 時間正規化，唔做時區轉換。

    2026-10-01 改：時區轉換收起喺 main.py 一處做（之前散喺呢度，
    結果條條路徑唔一致 —— 試過日期做咗、時間又用原始值蓋返）。
    OCR 只負責如實讀出 MT4 顯示嘅時間。
    """
    from datetime import datetime, timedelta
    base = _norm_dt(txt)
    if not base:
        return None
    return base


# ------------------------------------------------------------------ 主流程
def _rows(data) -> list[dict]:
    """抽出截圖入面所有「似成交」嘅行（開倉 + 平倉都收）。

    2026-10-03：以前呢個函數叫 extract_trades，但佢連「交易」分頁嘅
    未平倉持倉都當成已平倉成交 —— 而「交易」分頁嘅第 4 個價格其實係
    「現價」、最後個數字係「浮動盈虧」。結果會寫入假平倉價同假盈虧。
    而家一律先收齊，再由 extract_trades / extract_positions 分辨。
    """
    if isinstance(data, (bytes, bytearray)):
        img = Image.open(io.BytesIO(data)).convert("RGB")
    else:
        img = data.convert("RGB")
    raw_scale = img.width / 1280.0 or 1.0

    words = _words(img)
    if not words:
        return []

    # 1) 搵「有 9–12 位訂單號」嘅行 —— 嗰啲就係成交行
    tickets = [w for w in words if _kind(w[2]) == "ticket"]
    if not tickets:
        return []

    tol = max(10.0, 20.0 * raw_scale)   # 同一行嘅 y 容差
    trades = []
    # ⚠️ 2026-10-01：原本用 id((tx,ty)) 做「已處理」標記 —— 錯。
    #    Python 釋放咗嘅 tuple，下一個 tuple 會攞返同一個記憶體地址，
    #    所以第 2–4 行被誤判為已處理而跳過（實測 4 筆只抽到 1 筆）。
    #    改為用 y 座標本身做去重（同一行嘅 y 差唔會超過 tol）。
    seen_y = []
    for tx, ty, ttxt, tconf in sorted(tickets, key=lambda w: w[1]):
        if any(abs(ty - y0) <= tol for y0 in seen_y):
            continue
        seen_y.append(ty)
        row = [w for w in words if abs(w[1] - ty) <= tol]
        # 同一行唔可以有兩個訂單號（避免重複收行）
        if sum(1 for w in row if _kind(w[2]) == "ticket") > 1:
            row = [w for w in row if w[2] == ttxt or _kind(w[2]) != "ticket"]
        row.sort(key=lambda w: w[0])

        # ⚠️ 2026-10-01：MT4 手機版價位寫成「4 290.43」（千位分隔用空格）。
        #    rapidocr 會讀成一段，但 tesseract 會拆開 → 平倉價變 290.43（少個 4）。
        #    呢度將「單一數字 + 後面嘅價格」合併返。
        merged = []
        i = 0
        while i < len(row):
            x, y, txt, cf = row[i]
            if (re.fullmatch(r"[1-9]", txt.strip()) and i + 1 < len(row)
                    and re.fullmatch(r"\d{3}\.\d{2}", row[i + 1][2].strip())):
                merged.append((x, y, txt.strip() + row[i + 1][2].strip(),
                               min(cf, row[i + 1][3])))
                i += 2
                continue
            merged.append(row[i])
            i += 1
        row = merged

        got = {"ticket": ttxt}
        dates, prices, extra = [], [], []
        _pending_date = None
        confs = [tconf]
        types = []
        for x, y, txt, cf in row:
            k = _kind(txt)
            confs.append(cf)
            if k == "date":
                # 日期同時間可能係兩段獨立文字 → 記住原始日期，等時間嚟到先合併。
                # ⚠️ 唔可以喺呢度就 _to_hk()：如果時間係另一段文字，下面
                #    會用原始 MT4 時間覆蓋返個鐘，令 +6 轉換白做（2026-10-01 修）。
                if _pending_date:
                    dates.append(_norm_dt(_pending_date))
                _pending_date = txt.strip()
            elif k == "time":
                # 日期 + 時間兩段 → 先合併成原始 MT4 時間，再一次過轉香港時間。
                if _pending_date:
                    raw = _pending_date[:10] + " " + txt.strip().zfill(5)
                    dates.append(_norm_dt(raw))
                    _pending_date = None
            elif k == "type":
                types.append(_TYPE.get(txt.strip().lower().replace(" ", ""), None))
            elif k == "lots" and "lots" not in got:
                got["lots"] = _num(txt)
            elif k == "symbol" and txt.isupper() and "symbol" not in got:
                got["symbol"] = txt
            elif k == "price":
                prices.append(_num(txt))
            elif k == "small":
                extra.append(_num(txt))
            elif k == "ticket":
                pass
        if _pending_date:
            dates.append(_norm_dt(_pending_date))
        got["direction"] = next((t for t in types if t), None)
        got["opened_at"] = dates[0] if dates else None
        got["closed_at"] = dates[1] if len(dates) > 1 else None
        got["actual_entry"] = prices[0] if prices else None
        got["actual_stop"] = prices[1] if len(prices) > 1 else None
        got["signal_target_hint"] = prices[2] if len(prices) > 2 else None
        got["exit_price"] = prices[3] if len(prices) > 3 else None
        got["profit"] = extra[-1] if extra else None
        got["confidence"] = round(min(confs), 2)
        # 一個真行：至少要有入場價 + 另一個價格。
        # ⚠️ 唔可以用 exit_price 做條件 —— 「交易」分頁嘅未平倉單都有
        #    第 4 個價格（現價），會令佢被誤當成已平倉。
        if got.get("actual_entry") and len(prices) >= 2:
            got["is_closed"] = bool(got.get("opened_at") and got.get("closed_at"))
            trades.append(got)
    return trades


_CACHE = {}


def analyze(data) -> dict:
    """一次過 OCR 完，之後所有查詢都重用個結果。

    點解要 cache：同一張圖跑兩次 OCR = 雙倍記憶體 + 雙倍時間。
    喺 Railway 細容器上好容易令 process 死 → 容器重啟 →
    Telegram 重新派發未確認嘅 update → 你就見到「sd 一次讀兩次」。
    用圖片內容嘅 hash 做 key，同一張圖只會真正 OCR 一次。
    """
    import hashlib
    try:
        key = hashlib.sha1(bytes(data)).hexdigest()
    except Exception:  # noqa: BLE001
        key = None
    hit = _CACHE.get("v")
    if key and hit and hit[0] == key:
        return hit[1]
    rows = _rows(data)
    closed = [r for r in rows if r.get("is_closed")]
    opened = [r for r in rows if not r.get("is_closed")]
    what = "history" if closed else ("positions" if opened else "nothing")
    out = {"rows": rows, "closed": closed, "open": opened, "what": what}
    if key:
        _CACHE["v"] = (key, out)      # 只留一份，唔會愈食愈多記憶體
    return out


def extract_trades(data) -> list[dict]:
    """**已平倉**成交（MT4「歷史」分頁）—— 呢啲先可以入日誌。"""
    return analyze(data)["closed"]


def extract_positions(data) -> list[dict]:
    """**未平倉**持倉（MT4「交易」分頁）—— 唔可以入日誌。

    「交易」分頁每行只有一個時間（開倉），冇平倉時間；
    第 4 個價格係「現價」，最後個數字係「浮動盈虧」。
    """
    return analyze(data)["open"]


def diagnose(data) -> dict:
    """話畀用戶知：呢張圖到底係咩？等錯誤訊息講得出真正原因。"""
    try:
        a = analyze(data)
    except Exception as e:  # noqa: BLE001
        return {"rows": 0, "closed": 0, "open": 0,
                "error": "%s: %s" % (type(e).__name__, e)}
    return {"rows": len(a["rows"]), "closed": len(a["closed"]),
            "open": len(a["open"]), "what": a["what"]}


def extract(data) -> dict:
    """向後兼容：回傳第一筆（連 raw list）。"""
    ts = extract_trades(data)
    return {"trades": ts, "count": len(ts)}
