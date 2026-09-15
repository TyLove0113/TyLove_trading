"""
掃描引擎 — 整個系統嘅大腦
流程：拉數據 → 過濾 → 計指標 → 評分 → 新聞否決 → 出交易計劃 → 通知
另外負責持倉監控（呢個係原版完全冇嘅部分）。
"""
import logging

import data_fetcher as df_mod
import indicators
import news_sentiment
import notifier
import positions as pos_mod
import scoring
import trade_plan
from config import ALERT_THRESHOLD, WATCH_THRESHOLD, WATCHLIST

log = logging.getLogger("tylove.scanner")

NAME_MAP = {
    "0700.HK": "騰訊控股", "9988.HK": "阿里巴巴", "3690.HK": "美團",
    "1810.HK": "小米集團", "9618.HK": "京東集團", "1211.HK": "比亞迪股份",
    "2318.HK": "中國平安", "0388.HK": "香港交易所", "2020.HK": "安踏體育",
    "0981.HK": "中芯國際", "2382.HK": "舜宇光學", "6690.HK": "海爾智家",
    "0005.HK": "滙豐控股", "1299.HK": "友邦保險", "0939.HK": "建設銀行",
    "1810.HK ": "小米集團",
}


def name_of(symbol: str) -> str:
    return NAME_MAP.get(symbol.upper(), symbol.replace(".HK", ""))


def analyse_one(symbol: str, bench: "object", want_news: bool = True) -> dict | None:
    """完整分析一隻股票。"""
    daily = df_mod.fetch_daily(symbol)
    if len(daily) < 60:
        log.info("%s 數據不足，跳過", symbol)
        return None

    ind = indicators.compute(daily)
    s = indicators.latest_snapshot(ind)
    s["rs_20d"] = df_mod.relative_strength(daily, bench, 20)

    # 即時價：日線最後一支可能係昨日，用 15 分鐘線修正
    intraday = df_mod.fetch_15m(symbol)
    if len(intraday) > 0:
        live = float(intraday["Close"].iloc[-1])
        if live > 0:
            s["live_price"] = live

    news = (news_sentiment.analyse(symbol, name_of(symbol), [name_of(symbol), symbol.replace(".HK", "")])
            if want_news else {"penalty": 0, "veto": False, "sentiment": "neutral",
                               "severity": "low", "catalyst": "", "reason": "未查新聞", "titles": []})

    tech = scoring.score_technical(s)
    combined = scoring.combine(tech, news, s)

    price = s.get("live_price", s["close"])
    plan = trade_plan.build_plan(symbol, name_of(symbol), price, s["atr"], combined["total_score"])

    # 日內走勢確認：15 分鐘線方向
    intraday_note = ""
    if len(intraday) >= 20:
        i_ind = indicators.compute(intraday)
        i_snap = indicators.latest_snapshot(i_ind)
        if i_snap["ma5"] and i_snap["ma20"]:
            intraday_note = ("15 分鐘：多頭" if i_snap["ma5"] > i_snap["ma20"] else "15 分鐘：空頭")
            intraday_note += f"（RSI {i_snap['rsi']:.0f}）"

    out = {
        "symbol": symbol,
        "name": name_of(symbol),
        "price": round(price, 3),
        "total_score": combined["total_score"],
        "technical_score": combined["technical_score"],
        "breakdown": combined["breakdown"],
        "notes": combined["notes"],
        "news": news,
        "veto": combined["veto"],
        "pass_filter": combined["pass_filter"],
        "filter_reason": combined["filter_reason"],
        "plan": plan,
        "intraday": intraday_note,
        "snapshot": s,
    }
    return out


def scan(watchlist: list[str] | None = None, push_summary: bool = True,
         scan_type: str = "定時掃描") -> dict:
    """掃描整個觀察名單。"""
    watchlist = watchlist or WATCHLIST
    bench = df_mod.fetch_benchmark()

    results, signals, vetoed, filtered = [], [], [], []
    for symbol in watchlist:
        try:
            r = analyse_one(symbol, bench)
        except Exception as exc:  # noqa: BLE001
            log.exception("分析 %s 失敗", symbol)
            notifier.push(notifier.fmt_error(f"分析 {symbol}", str(exc)))
            continue
        if not r:
            continue
        results.append(r)

        if r["veto"]:
            vetoed.append(r)
        elif not r["pass_filter"]:
            filtered.append(r)
        elif r["total_score"] >= ALERT_THRESHOLD and r["plan"].get("valid"):
            signals.append(r)
        elif r["total_score"] >= WATCH_THRESHOLD:
            signals.append(r)

        if r["total_score"] >= WATCH_THRESHOLD:
            pos_mod.log_signal({
                "symbol": r["symbol"], "name": r["name"], "total_score": r["total_score"],
                "price": r["price"], "stop_loss": r["plan"].get("stop_loss"),
                "target1": r["plan"].get("target1"), "target2": r["plan"].get("target2"),
                "rr_ratio": r["plan"].get("rr_ratio"), "veto": r["veto"],
            })

    signals.sort(key=lambda x: -x["total_score"])
    results.sort(key=lambda x: -x["total_score"])

    # 組合風控：有冇持倉、今日輸夠未
    open_pos = pos_mod.list_open()
    allow = trade_plan.portfolio_guard(open_pos, today_pnl_pct())

    if push_summary:
        _push_scan(signals, results, vetoed, filtered, allow, scan_type)

    return {"signals": signals, "all": results, "vetoed": vetoed,
            "filtered": filtered, "allow_new": allow, "scanned_at": None}


def _push_scan(signals, results, vetoed, filtered, allow, scan_type):
    head = notifier.fmt_scan_header(scan_type, len(results), len(signals))
    if not signals:
        top = results[:3]
        lines = [head, "今日暫時冇股票達到入場門檻。", ""]
        for r in top:
            lines.append(f"{r['symbol']} {r['name']} {r['total_score']} 分（技術 {r['technical_score']}）")
        if allow.get("reason"):
            lines.append("")
            lines.append(allow["reason"])
        lines.append("─ 寧願唔做，都唔好亂做 ─")
        notifier.push("\n".join(lines))
        return

    notifier.push(head)
    for item in signals[:5]:
        notifier.push(notifier.fmt_signal(item))
    if len(signals) > 5:
        notifier.push(f"（另有 {len(signals)-5} 隻達標，可到 dashboard 睇全部）")
    if allow.get("reason"):
        notifier.push(f"📋 倉位提示：{allow['reason']}")
    if vetoed:
        names = "、".join(f"{v['symbol']}({v['news'].get('reason','')})" for v in vetoed[:4])
        notifier.push(f"🚫 已否決：{names}")


# ------------------------------------------------------------------ 持倉監控
def today_pnl_pct() -> float:
    """用已平倉 + 未平倉計當日盈虧百分比（簡化：以今日開倉嘅倉位計）。"""
    open_pos = pos_mod.list_open()
    if not open_pos:
        return 0.0
    total_pnl, total_cost = 0.0, 0.0
    for p in open_pos:
        price = df_mod.live_price(p["symbol"])
        if not price:
            continue
        total_pnl += (price - p["entry_price"]) * p["qty"]
        total_cost += p["entry_price"] * p["qty"]
    if total_cost == 0:
        return 0.0
    return total_pnl / total_cost * 100


def monitor_positions(push: bool = True, force: bool = False) -> list[dict]:
    """
    逐個持倉對照現價，出「幾時走」嘅建議。
    呢個就係你最想要嘅功能：程式主動話你知止蝕止賺喺邊、幾時要動。
    """
    open_pos = pos_mod.list_open()
    if not open_pos:
        return []

    out = []
    for p in open_pos:
        daily = df_mod.fetch_daily(p["symbol"])
        if len(daily) < 30:
            continue
        ind = indicators.compute(daily)
        atr = float(ind["ATR14"].iloc[-1])
        price = df_mod.live_price(p["symbol"]) or float(daily["Close"].iloc[-1])

        rec = trade_plan.evaluate_position(p, price, atr, p.get("highest_price"))
        rec["id"] = p["id"]

        # 更新移動止蝕
        if rec["stop_loss"] > float(p["stop_loss"]) + 0.0005 or price > float(p.get("highest_price") or 0):
            pos_mod.update_position_risk(p["id"], rec["stop_loss"], price)

        out.append(rec)

    if push:
        urgent = [r for r in out if r["action"] in ("EXIT", "TRIM")]
        if urgent or force:
            for r in urgent:
                notifier.push(notifier.fmt_position(r))
            if not urgent and force:
                body = "\n\n".join(notifier.fmt_position(r) for r in out)
                notifier.push(body)
    return out


def closing_reminder() -> str:
    """收市前提醒 —— 連埋每個持倉嘅實際盈虧同建議。"""
    open_pos = pos_mod.list_open()
    recs = monitor_positions(push=False)
    allow = trade_plan.portfolio_guard(open_pos, today_pnl_pct())
    text = notifier.fmt_close_reminder(recs, allow)
    notifier.push(text)
    return text
