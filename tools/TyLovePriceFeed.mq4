//+------------------------------------------------------------------+
//|  TyLovePriceFeed.mq4   v1.1                                      |
//|  將你券商嘅真實報價推送去 TyLove 交易系統                          |
//+------------------------------------------------------------------+
#property copyright "TyLove"
#property version   "1.10"
#property strict

//================= 你只需要改呢兩行 =================
extern string API_BASE         = "https://tylovetrading-production.up.railway.app";
extern string FEED_TOKEN       = "TyLove_Gold";
//===================================================

extern int    TickSeconds      = 5;    // 每幾秒推一次即時報價
extern int    BarMinutes       = 15;   // K 線週期（分鐘）
extern int    BarCountFull     = 300;  // 開頭推幾多支歷史 K 線
extern int    BarCountQuick    = 6;    // 之後每次補推幾多支
extern int    PushBarsEveryMin = 5;    // 每幾分鐘補推一次 K 線
extern string SymbolName       = "";   // 留空 = 用圖表本身嘅品種

string   g_sym;
int      g_tf, g_digits, g_tzOffset = 0;
datetime g_lastBarPush    = 0;   // 用 TimeLocal() 計
datetime g_lastServerTime = 0;   // 上次見到嘅券商時間
datetime g_lastQuoteAt    = 0;   // 上次「券商時間有前進」嘅電腦時間
int      g_quoteLag       = 0;   // 券商報價落後幾秒
int      g_okTicks = 0, g_okBars = 0, g_fail = 0, g_failStreak = 0;

int MapTF(int m) {
    if (m == 1)    return PERIOD_M1;
    if (m == 5)    return PERIOD_M5;
    if (m == 15)   return PERIOD_M15;
    if (m == 30)   return PERIOD_M30;
    if (m == 60)   return PERIOD_H1;
    if (m == 240)  return PERIOD_H4;
    if (m == 1440) return PERIOD_D1;
    return PERIOD_M15;
}

string D2S(double v) { return DoubleToString(v, g_digits); }
bool Post(string path, string body) {
    string url = API_BASE + path;
    string headers = "Content-Type: application/json\r\n";
    if (StringLen(FEED_TOKEN) > 0) headers += "X-Feed-Token: " + FEED_TOKEN + "\r\n";
    char post[], result[];
    string rh = "";
    int len = StringToCharArray(body, post, 0, WHOLE_ARRAY, CP_UTF8) - 1;
    if (len < 0) len = 0;
    ArrayResize(post, len);
    ResetLastError();
    int code = WebRequest("POST", url, headers, 5000, post, result, rh);
    if (code == 200) { g_failStreak = 0; return true; }
    g_fail++; g_failStreak++;
    Print("❌ 推送失敗 ", path, "　HTTP=", code, "　錯誤碼=", GetLastError());
    if (code == -1 && GetLastError() == 4060)
        Print("   → 去「工具→選項→智能交易系統→允許 WebRequest 網址」加入：", API_BASE);
    return false;
}

void SendTick() {
    double bid = MarketInfo(g_sym, MODE_BID);
    double ask = MarketInfo(g_sym, MODE_ASK);
    if (bid <= 0 || ask <= 0) return;
    string body = StringFormat(
        "{\"symbol\":\"%s\",\"bid\":%s,\"ask\":%s,\"source\":\"mt4\",\"t\":%d,\"lag\":%d}",
        g_sym, D2S(bid), D2S(ask), (int)TimeCurrent() - g_tzOffset, g_quoteLag);
    if (Post("/api/gold/tick", body)) g_okTicks++;
}

void SendBars(int n) {
    int total = iBars(g_sym, g_tf);
    if (total < 20) { Print("⚠️ 歷史 K 線不足（", total, "）"); return; }
    if (n > total - 1) n = total - 1;
    string body = StringFormat(
        "{\"symbol\":\"%s\",\"interval\":%d,\"source\":\"mt4\",\"bars\":[", g_sym, BarMinutes);
    for (int i = n; i >= 0; i--) {
        if (i != n) body += ",";
        body += StringFormat("{\"t\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s}",
            (int)iTime(g_sym, g_tf, i) - g_tzOffset,
            D2S(iOpen(g_sym,g_tf,i)), D2S(iHigh(g_sym,g_tf,i)),
            D2S(iLow(g_sym,g_tf,i)),  D2S(iClose(g_sym,g_tf,i)));
    }
    body += "]}";
    if (Post("/api/gold/bars", body)) g_okBars++;
}
void ShowStatus() {
    string conn = (g_quoteLag > 180)
                  ? "⚠️ 券商報價停滯 " + IntegerToString(g_quoteLag) + " 秒"
                  : "✅ 券商報價正常";
    Comment("TyLove 報價推送 v1.1\n",
            "──────────────────\n",
            g_sym, "　M", BarMinutes, "\n",
            "報價成功 ", g_okTicks, " 次\n",
            "K線成功 ", g_okBars, " 次　失敗 ", g_fail, " 次\n",
            "最後 K 線：", TimeToString(g_lastBarPush, TIME_SECONDS), "\n",
            conn);
}

int OnInit() {
    g_sym = (StringLen(SymbolName) > 0 ? SymbolName : Symbol());
    g_tf  = MapTF(BarMinutes);
    g_digits = (int)MarketInfo(g_sym, MODE_DIGITS);
    if (g_digits <= 0) g_digits = 2;

    // MT4 時間戳係「券商 server 時間」，換成真 UTC 先唔會搞亂時段判斷
    g_tzOffset = (int)(MathRound((TimeCurrent() - TimeGMT()) / 900.0) * 900);

    Print("=================== TyLove 報價推送 v1.1 ===================");
    Print("品種 ", g_sym, "　週期 M", BarMinutes, "　每 ", TickSeconds, " 秒推一次");
    Print("券商 server 同 UTC 相差 ", g_tzOffset / 3600.0, " 小時");
    Print("目標 ", API_BASE);
    Print("===========================================================");

    SendBars(BarCountFull);
    g_lastBarPush    = TimeLocal();
    g_lastQuoteAt    = TimeLocal();
    g_lastServerTime = TimeCurrent();
    EventSetTimer(TickSeconds);
    ShowStatus();
    return (INIT_SUCCEEDED);
}

void OnTimer() {
    // 監測券商報價有冇停：TimeCurrent() 只會喺收到新報價時前進
    if (TimeCurrent() != g_lastServerTime) {
        g_lastServerTime = TimeCurrent();
        g_lastQuoteAt = TimeLocal();
    }
    g_quoteLag = (int)(TimeLocal() - g_lastQuoteAt);

    SendTick();

    // ⚠️ 一定要用 TimeLocal()（電腦時鐘）計時。
    //    v1.0 用咗 TimeCurrent() —— 佢係「最後報價時間」，
    //    市靜嗰陣會凍結，令 K 線永遠唔重推 →
    //    網站收唔到新 K 線變紅，但 EA 照顯示成功。呢個就係 v1.1 修嘅 bug。
    if (TimeLocal() - g_lastBarPush >= PushBarsEveryMin * 60) {
        g_lastBarPush = TimeLocal();
        SendBars(BarCountQuick);
    }
    ShowStatus();
}

void OnDeinit(const int reason) {
    EventKillTimer();
    Comment("");
}
