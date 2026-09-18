"""
通知模組 — Telegram 推送（純 HTTP，唔用 asyncio）

支援兩個獨立 bot：
  · channel="hk"   → 港股分析（TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）
  · channel="gold" → 黃金分析（TELEGRAM_GOLD_BOT_TOKEN / TELEGRAM_GOLD_CHAT_ID）

兩個 bot 唔填就得，程式會自動降級成 console 輸出，方便本地測試。
"""
import logging

import requests

log = logging.getLogger("tylove.notify")
_API = "https://api.telegram.org/bot{token}/sendMessage"

# channel -> (token 設定名, chat_id 設定名)
CHANNEL_KEYS = {
    "hk": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
    "gold": ("GOLD_TELEGRAM_BOT_TOKEN", "GOLD_TELEGRAM_CHAT_ID"),
}

# ⚠️ 歷史陷阱：config 用 GOLD_TELEGRAM_*，但有人會寫 TELEGRAM_GOLD_*。
# 兩個名都試，先 config（可被網頁覆蓋）再 os.environ。
_ALIASES = {
    "TELEGRAM_BOT_TOKEN": ("TELEGRAM_BOT_TOKEN",),
    "TELEGRAM_CHAT_ID": ("TELEGRAM_CHAT_ID",),
    "GOLD_TELEGRAM_BOT_TOKEN": ("GOLD_TELEGRAM_BOT_TOKEN", "TELEGRAM_GOLD_BOT_TOKEN"),
    "GOLD_TELEGRAM_CHAT_ID": ("GOLD_TELEGRAM_CHAT_ID", "TELEGRAM_GOLD_CHAT_ID"),
}


def _lookup(name: str) -> str:
    """搵一個設定值：config → 環境變數 → 別名。搵唔到回傳空字串。"""
    import os

    import config
    keys = _ALIASES.get(name, (name,))
    for key in keys:
        val = getattr(config, key, None)
        if val:
            return str(val).strip()
    for key in keys:
        val = os.environ.get(key)
        if val:
            return str(val).strip()
    return ""


def _creds(channel: str):
    """每次都重新讀，咁就唔會被 import 時嘅舊值鎖死。"""
    tok_name, chat_name = CHANNEL_KEYS.get(channel, CHANNEL_KEYS["hk"])
    return _lookup(tok_name), _lookup(chat_name)


def _send(text: str, chat_id: str, token: str) -> bool:
    if not token:
        log.info("[通知-未設定Telegram]\n%s", text)
        return False
    try:
        resp = requests.post(
            _API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if resp.status_code != 200:
            # Markdown 解析失敗時退回純文字，確保你一定收得到
            resp = requests.post(
                _API.format(token=token),
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=15,
            )
        ok = resp.status_code == 200
        if not ok:
            log.warning("Telegram 發送失敗 %s: %s", resp.status_code, resp.text[:200])
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram 發送異常: %s", exc)
        return False


# ------------------------------------------------------------------ 診斷
def diagnose(channel: str = "gold", send_test: bool = True) -> dict:
    """直接問 Telegram 發生咩事，唔靠猜。

    回傳 {ok, verdict, steps:[{step, ok, detail}]}，每一步都係實測結果。
    """
    token, chat = _creds(channel)
    out = {
        "channel": channel,
        "token_set": bool(token),
        "chat_set": bool(chat),
        "token_masked": (token[:12] + "…") if token else "(空白)",
        "chat_masked": (chat[:4] + "…" + chat[-3:]) if len(chat) > 8 else (chat or "(空白)"),
        "steps": [],
    }

    if not token:
        out["ok"] = False
        out["verdict"] = f"讀唔到 bot token（channel={channel}）。檢查 Railway Variables 名稱。"
        return out
    if not chat:
        out["ok"] = False
        out["verdict"] = f"讀唔到 chat id（channel={channel}）。檢查 Railway Variables 名稱。"
        return out
    if ":" not in token:
        out["ok"] = False
        out["verdict"] = "Bot token 格式唔對：應該係「數字:英數字」，中間有個冒號。"
        return out

    # ① token 有冇效
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
        uname = ((r.get("result") or {}).get("username") or "")
        out["steps"].append({
            "step": "① 檢查 bot token",
            "ok": bool(r.get("ok")),
            "detail": f"@{uname}" if r.get("ok") else (r.get("description") or "未知錯誤"),
        })
        if not r.get("ok"):
            out["ok"] = False
            out["verdict"] = "Bot token 唔正確（Telegram 拒絕）。去 BotFather 確認。"
            return out
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["steps"].append({"step": "① 檢查 bot token", "ok": False, "detail": str(exc)})
        out["verdict"] = "連唔到 Telegram（網絡問題）。"
        return out

    if not send_test:
        out["ok"] = True
        out["verdict"] = "Token 有效。"
        return out

    # ② 真正發一條訊息
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "✅ 黃金分析 bot 連線成功（TyLove）"},
            timeout=15,
        ).json()
        ok = bool(r.get("ok"))
        out["steps"].append({
            "step": "② 發送測試訊息",
            "ok": ok,
            "detail": "已發送，去 Telegram 睇下" if ok else (r.get("description") or "未知錯誤"),
        })
        if ok:
            out["ok"] = True
            out["verdict"] = "成功！訊息已送到你 Telegram。"
        else:
            desc = str(r.get("description") or "")
            out["ok"] = False
            if "chat not found" in desc:
                out["verdict"] = ("Chat ID 唔正確（Telegram 話 chat not found）。"
                                  "Chat ID 係你自己嘅用戶 ID，例如 524897657，唔係 bot token 開頭嗰串。")
            elif "blocked" in desc:
                out["verdict"] = "你封鎖咗個 bot。喺 Telegram 解除封鎖，或者重新撳 Start。"
            elif "not enough rights" in desc:
                out["verdict"] = "個 bot 冇權發訊息俾你。去 Telegram 搵佢撳 Start。"
            else:
                out["verdict"] = f"Telegram 拒絕：{desc}"
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["steps"].append({"step": "② 發送測試訊息", "ok": False, "detail": str(exc)})
        out["verdict"] = "發送時出錯。"
    return out


def push(text: str, channel: str = "hk") -> bool:
    """發到指定 channel。長訊息自動分段（Telegram 上限 4096 字）。"""
    token, chat_id = _creds(channel)
    if not chat_id:
        log.info("[通知-%s-無chat_id]\n%s", channel, text)
        return False
    if not token:
        log.info("[通知-%s-無token]\n%s", channel, text)
        return False
    parts, chunk = [], text
    while len(chunk) > 3800:
        cut = chunk.rfind("\n", 0, 3800)
        cut = cut if cut > 0 else 3800
        parts.append(chunk[:cut])
        chunk = chunk[cut:]
    parts.append(chunk)
    return all(_send(p, chat_id, token) for p in parts)


# ------------------------------------------------------------------ 格式化：港股
def fmt_scan_header(scan_type: str, scanned: int, passed: int) -> str:
    return f"*【TyLove 掃描】{scan_type}*\n檢查 {scanned} 隻，{passed} 隻有訊號\n" + "─" * 18


def fmt_signal(item: dict) -> str:
    plan = item.get("plan") or {}
    news = item.get("news") or {}
    lines = [
        f"*{item['symbol']} {item.get('name','')}*",
        f"總分 *{item['total_score']}/100*（技術 {item['technical_score']}）",
        f"現價 {plan.get('entry')}",
        f"止蝕 *{plan.get('stop_loss')}*（{plan.get('stop_pct')}%）",
        f"目標一 {plan.get('target1')}（{plan.get('target1_pct')}%，減半）",
        f"目標二 {plan.get('target2')}（{plan.get('target2_pct')}%，清倉）",
        f"風險回報比 {plan.get('rr_ratio')}",
        f"建議注碼 *{plan.get('shares')} 股*（約 HK${plan.get('capital_used'):,.0f}，"
        f"最大虧損 HK${plan.get('max_loss_hkd'):,.0f}）",
    ]
    if news.get("catalyst"):
        lines.append(f"催化劑：{news['catalyst']}")
    if news.get("reason"):
        lines.append(f"新聞：{news['reason']}")
    top = [n for n in item.get("notes", []) if "+" in n][:3]
    if top:
        lines.append("評分要點：" + "；".join(top))
    lines.append("─ 買入決定由你本人執行 ─")
    return "\n".join(lines)


def fmt_position(item: dict) -> str:
    icon = {"EXIT": "🔴", "TRIM": "🟡", "WATCH": "🟠", "HOLD": "🟢"}.get(item["action"], "⚪")
    lines = [
        f"{icon} *{item['symbol']} {item.get('name','')}* — {item['action']}",
        f"入場 {item['entry']} → 現價 {item['price']}（{item['pnl_pct']:+.2f}%，HK${item['pnl_hkd']:+,.0f}）",
        f"止蝕 {item['stop_loss']}（距 {item['dist_to_stop_pct']:.2f}%）"
        + ("　↑已上移" if item.get("stop_moved") else ""),
        f"目標一 {item['target1']}（距 {item['dist_to_target1_pct']:.2f}%）／目標二 {item['target2']}",
        f"建議：{item['reason']}",
    ]
    return "\n".join(lines)


def fmt_close_reminder(items: list, allow_new: dict) -> str:
    head = "*【收市前提醒】* 仲有 10 分鐘收市"
    if not items:
        return head + "\n手上無持倉。今日操作完結，聽日再戰。"
    lines = [head, f"你有 {len(items)} 個持倉："]
    for it in items:
        lines.append("")
        lines.append(fmt_position(it))
    lines.append("")
    lines.append("⚠️ 短炒唔建議持倉過夜：港股隔夜跳空風險高，"
                 "除非已經鎖定利潤，否則考慮收市前平倉。")
    if allow_new.get("reason"):
        lines.append(allow_new["reason"])
    return "\n".join(lines)


def fmt_error(where: str, err: str) -> str:
    return f"⚠️ *TyLove 出錯*\n位置：{where}\n{err[:300]}"


# ------------------------------------------------------------------ 格式化：黃金
def fmt_gold_signal(g: dict) -> str:
    """黃金訊號通知（正式版：K 線已收盤）。"""
    if not g.get("has_signal"):
        return ("*【黃金 XAU/USD】暫無訊號*\n"
                f"現價 {g['price']}（{g['time']}）\n"
                f"狀態：{g.get('state','')}\n"
                f"{'／'.join(g.get('reasons', [])[:3])}")

    off = g.get("spot_offset", 0) or 0
    long_ = g["direction"] == "long"
    arrow = "🟢 做多" if long_ else "🔴 做空"
    age = g.get("data_age_min")
    age_txt = f"{age:.0f} 分鐘" if isinstance(age, (int, float)) else "—"

    def s(v):                       # 換算成 MT4 現貨等價位
        return round(float(v) - off, 2)

    lines = [
        f"*【黃金 XAU/USD】{arrow}*",
        f"訊號 K 線 {g['time']}（香港時間）",
        "─" * 18,
    ]
    if off:
        lines += [
            f"✅ 已做基準校正（−{off:g}），以下係 *MT4 現貨等價位*",
            f"入場 *{s(g['entry'])}*",
            f"止蝕 *{s(g['stop'])}*（距離 {g['stop_dist']} 美元）",
            f"目標 *{s(g['target'])}*（距離 {g['target_dist']} 美元）",
        ]
    else:
        lines += [
            "⚠️ *未做基準校正* — 程式用 COMEX 期貨，你 MT4 係現貨，",
            "兩者差 US$20–35。*請只採用「距離」*，套落你自己 MT4 嘅現價：",
            f"止蝕 = 你入場價 {'+' if not long_ else '−'} {g['stop_dist']} 美元",
            f"目標 = 你入場價 {'−' if not long_ else '+'} {g['target_dist']} 美元",
            f"（期貨參考位 入場 {g['entry']}／止蝕 {g['stop']}／目標 {g['target']}）",
        ]
    lines += [
        f"風險回報比 1 : {g['rr']}",
        "─" * 18,
        f"建議手數 *{g['lot']} 手*（= {g['oz']} 盎司）",
        f"如果用 0.01 手：最大虧損 *US${g['risk_usd']:.2f}*",
        f"ATR(14) = {g['atr']}（{g['atr_pct']}% of 價格）",
        f"⏱ 數據時間距今 {age_txt}（正式訊號要等 K 線收盤）",
        "",
        "理由：" + "；".join(g.get("reasons", [])[:3]),
        "",
        f"⚠️ {g.get('disclaimer','')}",
        "─ 落單由你本人喺 MT4 執行，記住填 S/L ─",
    ]
    return "\n".join(lines)


def fmt_gold_early(a: dict) -> str:
    """⚡ 黃金即時預警（價格啱啱穿區間，K 線未收盤）。"""
    long_ = a.get("direction") == "long"
    arrow = "🟢 向上突破" if long_ else "🔴 向下突破"
    off = a.get("spot_offset", 0) or 0
    lvl = round(float(a["level"]) - off, 2)
    px = round(float(a["price"]) - off, 2)
    return "\n".join([
        f"*【黃金 XAU/USD】⚡ 即時預警 — {arrow}*",
        f"時間 {a['time']}（香港時間）",
        f"突破位 *{lvl}*　現價 *{px}*",
        f"（已穿 {abs(a['diff']):.2f} 美元）",
        "─" * 18,
        f"通道：前 20 支 K 線高／低點",
        "⚠️ 呢個係 *未收盤* 嘅即時預警，價位可能彈返入區間。",
        "K 線收盤後如果仍然突破，會再出正式訊號確認。",
        "─ 落單由你本人喺 MT4 執行，記住填 S/L ─",
    ])
