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
    "gold": ("TELEGRAM_GOLD_BOT_TOKEN", "TELEGRAM_GOLD_CHAT_ID"),
}


def _creds(channel: str):
    """每次都重新讀 config，咁就唔會被 import 時嘅舊值鎖死。"""
    import config
    tok_name, chat_name = CHANNEL_KEYS.get(channel, CHANNEL_KEYS["hk"])
    return (getattr(config, tok_name, "") or "").strip(), (getattr(config, chat_name, "") or "").strip()


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
    """黃金訊號通知。"""
    if not g.get("has_signal"):
        return ("*【黃金 XAU/USD】暫無訊號*\n"
                f"現價 {g['price']}（{g['time']}）\n"
                f"狀態：{g.get('state','')}\n"
                f"{'／'.join(g.get('reasons', [])[:3])}")

    arrow = "🟢 做多" if g["direction"] == "long" else "🔴 做空"
    lines = [
        f"*【黃金 XAU/USD】{arrow}*",
        f"時間 {g['time']}　現價 {g['price']}",
        "─" * 18,
        f"入場 *{g['entry']}*",
        f"止蝕 *{g['stop']}*（距離 {g['stop_dist']} 美元）",
        f"目標 *{g['target']}*（距離 {g['target_dist']} 美元）",
        f"風險回報比 1 : {g['rr']}",
        "─" * 18,
        f"建議手數 *{g['lot']} 手*（= {g['oz']} 盎司）",
        f"如果用 0.01 手：最大虧損 *US${g['risk_usd']:.2f}*",
        f"ATR(14) = {g['atr']}（{g['atr_pct']}% of 價格）",
        "",
        "理由：" + "；".join(g.get("reasons", [])[:3]),
        "",
        f"⚠️ {g.get('disclaimer','')}",
        "─ 落單由你本人喺 MT4 執行，記住填 S/L ─",
    ]
    return "\n".join(lines)
