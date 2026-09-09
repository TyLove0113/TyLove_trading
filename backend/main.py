# ─────────────────────────────────────────
# main.py — 主程式（完整版）
# ─────────────────────────────────────────

import sys
import subprocess
import schedule
import time
from datetime import datetime
import config
from data_fetcher import get_all_stocks
from scoring import score_all_stocks, print_score_report
from telegram_bot import send, format_morning_report, format_alert, format_closing_reminder


# ── 自動更新 yfinance ─────────────────────

def update_yfinance():
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--upgrade", "yfinance", "--quiet"],
            capture_output=True, text=True
        )
        print("✅ yfinance 已是最新版本")
    except Exception as e:
        print(f"⚠️ yfinance 更新失敗: {e}")


# ── 核心分析流程 ──────────────────────────

def run_analysis(send_telegram=True):
    """執行完整分析，可選擇是否推送 Telegram"""
    from config import HK_TZ
    now = datetime.now(HK_TZ)
    print(f"\n{'═'*55}")
    print(f"  🕐 執行分析：{now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*55}")

    stocks = get_all_stocks()
    if not stocks:
        print("❌ 無法獲取數據，跳過本次分析")
        return None

    results = score_all_stocks(stocks)
    print_score_report(results)

    if send_telegram:
        # 發送早市報告
        report = format_morning_report(results)
        if send(report):
            print("📲 Telegram 報告已發送")
        else:
            print("⚠️ Telegram 發送失敗")

    return results


def run_closing_reminder():
    """下午 2:50 平倉提醒"""
    msg = format_closing_reminder()
    send(msg)
    print("📲 平倉提醒已發送")


# ── 排程設定 ──────────────────────────────
import os
os.environ["TZ"] = "Asia/Hong_Kong"

def setup_schedule():
    schedule.every().day.at("09:25").do(run_analysis)
    schedule.every().day.at("10:00").do(run_analysis)
    schedule.every().day.at("11:30").do(run_analysis)
    schedule.every().day.at("14:00").do(run_analysis)
    schedule.every().day.at("14:50").do(run_closing_reminder)

    print("⏰ 排程已設定：")
    print("   09:25 開市前分析 + Telegram 報告")
    print("   10:00 開市後確認 + Telegram 報告")
    print("   11:30 午市前分析 + Telegram 報告")
    print("   14:00 下午分析   + Telegram 報告")
    print("   14:50 平倉提醒")


# ── 主程式 ────────────────────────────────

def main():
    print("=" * 55)
    print("  🚀 港股日內交易系統 啟動")
    print(f"  版本：v0.2.0")
    print(f"  時間：{datetime.now(HK_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    # 1. 更新 yfinance
    print("\n🔄 檢查套件更新...")
    update_yfinance()

    # 2. 發送啟動通知
    send(f"🚀 港股交易系統已啟動\n{datetime.now(HK_TZ).strftime('%Y-%m-%d %H:%M')}")

    # 3. 立即執行一次分析
    print("\n📊 執行初始分析...")
    run_analysis(send_telegram=True)

    # 4. 設定排程
    print()
    setup_schedule()

    # 5. 進入排程循環
    print("\n✅ 系統運行中，等待下次排程...")
    print("   （按 Ctrl+C 停止）\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()