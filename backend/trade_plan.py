"""
入場計劃 + 倉位風控 + 出場決策
===============================
呢個檔案就係原本完全缺失嘅「幾時買幾多、幾時賣、止蝕止賺喺邊」。

核心原則（專業交易員做法）：
  1. 先定「輸幾多」，再定「買幾多」—— 唔係反過來
  2. 止蝕位由市場波動決定（ATR），唔係隨手劃一個 %
  3. 風險回報比唔夠就唔入，情願冇交易
  4. 賺到 1 倍風險就將止蝕推上成本價，之後跟移動止蝕
"""
from config import (
    ACCOUNT_SIZE_HKD, ATR_STOP_MULT, ATR_TARGET1_MULT, ATR_TARGET2_MULT,
    BREAKEVEN_ATR, DAILY_LOSS_LIMIT_PCT, MAX_HOLD_DAYS, MAX_OPEN_POSITIONS,
    MAX_POSITION_PCT, MIN_RR_RATIO, RISK_PER_TRADE_PCT, TRAIL_ATR_MULT,
)


def build_plan(symbol: str, name: str, price: float, atr: float,
               score: float, account_size: float | None = None,
               risk_pct: float | None = None) -> dict:
    """
    計入場計劃。
    回傳：止蝕價、止賺價（兩段）、建議股數、實際風險、風險回報比。
    """
    account_size = account_size if account_size is not None else ACCOUNT_SIZE_HKD
    risk_pct = risk_pct if risk_pct is not None else RISK_PER_TRADE_PCT

    if price <= 0 or atr <= 0:
        return {"symbol": symbol, "name": name, "valid": False,
                "reason": "價格或 ATR 無效，無法計算風險"}

    stop = price - ATR_STOP_MULT * atr
    target1 = price + ATR_TARGET1_MULT * atr
    target2 = price + ATR_TARGET2_MULT * atr

    risk_per_share = price - stop
    reward_per_share = target2 - price
    rr = reward_per_share / risk_per_share if risk_per_share > 0 else 0.0

    risk_budget = account_size * risk_pct / 100.0
    raw_shares = int(risk_budget / risk_per_share) if risk_per_share > 0 else 0

    # 單一倉位唔可以超過本金某個比例
    max_shares_by_exposure = int(account_size * MAX_POSITION_PCT / 100.0 / price)
    shares = max(0, min(raw_shares, max_shares_by_exposure))

    # 港股手數差異大，但短炒可以碎股；呢度唔強制整手，交由你自己落單決定
    capital_used = shares * price
    actual_risk = shares * risk_per_share

    valid = rr >= MIN_RR_RATIO and shares > 0

    return {
        "symbol": symbol,
        "name": name,
        "valid": valid,
        "entry": round(price, 3),
        "atr": round(atr, 3),
        "stop_loss": round(stop, 3),
        "target1": round(target1, 3),
        "target2": round(target2, 3),
        "stop_pct": round((stop / price - 1) * 100, 2),
        "target1_pct": round((target1 / price - 1) * 100, 2),
        "target2_pct": round((target2 / price - 1) * 100, 2),
        "risk_per_share": round(risk_per_share, 3),
        "rr_ratio": round(rr, 2),
        "shares": shares,
        "capital_used": round(capital_used, 0),
        "capital_pct": round(capital_used / account_size * 100, 1) if account_size else 0,
        "max_loss_hkd": round(actual_risk, 0),
        "max_loss_pct_of_account": round(actual_risk / account_size * 100, 2) if account_size else 0,
        "max_gain_hkd": round(shares * (target2 - price), 0),
        "plan": (
            f"入場 {price:.3f}｜止蝕 {stop:.3f}（{((stop/price)-1)*100:+.1f}%）｜"
            f"目標一 {target1:.3f}（{((target1/price)-1)*100:+.1f}%，減半倉）｜"
            f"目標二 {target2:.3f}（{((target2/price)-1)*100:+.1f}%，清倉）"
        ),
        "reason": ("" if valid else
                   f"風險回報比只有 {rr:.2f}，低於要求 {MIN_RR_RATIO}，唔值得入"),
    }


def evaluate_position(pos: dict, price: float, atr: float,
                      highest: float | None = None) -> dict:
    """
    對一個持倉做實時判斷，回傳建議動作。
    pos 需要：entry_price, stop_loss, target1, target2, qty, opened_at
    """
    entry = float(pos["entry_price"])
    stop = float(pos["stop_loss"])
    t1 = float(pos["target1"])
    t2 = float(pos["target2"])
    qty = float(pos.get("qty", 0))
    highest = max(float(highest or price), price)

    pnl_pct = (price / entry - 1) * 100 if entry else 0.0
    pnl_hkd = (price - entry) * qty

    # 動態移動止蝕
    new_stop = stop
    trail_stop = highest - TRAIL_ATR_MULT * atr
    if atr > 0:
        if price >= entry + BREAKEVEN_ATR * atr:
            new_stop = max(new_stop, entry)          # 保本
        if highest > entry + BREAKEVEN_ATR * atr:
            new_stop = max(new_stop, trail_stop)     # 移動止蝕

    if price <= stop:
        action, urgency = "EXIT", "高"
        reason = f"已跌破止蝕 {stop:.3f}，立即離場"
    elif price >= t2:
        action, urgency = "EXIT", "中"
        reason = f"已達目標二 {t2:.3f}，全部清倉鎖定利潤"
    elif price >= t1:
        action, urgency = "TRIM", "中"
        reason = f"已達目標一 {t1:.3f}，建議減一半倉，其餘推止蝕到成本價"
    elif price < entry and pnl_pct < -1.5:
        action, urgency = "WATCH", "中"
        reason = f"倒跌 {pnl_pct:.1f}%，貼近止蝕，留意"
    else:
        action, urgency = "HOLD", "低"
        reason = "持倉中，未觸及任何關鍵位"

    dist_stop = (price / new_stop - 1) * 100 if new_stop else 0.0
    dist_t1 = (t1 / price - 1) * 100 if price else 0.0

    return {
        "symbol": pos["symbol"],
        "name": pos.get("name", ""),
        "entry": entry,
        "price": round(price, 3),
        "qty": qty,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_hkd": round(pnl_hkd, 0),
        "stop_loss": round(new_stop, 3),
        "stop_moved": round(new_stop - stop, 3) > 0.0005,
        "target1": t1,
        "target2": t2,
        "dist_to_stop_pct": round(dist_stop, 2),
        "dist_to_target1_pct": round(dist_t1, 2),
        "action": action,
        "urgency": urgency,
        "reason": reason,
        "atr": round(atr, 3),
    }


def portfolio_guard(open_positions: list[dict], today_pnl_pct: float) -> dict:
    """組合層面風控：開倉數上限、當日停損。"""
    if today_pnl_pct <= -DAILY_LOSS_LIMIT_PCT:
        return {"allow_new": False,
                "reason": f"今日已虧損 {today_pnl_pct:.1f}%，觸及 {DAILY_LOSS_LIMIT_PCT}% 停損線，今日應該收手。"
                          "追單係散戶破產第一大原因。"}
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        return {"allow_new": False,
                "reason": f"已有 {len(open_positions)} 個持倉，達上限 {MAX_OPEN_POSITIONS} 個，先處理手上倉位。"}
    if len(open_positions) >= 1:
        return {"allow_new": True,
                "reason": f"現有 {len(open_positions)} 倉，可再開 {MAX_OPEN_POSITIONS - len(open_positions)} 個。"}
    return {"allow_new": True, "reason": ""}
