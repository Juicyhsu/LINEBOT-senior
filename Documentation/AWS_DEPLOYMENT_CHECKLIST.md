# AWS 部署注意事項清單 (Deployment Checklist)

本文件專為部署至 AWS (App Runner 或 Elastic Beanstalk) 所設計。
請將此文件與 `長輩LINE文檔/Google_Cloud_Deployment_Guide.md` 一起提供給部署人員。

---

## 🚀 部署前準備 (Prerequisites)

1.  **取得 Google Cloud 金鑰與資源**
    *   請先閱讀並執行 **`Documentation/Google_Cloud_Deployment_Guide.md`** 中的步驟。
    *   確認您已經下載了 `service-account-key.json`。
    *   確認您已經建立了 Google Cloud Storage Bucket。

2.  **準備 Base64 編碼的金鑰 (關鍵步驟)**
    *   AWS 環境變數通常不支援直接上傳 JSON 檔案。
    *   請將您的 `service-account-key.json` 文件內容轉換為 **Base64 字串**。
    *   **如何轉換？**
        *   **Mac/Linux**: 打開終端機輸入 `base64 -i service-account-key.json -o key_base64.txt`
        *   **Windows (PowerShell)**: `[Convert]::ToBase64String([IO.File]::ReadAllBytes('service-account-key.json')) | Set-Content key_base64.txt`
        *   或者使用線上工具 (搜尋 "JSON to Base64")，將轉換後的一長串英數字複製起來備用。

---

## ☁️ 1. 選擇部署服務 (AWS Service)

| 服務 | 推薦度 | 說明 |
|---|---|---|
| **AWS App Runner** | ⭐⭐⭐⭐⭐ | **最推薦！** 直接連結 GitHub 即可自動部署，支援自動擴展，設定最簡單。 |
| **Elastic Beanstalk** | ⭐⭐⭐ | 傳統 Python 部署方式，設定較繁瑣，但可控性高。 |

---

## 🔑 2. 環境變數設定 (Environment Variables)

在 AWS 部署介面 (Configuration -> Environment variables) 中，**務必**填入以下變數：

### 🅰️ 核心設定 (Core)
| 變數名稱 | 填寫內容 | 說明 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | (您的 LINE Token) | LINE Developers 後台取得 |
| `LINE_CHANNEL_SECRET` | (您的 LINE Secret) | LINE Developers 後台取得 |
| `TZ` | `Asia/Taipei` | 設定時區，確保長輩早安圖時間正確 |
| `web_concurrency` | `1` | **重要！** 請設為 1，避免多個 Worker 造成長輩圖生成異常 |

### 🅱️ Google Cloud 設定 (GCP)
| 變數名稱 | 填寫內容 | 說明 |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | (您的 GCP Project ID) | 例如 `my-project-12345` |
| `GOOGLE_APPLICATION_CREDENTIALS` | `service-account-key.json` | **請填入此固定檔名** (程式會自動產生此檔案) |
| `GOOGLE_CREDENTIALS_JSON` | (您的 Base64 字串) | **貼上剛剛準備的 Base64 長字串** |
| `GCS_BUCKET_NAME` | (您的 Bucket 名稱) | 用於儲存影片與圖片 |

### 🆎 其他服務 (Optional)
| 變數名稱 | 填寫內容 | 說明 |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | (您的 Maps API Key) | 若需要地圖功能則填寫 |
| `IMGBB_API_KEY` | (您的 ImgBB Key) | 備用圖床，若無可留空 |

---

## 📝 3. 部署步驟 (以 App Runner 為例)

1.  **Source Code**: 連結您的 GitHub Repository。
2.  **Configuration**:
    *   **Runtime**: Python 3
    *   **Build Command**: `pip install -r requirements.txt`
    *   **Start Command**: `gunicorn main:app`
    *   **Port**: `5000` (或是 `8080`，視 AWS 預設而定，程式會讀取 PORT 變數)
3.  **Environment Variables**: 填入上述所有變數。
4.  **Health Check**:
    *   Path: `/` (程式首頁會回傳 "Senior Line Bot is running!")
5.  **Deploy**: 點擊部署，等待約 5-10 分鐘即可完成。

---

## ⚠️ 常見問題 (Troubleshooting)

*   **Q: 部署後 Log顯示 "No Google Cloud credentials found"?**
    *   A: 請檢查 `GOOGLE_CREDENTIALS_JSON` 是否有正確貼上 Base64 字串，且 `GOOGLE_APPLICATION_CREDENTIALS` 設為 `service-account-key.json`。
*   **Q: 圖片無法生成？**
    *   A: 請確認 Google Cloud 專案是否已啟用 **Vertex AI API**。
*   **Q: 資料庫錯誤？**
    *   A: 本專案預設使用 SQLite，每次重新部署資料會重置。若需保留資料，請設定 `DATABASE_URL` 連接外部 PostgreSQL。
