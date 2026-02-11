# 長輩版機器人 - Google Cloud 部署與設定指南

這份文件是給協助部署或想要自己架設此機器人的開發者閱讀。
為了讓機器人正常運作，並且將相關費用（如 Gemini API、語音合成、圖片儲存）算在您的 Google Cloud 帳號下，請按照以下步驟進行設定。

---

## 第一步：建立 Google Cloud 專案 (GCP Project)

1.  前往 [Google Cloud Console](https://console.cloud.google.com/)。
2.  點擊左上角的專案選單，選擇「**建立專案 (New Project)**」。
3.  輸入專案名稱（例如：`senior-linebot`），記下您的 **專案 ID (Project ID)**。
    *   *注意：專案 ID 通常是唯一的，例如 `senior-linebot-123456`。*
4.  確認建立。

## 第二步：啟用必要的 API

請在 Google Cloud Console 上方的搜尋列，搜尋並啟用以下服務：

1.  **Vertex AI API** (用於 Gemini 模型對話與圖片生成)
2.  **Cloud Text-to-Speech API** (用於語音回覆功能)
3.  **Google Maps Platform** (如有使用地圖功能，需建立 API Key)
4.  **Cloud Storage** (用於儲存生成的影片與圖片)

## 第三步：建立 Cloud Storage Bucket (儲存空間)

1.  在左側選單找到「**Cloud Storage**」 > 「**Bucket**」。
2.  點擊「**建立 (CREATE)**」。
3.  輸入一個全球唯一的名稱（例如：`your-name-linebot-video`）。
4.  設定地點（建議選 `Region` -> `asia-east1 (Taiwan)` 以獲得最佳速度）。
5.  其餘保持預設，點擊建立。
6.  **重要：** 設定公開存取權限 (若需要讓 LINE 讀取圖片/影片)
    *   點擊剛剛建立的 Bucket。
    *   進入「權限 (Permissions)」分頁。
    *   點擊「授權存取權 (Grant Access)」。
    *   新增主體輸入：`allUsers`。
    *   角色選擇：`Cloud Storage` > `Storage Object Viewer` (儲存物件檢視者)。
    *   儲存。
    *   *這會讓 bucket 內的檔案可以被公開讀取，LINE 才能顯示圖片。*

## 第四步：建立 Service Account (服務帳號) 與金鑰

這是機器人存取您 GCP 資源的身份證。

1.  在左側選單找到「**IAM 與管理 (IAM & Admin)**」 > 「**服務帳號 (Service Accounts)**」。
2.  點擊「**建立服務帳號 (Create Service Account)**」。
3.  輸入名稱（例如：`linebot-admin`），點擊「建立並繼續」。
4.  **授與此服務帳號對專案的存取權** (Role)：
    *   請新增以下角色：
        *   `Storage Admin` (儲存空間管理員) - 用於上傳圖片/影片
        *   `Vertex AI User` (Vertex AI 使用者) - 用於呼叫 Gemini
        *   `Cloud Speech Client` (Cloud Speech 用戶端) - 用於語音合成 (若有使用)
5.  點擊「完成」。
6.  **下載金鑰 (Key)**：
    *   在服務帳號列表中，點擊剛剛建立的帳號（Email 格式的那一行）。
    *   進入「**金鑰 (Keys)**」分頁。
    *   點擊「**新增金鑰 (Add Key)**」 > 「**建立新金鑰 (Create new key)**」。
    *   選擇 **JSON** 格式，點擊「建立」。
    *   **檔案會自動下載**，請將此檔案重新命名為 `service-account-key.json`。
    *   **將此檔案放入機器人專案的根目錄中。**

---

## 第五步：設定環境變數 (.env)

請複製專案中的 `.env.example` 檔案，並重新命名為 `.env`。
接著填入以下資訊：

```ini
# ... 其他 LINE 相關設定 ...

# Google AI 與 Cloud 設定
# 填入您的 Google Cloud 專案 ID
GOOGLE_CLOUD_PROJECT=your-project-id-here

# 剛下載的 JSON 金鑰檔名 (通常放在根目錄)
GOOGLE_APPLICATION_CREDENTIALS=service-account-key.json

# 剛剛建立的 Cloud Storage Bucket 名稱
GCS_BUCKET_NAME=your-bucket-name

# ... 其他設定 ...
```

完成以上步驟後，機器人所使用的 Google 資源費用就會計算在您的帳號下，並且擁有完整的權限。
