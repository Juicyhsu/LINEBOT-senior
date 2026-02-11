# 👵 長輩版 Line Bot (Senior Companion Bot)

這是一個專為長輩設計的 Line Bot，具備大字體、語音互動、長輩圖生成、健康提醒等功能。

---

## 🤝 給接手維護/部署的人員 (For Maintainers)

如果您是協助部署此專案的人員，請依照以下步驟進行：

### 1. 閱讀部署文件
本專案包含完整的部署教學，請務必閱讀 `Documentation` 資料夾中的文件：

*   **Google Cloud 設定** (最優先)：
    *   請閱讀 **`Documentation/Google_Cloud_Deployment_Guide.md`**
    *   這份文件會教您如何建立自己的 GCP 專案、取得 API Key 和 `service-account-key.json`。
    *   **注意**：請使用您自己的 GCP 帳號，以確保費用計算在您的帳號下。

*   **AWS 部署教學**：
    *   請閱讀 **`Documentation/AWS_DEPLOYMENT_CHECKLIST.md`**
    *   這份文件適用於 AWS App Runner 或 Elastic Beanstalk 部署。
    *   包含如何設定環境變數（Environment Variables）與 Base64 金鑰。

### 2. 環境變數設定 (.env)
專案根目錄附帶了一個 `.env.example` 檔案。
1.  請將其複製並重新命名為 `.env`。
2.  填入您的相關金鑰（LINE Token, GCP Project ID, Bucket Name 等）。
3.  **不要** 將含有真實金鑰的 `.env` 檔案上傳到 GitHub。

### 3. 本地開發 (Local Development)
若需在本地測試：
```bash
pip install -r requirements.txt
python main.py
```

---

## ✨ 主要功能

### 💬 智能對話
- **激勵風格**：充滿正能量的對話風格
- **對話記憶**：記住 7 天內的聊天內容
- **圖片理解**：上傳圖片自動描述並繼續對話

### 🎨 圖片功能
- **AI 生圖**：使用 Imagen 3 生成高品質圖片
- **長輩圖製作**：引導式製作長輩圖，輕鬆轉傳
- **圖片美化**：自動優化照片（開發中）

### 🛡️ 連結查證 
- **自動偵測**：傳送連結自動檢查安全性
- **多層查證**：網域年齡、媒體白名單、可疑關鍵字
- **智能詢問**：根據風險等級提供「閱讀」或「查證」選項
- **內容摘要**：AI 摘要網頁重點，長輩易懂

### 📰 新聞查詢 
- **對話觸發**：說「今天新聞」即可查詢
- **精選摘要**：AI 從多則新聞中挑選 3 則重要的
- **語音播報**：可選擇語音朗讀新聞內容
- **可信來源**：僅抓取台灣官方新聞媒體

### ⏰ 提醒通知
- **語音設定**：說「提醒我明天早上吃藥」
- **自動通知**：時間到自動 LINE 通知
- **清單管理**：隨時查看待辦提醒

### 🗺️ 地圖整合
- **景點推薦**：根據興趣推薦附近景點
- **地址查找**：自動搜尋並規劃
- **行程規劃**：結合地圖資訊的專業行程

---

## 🛠️ 技術架構

- **核心框架**：Flask + LINE Bot SDK v3
- **AI 模型**：Gemini 2.5 Flash (對話、摘要)
- **圖片生成**：Google Imagen 3
- **語音處理**：Google Cloud Speech-to-Text / Text-to-Speech
- **連結查證**：python-whois + BeautifulSoup4
- **新聞來源**：RSS (中央社等台灣媒體)
- **排程提醒**：APScheduler + SQLite
- **地圖服務**：Google Maps API (Optional)

## 📦 部署

詳細部署說明請參閱 `Documentation/AWS_DEPLOYMENT_CHECKLIST.md`

本專案已針對 **Zeabur** 與 **AWS (App Runner/Beanstalk)** 優化。

### 快速開始

1. 複製專案
2. 安裝依賴：`pip install -r requirements.txt`
3. 設定環境變數（參考 `.env.example`）
4. 執行：`python main.py`

## 💰 成本分析（確保 LINE 免費）

### 完全免費的功能 ✅
- **Gemini API**：免費額度 1500 次/天（對話、摘要）
- **連結查證**：python-whois 本地查詢，無 API 費用
- **新聞抓取**：RSS 訂閱，無 API 費用
- **網頁解析**：BeautifulSoup4 本地處理，無費用

### 有免費額度的功能 ⚠️
- **Imagen 3**：$0.04/張（建議控制使用量）
- **Google Cloud TTS**：每月 100 萬字元免費（約 1000 次播報）
- **語音辨識**：每月 60 分鐘免費
- **地圖服務**：每月 $200 免費額度

### LINE 訊息成本 📱
- **Reply Message**：完全免費（本專案主要使用）
- **Push Message**：每月 200 則免費，超過 $0.30/則

> 💡 **重要**：所有新功能（連結查證、新聞查詢）都使用 Reply Message，**完全不會產生 LINE 訊息費用**！

**預估月費**：$5-20（主要來自 Imagen 生圖，其他功能幾乎免費）

## 📄 授權

MIT License

## 🙏 致謝

感謝 Google Cloud AI Platform 提供強大的 AI 服務。
