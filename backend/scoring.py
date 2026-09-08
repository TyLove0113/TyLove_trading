# ─────────────────────────────────────────
# scoring.py — 股票評分系統
# 根據技術面、消息面、風險面計算 0-100 分
# ─────────────────────────────────────────

import config

def score_technical(data: dict) -> dict:
    """
    技術面評分（50分）
    ├─ 趨勢方向（MA排列）：15分
    ├─ RSI 位置：15分
    ├─ 成交量：10分
    └─ 價格位置：10分
    """
    score  = 0
    detail = {}

    # ── 趨勢方向（MA5 vs MA20）──────────────
    ma5   = data.get("ma5", 0)
    ma20  = data.get("ma20", 0)
    price = data.get("price", 0)

    if ma5 > ma20 and price > ma5:
        trend_score = 15      # 強勢多頭
        trend_label = "強勢上升"
    elif ma5 > ma20:
        trend_score = 10      # 溫和多頭
        trend_label = "溫和上升"
    elif ma5 < ma20 and price < ma5:
        trend_score = 0       # 強勢空頭
        trend_label = "強勢下跌"
    else:
        trend_score = 5       # 橫行
        trend_label = "橫行整固"

    score += trend_score
    detail["趨勢"] = f"{trend_label} ({trend_score}/15)"

    # ── RSI 位置 ──────────────────────────
    rsi = data.get("rsi", 50)

    if 30 <= rsi <= 50:
        rsi_score = 15        # 超賣回升，最佳入場區
        rsi_label = "超賣回升 ✅"
    elif 50 < rsi <= 65:
        rsi_score = 10        # 健康上升
        rsi_label = "健康上升"
    elif 65 < rsi <= 75:
        rsi_score = 5         # 偏熱
        rsi_label = "偏熱注意"
    elif rsi < 30:
        rsi_score = 8         # 極度超賣，反彈機會但有風險
        rsi_label = "極度超賣"
    else:
        rsi_score = 0         # 超買，唔追
        rsi_label = "超買 ❌"

    score += rsi_score
    detail["RSI"] = f"{rsi:.1f} — {rsi_label} ({rsi_score}/15)"

    # ── 成交量 ────────────────────────────
    # 暫時用漲跌幅同 RSI 推算（之後接真實成交量數據再優化）
    change_pct = abs(data.get("change_pct", 0))

    if change_pct > 2.0:
        vol_score = 10        # 大幅波動，有資金入場
        vol_label = "成交活躍"
    elif change_pct > 1.0:
        vol_score = 6
        vol_label = "成交正常"
    else:
        vol_score = 3
        vol_label = "成交清淡"

    score += vol_score
    detail["成交量"] = f"{vol_label} ({vol_score}/10)"

    # ── 價格位置（相對 MA20）──────────────
    if ma20 > 0:
        pct_from_ma20 = (price - ma20) / ma20 * 100
        if -2 <= pct_from_ma20 <= 3:
            pos_score = 10    # 靠近 MA20，理想入場區
            pos_label = "MA20 支撐附近 ✅"
        elif 3 < pct_from_ma20 <= 8:
            pos_score = 6
            pos_label = "MA20 以上"
        elif pct_from_ma20 > 8:
            pos_score = 2
            pos_label = "遠離 MA20，追高風險"
        else:
            pos_score = 4
            pos_label = "MA20 以下"
    else:
        pos_score = 5
        pos_label = "無法計算"

    score += pos_score
    detail["價格位置"] = f"{pos_label} ({pos_score}/10)"

    return {"score": score, "max": 50, "detail": detail}


def score_risk(data: dict) -> dict:
    """
    風險面評分（20分）
    ├─ 波動率：10分
    └─ 趨勢一致性：10分
    """
    score  = 0
    detail = {}

    # ── 波動率（用漲跌幅衡量）────────────
    change_pct = abs(data.get("change_pct", 0))

    if change_pct <= 2.0:
        vol_score = 10        # 波動適中，風險可控
        vol_label = "波動可控 ✅"
    elif change_pct <= 4.0:
        vol_score = 6
        vol_label = "波動略高"
    else:
        vol_score = 2         # 波動過大，風險高
        vol_label = "波動過大 ⚠️"

    score += vol_score
    detail["波動率"] = f"{vol_label} ({vol_score}/10)"

    # ── 趨勢一致性 ────────────────────────
    rsi        = data.get("rsi", 50)
    change_pct_raw = data.get("change_pct", 0)
    above_ma5  = data.get("above_ma5", False)

    # RSI 同價格走勢一致 = 低風險
    if change_pct_raw > 0 and rsi > 45 and above_ma5:
        cons_score = 10
        cons_label = "趨勢一致 ✅"
    elif change_pct_raw < 0 and rsi < 55:
        cons_score = 6
        cons_label = "下跌趨勢一致"
    else:
        cons_score = 4
        cons_label = "趨勢背離，需注意"

    score += cons_score
    detail["趨勢一致性"] = f"{cons_label} ({cons_score}/10)"

    return {"score": score, "max": 20, "detail": detail}


def score_news(symbol: str, total_score: int = 0) -> dict:
    """
    消息面評分（30分）
    呼叫 news_sentiment 模組
    """
    try:
        from news_sentiment import analyse_stock_news
        return analyse_stock_news(symbol, total_score)
    except Exception as e:
        print(f"  ⚠️ 新聞分析失敗 {symbol}: {e}")
        return {
            "score":   15,
            "max":     30,
            "label":   "中性",
            "detail":  {"新聞情緒": "分析失敗，給予中性分數（15/30）"},
            "summary": "",
        }


def calculate_total_score(data: dict, run_news: bool = True) -> dict:
    """
    計算綜合評分
    """
    technical = score_technical(data)
    risk      = score_risk(data)

    # 先用技術+風險分數估算，決定係咪做新聞分析
    estimated_total = technical["score"] + risk["score"] + 15

    if run_news:
        news = score_news(data["symbol"], estimated_total)
    else:
        news = {"score": 15, "max": 30, "label": "中性",
                "detail": {"新聞情緒": "跳過（15/30）"}, "summary": ""}

    total = technical["score"] + risk["score"] + news["score"]

    # 評級
    if total >= 80:
        grade  = "🟢 強烈留意"
        action = "條件理想，值得重點關注"
    elif total >= 70:
        grade  = "🟡 值得留意"
        action = "條件不錯，可以考慮入場"
    elif total >= 60:
        grade  = "🟠 一般"
        action = "條件一般，建議觀望"
    else:
        grade  = "🔴 跳過"
        action = "條件不足，唔符合入場要求"

    return {
        "symbol":     data["symbol"],
        "price":      data["price"],
        "change_pct": data["change_pct"],
        "total":      total,
        "grade":      grade,
        "action":     action,
        "technical":  technical,
        "risk":       risk,
        "news":       news,
    }


def score_all_stocks(stocks: list) -> list:
    """
    對所有股票評分並排序
    """
    results = []
    for stock in stocks:
        result = calculate_total_score(stock)
        results.append(result)

    # 由高到低排序
    results.sort(key=lambda x: x["total"], reverse=True)
    return results


def print_score_report(results: list):
    """印出評分報告（含新聞分析）"""
    print("\n" + "═" * 55)
    print("  📊 港股評分報告")
    print("═" * 55)

    for r in results:
        sign = "▲" if r["change_pct"] >= 0 else "▼"
        print(f"\n  {r['symbol']:<12} "
              f"{r['price']:>8.3f}  "
              f"{sign}{abs(r['change_pct']):.2f}%")
        print(f"  總分：{r['total']}/100  {r['grade']}")
        print(f"  建議：{r['action']}")

        # 技術面
        print(f"  技術面（{r['technical']['score']}/50）：")
        for k, v in r['technical']['detail'].items():
            print(f"    {k}：{v}")

        # 風險面
        print(f"  風險面（{r['risk']['score']}/20）：")
        for k, v in r['risk']['detail'].items():
            print(f"    {k}：{v}")

        # 新聞面
        news = r.get("news", {})
        print(f"  新聞面（{news.get('score', 15)}/30）：")
        for k, v in news.get("detail", {}).items():
            print(f"    {k}：{v}")

        # 新聞標題
        headlines = news.get("headlines", [])
        if headlines:
            print(f"  新聞標題：")
            for h in headlines:
                print(f"    • {h}")

        # AI 詳細分析（只有高分股票先有）
        summary = news.get("summary", "")
        if summary:
            print(f"  🤖 AI 分析：")
            for line in summary.split("\n"):
                if line.strip():
                    print(f"    {line}")

    print("\n" + "═" * 55)

    # 值得留意嘅股票
    top = [r for r in results if r["total"] >= 70]
    if top:
        print(f"\n  🔔 今日值得留意（{len(top)} 隻）：")
        for r in top:
            print(f"    {r['symbol']}  {r['total']}分  {r['grade']}")
    else:
        print("\n  今日暫無符合條件嘅股票")

    print("═" * 55 + "\n")


# ── 測試用 ────────────────────────────────
if __name__ == "__main__":
    from data_fetcher import get_all_stocks
    stocks  = get_all_stocks()
    results = score_all_stocks(stocks)
    print_score_report(results)