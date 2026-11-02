"""K 線圖表 —— 2026-11-02 加（用戶要求訊號附 M1/M15 圖）。

為咩要圖：用戶實測發現「有機會係反方向」，所以想睇實際走勢自己判斷。
圖用英文標籤，避免伺服器冇中文字型變豆腐塊。
"""
import io
import logging

import matplotlib
matplotlib.use("Agg")          # 無 GUI 環境必用
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

log = logging.getLogger("tylove.charts")

BG = "#0f172a"
FG = "#94a3b8"
UP = "#22c55e"
DN = "#ef4444"


def render(df, interval: int, bars: int = 60, title: str = "") -> bytes | None:
    """把 K 線畫成 PNG（回傳 bytes）。df 需要有 Open/High/Low/Close。"""
    if df is None or len(df) < 10:
        return None
    d = df.tail(bars).copy()
    try:
        if getattr(d.index, "tz", None) is not None:
            d.index = d.index.tz_localize(None)
    except Exception:  # noqa: BLE001
        pass

    n = len(d)
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for i in range(n):
        o = float(d["Open"].iloc[i]); c = float(d["Close"].iloc[i])
        h = float(d["High"].iloc[i]); l = float(d["Low"].iloc[i])
        col = UP if c >= o else DN
        ax.vlines(i, l, h, color=col, linewidth=0.8)
        lo, hi = min(o, c), max(o, c)
        ax.add_patch(Rectangle((i - 0.35, lo), 0.7, max(hi - lo, 1e-9),
                               facecolor=col, edgecolor=col, linewidth=0.5))

    # EMA20 / EMA50（同系統指標一致）
    if n >= 50:
        e20 = d["Close"].ewm(span=20, adjust=False).mean()
        e50 = d["Close"].ewm(span=50, adjust=False).mean()
        ax.plot(range(n), e20.values, color="#38bdf8", linewidth=1.0, label="EMA20")
        ax.plot(range(n), e50.values, color="#f59e0b", linewidth=1.0, label="EMA50")
        ax.legend(loc="upper left", fontsize=6, facecolor=BG,
                  edgecolor="#334155", labelcolor=FG)

    label = "M%d" % interval if interval < 60 else ("H%d" % (interval // 60))
    ttl = title or ("XAUUSD  %s  last %d bars" % (label, n))
    ax.set_title(ttl, color="#e2e8f0", fontsize=8, pad=4)
    ax.tick_params(colors=FG, labelsize=6)
    for sp in ax.spines.values():
        sp.set_color("#334155")
    ax.grid(True, color="#1e293b", linewidth=0.4)
    ax.set_xlim(-1, n)
    # x 軸只顯示幾個時間標籤，避免擠
    step = max(1, n // 6)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([d.index[i].strftime("%m-%d %H:%M") for i in range(0, n, step)],
                       rotation=0, fontsize=5)

    buf = io.BytesIO()
    fig.tight_layout(pad=0.4)
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
