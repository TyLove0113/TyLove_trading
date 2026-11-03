//+------------------------------------------------------------------+
//|  TyLovePriceFeed.mq4   v2.1       2026-09-29                     |
//|  將你券商嘅真實報價推送去 TyLove 交易系統                          |
//+------------------------------------------------------------------+
//  版本記錄（想確認有冇更新，睇圖表左上角會唔會寫 v2.1）
//
//  v2.2  2026-11-02  加多時框（PushFrames）；用自訂 SplitString 取代 MQL4 冇嘅 StringSplit
//  v2.1  2026-09-29  ⭐ 修好「EA 話成功、但網站收唔到」嘅元兇
//                     - API_BASE 少咗 https:// 會令 WebRequest 一定失敗。
//                       新版會自動補返，填漏都用得。
//                     - 錯誤訊息具體化（4060=未允許 / 4062=主機搵唔到 /
//                       401,403=token 唔對 / 4xx=伺服器問題）
//
//  v2.0  2026-09-24  ⭐ 修好「EA 顯示成功、但網站變紅」嘅 bug
//                     - 計時改用 TimeLocal()（電腦時鐘）
//                       舊版用 TimeCurrent()，佢係「最後一個報價嘅時間」，
//                       市靜嗰陣會凍結 → K 線永遠唔重推 → 網站變紅
//                     - K 線改為每 5 分鐘推最近 6 支（舊版每 15 分鐘推 300 支）
//                     - 新增「券商報價落後幾秒」診斷顯示
//
//  v1.1  2026-09-23  加時區偏移自動偵測（MT4 時間戳係券商 server 時間）
//  v1.0  2026-09-23  第一版
//+------------------------------------------------------------------+
#property copyright "TyLove"
#property version   "2.31"
#property strict

// 2026-11-02：半自動落單用嘅確認窗（要喺 MT4 開「允許 DLL 匯入」）
#import "user32.dll"
int MessageBoxW(int hWnd, string text, string caption, int type);
#import

#define MB_YESNO        4
#define MB_ICONQUESTION 32
#define IDYES           6

#define EA_VERSION "v2.6"

//================= 你只需要改呢兩行 =================
extern string API_BASE         = "https://tylovetrading-production.up.railway.app";
extern string FEED_TOKEN       = "TyLove_Gold";
//===================================================

extern int    TickSeconds      = 5;    // 每幾秒推一次即時報價
// ── 半自動落單（2026-11-02）──
extern bool   AllowAutoTrade   = true;   // 開關：允許 EA 幫你落單（只限 demo）
extern bool   ConfirmEach      = false;  // 2026-11-03 預設 false：改由 Telegram 撳「是」批准
extern double MaxAutoLot       = 0.01;   // 自動落單手數上限（安全網）
extern int    AutoEveryMin     = 1;      // 每幾分鐘查一次有冇新訊號
extern int    AutoMaxAgeMin    = 30;     // 訊號超過幾多分鐘就唔再落（2026-11-03：10→30，同後端一致）
extern int    BarMinutes       = 15;   // K 線週期（分鐘）
extern string PushFrames       = "M1,M15,H1,H4"; // 多時框（2026-11-03 改預設）；留空 = 只用 BarMinutes
extern int    M1Bars           = 2000; // M1 開頭推幾多支（M1 變化快，要多啲）
extern int    BarCountFull     = 2000; // 開頭推幾多支歷史 K 線（2026-11-02：300 太少，改 2000）
extern int    BarCountQuick    = 6;    // 之後每次補推幾多支
extern int    PushBarsEveryMin = 5;    // 每幾分鐘補推一次 K 線
extern string SymbolName       = "";   // 留空 = 用圖表本身嘅品種
extern int    DealsOnStart      = 50;  // 開頭推幾多筆歷史成交
extern int    DealsEveryMin     = 5;   // 每幾分鐘檢查有無新成交

string   g_sym;
string   g_base;                 // 正規化後嘅 API 網址（一定有 https://）
int      g_tf, g_digits, g_tzOffset = 0;
datetime g_lastBarPush    = 0;   // 用 TimeLocal() 計
datetime g_lastDealPush   = 0;   // 上次檢查成交記錄
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

// 2026-09-29：MQL4 嘅 WebRequest 一定要有完整協定（https://）。
// 只填域名（例如 xxx.up.railway.app）會令請求失敗，而且錯誤訊息唔清楚，
// 容易誤以為「有推送成功」。呢個函數自動補返，填漏都用得。
string NormalizeBase(string s) {
    StringTrimLeft(s);
    StringTrimRight(s);
    while (StringLen(s) > 0 && StringGetChar(s, StringLen(s) - 1) == '/')
        s = StringSubstr(s, 0, StringLen(s) - 1);
    if (StringLen(s) == 0) return ("");
    string low = s;
    StringToLower(low);
    if (StringFind(low, "http://") == 0 || StringFind(low, "https://") == 0)
        return (s);
    return ("https://" + s);
}
bool Post(string path, string body) {
    string url = g_base + path;
    string headers = "Content-Type: application/json\r\n";
    if (StringLen(FEED_TOKEN) > 0) headers += "X-Feed-Token: " + FEED_TOKEN + "\r\n";
    char post[], result[];
    string rh = "";
    int len = StringToCharArray(body, post, 0, WHOLE_ARRAY, CP_UTF8) - 1;
    if (len < 0) len = 0;
    ArrayResize(post, len);
    ResetLastError();
    int code = WebRequest("POST", url, headers, 5000, post, result, rh);
    if (code == 200) { g_failStreak = 0; return (true); }
    g_fail++;
    g_failStreak++;
    Print("❌ 推送失敗 ", path, "　HTTP=", code, "　錯誤碼=", GetLastError());
    if (code == -1) {
        int e = GetLastError();
        if (e == 4060)
            Print("   → WebRequest 未獲允許。工具→選項→智能交易系統→"
                  "允許 WebRequest 網址，加入：", g_base);
        else if (e == 4062)
            Print("   → 搵唔到主機。檢查 API_BASE 有冇打錯：", g_base);
        else
            Print("   → 錯誤碼 ", e, "　目標：", g_base);
    } else if (code == 401 || code == 403) {
        Print("   → 認證失敗。EA 嘅 FEED_TOKEN 要同 Railway 嘅 FEED_TOKEN 一樣"
              "（現時填嘅係：", FEED_TOKEN, "）");
    } else if (code >= 400) {
        Print("   → 伺服器回應 ", code, "。可能係服務未起好，或者網址路徑錯");
    }
    return (false);
}

// ---------------------------------------------------------------- 成交記錄
// 2026-09-30 新增。想自動記錄交易日誌，唔想手動打字或者 OCR 認圖。
//
// 關鍵：MT4 嘅成交記錄係存喺【券商伺服器】，桌面版會下載返嚟。
// 所以你用手機 app 落嘅單，桌面版呢個 EA 一樣讀得到 ——
// 前提只係桌面版開住（你推報價本身已經要開）。
//
// 系統收到之後唔會直接入帳，會開一張「草稿」，
// 你要喺網頁核對完撳「確認」先成為正式記錄。
void SendDeals(int n) {
    int total = OrdersHistoryTotal();
    if (total <= 0) return;
    string arr = "";
    int cnt = 0;
    for (int i = total - 1; i >= 0 && cnt < n; i--) {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
        if (OrderSymbol() != g_sym) continue;
        int t = OrderType();
        if (t != OP_BUY && t != OP_SELL) continue;
        if (OrderCloseTime() <= 0) continue;      // 未平倉，跳過

        string cm = OrderComment();
        StringReplace(cm, "\"", "'");             // 避免整爛 JSON

        if (cnt > 0) arr += ",";
        arr += "{\"ticket\":" + IntegerToString(OrderTicket())
             + ",\"direction\":\"" + (t == OP_BUY ? "buy" : "sell") + "\""
             + ",\"lots\":" + DoubleToString(OrderLots(), 2)
             + ",\"open_ts\":" + IntegerToString((int)OrderOpenTime() - g_tzOffset)
             + ",\"open_price\":" + DoubleToString(OrderOpenPrice(), g_digits)
             + ",\"close_ts\":" + IntegerToString((int)OrderCloseTime() - g_tzOffset)
             + ",\"close_price\":" + DoubleToString(OrderClosePrice(), g_digits)
             + ",\"sl\":" + DoubleToString(OrderStopLoss(), g_digits)
             + ",\"tp\":" + DoubleToString(OrderTakeProfit(), g_digits)
             + ",\"profit\":" + DoubleToString(OrderProfit() + OrderSwap()
                                              + OrderCommission(), 2)
             + ",\"comment\":\"" + cm + "\"}";
        cnt++;
    }
    if (cnt == 0) return;
    string body = "{\"symbol\":\"" + g_sym + "\",\"source\":\"mt4\",\"deals\":["
                  + arr + "]}";
    if (Post("/api/gold/deals", body))
        Print("✅ 已推 ", cnt, " 筆成交記錄（系統會開草稿等你確認）");
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

int TFMinutes(int tf) {
    if (tf == PERIOD_M1)  return 1;
    if (tf == PERIOD_M5)  return 5;
    if (tf == PERIOD_M15) return 15;
    if (tf == PERIOD_M30) return 30;
    if (tf == PERIOD_H1)  return 60;
    if (tf == PERIOD_H4)  return 240;
    if (tf == PERIOD_D1)  return 1440;
    return 15;
}

// 2026-11-01：支援一次推多個時框 —— 用戶要求 M15 方向之外，
// 同時收 M1/M5/H1/H4 做 cross-check。後端 feed_bars 主鍵已含
// (symbol, interval, ts)，所以多時框並存冇問題。
void SendBarsTF(int tf, int n) {
    int mins  = TFMinutes(tf);
    int total = iBars(g_sym, tf);
    if (total < 20) { Print("⚠️ 歷史 K 線不足（", total, "）"); return; }
    if (n > total - 1) n = total - 1;
    string body = StringFormat(
        "{\"symbol\":\"%s\",\"interval\":%d,\"source\":\"mt4\",\"bars\":[", g_sym, mins);
    for (int i = n; i >= 0; i--) {
        if (i != n) body += ",";
        body += StringFormat("{\"t\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s}",
            (int)iTime(g_sym, tf, i) - g_tzOffset,
            D2S(iOpen(g_sym,tf,i)), D2S(iHigh(g_sym,tf,i)),
            D2S(iLow(g_sym,tf,i)),  D2S(iClose(g_sym,tf,i)));
    }
    body += "]}";
    if (Post("/api/gold/bars", body)) g_okBars++;
}

// 逐個時框推送；PushFrames 留空就只推 BarMinutes（舊行為）。
// MQL4 冇 StringSplit（MQL5 才有）—— 2026-11-02 加。
// 原本直接叫 StringSplit 會編譯失敗，令 .ex4 生成唔到，
// Visual Studio 見到檔案但撳唔到「加入圖表」。
int SplitString(string s, string sep, string &out[]) {
    int n = 0, start = 0;
    int len = StringLen(s), slen = StringLen(sep);
    for (int i = 0; i <= len; i++) {
        bool atEnd  = (i == len);
        bool isSep  = (!atEnd && StringSubstr(s, i, slen) == sep);
        if (atEnd || isSep) {
            if (i > start) {
                ArrayResize(out, n + 1);
                out[n] = StringSubstr(s, start, i - start);
                n++;
            }
            start = i + slen;
        }
    }
    return n;
}

void PushAllFrames(int n) {
    string spec = PushFrames;
    if (StringLen(spec) == 0) { SendBarsTF(g_tf, n); return; }
    string parts[];
    int k = SplitString(spec, ",", parts);
    for (int i = 0; i < k; i++) {
        string t = parts[i];
        StringTrimLeft(t); StringTrimRight(t);
        StringToUpper(t);
        int tf = 0, cnt = n;
        if (t == "M1")       { tf = PERIOD_M1;  cnt = M1Bars; }
        else if (t == "M5")  tf = PERIOD_M5;
        else if (t == "M15") tf = PERIOD_M15;
        else if (t == "M30") tf = PERIOD_M30;
        else if (t == "H1")  tf = PERIOD_H1;
        else if (t == "H4")  tf = PERIOD_H4;
        else if (t == "D1")  tf = PERIOD_D1;
        if (tf == 0) continue;
        SendBarsTF(tf, cnt);
    }
}

// ==================== 半自動落單（2026-11-02）====================
// 設計：EA 只查詢系統有冇新訊號 → 彈窗問你 → 你撳「是」才落單。
// 三重安全：① 只限 demo 帳戶 ② 手數上限 MaxAutoLot ③ AllowAutoTrade 開關
int g_lastDoneId = 0;
datetime g_lastAutoCheck = 0;

bool Get(string path, string &out) {
    string url = g_base + path;
    string headers = "X-Feed-Token: " + FEED_TOKEN + "\r\n";
    char post[], result[];
    string rh;
    ResetLastError();
    int code = WebRequest("GET", url, headers, 5000, post, result, rh);
    if (code == -1) {
        Print("GET 失敗 ", path, " 錯誤碼=", GetLastError());
        return false;
    }
    if (code != 200) { Print("GET ", path, " HTTP=", code); return false; }
    out = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
    return true;
}

// 由 JSON 抓 "key": 後面嘅值（數字或字串）
string JVal(string js, string key) {
    string pat = "\"" + key + "\":";
    int i = StringFind(js, pat);
    if (i < 0) return "";
    i += StringLen(pat);
    while (i < StringLen(js) && StringGetChar(js, i) == ' ') i++;
    int a = i;
    while (i < StringLen(js)) {
        int ch = StringGetChar(js, i);
        if (ch == ',' || ch == '}' || ch == ']') break;
        i++;
    }
    string v = StringSubstr(js, a, i - a);
    StringTrimLeft(v); StringTrimRight(v);
    if (StringLen(v) > 0 && StringGetChar(v, 0) == '"')
        v = StringSubstr(v, 1, StringLen(v) - 2);
    return v;
}

void DoTrade(int sid, string dir, double entry, double stop, double target, double lot) {
    if (!IsDemo()) {
        Print("⚠️ 唔係 demo 帳戶 —— 為安全起見拒絕自動落單");
        return;
    }
    double use = MathMin(lot, MaxAutoLot);
    if (use < MarketInfo(g_sym, MODE_MINLOT)) use = MarketInfo(g_sym, MODE_MINLOT);
    if (use > MarketInfo(g_sym, MODE_MAXLOT)) use = MarketInfo(g_sym, MODE_MAXLOT);

    int type = (dir == "long") ? OP_BUY : OP_SELL;
    double px = (type == OP_BUY) ? Ask : Bid;
    int t = OrderSend(g_sym, type, use, px, 30, stop, target,
                      "TyLove#" + IntegerToString(sid), sid, 0,
                      (type == OP_BUY) ? clrDodgerBlue : clrOrangeRed);
    if (t < 0) {
        Print("❌ 落單失敗 錯誤碼=", GetLastError(), " 手數=", DoubleToString(use, 2));
        Alert("TyLove 落單失敗，錯誤碼 ", GetLastError());
        return;
    }
    g_lastDoneId = sid;
    Print("✅ 已落單 #", t, " 訊號#", sid, " ", dir, " ",
          DoubleToString(use, 2), " 手 @", DoubleToString(px, g_digits),
          " SL=", DoubleToString(stop, g_digits),
          " TP=", DoubleToString(target, g_digits));
    Alert("TyLove 已落單：", dir, " ", DoubleToString(use, 2), " 手");
    // 2026-11-02：通知後端「已執行」，令同一張訊號唔會再落第二次
    Post("/api/gold/signal/executed",
         StringFormat("{\"id\":%d,\"ticket\":%d,\"price\":%s}",
                      sid, t, DoubleToString(px, g_digits)));
}

void CheckPending() {
    if (!AllowAutoTrade) return;
    if (TimeLocal() - g_lastAutoCheck < AutoEveryMin * 60) return;
    g_lastAutoCheck = TimeLocal();

    // 2026-11-02：改為查「Telegram 已批准」嘅訊號。
    // 你喺手機撳「✅ 是」→ 後端標記 approved → EA 一分鐘內落單。
    // 唔撳 = 永遠唔會落單（呢個係設計核心，唔係漏洞）。
    string js;
    if (!Get("/api/gold/signal/approved?max_age=" + IntegerToString(AutoMaxAgeMin), js)) {
        Print("❌ 查詢已批准訊號失敗（WebRequest 唔通）");
        return;
    }

    int p = StringFind(js, "[{");
    // 2026-11-03：每秒都印會洗版，所以每 20 次（約 20 分鐘）報一次狀態
    static int quietCnt = 0;
    if (p < 0) {
        quietCnt++;
        if (quietCnt % 20 == 1)
            Print("已批准訊號：暫時冇（EA 正常輪詢中，每 ",
                  AutoEveryMin, " 分鐘一次）");
        return;
    }
    quietCnt = 0;
    if (p < 0) return;

    // 逐個物件處理（由新到舊）
    int guard = 0;
    while (p >= 0 && guard < 20) {
        guard++;
        int e = StringFind(js, "}", p);
        if (e < 0) break;
        string obj = StringSubstr(js, p, e - p + 1);
        p = StringFind(js, "{\"id\":", e);

        int sid = (int)StringToInteger(JVal(obj, "id"));
        if (sid <= 0 || sid <= g_lastDoneId) continue;
        string dir = JVal(obj, "direction");
        double entry  = StringToDouble(JVal(obj, "entry"));
        double stop   = StringToDouble(JVal(obj, "stop"));
        double target = StringToDouble(JVal(obj, "target"));
        double lot    = StringToDouble(JVal(obj, "lot"));
        if (dir == "" || entry <= 0 || stop <= 0 || target <= 0) continue;

        string msg = StringFormat(
            "有新的黃金訊號（#%d）\n\n方向：%s\n入場：%s\n止蝕：%s\n目標：%s\n手數：%s\n\n確定落單？",
            sid, (dir == "long" ? "做多（BUY）" : "做空（SELL）"),
            DoubleToString(entry, g_digits), DoubleToString(stop, g_digits),
            DoubleToString(target, g_digits), DoubleToString(MathMin(lot, MaxAutoLot), 2));

        // 2026-11-02：批准權已搬去 Telegram（手機一撳即落單）。
        // 呢度只做安全檢查，唔再彈窗 —— 因為彈窗要你坐喺電腦前面，
        // 就失去「隨時落單」嘅意義。ConfirmEach=false 時仍可保留彈窗做後備。
        if (ConfirmEach) {
            int r = MessageBoxW(0, msg, "TyLove 訊號確認", MB_YESNO | MB_ICONQUESTION);
            // 2026-11-03 修（用戶反映「撳咗批准但成個鐘冇落單」）：
            // MessageBoxW 返回 0 代表「呼叫失敗」，最常見係未開
            // 「工具→選項→智能交易系統→允許 DLL 匯入」。
            // 舊版把 0 當成「用戶取消」→ 設定 g_lastDoneId → 訊號永久被吃掉。
            // 批准權其實喺 Telegram，彈窗只係後備，所以失敗時應該繼續落單。
            if (r == IDNO) {
                Print("用戶喺彈窗撳「否」，取消訊號 #", sid);
                g_lastDoneId = sid;
                continue;
            }
            if (r != IDYES) {
                Print("⚠️ 彈窗失敗（返回 ", r, "，錯誤碼=", GetLastError(),
                      "）—— 通常係未開「允許 DLL 匯入」。");
                Print("   批准權已在 Telegram，繼續落單 #", sid);
            }
        }
        DoTrade(sid, dir, entry, stop, target, lot);
    }
}

void ShowStatus() {
    string conn = (g_quoteLag > 180)
                  ? "⚠️ 券商報價停滯 " + IntegerToString(g_quoteLag) + " 秒"
                  : "✅ 券商報價正常";
    Comment("🟦 TyLove 報價推送  ", EA_VERSION, "\n",
            "══════════════════\n",
            g_sym, "　", (StringLen(PushFrames) > 0 ? PushFrames : "M" + IntegerToString(BarMinutes)), "\n",
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

    Print("============== TyLove 報價推送  ", EA_VERSION, " ==============");

    // ⚠️ WebRequest 一定要有 https://，冇嘅話一定失敗
    g_base = NormalizeBase(API_BASE);
    if (StringLen(g_base) == 0) {
        Print("❌ API_BASE 係空！請填入你 Railway 網址。");
        Print("   例如：https://tylovetrading-production.up.railway.app");
        return (INIT_PARAMETERS_INCORRECT);
    }
    if (g_base != API_BASE)
        Print("🔧 API_BASE 冇協定，已自動修正：", API_BASE, " → ", g_base);

    Print("品種 ", g_sym, "　週期 M", BarMinutes, "　每 ", TickSeconds, " 秒推一次");
    SendDeals(DealsOnStart);   // 開頭補推歷史成交，等系統有齊記錄
    Print("券商 server 同 UTC 相差 ", g_tzOffset / 3600.0, " 小時");
    Print("目標 ", g_base);
    Print("⚠️ 如果一直收唔到數據，去「工具 → 選項 → 智能交易系統 →"
          " 允許 WebRequest 網址」加入呢個：");
    Print("   ", g_base);
    Print("===========================================================");

    SendBarsTF(g_tf, BarCountFull);   // 2026-11-02 修：舊名 SendBars 已改名做 SendBarsTF
    g_lastBarPush    = TimeLocal();
    g_lastQuoteAt    = TimeLocal();
    g_lastServerTime = TimeCurrent();
    EventSetTimer(TickSeconds);
    ShowStatus();
    return (INIT_SUCCEEDED);
}

bool PushFramesInitDone = false;

void OnTimer() {
    // 監測券商報價有冇停：TimeCurrent() 只會喺收到新報價時前進
    if (TimeCurrent() != g_lastServerTime) {
        g_lastServerTime = TimeCurrent();
        g_lastQuoteAt = TimeLocal();
    }
    g_quoteLag = (int)(TimeLocal() - g_lastQuoteAt);

    SendTick();
    CheckPending();

    // ⚠️ 一定要用 TimeLocal()（電腦時鐘）計時。
    //    v1.0 用咗 TimeCurrent() —— 佢係「最後報價時間」，
    //    市靜嗰陣會凍結，令 K 線永遠唔重推 →
    //    網站收唔到新 K 線變紅，但 EA 照顯示成功。呢個就係 v2.0 修嘅 bug。
    if (TimeLocal() - g_lastBarPush >= PushBarsEveryMin * 60) {
        g_lastBarPush = TimeLocal();
        PushAllFrames(PushFramesInitDone ? BarCountQuick : BarCountFull);
        PushFramesInitDone = true;
    }

    // 2026-09-30：定時檢查有無新成交（用手機落單都會出現喺呢度）。
    // 系統會 dedupe（同一 ticket 唔會重複），所以重推係安全嘅。
    if (TimeLocal() - g_lastDealPush >= DealsEveryMin * 60) {
        g_lastDealPush = TimeLocal();
        SendDeals(20);
    }
    ShowStatus();
}

void OnDeinit(const int reason) {
    EventKillTimer();
    Comment("");
}
