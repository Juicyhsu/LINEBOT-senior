# AWS 部署注意事項清單 (Deployment Checklist)

本文件專為部署至 AWS App Runner 或 AWS Elastic Beanstalk 設計。

---

## ☁️ 1. 選擇部署服務
本專案適合以下兩種 AWS 服務（推薦 **App Runner** 最簡單）：

| 服務 | 適合情境 | 優點 | 預估費用 |
|---|---|---|---|
| **AWS App Runner** (推薦) | 容器化部署、不想管伺服器 | 自動擴展、與 GitHub 連動最簡單 | 稍高 ($5-25/月) |
| **Elastic Beanstalk** (Python) | 傳統 Python 部署 | 傳統架構、可細調 EC2 | 彈性 (依 EC2 規格) |

---

## 🔑 2. 環境變數設定 (Environment Variables)
在 AWS 部署介面 (Configuration -> Environment variables) 中，務必填入以下變數：

### 必須填寫 (Core)
| 變數名稱 | 範例值 / 說明 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | (您的 LINE Token) |
| `LINE_CHANNEL_SECRET` | (您的 LINE Secret) |
| `GOOGLE_APPLICATION_CREDENTIALS` | `service-account-key.json` (指向憑證檔名) |
| `GOOGLE_CREDENTIALS_JSON` | (您的 GCP JSON 內容 Base64 編碼，**這是關鍵**) |
| `WEB_CONCURRENCY` | `1` (**重要！** 限制 1 個 Worker 以避免長輩圖中斷) |
| `TZ` | `Asia/Taipei` (確保時間正確) |

### 資源與功能開關 (Resource Config)
| 變數名稱 | 範例值 / 說明 |
|---|---|
| `DATABASE_URL` | **(選填)** 若留空，使用暫存 SQLite (符合您的需求)；若要持久化，填入 PostgreSQL 連線字串。 |
| `GCS_BUCKET_NAME` | (您的 GCP Storage Bucket 名稱，用於存圖) |
| `IMGBB_API_KEY` | (備用圖床 Key，若 GCS 失敗時使用) |

---

## 📝 3. 憑證處理 (GCP Credentials)
因為 AWS 不像本地可以直接放一個 `.json` 檔案上去，最好的做法是：
1. 將原本的 `service-account-key.json` 內容複製。
2. 轉成 **Base64** 字串 (網路上有 Base64 Encoder，或用 Python `import base64; base64.b64encode(...)`)。
3. 將這串長字串貼到 AWS 環境變數 `GOOGLE_CREDENTIALS_JSON`。
4. 程式啟動時會自動還原成檔案，確保 GCP 功能正常。

---

## 🗑️ 4. 資料處理策略 (Data Policy)

### 如果您想要「服務重啟就清空資料」 (Ephemeral)
- **資料庫**：**不要** 設定 `DATABASE_URL`。
  - 程式會自動使用 SQLite (`bot_data.db`)。
  - 在 App Runner / Beanstalk 上，每次部署或重啟，這個檔案都會被重置。
  - 達成您想要的「不保留用戶資料」效果。

### 圖片/影片自動刪除
- 請前往 **Google Cloud Console** -> **Cloud Storage**。
- 點選您的 Bucket -> **Lifecycle** (生命週期)。
- 新增規則：
  - Action: **Delete object**
  - Condition: **Age > 1 day** (超過 1 天自動刪除)。
- 這樣雲端也不會累積過多暫存圖檔。

---

## 🚢 5. 部署步驟簡述
1. 將代碼 Push 到 GitHub。
2. 在 AWS App Runner 建立服務 -> Source 選擇 GitHub Repository。
3. 選擇 Python 3 運行環境。
4. Start Command 填入：`gunicorn main:app` (或留空讀取 Procfile)。
5. 填入上述環境變數。
6. 點擊 **Deploy**。

🎉 完成！您的長輩機器人就在 AWS 上運行了！
