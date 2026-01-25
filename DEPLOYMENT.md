# LINE Bot 長輩版機器人 - 部署指南

## 📋 部署前準備

### 1. 取得必要的 API 金鑰

#### LINE Bot
1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider 和 Channel (Messaging API)
3. 取得：
   - Channel Secret
   - Channel Access Token

#### Google Cloud (Gemini + Imagen + Speech)
1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立新專案
3. 啟用以下 API：
   - Generative AI API (Gemini)
   - Vertex AI API (Imagen)
   - Cloud Speech-to-Text API
   - Cloud Text-to-Speech API
4. 建立服務帳號金鑰：
   - IAM & Admin → Service Accounts
   - 建立服務帳號，授予 "Vertex AI User" 和 "Cloud Speech Client" 角色
   - 建立 JSON 金鑰並下載

#### Gemini API Key
1. 前往 [Google AI Studio](https://makersuite.google.com/app/apikey)
2. 建立 API Key

#### ImgBB (圖片上傳服務，可選)
1. 前往 [ImgBB](https://api.imgbb.com/)
2. 註冊並取得 API Key
3. 或使用其他圖床服務（需修改 `upload_image_to_external_host` 函數）

---

## 🚀 部署步驟

### 方法一：部署到 Zeabur (推薦)

1. **建立 GitHub Repository**
   ```bash
   cd "c:\Users\User\Desktop\LINEBOT\長輩版機器人"
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin <your-github-repo-url>
   git push -u origin main
   ```

2. **在 Zeabur 部署**
   - 登入 [Zeabur](https://zeabur.com/)
   - 建立新專案
   - 連接 GitHub Repository
   - Zeabur 會自動偵測 Python 專案

3. **設定環境變數**
   在 Zeabur 專案設定中新增：
   ```
   LINE_CHANNEL_ACCESS_TOKEN=<your_line_token>
   LINE_CHANNEL_SECRET=<your_line_secret>
   GEMINI_API_KEY=<your_gemini_key>
   GOOGLE_CLOUD_PROJECT=<your_gcp_project_id>
   GOOGLE_APPLICATION_CREDENTIALS=/app/service-account-key.json
   IMGBB_API_KEY=<your_imgbb_key>
   PORT=8080
   ```

4. **上傳服務帳號金鑰**
   - 將 Google Cloud 服務帳號 JSON 檔案內容複製
   - 在專案根目錄建立 `service-account-key.json`
   - 或使用 Zeabur 的檔案上傳功能

5. **設定 LINE Webhook**
   - 部署完成後，複製 Zeabur 提供的網址
   - 前往 LINE Developers Console
   - 設定 Webhook URL: `https://your-app.zeabur.app/callback`
   - 啟用 "Use webhook"

---

### 方法二：本地測試 (使用 ngrok)

1. **安裝套件**
   ```bash
   pip install -r requirements.txt
   ```

2. **建立 .env 檔案**
   ```bash
   cp .env.example .env
   # 編輯 .env，填入真實的 API 金鑰
   ```

3. **設定 Google Cloud 認證**
   ```bash
   # Windows
   set GOOGLE_APPLICATION_CREDENTIALS=path\to\service-account-key.json
   
   # Mac/Linux
   export GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account-key.json
   ```

4. **啟動應用**
   ```bash
   python main.py
   ```

5. **使用 ngrok 建立公開網址**
   ```bash
   ngrok http 5000
   ```

6. **設定 LINE Webhook**
   - 使用 ngrok 提供的 HTTPS 網址
   - 例如：`https://abc123.ngrok.io/callback`

---

## 🔧 故障排除

### 圖片生成失敗
- 檢查 Google Cloud 專案是否啟用 Vertex AI API
- 確認服務帳號有 "Vertex AI User" 權限
- 檢查 `GOOGLE_CLOUD_PROJECT` 環境變數是否正確

### 語音功能失敗
- 確認啟用 Cloud Speech-to-Text API
- 檢查服務帳號權限

### 圖片無法傳送到 LINE
- 確認 ImgBB API Key 是否設定
- 檢查圖片上傳服務是否正常
- LINE 要求圖片必須是 HTTPS URL

### Memory Error
- 生成圖片可能需要較大記憶體
- Zeabur 免費版可能不足，建議升級方案

---

## 💡 功能使用說明

### 基本對話
- 直接傳送文字訊息即可
- 機器人會記住 7 天內的對話

### 圖片生成
1. 說「幫我生成一張圖片」
2. 機器人詢問需求
3. 詳細描述想要的圖片
4. 等待生成並接收圖片

### 長輩圖製作
1. 說「做長輩圖」
2. 描述想要的背景（例如：花朵、風景）
3. 提供想要的文字內容
4. 接收完成的長輩圖

### 行程規劃
1. 說「幫我規劃行程」
2. 回答機器人的引導問題
3. 獲得詳細的行程建議

### 清除記憶
- 說「清除記憶」或「重新開始」

### 查看功能
- 說「功能」或「選單」

---

## 📊 成本估算

- Gemini API：免費額度內
- Imagen 3：$0.04/張圖片
- Speech-to-Text：每月 60 分鐘免費
- Text-to-Speech：每月 100 萬字元免費
- LINE Messaging API：每月 500 則訊息免費
- Zeabur：免費方案或 $5-10/月

**預估月費用**：$10-40（視使用量）

---

## 🎯 後續優化建議

1. **圖片儲存**：整合 Cloud Storage 或 S3
2. **資料庫**：加入 PostgreSQL 儲存提醒和行程
3. **快取**：減少重複 API 呼叫
4. **監控**：加入 logging 和錯誤追蹤
5. **限流**：防止 API 濫用

---

## 📞 技術支援

遇到問題？檢查以下項目：
1. 環境變數是否正確設定
2. API 金鑰是否有效
3. Google Cloud API 是否已啟用
4. 服務帳號權限是否足夠
5. Webhook URL 是否正確
