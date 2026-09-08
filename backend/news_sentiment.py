# ─────────────────────────────────────────
# news_sentiment.py
# 兩層新聞情緒分析
# 第一層：快速判斷正面/負面/中性
# 第二層：高分股票詳細解讀
# ─────────────────────────────────────────

import json
import time
import requests
import xml.etree.ElementTree as ET
from openai import OpenAI
import config

# 初始化 OpenAI client
client = OpenAI(api_key=config.OPENAI_API_KEY)


# ── 新聞抓取 ──────────────────────────────

# 股票代碼對應公司名稱（搜尋新聞用）
STOCK_NAMES = {
    "0700.HK": "騰訊 Tencent",
    "0005.HK": "匯豐 HSBC",
    "1398.HK": "工商銀行 ICBC",
    "0388.HK": "港交所 HKEX",
    "2318.HK": "平安保險 Ping An",
    "0941.HK": "中國移動 China Mobile",
    "1299.HK": "友邦保險 AIA",
    "3690.HK": "美團 Meituan",
    "0016.HK": "新鴻基 Sun Hung Kai",
    "2388.HK": "中銀香港 BOC Hong Kong",
}


def fetch_news_yahoo(symbol: str) -> list[str]:
    """
    從 Yahoo Finance 抓取新聞標題
    返回最新 3-5 條標題
    """
    try:
        # Yahoo Finance RSS feed
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=HK&lang=zh-Hant-HK"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return []

        # 解析 RSS XML
        root = ET.fromstring(response.content)
        titles = []
        for item in root.findall(".//item"):
            title = item.find("title")
            if title is not None and title.text:
                titles.append(title.text.strip())
            if len(titles) >= config.NEWS_PER_STOCK:
                break

        return titles

    except Exception as e:
        print(f"  ⚠️ Yahoo 新聞抓取失敗 {symbol}: {e}")
        return []


def fetch_news_google(symbol: str) -> list[str]:
    """
    從 Google News 抓取新聞標題（備用）
    """
    try:
        name = STOCK_NAMES.get(symbol, symbol)
        query = name.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={query}+股票&hl=zh-HK&gl=HK&ceid=HK:zh-Hant"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return []

        root = ET.fromstring(response.content)
        titles = []
        for item in root.findall(".//item"):
            title = item.find("title")
            if title is not None and title.text:
                # 清理標題（去除來源名稱）
                clean = title.text.split(" - ")[0].strip()
                titles.append(clean)
            if len(titles) >= config.NEWS_PER_STOCK:
                break

        return titles

    except Exception as e:
        print(f"  ⚠️ Google 新聞抓取失敗 {symbol}: {e}")
        return []


def get_news(symbol: str) -> list[str]:
    """
    先試 Yahoo，失敗就用 Google
    """
    headlines = fetch_news_yahoo(symbol)
    if not headlines:
        headlines = fetch_news_google(symbol)
    return headlines


# ── 第一層：快速情緒判斷 ──────────────────

def layer1_quick_sentiment(headline: str) -> dict:
    """
    第一層：判斷單條新聞係正面/負面/中性
    防幻想措施：
    - Temperature = 0
    - 限制只回答 JSON
    - 信心度低於閾值當中性
    """
    prompt = f"""你係一個金融新聞情緒分析器。
    
分析以下新聞標題的情緒傾向。

規則：
- 只可以回答 JSON 格式
- sentiment 只可以係：正面、負面、中性
- confidence 係 0-100 的整數
- 唔可以有其他文字

新聞標題：{headline}

回覆格式：
{{"sentiment": "正面/負面/中性", "confidence": 85}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,          # 防幻想：最保守
            max_tokens=50,          # 限制輸出長度
            messages=[
                {"role": "system", "content": "你係金融新聞情緒分析器，只回覆 JSON。"},
                {"role": "user",   "content": prompt}
            ]
        )

        raw = response.choices[0].message.content.strip()

        # 解析 JSON
        result = json.loads(raw)
        sentiment   = result.get("sentiment", "中性")
        confidence  = int(result.get("confidence", 0))

        # 防幻想：信心度不足當中性
        if confidence < config.NEWS_CONFIDENCE_MIN:
            sentiment = "中性"

        return {
            "headline":   headline,
            "sentiment":  sentiment,
            "confidence": confidence,
        }

    except json.JSONDecodeError:
        # JSON 解析失敗，當中性處理
        return {"headline": headline, "sentiment": "中性", "confidence": 0}
    except Exception as e:
        print(f"  ⚠️ 第一層分析失敗: {e}")
        return {"headline": headline, "sentiment": "中性", "confidence": 0}


# ── 交叉驗證 ─────────────────────────────

def verify_sentiment(headline: str, first_result: str) -> str:
    """
    方法五：交叉驗證
    同一條新聞問第二次，答案唔同就當中性
    """
    try:
        second = layer1_quick_sentiment(headline)
        if second["sentiment"] != first_result:
            return "中性"   # 兩次答案唔同，保守當中性
        return first_result
    except Exception:
        return "中性"


# ── 第二層：詳細分析 ──────────────────────

def layer2_detailed_analysis(symbol: str, headlines: list[str], score: int) -> str:
    """
    第二層：只對高分股票（≥ DETAIL_THRESHOLD）做詳細分析
    返回自然語言摘要（推送到 Telegram）
    """
    if not headlines:
        return "暫無相關新聞"

    name  = STOCK_NAMES.get(symbol, symbol)
    news_text = "\n".join([f"- {h}" for h in headlines])

    prompt = f"""你係一個香港股票分析師。

以下係 {name}（{symbol}）嘅最新新聞標題：
{news_text}

請根據以上新聞，用2-3句廣東話簡短總結：
1. 主要消息係咩
2. 對股價短期影響係正面定負面
3. 有咩需要特別留意

要求：
- 只根據提供嘅新聞分析，唔好加入自己嘅猜測
- 如果新聞唔夠判斷，直接話「新聞資訊不足，建議觀望」
- 唔好預測具體股價"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,        # 略高少少，令語言更自然
            max_tokens=200,
            messages=[
                {"role": "system", "content": "你係香港股票分析師，分析簡潔客觀。"},
                {"role": "user",   "content": prompt}
            ]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"  ⚠️ 第二層分析失敗: {e}")
        return "AI 分析暫時不可用"


# ── 主分析函數 ────────────────────────────

def analyse_stock_news(symbol: str, total_score: int = 0) -> dict:
    """
    完整新聞分析流程：
    1. 抓取新聞
    2. 第一層：快速情緒判斷
    3. 交叉驗證
    4. 第二層（高分股票）：詳細分析
    返回新聞評分（0-30分）
    """
    print(f"  📰 分析新聞：{symbol}")

    # 抓取新聞
    headlines = get_news(symbol)

    if not headlines:
        print(f"     無新聞，給予中性分數")
        return {
            "score":    15,
            "max":      30,
            "label":    "中性",
            "headlines": [],
            "detail":   {"新聞情緒": "暫無新聞（15/30）"},
            "summary":  "",
        }

    # 第一層：逐條分析
    sentiments  = []
    positive    = 0
    negative    = 0
    neutral     = 0

    for headline in headlines:
        result = layer1_quick_sentiment(headline)

        # 交叉驗證（只對高信心度結果驗證）
        if result["confidence"] >= 80:
            verified = verify_sentiment(headline, result["sentiment"])
            result["sentiment"] = verified

        sentiments.append(result)

        if result["sentiment"] == "正面":
            positive += 1
        elif result["sentiment"] == "負面":
            negative += 1
        else:
            neutral += 1

        time.sleep(0.3)  # 避免 API rate limit

    # 計算新聞評分
    total_news = len(sentiments)
    if total_news == 0:
        news_score = 15
        overall    = "中性"
    else:
        pos_ratio = positive / total_news
        neg_ratio = negative / total_news

        if pos_ratio >= 0.6:
            news_score = 28
            overall    = "正面"
        elif pos_ratio >= 0.4:
            news_score = 22
            overall    = "略正面"
        elif neg_ratio >= 0.6:
            news_score = 5
            overall    = "負面"
        elif neg_ratio >= 0.4:
            news_score = 10
            overall    = "略負面"
        else:
            news_score = 15
            overall    = "中性"

    # 第二層：高分股票詳細分析
    summary = ""
    if total_score >= config.DETAIL_THRESHOLD:
        print(f"     🔍 執行第二層詳細分析（總分 {total_score} ≥ {config.DETAIL_THRESHOLD}）")
        summary = layer2_detailed_analysis(symbol, headlines, total_score)

    # 整理結果
    detail_text = f"{overall}（{positive}正/{negative}負/{neutral}中）"

    return {
        "score":     news_score,
        "max":       30,
        "label":     overall,
        "headlines": [s["headline"] for s in sentiments],
        "sentiment_detail": sentiments,
        "detail":    {"新聞情緒": f"{detail_text} ({news_score}/30)"},
        "summary":   summary,
    }


# ── 測試用 ────────────────────────────────
if __name__ == "__main__":
    print("測試新聞情緒分析...\n")

    # 測試單隻股票
    symbol = "0700.HK"
    result = analyse_stock_news(symbol, total_score=76)

    print(f"\n結果：{symbol}")
    print(f"新聞評分：{result['score']}/30")
    print(f"整體情緒：{result['label']}")
    print(f"新聞標題：")
    for h in result["headlines"]:
        print(f"  - {h}")
    if result["summary"]:
        print(f"\nAI 分析：\n{result['summary']}")