# LINE Bot 長輩版機器人

🤖 專為老人家設計的全功能 LINE 聊天機器人，提供陪伴、圖片生成、行程規劃等實用功能。

## ✨ 主要功能

### 💬 智能對話
- **激勵風格**：充滿正能量的對話風格
- **對話記憶**：記住 7 天內的聊天內容
- **圖片理解**：上傳圖片自動描述並繼續對話

### 🎨 圖片功能
- **AI 生圖**：使用 Imagen 3 生成高品質圖片
- **長輩圖製作**：引導式製作長輩圖，輕鬆轉傳
- **圖片美化**：自動優化照片（開發中）

### 🎥 影片生成 (新❗️)
- **AI 生影片**：使用 Google Veo (Vertex AI) 生成影片
- **指令**：說「生成影片」或「做影片」
- **限制**：每人每天限生成 1 部

### ⏰ 提醒通知 (新❗️)
- **語音設定**：說「提醒我明天早上吃藥」
- **自動通知**：時間到自動 LINE 通知
- **清單管理**：隨時查看待辦提醒

### 🗺️ 地圖整合 (新❗️)
- **景點推薦**：根據興趣推薦附近景點
- **地址查找**：自動搜尋並規劃
- **行程規劃**：結合地圖資訊的專業行程

## 🛠️ 技術架構

- **核心框架**：Flask + LINE Bot SDK v3
- **AI 模型**：Gemini 2.0 Flash (對話)
- **圖片生成**：Google Imagen 3
- **影片生成**：Google Veo (Vertex AI) + Google Cloud Storage
- **語音處理**：Google Cloud Speech-to-Text / Text-to-Speech
- **排程提醒**：APScheduler + SQLite
- **地圖服務**：Google Maps API (Optional)

## 📦 部署

詳細部署說明請參閱 [DEPLOYMENT.md](DEPLOYMENT.md)

### 快速開始

1. 複製專案
2. 安裝依賴：`pip install -r requirements.txt`
3. 設定環境變數（參考 `.env.example`）
4. 執行：`python main.py`

## 💰 成本

- Gemini API：免費額度內
- Imagen 3：$0.04/張
- 影片生成：視 Veo 定價而定
- 語音服務：有免費額度
- 地圖服務：每月 $200 免費額度
- 預估月費：$10-40（視使用量）

## 📄 授權

MIT License

## 🙏 致謝

感謝 Google Cloud AI Platform 提供強大的 AI 服務。
