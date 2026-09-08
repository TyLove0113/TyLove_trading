# ─────────────────────────────────────────
# telegram_bot.py — Telegram 推送通知
# ─────────────────────────────────────────

import asyncio
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
import config


async def send_message(text: str) -> bool:
    """發送 Telegram 訊息"""
    try:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML"
        )
        return True
    except TelegramError as e:
        print(f"❌ Telegram 發送失敗: {e}")
        return False


def send(text: str) -> bool:
    """同步版本（方便喺其他檔案呼叫）"""
    return asyncio.run(send_message(text))


def format_morning_report(results: list) -> str:
    """
    格式化早市報告
    每日 09:25 發送
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 值得留意嘅股票
    watchlist = [r for r in results if r["total"] >= config.ALERT_THRESHOLD]
    
    lines = [
        f"🌅 <b>港股早市報告</b>",
        f"📅 {now}",
        f"{'─' * 30}",
    ]

    if watchlist:
        lines.append(f"\n🔔 <b>今日值得留意 ({len(watchlist)} 隻)</b>\n")
        for r in watchlist:
            sign = "▲" if r["change_pct"] >= 0 else "▼"
            lines += [
                f"<b>{r['symbol']}</b>  {r['price']:.3f}  "
                f"{sign}{abs(r['change_pct']):.2f}%",
                f"評分：{r['total']}/100  {r['grade']}",
                f"技術：{r['technical']['score']}/50  "
                f"風險：{r['risk']['score']}/20",
                f"建議：{r['action']}",
                "",
            ]
    else:
        lines.append("\n📭 今日暫無符合條件嘅股票\n")

    # 大市概況
    gainers = len([r for r in results if r["change_pct"] > 0])
    losers  = len([r for r in results if r["change_pct"] < 0])
    best    = max(results, key=lambda x: x["change_pct"])
    worst   = min(results, key=lambda x: x["change_pct"])

    lines += [
        f"{'─' * 30}",
        f"📊 <b>Watchlist 概況</b>",
        f"上漲 {gainers} 隻  下跌 {losers} 隻",
        f"最強：{best['symbol']} {best['change_pct']:+.2f}%",
        f"最弱：{worst['symbol']} {worst['change_pct']:+.2f}%",
        f"\n⚠️ 僅供參考，自行判斷",
    ]

    return "\n".join(lines)


def format_alert(result: dict) -> str:
    """格式化單隻股票提醒（含新聞摘要）"""
    sign = "▲" if result["change_pct"] >= 0 else "▼"
    tech_detail = result["technical"]["detail"]
    trend = tech_detail.get("趨勢", "")
    rsi   = tech_detail.get("RSI", "")
    news  = result.get("news", {})
    news_label   = news.get("label", "中性")
    news_summary = news.get("summary", "")

    msg = (
        f"🔔 <b>入場信號提醒</b>\n"
        f"{'─' * 30}\n"
        f"<b>{result['symbol']}</b>  "
        f"{result['price']:.3f}  {sign}{abs(result['change_pct']):.2f}%\n\n"
        f"總分：<b>{result['total']}/100</b>  {result['grade']}\n"
        f"技術面：{result['technical']['score']}/50  "
        f"風險面：{result['risk']['score']}/20  "
        f"新聞：{news['score']}/30\n\n"
        f"📈 {trend}\n"
        f"📊 {rsi}\n"
        f"📰 新聞情緒：{news_label}\n"
    )

    if news_summary:
        msg += f"\n🤖 <b>AI 新聞分析</b>\n{news_summary}\n"

    msg += (
        f"\n{'─' * 30}\n"
        f"⚠️ 僅供參考，自行判斷"
    )
    return msg


def format_closing_reminder() -> str:
    """下午 2:50 平倉提醒"""
    return (
        f"⏰ <b>平倉提醒</b>\n"
        f"距離收市還有 10 分鐘\n"
        f"請確認所有倉位已平倉\n"
        f"避免持倉過夜"
    )


# ── 測試用 ────────────────────────────────
if __name__ == "__main__":
    print("測試 Telegram 推送...")
    result = send("✅ 港股交易系統測試訊息\n系統運行正常！")
    if result:
        print("✅ 發送成功！請檢查 Telegram")
    else:
        print("❌ 發送失敗，請檢查 Token 同 Chat ID")