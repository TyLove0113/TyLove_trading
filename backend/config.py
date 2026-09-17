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

# 策略參數（對應回測：Donchian 突破 1.0×ATR 止蝕 / 3.0×ATR 目標）
GOLD_BREAKOUT_BARS = int(os.getenv("GOLD_BREAKOUT_BARS", "20"))
GOLD_STOP_ATR = float(os.getenv("GOLD_STOP_ATR", "1.0"))
GOLD_TARGET_ATR = float(os.getenv("GOLD_TARGET_ATR", "3.0"))
GOLD_MAX_TRADES_PER_DAY = int(os.getenv("GOLD_MAX_TRADES_PER_DAY", "1"))

# 掃描時段（香港時間）—— 覆蓋倫敦開市、紐約開市、美市活躍時段
GOLD_SCAN_TIMES = os.getenv(
    "GOLD_SCAN_TIMES", "15:05,16:05,17:05,20:35,21:35,22:35,23:35"
).split(",")

# 黃金專用 Telegram bot（唔填就唔會推送，但網頁照睇得到）
GOLD_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_GOLD_BOT_TOKEN", "").strip()
GOLD_TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_GOLD_CHAT_ID", "").strip()

# ---------------------------------------------------------------- 儲存
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "tylove.db"))

# ---------------------------------------------------------------- 服務
PORT = int(os.getenv("PORT", "8080"))
DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")
