//+------------------------------------------------------------------+
//|  TyLovePriceFeed.mq4                                             |
//|  將你券商（AXI）嘅真實報價推送去 TyLove 交易系統                  |
//|                                                                  |
//|  目的：程式原本用 yfinance 期貨（GC=F），同你 MT4 現貨差          |
//|        US$20-35。行咗呢隻 EA 之後，系統就用你券商嘅真實報價，     |
//|        價位同你 MT4 完全一致，唔需要再估校正值。                  |
//|                                                                  |
//|  安裝步驟見 tools/設定指示.md                                     |
//+------------------------------------------------------------------+
#property copyright "TyLove"
#property version   "1.00"
#property strict

//============= 你只需要改呢兩行 =============
extern string API_BASE   = "https://你的-app-名.up.railway.app";
extern string FEED_TOKEN = "";   // 同 Railway 環境變數 GOLD_FEED_TOKEN 填一樣；冇設定就留空
//============================================

extern int    TickSeconds      = 5;    // 每幾秒推一次即時報價
extern int    BarMinutes       = 15;   // K 線週期（分鐘）—— 同策略一致，唔好亂改
extern int    BarCount         = 300;  // 每次推幾多支 K 線
extern int    PushBarsEveryMin = 15;   // 每幾分鐘重推一次 K 線
extern string SymbolName       = "";   // 留空 = 用圖表本身嘅品種

string   g_sym;
int      g_tf;
int      g_digits;
int      g_tzOffset = 0;   // 券商 server 同 UTC 嘅差（秒）—— MT4 時間戳係 server time
datetime g_lastBarPush = 0;
int      g_okTicks = 0, g_okBars = 0, g_fail = 0;
string   g_lastMsg = "（未開始）";

//+------------------------------------------------------------------+
int MapTF(int minutes) {
    switch (minutes) {
        case 1:    return PERIOD_M1;
        case 5:    return PERIOD_M5;
        case 15:   return PERIOD_M15;
        case 30:   return PERIOD_M30;
        case 60:   return PERIOD_H1;
        case 240:  return PERIOD_H4;
        case 1440: return PERIOD_D1;
    }
    return PERIOD_M15;
}

string D2S(double v) {
    return DoubleToString(v, g_digits);
}

//+------------------------------------------------------------------+
//| 發一個 POST 出去。回傳 true = 成功                                |
//+------------------------------------------------------------------+
bool Post(string path, string body) {
    string url = API_BASE + path;
    string headers = "Content-Type: application/json\r\n";
    if (StringLen(FEED_TOKEN) > 0)
        headers = headers + "X-Feed-Token: " + FEED_TOKEN + "\r\n";

    char   post[], result[];
    string resultHeaders = "";
    int len = StringToCharArray(body, post, 0, WHOLE_ARRAY, CP_UTF8) - 1;
    if (len < 0) len = 0;
    ArrayResize(post, len);

    ResetLastError();
    int code = WebRequest("POST", url, headers, 5000, post, result, resultHeaders);

    if (code == -1) {
        g_fail++;
        g_lastMsg = "WebRequest 錯誤 " + IntegerToString(GetLastError());
        if (g_fail <= 3) {
            Print("❌ WebRequest 失敗（錯誤 ", GetLastError(), "）");
            Print("   → 請去：工具 → 選項 → 智能交易系統 → ");
            Print("     勾「允許 WebRequest 嘅 URL 列表」，加入：", API_BASE);
        }
        return (false);
    }
    if (code != 200) {
        g_fail++;
        g_lastMsg = "HTTP " + IntegerToString(code);
        Print("⚠️ 伺服器回 HTTP ", code, "：",
              CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8));
        return (false);
    }
    return (true);
}

//+------------------------------------------------------------------+
void SendTick() {
    double bid = MarketInfo(g_sym, MODE_BID);
    double ask = MarketInfo(g_sym, MODE_ASK);
    if (bid <= 0.0 || ask <= 0.0) return;

    string body = StringFormat(
        "{\"symbol\":\"%s\",\"bid\":%s,\"ask\":%s,\"source\":\"mt4\",\"t\":%d}",
        g_sym, D2S(bid), D2S(ask), (int)TimeCurrent() - g_tzOffset);

    if (Post("/api/gold/tick", body)) {
        g_okTicks++;
        g_lastMsg = "報價已推（spread " + D2S(ask - bid) + "）";
    }
    ShowStatus();
}

//+------------------------------------------------------------------+
void SendBars() {
    int total = iBars(g_sym, g_tf);
    if (total < 30) {
        Print("⚠️ 歷史 K 線不足（只有 ", total, " 支），請喺 MT4 下載多啲歷史");
        return;
    }
    int n = MathMin(BarCount, total - 2);
    if (n < 30) return;

    // 由舊到新砌；i = 0 係仲未收盤嘅當前支（系統需要佢做即時預警）
    string body = StringFormat(
        "{\"symbol\":\"%s\",\"interval\":%d,\"source\":\"mt4\",\"bars\":[",
        g_sym, BarMinutes);

    for (int i = n; i >= 0; i--) {
        if (i != n) body = body + ",";
        body = body + StringFormat(
            "{\"t\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s}",
            (int)iTime(g_sym, g_tf, i) - g_tzOffset,
            D2S(iOpen(g_sym,  g_tf, i)),
            D2S(iHigh(g_sym,  g_tf, i)),
            D2S(iLow(g_sym,   g_tf, i)),
            D2S(iClose(g_sym, g_tf, i)));
    }
    body = body + "]}";

    if (Post("/api/gold/bars", body)) {
        g_okBars++;
        g_lastMsg = IntegerToString(n + 1) + " 支 K 線已推";
        Print("✅ 已推 ", n + 1, " 支 ", g_sym, " M", BarMinutes, " K 線");
    }
    ShowStatus();
}

//+------------------------------------------------------------------+
void ShowStatus() {
    Comment("=== TyLove 報價推送 ===",
            "\n品種：", g_sym, "　週期：M", BarMinutes,
            "\n目標：", API_BASE,
            "\n\n✅ 報價成功：", g_okTicks, " 次",
            "\n✅ K線成功：", g_okBars, " 次",
            "\n❌ 失敗：", g_fail, " 次",
            "\n\n狀態：", g_lastMsg);
}

//+------------------------------------------------------------------+
int OnInit() {
    g_sym    = (StringLen(SymbolName) > 0 ? SymbolName : Symbol());
    g_tf     = MapTF(BarMinutes);
    g_digits = (int)MarketInfo(g_sym, MODE_DIGITS);
    if (g_digits <= 0) g_digits = 2;

    // MT4 嘅時間戳係「券商 server 時間」。要換成真正 UTC 先唔會搞亂
    // 系統嘅活躍時段判斷同「數據距今幾分鐘」。取 15 分鐘倍數消除秒級誤差。
    g_tzOffset = (int)(MathRound((TimeCurrent() - TimeGMT()) / 900.0) * 900);
    Print("券商 server 同 UTC 相差 ", g_tzOffset / 3600.0, " 小時");

    Print("====================================================");
    Print("  TyLove 報價推送已啟動");
    Print("  品種：", g_sym, "　週期：M", BarMinutes, "　每 ",
          TickSeconds, " 秒推一次");
    Print("  目標：", API_BASE);
    Print("====================================================");

    if (!IsConnected()) Print("⚠️ 未連線到券商伺服器");
    if (StringLen(API_BASE) < 12 || StringFind(API_BASE, "你的-app") >= 0)
        Print("⚠️ 你未改 API_BASE！請改成你 Railway app 嘅網址");

    SendBars();
    g_lastBarPush = TimeCurrent();
    EventSetTimer(TickSeconds);
    ShowStatus();
    return (INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnTimer() {
    SendTick();
    if (TimeCurrent() - g_lastBarPush >= PushBarsEveryMin * 60) {
        g_lastBarPush = TimeCurrent();
        SendBars();
    }
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
    EventKillTimer();
    Comment("");
    Print("TyLove 報價推送已停止（原因 ", reason, "）。總共成功推咗 ",
          g_okTicks, " 次報價、", g_okBars, " 次 K 線。");
}
//+------------------------------------------------------------------+
