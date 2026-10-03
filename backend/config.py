"""
TyLove Trading v2 — 中央設定
所有可調參數集中在這裡，改一個地方就可以。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

load_dotenv(ROOT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

HK_TZ = "Asia/Hong_Kong"
# MT4 券商伺服器時間 vs 香港時間差幾多個鐘（香港 = MT4 + 呢個數）
# 2026-10-01 由真實成交反推：你 MT4 顯示 13:49，訊號係香港 19:30 → +6
MT4_TZ_OFFSET = int(os.getenv("MT4_TZ_OFFSET", "6"))

# ---------------------------------------------------------------- 憑證
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# ---------------------------------------------------------------- 市場
BENCHMARK = "^HSI"

# 短炒觀察名單：以大流動性、有波幅嘅港股為主。自己隨意加減。
WATCHLIST = [
    "0700.HK",   # 騰訊
    "9988.HK",   # 阿里巴巴
    "3690.HK",   # 美團
    "1810.HK",   # 小米
    "9618.HK",   # 京東
    "1211.HK",   # 比亞迪
    "2318.HK",   # 中國平安
    "0388.HK",   # 香港交易所
    "2020.HK",   # 安踏體育
    "0981.HK",   # 中芯國際
    "2382.HK",   # 舜宇光學
    "6690.HK",   # 海爾智家
]

# ---------------------------------------------------------------- 評分
# 訊號門檻：>= ALERT_THRESHOLD 才出「可留意」通知
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "70"))
WATCH_THRESHOLD = int(os.getenv("WATCH_THRESHOLD", "58"))

# 新聞只做「否決 / 扣分」，唔做加分主力
NEWS_VETO_PENALTY = 30       # 重大負面：直接否決
NEWS_MEDIUM_PENALTY = 12     # 中度負面：扣分
NEWS_BONUS_CAP = 5           # 正面新聞最多加 5 分（避免新聞噪音主導）

# ---------------------------------------------------------------- 流動性 / 入場硬性過濾
MIN_TURNOVER_HKD = float(os.getenv("MIN_TURNOVER_HKD", "50000000"))  # 20 日平均成交額 ≥ 5000 萬
MIN_PRICE_HKD = 1.0
MIN_ATR_PCT = 1.2            # ATR% 低過呢個 = 太死，短炒無肉食
MAX_ATR_PCT = 8.0            # 太高 = 純賭博

# ---------------------------------------------------------------- 倉位與風控
ACCOUNT_SIZE_HKD = float(os.getenv("ACCOUNT_SIZE_HKD", "100000"))    # 你嘅本金
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))   # 每筆最多輸本金 1%
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))       # 同時最多持幾隻
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "30"))        # 單一倉位最多佔本金 30%
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3")) # 當日輸夠 3% 就停手

# ---------------------------------------------------------------- 出場參數（ATR 制）
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", "1.5"))   # 止蝕 = 入場 − 1.5 × ATR
ATR_TARGET1_MULT = float(os.getenv("ATR_TARGET1_MULT", "2.0"))  # 第一目標（減半倉）
ATR_TARGET2_MULT = float(os.getenv("ATR_TARGET2_MULT", "3.0"))  # 第二目標（清倉）
MIN_RR_RATIO = 1.8          # 風險回報比低過呢個就唔值得入
MAX_HOLD_DAYS = 10          # 短炒最長持倉日數，到期無表現就走
BREAKEVEN_ATR = 1.0         # 賺到 1×ATR 之後，止蝕上移到成本價
TRAIL_ATR_MULT = 1.5        # 移動止蝕：最高價 − 1.5 × ATR

# ---------------------------------------------------------------- 排程（香港時間）
SCAN_TIMES = os.getenv("SCAN_TIMES", "09:25,10:30,11:30,14:00").split(",")
MONITOR_INTERVAL_MIN = int(os.getenv("MONITOR_INTERVAL_MIN", "15"))
CLOSE_REMINDER_TIME = os.getenv("CLOSE_REMINDER_TIME", "15:50")

# ---------------------------------------------------------------- 黃金 XAU/USD
# 數據源用 GC=F（COMEX 黃金期貨）：yfinance 冇 XAUUSD 現貨代號，
# 而 GC=F 同現貨走勢同步（價差正常 0–2 美元），足夠做技術分析。
GOLD_ENABLED = os.getenv("GOLD_ENABLED", "1").strip() == "1"
GOLD_SYMBOL = os.getenv("GOLD_SYMBOL", "GC=F").strip()
GOLD_LABEL = os.getenv("GOLD_LABEL", "XAU/USD").strip()
GOLD_SPREAD_USD = float(os.getenv("GOLD_SPREAD_USD", "0.30"))  # MT4 實測點差（美元／盎司）
GOLD_LOT_MIN = float(os.getenv("GOLD_LOT_MIN", "0.01"))        # 你嘅最低手數
GOLD_LOT_MAX = float(os.getenv("GOLD_LOT_MAX", "0.05"))        # 你嘅最高手數
GOLD_OZ_PER_LOT = float(os.getenv("GOLD_OZ_PER_LOT", "100"))   # 1 手 = 100 盎司

# 手數計算用：你黃金戶口嘅本金（美元）同每筆可承受風險（%）
# ⚠️ 呢兩個一定要填啱，唔係程式會建議過大手數
GOLD_EQUITY_USD = float(os.getenv("GOLD_EQUITY_USD", "500"))
GOLD_RISK_PCT = float(os.getenv("GOLD_RISK_PCT", "1.0"))

# 策略參數（Donchian 突破）
# 2026-09-29 回測結論：維持 1.0×ATR 止蝕。
#   曾試 1.5×ATR（因 09-25 三筆連續被回吐掃損），但完整回測顯示
#   喺實盤 M15 週期上反而更差：
#     樣本外 PF  1.0×=1.24 / 1.5×=1.08　期望值 +1.57R / +0.70R
#   H1 長樣本則相反（1.5 較好）→ 兩個週期結論相反 = 微調無意義。
#   真正問題：PF 喺樣本內外由 0.87 搖到 1.59，即策略本身冇穩定優勢。
#   所以唔應該再喺噪音上面調參數 —— 應該累積實測數據再判斷。
GOLD_BREAKOUT_BARS = int(os.getenv("GOLD_BREAKOUT_BARS", "20"))
GOLD_STOP_ATR = float(os.getenv("GOLD_STOP_ATR", "1.0"))
GOLD_TARGET_ATR = float(os.getenv("GOLD_TARGET_ATR", "3.0"))
GOLD_MAX_TRADES_PER_DAY = int(os.getenv("GOLD_MAX_TRADES_PER_DAY", "3"))

# 掃描時段（香港時間）—— 只作後備；正常用下面嘅連續監控
GOLD_SCAN_TIMES = os.getenv(
    "GOLD_SCAN_TIMES", "15:05,16:05,17:05,20:35,21:35,22:35,23:35"
).split(",")

# 連續監控：活躍時段（香港 15:00–01:00）內每 N 分鐘檢查一次突破。
# 0 = 停用，改為只用上面嘅 GOLD_SCAN_TIMES。
# 點解要連續：M15 每 15 分鐘一支 K 線，一日活躍時段有約 40 支；
# 只喺固定幾個鐘數掃，會 miss 九成突破。
GOLD_CHECK_MIN = int(os.getenv("GOLD_CHECK_MIN", "1"))

# 即時預警：價格一穿區間就即刻推，唔等 K 線收盤（快約 15 分鐘）。
GOLD_EARLY_ALERT = int(os.getenv("GOLD_EARLY_ALERT", "1"))

# ⚠️ 基準校正（好重要）
# 程式用 COMEX 期貨 GC=F，但你 MT4 係現貨 XAUUSD —— 兩者差約 US$20–35，
# 而且差距會隨時間漂移。填「期貨價 − 你 MT4 現價」嘅差額（正數），
# 程式就會將所有價位換算成你 MT4 睇到嘅價。
# 例：期貨 4392、MT4 現貨 4370 → 填 22
# 唔填（0）的話，通知會改為只提供「距離」，你自己套落 MT4 現價。
GOLD_SPOT_OFFSET = float(os.getenv("GOLD_SPOT_OFFSET", "28"))

# ① 趨勢過濾：跌勢唔做多、升勢唔做空。1 = 開，0 = 關。
GOLD_TREND_FILTER = int(os.getenv("GOLD_TREND_FILTER", "1"))

# ② 追高警告門檻：入場價離突破位超過幾個 ATR 就標「已追高」。
#    0.5 = 半個 ATR。設大啲（例如 2）等於唔理。
# ── 券商真實報價：保留幾多 ──────────────────────────────
# 2026-09-30：原本 KEEP_BARS=3000（只 31 日）、prune_ticks(keep=5000)
# （只 6.9 小時）—— tick 數據每 7 個鐘就畀人刪走，永遠儲唔到長期記錄。
# 你嘅目標係累積真實 MT4 數據做回測，所以兩個都要大幅放寬。
#
# K 線：M15 一日 96 支 → 20 萬支 ≈ 5.7 年（約 20–30 MB，好抵）
# Tick：一日約 17,280 個 → 150 萬個 ≈ 87 日（約 150–200 MB）
# 如果你 Railway Volume 空間緊，可以調細個 tick 數；K 線唔建議細過 5 萬。
KEEP_BARS = int(os.getenv("KEEP_BARS", "200000"))
KEEP_TICKS = int(os.getenv("KEEP_TICKS", "1500000"))

GOLD_MAX_CHASE_ATR = float(os.getenv("GOLD_MAX_CHASE_ATR", "0.5"))

# ── 2026-10-02 新增 ────────────────────────────────────────────────
# B. 最少穿透門檻（以 ATR 計）。實測（2026-10-02）系統喺 1 個鐘內出咗 5 條
#    向下突破預警，穿透幅度只有 0.30 / 0.70 / 1.70 / 3.80 美元，
#    即 ATR(14) 嘅 2%–27% —— $0.30 喺黃金上純粹係噪音。
#    0 = 唔設門檻（舊行為）；0.15 = 至少穿 ATR 嘅 15% 才通知。
GOLD_MIN_BREAK_ATR = float(os.getenv("GOLD_MIN_BREAK_ATR", "0.15"))

# C. 每日方向偏見：每日只准做同一個方向（升／跌），唔會中途反手。
#    由 H1 圖 EMA20 vs EMA50 決定，每日只計一次、當日固定不變。
#    起因：2026-10-02 18:33 出「向下突破」→ 跟咗輸 $53.20；
#    同日 20:45 系統自己反手出「做多」。當日內方向反覆 = 兩邊輸。
#    0 = 停用；1 = 啟用。
GOLD_DAILY_BIAS = int(os.getenv("GOLD_DAILY_BIAS", "1"))

# 每日偏見嘅計算時刻（香港時間，HH:MM）。當日第一支 H1 K 線收盤後為之。
GOLD_BIAS_HOUR = int(os.getenv("GOLD_BIAS_HOUR", "9"))

# ③ 真實報價接收（Option A/C）—— 由你 MT4 EA 或 MT5 Python 推送過嚟
#    超過幾多分鐘冇新 K 線就當斷線，自動回落到 yfinance
GOLD_FEED_MAX_AGE_MIN = int(os.getenv("GOLD_FEED_MAX_AGE_MIN", "30"))
#    防偽 token：同 EA / Python 收集器填同一個值（留空 = 唔檢查）
GOLD_FEED_TOKEN = os.getenv("GOLD_FEED_TOKEN", "").strip()
#    設 1 = 強制唔用真實報價（除錯用）
GOLD_FORCE_YFINANCE = os.getenv("GOLD_FORCE_YFINANCE", "0").strip() == "1"

# 黃金專用 Telegram bot（唔填就唔會推送，但網頁照睇得到）
# 兩種寫法都接受（TELEGRAM_GOLD_* 同 GOLD_TELEGRAM_*），唔想再因為名唔對而收唔到通知
GOLD_TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_GOLD_BOT_TOKEN")
                           or os.getenv("GOLD_TELEGRAM_BOT_TOKEN") or "").strip()
GOLD_TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_GOLD_CHAT_ID")
                         or os.getenv("GOLD_TELEGRAM_CHAT_ID") or "").strip()

# ---------------------------------------------------------------- 儲存
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "tylove.db"))

# ---------------------------------------------------------------- 服務
PORT = int(os.getenv("PORT", "8080"))
DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")


# ── 2026-10-03 新增 ───────────────────────────────────────────
# 每日心跳：固定時間報「系統正常運作／今日休市」，令你分得出
# 「市場靜」同「系統死」。2026-10-03（星期六）用戶全日冇訊息，
# 但分唔到係正常休市定係系統掛咗。
HEARTBEAT_TIME = os.getenv("HEARTBEAT_TIME", "09:00").strip()
SKIP_WEEKEND = int(os.getenv("SKIP_WEEKEND", "1"))   # 休市日唔做無謂掃描
