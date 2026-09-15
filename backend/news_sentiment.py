"""
新聞分析 v2 — 由「加分主力」改為「否決 / 扣分機制」
理由：新聞情緒同短炒入場時機關係薄弱，正面新聞更幾乎冇預測力；
      但「盈警、配股、被查、停牌」呢類事件係真實、可即時反映嘅風險。
所以新版：
  · 重大負面事件 → VETO，直接否決該股（唔理技術分幾高）
  · 中度負面     → 扣分
  · 正面新聞     → 最多加 NEWS_BONUS_CAP 分（避免噪音主導）
  · 順便抽「催化劑」關鍵詞，幫你知今日點解郁
"""
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from config import NEWS_BONUS_CAP, NEWS_MEDIUM_PENALTY, NEWS_VETO_PENALTY, OPENAI_API_KEY, OPENAI_MODEL

log = logging.getLogger("tylove.news")

# 港股相關新聞來源（RSS，免 key）
RSS_FEEDS = [
    "https://news.google.com/rss/search?q={q}+stock&hl=zh-HK&gl=HK&ceid=HK:zh-Hant",
    "https://news.google.com/rss/search?q={q}+%E8%82%A1%E7%A5%A8&hl=zh-HK&gl=HK&ceid=HK:zh-Hant",
    "https://news.google.com/rss/search?q={q}+HK+stock&hl=en-HK&gl=HK&ceid=HK:en",
]

# 關鍵詞硬規則 —— 唔靠模型都一定捉得到嘅重大風險
VETO_KEYWORDS = [
    "盈警", "盈利警告", "發盈警", "虧損擴大", "業績倒退",
    "配股", "供股", "折讓配售", "大股東減持", "悉數減持",
    "被調查", "廉政公署", "證監會調查", "停牌", "除牌", "清盤",
    "profit warning", "placement", "share sale", "investigation",
    "suspended", "delisting", "winding up",
]
MEDIUM_KEYWORDS = [
    "裁員", "重組", "退市", "罰款", "訴訟", "下調目標價", "降級",
    "downgrade", "lawsuit", "layoff", "fine",
]
POSITIVE_KEYWORDS = [
    "回購", "增持", "盈喜", "盈利預喜", "超預期", "上調目標價", "獲批",
    "buyback", "upgrade", "beat estimates",
]

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (TyLove/2.0)"})


def _clean_text(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    raw = re.sub(r"&[a-z]+;", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def fetch_news(keywords: list[str], max_items: int = 12, hours: int = 48) -> list[dict]:
    """拉最近 N 小時嘅相關新聞標題。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen, items = set(), []

    for kw in keywords:
        for tpl in RSS_FEEDS:
            url = tpl.format(q=requests.utils.quote(kw))
            try:
                resp = _SESSION.get(url, timeout=12)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
            except Exception as exc:  # noqa: BLE001
                log.debug("RSS 失敗 %s: %s", url, exc)
                continue

            for node in root.iter("item"):
                title = _clean_text(node.findtext("title", ""))
                if not title or title in seen:
                    continue
                pub = node.findtext("pubDate", "")
                try:
                    dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                except Exception:  # noqa: BLE001
                    dt = datetime.now(timezone.utc)
                if dt < cutoff:
                    continue
                seen.add(title)
                items.append({"title": title, "time": dt.astimezone().strftime("%m-%d %H:%M"),
                              "link": node.findtext("link", "")})
                if len(items) >= max_items:
                    return items
    return items


def _keyword_scan(titles: list[str]) -> dict:
    joined = " ".join(titles).lower()
    veto = [k for k in VETO_KEYWORDS if k.lower() in joined]
    medium = [k for k in MEDIUM_KEYWORDS if k.lower() in joined]
    positive = [k for k in POSITIVE_KEYWORDS if k.lower() in joined]
    return {"veto": veto, "medium": medium, "positive": positive}


def _llm_judge(symbol: str, name: str, titles: list[str]) -> dict:
    """叫模型判斷有冇重大負面事件。冇 API key 或失敗時回傳中性。"""
    neutral = {"sentiment": "neutral", "severity": "low", "reason": "", "catalyst": ""}
    if not OPENAI_API_KEY or not titles:
        return neutral
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = (
            f"你係港股短線交易員。以下係 {name}（{symbol}）最近 48 小時嘅新聞標題。\n\n"
            + "\n".join(f"- {t}" for t in titles[:12])
            + "\n\n只回傳 JSON，唔要任何其他文字：\n"
            '{"sentiment":"positive|neutral|negative","severity":"high|medium|low",'
            '"catalyst":"一句講今日主要催化劑（冇就空字串）",'
            '"reason":"一句解釋"}\n'
            "判斷標準：只有真正會令股價短線大跌嘅事件（盈警、配股、被查、停牌、業績大幅倒退）"
            "才算 high；行業新聞或大市新聞唔算。"
        )
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        data.setdefault("catalyst", "")
        data.setdefault("reason", "")
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("新聞模型判斷失敗 %s: %s", symbol, exc)
        return neutral


def analyse(symbol: str, name: str, keywords: list[str]) -> dict:
    """
    回傳：
      penalty      : 要從技術分扣幾多
      veto         : True = 直接否決，唔好入
      sentiment / severity / catalyst / reason
      titles       : 原文標題（顯示用）
    """
    news = fetch_news(keywords)
    titles = [n["title"] for n in news]
    result = {"symbol": symbol, "news": news, "titles": titles, "penalty": 0,
              "veto": False, "sentiment": "neutral", "severity": "low",
              "catalyst": "", "reason": ""}

    if not titles:
        result["reason"] = "近 48 小時無相關新聞"
        return result

    kw = _keyword_scan(titles)
    llm = _llm_judge(symbol, name, titles)
    result["sentiment"] = llm.get("sentiment", "neutral")
    result["severity"] = llm.get("severity", "low")
    result["catalyst"] = llm.get("catalyst", "")
    result["reason"] = llm.get("reason", "")

    if kw["veto"] or (result["severity"] == "high" and result["sentiment"] == "negative"):
        result["veto"] = True
        result["penalty"] = NEWS_VETO_PENALTY
        tag = "、".join(kw["veto"]) if kw["veto"] else "模型判定重大負面"
        result["reason"] = f"重大負面事件（{tag}）"
        return result

    if kw["medium"] or (result["severity"] == "medium" and result["sentiment"] == "negative"):
        result["penalty"] = NEWS_MEDIUM_PENALTY
        if not result["reason"]:
            result["reason"] = "中度負面消息"
        return result

    if result["sentiment"] == "positive" or kw["positive"]:
        result["penalty"] = -min(NEWS_BONUS_CAP, 5)  # 負數 = 加分，但有上限
        if not result["reason"]:
            result["reason"] = "正面消息（加分有限，唔會主導）"
        return result

    result["reason"] = result["reason"] or "新聞中性"
    return result
