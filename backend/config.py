import os
from dotenv import load_dotenv
import pytz 

load_dotenv()

# 香港時區
HK_TZ = pytz.timezone("Asia/Hong_Kong") 

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# 港股 Watchlist
HK_STOCKS = [
    "0700.HK",  # 騰訊
    "0005.HK",  # 匯豐
    "1398.HK",  # 工商銀行
    "0388.HK",  # 港交所
    "2318.HK",  # 平安保險
    "0941.HK",  # 中國移動
    "1299.HK",  # 友邦保險
    "3690.HK",  # 美團
    "0016.HK",  # 新鴻基
    "2388.HK",  # 中銀香港
]

# 評分權重
SCORE_WEIGHTS = {
    "technical": 50,
    "news":      30,
    "risk":      20,
}

# 推送閾值
ALERT_THRESHOLD = 70

# 數據更新頻率（分鐘）
UPDATE_INTERVAL = 5

# 新聞分析設定
NEWS_PER_STOCK     = 3     # 每隻股票分析幾條新聞
DETAIL_THRESHOLD   = 75    # 總分 >= 呢個數字先做第二層詳細分析
NEWS_CONFIDENCE_MIN = 70   # 信心度低於呢個當中性處理