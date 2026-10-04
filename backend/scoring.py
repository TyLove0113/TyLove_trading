"""
評分系統 v2 — 修正原版三個實質錯誤
  1. 原版用「漲跌幅」當成交量代理 → 新版用真實 Volume / 20 日均量
  2. 原版 abs(change_pct) 同時被計成「成交活躍(+10)」同「波動過大(+2)」→ 同一個數字計兩次
     新版：成交量只看量比，波動只看 ATR%，兩者輸入完全唔同
  3. 原版冇相對強度 → 新版加入「跑贏恒指」維度，呢個係短線選股最有效嘅因子之一

100 分結構（純技術，唔含新聞）：
   趨勢結構    25
   動能        20
   成交量      20
   波動質素    15
   相對強度    20
新聞只做否決/扣分，另計。
"""
from config import MAX_ATR_PCT, MIN_ATR_PCT, MIN_TURNOVER_HKD


def _trend(s: dict) -> tuple[float, list[str]]:
    """趨勢結構 25 分。"""
    score, notes = 0.0, []
    close, ma5, ma20, ma50 = s["close"], s["ma5"], s["ma20"], s["ma50"]

    if ma20 and close > ma20:
        score += 8
        notes.append("價在MA20上 +8")
    if ma5 and ma20 and ma5 > ma20:
        score += 7
        notes.append("MA5>MA20 多頭排列 +7")
    if s["ma20_slope"] > 0.15:
        score += 10
        notes.append(f"MA20 上升中（{s['ma20_slope']:.2f}%/支）+10")
    elif s["ma20_slope"] > 0:
        score += 5
        notes.append("MA20 微升 +5")
    if ma50 and close > ma50:
        notes.append("站穩MA50")
    if s["adx"] >= 25:
        notes.append(f"ADX {s['adx']:.0f} 趨勢明確（僅供參考，唔重複加分）")
    return min(score, 25.0), notes


def _momentum(s: dict) -> tuple[float, list[str]]:
    """動能 20 分。"""
    score, notes = 0.0, []
    rsi, hist, hist_prev = s["rsi"], s["macd_hist"], s["hist_prev"]

    if 50 <= rsi <= 68:
        score += 10
        notes.append(f"RSI {rsi:.0f} 強勢未超買 +10")
    elif 45 <= rsi < 50 or 68 < rsi <= 75:
        score += 6
        notes.append(f"RSI {rsi:.0f} 中性偏強 +6")
    elif rsi > 80:
        notes.append(f"RSI {rsi:.0f} 嚴重超買，追入風險高")
    elif rsi < 30:
        notes.append(f"RSI {rsi:.0f} 超賣，可留意反彈但唔係強勢")

    if hist > 0 and hist > hist_prev:
        score += 10
        notes.append("MACD 柱狀放大 +10")
    elif hist > 0:
        score += 6
        notes.append("MACD 柱狀為正 +6")
    elif hist > hist_prev:
        score += 3
        notes.append("MACD 柱狀收窄中 +3")
    return min(score, 20.0), notes


def _volume(s: dict) -> tuple[float, list[str]]:
    """成交量 20 分 —— 只用真實量比，同波動完全分開。"""
    score, notes = 0.0, []
    ratio = s["vol_ratio"]

    if ratio >= 1.8:
        score += 12
        notes.append(f"量比 {ratio:.1f}x 明顯放量 +12")
    elif ratio >= 1.3:
        score += 9
        notes.append(f"量比 {ratio:.1f}x 溫和放量 +9")
    elif ratio >= 1.0:
        score += 5
        notes.append(f"量比 {ratio:.1f}x 正常 +5")
    else:
        notes.append(f"量比 {ratio:.1f}x 縮量，動力不足")

    if s["close"] * s["volume"] >= s["turnover_ma20"] * 1.2 and s["turnover_ma20"] > 0:
        score += 8
        notes.append("今日成交額高於 20 日均 20% +8")
    elif s["turnover_ma20"] > 0:
        score += 3
        notes.append("成交額平穩 +3")
    return min(score, 20.0), notes


def _volatility(s: dict) -> tuple[float, list[str]]:
    """波動質素 15 分 —— ATR% 有甜區：太死無肉食，太癲係賭博。"""
    atr_pct = s["atr_pct"]
    notes = []
    if 2.0 <= atr_pct <= 4.5:
        notes.append(f"ATR {atr_pct:.1f}% 短炒甜區 +15")
        return 15.0, notes
    if 1.5 <= atr_pct < 2.0 or 4.5 < atr_pct <= 6.0:
        notes.append(f"ATR {atr_pct:.1f}% 可接受 +9")
        return 9.0, notes
    if atr_pct < MIN_ATR_PCT:
        notes.append(f"ATR {atr_pct:.1f}% 太死，日內無波幅")
        return 0.0, notes
    if atr_pct > MAX_ATR_PCT:
        notes.append(f"ATR {atr_pct:.1f}% 波動過大，風險失控")
        return 0.0, notes
    notes.append(f"ATR {atr_pct:.1f}% 偏低 +3")
    return 3.0, notes


def _relative_strength(s: dict) -> tuple[float, list[str]]:
    """相對強度 20 分 —— 跑贏恒指先值得短炒。"""
    rs = s["rs_20d"]
    if rs >= 8:
        return 20.0, [f"20 日跑贏恒指 {rs:+.1f}% 強勢 +20"]
    if rs >= 4:
        return 15.0, [f"20 日跑贏恒指 {rs:+.1f}% +15"]
    if rs >= 0:
        return 9.0, [f"20 日略勝恒指 {rs:+.1f}% +9"]
    if rs >= -5:
        return 3.0, [f"20 日跑輸恒指 {rs:+.1f}% +3"]
    return 0.0, [f"20 日跑輸恒指 {rs:+.1f}% 弱勢，唔值搏"]


def liquidity_filter(s: dict) -> tuple[bool, str]:
    """入場硬性過濾：唔夠成交額就唔好玩。"""
    if s["close"] < 1.0:
        return False, "股價低於 HK$1，屬仙股風險"
    if s["turnover_ma20"] < MIN_TURNOVER_HKD:
        return False, f"20 日均成交額僅 {s['turnover_ma20']/1e6:.0f} 百萬，流動性不足"
    if s["atr_pct"] < MIN_ATR_PCT:
        return False, f"ATR 只有 {s['atr_pct']:.1f}%，短線無波幅"
    return True, ""


def score_technical(s: dict) -> dict:
    trend, t_notes = _trend(s)
    momentum, m_notes = _momentum(s)
    volume, v_notes = _volume(s)
    vola, vo_notes = _volatility(s)
    rs, rs_notes = _relative_strength(s)

    total = trend + momentum + volume + vola + rs
    return {
        "technical_score": round(total, 1),
        "breakdown": {
            "趨勢結構(25)": round(trend, 1),
            "動能(20)": round(momentum, 1),
            "成交量(20)": round(volume, 1),
            "波動質素(15)": round(vola, 1),
            "相對強度(20)": round(rs, 1),
        },
        "notes": t_notes + m_notes + v_notes + vo_notes + rs_notes,
    }


def combine(technical: dict, news: dict, s: dict) -> dict:
    """技術分 + 新聞調整 = 總分。新聞只扣分或小額加分。"""
    ok, reason = liquidity_filter(s)
    total = technical["technical_score"] + news["penalty"]
    total = max(0.0, min(100.0, total))
    return {
        "total_score": round(total, 1),
        "technical_score": technical["technical_score"],
        "news_penalty": news["penalty"],
        "veto": news["veto"],
        "pass_filter": ok,
        "filter_reason": reason,
        "breakdown": technical["breakdown"],
        "notes": technical["notes"],
        "news": news,
    }
