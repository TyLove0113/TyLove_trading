# TyLove OCR 修復指示（tesseract 執行檔唔見）

> 2026-10-15 新增。當網頁「診斷資料」顯示 **tesseract 執行檔 ❌** 時，跟呢份做。

---

## 一、發生咩事

```
Pillow            ✅     ← Python 影像庫，正常
pytesseract       ✅     ← Python 套件，正常
tesseract 執行檔   ❌     ← 真正嘅 OCR 程式，唔見咗
```

**`pytesseract` 只係一個「叫 tesseract 做嘢」嘅中間人。**
真正做 OCR 嘅係 `tesseract` 執行檔 —— 佢係一個**系統程式**，唔係 pip 套件，
所以要靠部署平台（Railway）喺建置時安裝。

**佢唔見咗 → 整個 OCR 停擺 → 你上載 MT4 截圖讀唔到數。**

---

## 二、系統已經識自己修（V5.6 起）

程式**每次啟動**都會自動檢查：

1. 搵唔搵到 `tesseract`（PATH + 幾個常見路徑）
2. 搵唔到 → 試加 PATH
3. 仲搵唔到 → 試即時安裝（`apt-get install tesseract-ocr`）
4. 都失敗 → 喺網頁診斷區顯示**明確指示**（唔會再只係一個 ❌）

**所以你只要重新部署一次，程式啟動時就會自己試修。**

---

## 三、如果自動修唔到（逐步做）

### 步驟 1：確認 Root Directory 設定

1. 去 Railway → 你嘅服務 → **Settings**
2. 搵 **Root Directory**
3. 應該係 **`backend`**
4. 如果係空／`/` → **改成 `backend`**（因為 `nixpacks.toml` 喺 `backend/` 入面）

> 呢個係最常見嘅原因：Root Directory 錯 → Railway 搵唔到 `nixpacks.toml`
> → 唔會裝 tesseract。

### 步驟 2：確認 GitHub 上有 `nixpacks.toml`

去 GitHub 你嘅 repo，確認呢個檔案存在：

```
backend/nixpacks.toml
```

**內容應該包含：**

```toml
[phases.setup]
aptPkgs = [
    "tesseract-ocr",
    "tesseract-ocr-eng",
]
```

**唔見咗就由 zip 重新上載返。**

### 步驟 3：清建置快取 + 重新部署

1. Railway → 你嘅服務 → **Deployments**
2. 揀最新嘅 deployment → 右邊 **⋯** → **Redeploy**
3. 如果仲係唔得：**Settings → 清 Build Cache**，再 Redeploy

> Railway 嘅建置快取有時會重用舊圖層，令新加嘅系統套件裝唔到。
> 清快取通常一次搞掂。

### 步驟 4：睇啟動日誌

Railway → **Deployments → 最新 → Logs**，搵：

```
tesseract 就緒：/usr/bin/tesseract
```

或者

```
tesseract 唔可用：裝唔到
```

**睇到第二句 → 直接將日誌截圖俾我。**

---

## 四、快速自我檢查

網頁 → 交易日誌 → 「上載 MT4 截圖」右邊應該顯示：

```
✅ OCR 已啟用（tesseract）
```

**如果顯示 ❌，就係呢份文件講嘅問題。**

---

## 五、要記住嘅重點

| | |
|---|---|
| **tesseract 唔關你事** | 佢係部署環境嘅系統程式，唔係你操作錯 |
| **唔需要重裝手機 App** | 呢個問題同你手機完全無關 |
| **每次部署都會再檢查** | 修好之後，程式會自己確認，唔會靜靜雞壞 |
| **OCR 壞嘅時候，其他功能照用** | 訊號、持倉、日記、統計全部正常，只係讀唔到截圖 |
