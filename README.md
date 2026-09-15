# TyLove Trading v2 — 港股短炒分析系統

> **定位**：幫你做最難嗰部分 —— 新聞同指標分析，同埋話你知幾時止蝕止賺。
> **唔會**幫你落單。所有買入賣出由你本人親手執行。

---

## 一、呢個版本同上一版最大分別

| 項目 | 舊版 | 新版 |
|---|---|---|
| 出場建議 | ❌ 完全冇 | ✅ ATR 止蝕 / 兩段止賺 / 移動止蝕 |
| 注碼計算 | ❌ 冇 | ✅ 按本金同風險 % 自動算股數 |
| 止蝕依據 | — | ATR(14) 市場波動，唔係隨手劃 |
| 日內數據 | ❌ 只有日線 | ✅ 日線 + 60 分鐘 + 15 分鐘 |
| 成交量評分 | ❌ 用漲跌幅假裝 | ✅ 真實量比 + 成交額 |
| 重複計分 | ❌ 同一個數字計兩次 | ✅ 五個維度輸入互不重疊 |
| 相對強度 | ❌ 冇 | ✅ 跑贏恒指 20 日 |
| 新聞 | 加分 30 分（噪音主導） | 只做否決 / 扣分，正面最多 +5 |
| 回測 | ❌ 冇 | ✅ `backtest.py` 驗證期望值 |
| 持倉追蹤 | 手動記帳，唔連現價 | 自動對照現價，主動提你走 |
| 風控 | ❌ 冇 | 每日停損、持倉上限、單倉上限 |
| Thread 安全 | 無鎖 | 加鎖，唔會重複掃描 |
| 依賴管理 | 每次開機 pip upgrade | 版本鎖定 |

---

## 二、快速開始（本地）

```bash
cd backend
py -m pip install -r requirements.txt
cp .env.example .env      # 填入 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
py main.py            # 開 http://localhost:8080
```

先跑一次回測，確認參數喺歷史數據上係正期望：

```bash
py backtest.py --years 3 --sweep
```

---

## 三、部署（GitHub + Railway，手機都用得）

### 1. 推上 GitHub

```bash
git add .
git commit -m "v2: 加入出場決策、倉位風控、回測"
git push
```

⚠️ **先確認 `.env` 已經被 `.gitignore` 擋住**，唔好將 API key 推上去。

### 2. Railway 部署

1. railway.app → New Project → Deploy from GitHub repo
2. 設定 **Root Directory** 為 `backend`
3. Variables 加入：
   - `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`
   - `OPENAI_API_KEY`
   - `ACCOUNT_SIZE_HKD`（你嘅本金）
   - `DASH_USER`、`DASH_PASS`（公開網址強烈建議設）
4. **加 Volume**（重要）：Settings → Volumes → 掛載去 `/data`，
   然後加環境變數 `DB_PATH=/data/tylove.db`
   ➜ 冇做呢步，每次重新部署你嘅持倉紀錄會清零。

### 3. 手機使用

- 部署完 Railway 會俾你一個網址，喺電話瀏覽器打開就係完整儀表板。
- 想加到主畫面：Safari / Chrome → 分享 → 加入主畫面，就同 App 一樣。
- Telegram 會自動收通知，唔使開網頁。

### 4. 保持清醒（可選）

Railway 免費額度會休眠。`.github/workflows/keepalive.yml` 已經幫你寫好，
去 GitHub repo → Settings → Secrets → Actions 加 `RAILWAY_URL` 就得。

> 想完全免費又唔想休眠，可以考慮 Fly.io（有免費 machine，支援 volume）。
> 但要自己搞 Dockerfile，複雜啲。以你嘅需求，Railway 係最省事嘅選擇。

---

## 四、Telegram 通知會有咩

| 時間 | 內容 |
|---|---|
| 09:25 / 10:30 / 11:30 / 14:00 | 掃描結果：達標股票 + 止蝕止賺 + 建議注碼 |
| 每 15 分鐘 | 持倉監控 —— **只有觸及止蝕或目標先會推**，唔會嘈你 |
| 15:50 | 收市前提醒：每個持倉實際盈虧 + 建議 + 隔夜風險提示 |

---

## 五、計分邏輯（100 分）

```
趨勢結構   25   價在 MA20 上 / MA5>MA20 / MA20 斜率
動能       20   RSI 位置 + MACD 柱狀方向
成交量     20   真實量比 + 成交額 vs 20 日均
波動質素   15   ATR% 甜區（2–4.5%）
相對強度   20   20 日跑贏恒指幾多
────────────────
新聞       另計  重大負面直接否決；正面最多 +5
```

**硬性過濾**（唔理幾高分都唔入）：
- 20 日平均成交額 < 5000 萬
- ATR% < 1.2%（太死，冇波幅食）
- 風險回報比 < 1.8

---

## 六、風險控制（呢個係舊版完全冇）

- **每筆最多輸本金 1%**（`RISK_PER_TRADE_PCT`）
- **單一倉位最多佔本金 30%**
- **同時最多 3 個倉**
- **當日虧損 3% 就停手**，唔准再開新倉
- **賺到 1 倍 ATR 就將止蝕推上成本價**，之後跟移動止蝕
- **最長持 10 個交易日**，唔動就換馬

---

## 七、要記住嘅事

1. **一定要先跑回測。** 如果期望值係負數，唔好落真錢。
2. 回測未計交易成本，港股來回約 0.25–0.4%，實際要扣。
3. 短炒唔建議持倉過夜，港股隔夜跳空風險高。
4. 呢個工具係輔助決策，唔係保證賺錢。控制注碼比起搵到神級指標重要一萬倍。

---

## 八、檔案結構

```
backend/
  config.py           所有參數集中喺度，改一個地方就得
  indicators.py       技術指標（RSI/ATR/MACD/ADX/布林）
  data_fetcher.py     數據層（日線 / 60m / 15m / 即時價）
  scoring.py          100 分評分系統
  news_sentiment.py   新聞否決機制
  trade_plan.py       ★ 入場計劃、注碼、出場決策
  positions.py        持倉管理（SQLite）
  scanner.py          ★ 掃描引擎 + 持倉監控
  notifier.py         Telegram 通知
  main.py             排程 + 網頁儀表板
  backtest.py         ★ 回測驗證
  templates/index.html  手機儀表板
```

---

## ⚠️ 疑難排解：Railway 崩潰 `ModuleNotFoundError: No module named 'dashboard'`

**原因**：Railway 服務裡面嘅 **Start Command 仍然係舊版 v1 嘅指令**：

```
gunicorn dashboard:app
```

v1 嘅 `dashboard.py` 係放 Flask app 嘅檔案，v2 已經合併入 `main.py`，
所以嗰句舊指令會搵唔到 `dashboard` 而崩潰。

**處理方法（二選一）**

方法 A（推薦，一勞永逸）— 改返做正確指令：
1. Railway → 你嘅服務 → **Settings** → **Deploy**
2. 搵 **Custom Start Command**，改成：
   ```
   python main.py
   ```
   （或者清空佢，就會用返 `Procfile` / `railway.json` 嘅設定）
3. 順手確認 **Settings → Source → Root Directory** 係 `backend`
4. 按 **Deploy** 重新部署

方法 B（懶人法）— 直接唔理佢：
v2 已經加咗 `dashboard.py` 相容層，舊指令都會行得通、唔會再崩潰。
但長遠建議都係用方法 A，比較乾淨。

**附帶修正**：v2 亦加咗 `gunicorn.conf.py`，將 worker 鎖死做 1 個。
因為程式喺背景跑緊定時排程，如果開多過一個 worker，
每個 worker 都會各自掃描一次，你會收到重複幾次嘅 Telegram 通知。



## ⚠️ 一定要做：Railway Volume（否則資料會清空）

預設情況下 Railway 每次重新部署都會清空容器，SQLite 資料庫入面嘅
**持倉紀錄、交易成績、你喺網頁改嘅風控參數**會全部消失。

1. Railway → 你嘅服務 → **Settings** → **Volumes** → **Add Volume**
2. Mount path 填 `/data`
3. 去 **Variables** 加一個：`DB_PATH` = `/data/tylove.db`
4. 按 **Redeploy**

之後每次更新程式，持倉同設定都會保留。
開機時如果偵測到資料庫唔喺持久化位置，Log 會出警告提醒你。
