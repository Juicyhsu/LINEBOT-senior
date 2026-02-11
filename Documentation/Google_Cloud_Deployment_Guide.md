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

---

## 進階：專案擁有權 vs. 付款帳號 (Sponsorship Model)

如果您是開發者，希望能全權管理專案程式碼與部署，但希望由 **業主 (Sponsor)** 直接支付 Google Cloud 費用，可以使用 **「帳單帳戶授權 (Billing Account Delegation)」** 模式。

這讓您可以保有專案的所有權 (Project Owner)，而業主只需要負責綁定信用卡即可。

### 設定步驟

**1. 業主端 (Sponsor)：建立付款帳戶**
1.  業主前往 [Google Cloud Billing](https://console.cloud.google.com/billing)。
2.  建立一個新的 **Billing Account (付款帳戶)**，並綁定信用卡。
3.  進入該付款帳戶的 **「Account Management (帳戶管理)」**。
4.  點擊右側的 **「Add Principal (新增成員)」**。
5.  輸入開發者 (您) 的 Google Email。
6.  賦予角色：**`Billing Account User` (付款帳戶使用者)**。
7.  完成後通知開發者。

**2. 開發者端 (You)：連結專案**
1.  進入您的 Google Cloud 專案。
2.  左側選單選擇 **「Billing (付款)」**。
3.  點擊 **「Link a Billing Account (連結付款帳戶)」** 或 **「Change Billing Account (變更付款帳戶)」**。
4.  選擇業主剛剛授權給您的那個付款帳戶。
5.  點擊 **「Set Account (設定帳戶)」**。

**結果：**
*   ✅ **專案管理權**：100% 在您手上 (您可以部署、寫 code)。
*   ✅ **費用支付**：100% 由業主信用卡扣款 (帳單直接寄給業主)。
*   ✅ **隱私**：業主看不到您的程式碼 (除非您另外開權限給他)，他只會看到帳單明細 (e.g. "Vertex AI 使用費: $10")。

### 🔄 如果業主停止贊助，如何切換回來？
非常簡單，只需要 **30 秒**：
1.  回到您的 Google Cloud Console > **Billing (付款)**。
2.  點擊 **「Manage Billing Accounts (管理付款帳戶)」**。
3.  在您的專案旁，點擊 **「Change Billing (變更付款)」**。
4.  選擇回 **您自己的付款帳戶** (My Billing Account)。
5.  點擊 **「Set Account (設定帳戶)」**。
    -> 完成！之後的帳單就會寄給您自己，服務完全不會中斷。

### 💰 如何設定預算通知 (Budget Alerts) - 強烈建議！
為了避免費用超支，建議業主設定「預算通知」：

1.  業主前往 [Google Cloud Billing](https://console.cloud.google.com/billing)。
2.  在左側選單選擇 **「Budgets & alerts (預算與快訊)」**。
3.  點擊 **「Create Budget (建立預算)」**。
4.  設定 **目標金額** (例如：$50 USD)。
5.  設定 **觸發條件** (例如：當花費達到 50%, 90%, 100% 時寄信)。
6.  **優點**：服務不會中斷，但當費用快達標時，業主與開發者都會收到 Email 提醒，方便及早應對。
