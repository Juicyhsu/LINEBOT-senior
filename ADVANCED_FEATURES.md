# 進階功能實作指南

## 📋 目錄
1. [記憶提醒功能](#記憶提醒功能)
2. [Google Maps 整合](#google-maps-整合)
3. [資料庫設定](#資料庫設定)
4. [排程器設定](#排程器設定)

---

## 記憶提醒功能

### 功能說明
- 用戶可設定提醒事項
- 自動定時推送提醒
- 支援查看、刪除提醒

### 使用方式

**設定提醒**：
```
用戶：提醒我明天早上8點吃藥
機器人：好的！我會在 2026-01-17 08:00 提醒你「吃藥」
```

**查看提醒**：
```
用戶：我的提醒
機器人：你目前有以下提醒：
        1. 明天 08:00 - 吃藥
        2. 後天 14:00 - 回診
```

### 資料庫結構

```sql
-- 提醒表格
CREATE TABLE reminders (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    reminder_text TEXT NOT NULL,
    reminder_time TIMESTAMP NOT NULL,
    is_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB
);
```

### 實作步驟

#### 1. 設定資料庫

**選項 A：使用 SQLite（本地開發）**
```bash
# 不需額外設定，會自動建立 bot_data.db
```

**選項 B：使用 PostgreSQL（生產環境，推薦）**

1. 在 Zeabur 新增 PostgreSQL 服務
2. 取得 DATABASE_URL
3. 設定環境變數：
   ```
   DATABASE_URL=postgresql://user:password@host:port/database
   ```

#### 2. 在 main.py 中整合

在 `main.py` 開頭加入：
```python
# 導入新模組
from database import db
from scheduler import init_scheduler
from maps_integration import maps

# 在 app 啟動後初始化排程器
if __name__ == "__main__":
    # 初始化提醒排程器
    reminder_scheduler = init_scheduler(channel_access_token)
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
```

#### 3. 新增提醒處理函數

在 `gemini_llm_sdk()` 函數中加入提醒意圖處理：

```python
# 檢測提醒設定意圖
if detect_reminder_intent(user_input):
    # 使用 AI 解析提醒內容和時間
    parse_prompt = f"""
    用戶說：「{user_input}」
    請解析出：
    1. 提醒內容
    2. 提醒時間（請轉換為 ISO 8601 格式）
    
    以 JSON 格式回答：
    {{
        "reminder_text": "提醒內容",
        "reminder_time": "2026-01-17T08:00:00"
    }}
    """
    
    parse_response = model.generate_content(parse_prompt)
    import json
    reminder_data = json.loads(parse_response.text)
    
    # 儲存到資料庫
    from datetime import datetime
    reminder_time = datetime.fromisoformat(reminder_data['reminder_time'])
    reminder_id = db.add_reminder(
        user_id=user_id,
        reminder_text=reminder_data['reminder_text'],
        reminder_time=reminder_time
    )
    
    return f"好的！我會在 {reminder_time.strftime('%Y-%m-%d %H:%M')} 提醒你「{reminder_data['reminder_text']}」！讚喔！Cheer up！"
```

---

## Google Maps & Video Integration

### 🎥 Veo 影片生成

#### 功能說明
- 使用 Google 最新 Veo 模型生成影片
- 透過 Prompt 生成高品質短片
- 自動上傳到 Google Cloud Storage (GCS)
- 產生公開分享連結

#### 流程架構
1. 用戶發送「生成影片」指令
2. Bot 偵測意圖，引導用戶輸入描述
3. 使用 Gemini 優化提示詞（Prompt Engineering）
4. 呼叫 Vertex AI (Veo) 生成 .mp4 影片
5. 暫存於本地伺服器
6. 上傳至 GCS Bucket
7. 回傳 GCS 公開網址給用戶

#### 環境設定
1. 啟用 **Vertex AI API**
2. 建立 **Google Cloud Storage Bucket**
   - 權限設定：`Storage Object Viewer` (allUsers)
3. 設定 `.env`：
   ```env
   GCS_BUCKET_NAME=your-bucket-name
   ```

#### 每日限額實作
- 為了控制成本和資源，每個用戶每日限額 1 部
- 使用 `user_daily_video_count` 變數追蹤
- 跨日自動重置計數

---

### 🗺️ Google Maps 整合

### 功能說明
- 地點搜尋
- 路線規劃
- 旅行時間計算
- 景點推薦

### 取得 API 金鑰

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 啟用以下 API：
   - Maps JavaScript API
   - Directions API
   - Places API
   - Geocoding API
3. 建立 API 金鑰
4. 設定環境變數：
   ```
   GOOGLE_MAPS_API_KEY=your_api_key_here
   ```

### 使用範例

#### 1. 地點搜尋
```python
from maps_integration import maps

# 搜尋地點
location = maps.geocode("台北101")
print(f"緯度: {location['lat']}, 經度: {location['lng']}")
```

#### 2. 路線規劃
```python
# 規劃路線
directions = maps.get_directions(
    origin="台北車站",
    destination="陽明山",
    mode="transit"  # 或 driving, walking
)

print(f"距離: {directions['distance']}")
print(f"時間: {directions['duration']}")
```

#### 3. 附近景點
```python
# 搜尋附近景點
places = maps.search_nearby_places(
    location="台北101",
    place_type="tourist_attraction",
    radius=5000  # 5公里
)

for place in places:
    print(f"{place['name']} - 評分: {place['rating']}")
```

### 整合到行程規劃

在 `gemini_llm_sdk()` 中加入 Maps 功能：

```python
# 檢測行程規劃意圖
if detect_trip_planning_intent(user_input):
    # AI 生成基本行程
    base_itinerary = chat.send_message(
        f"用戶想規劃行程：{user_input}。"
        f"請提供詳細的行程建議（景點、時間、交通）。"
        f"請考慮老人家需求（休息、無障礙）。"
    ).text
    
    # 使用 Maps API 增強資訊
    try:
        # 解析起點
        if "從" in user_input or "出發" in user_input:
            # 提取起點資訊...
            origin_location = maps.geocode(origin_text)
            
            # 搜尋附近景點
            nearby_places = maps.search_nearby_places(
                location=origin_text,
                place_type="tourist_attraction"
            )
            
            # AI 整合景點資訊
            enhanced_prompt = f"""
            基本行程：{base_itinerary}
            
            附近推薦景點：
            {nearby_places}
            
            請整合這些資訊，提供更詳細的行程建議。
            """
            
            enhanced_itinerary = model.generate_content(enhanced_prompt).text
            
            # 儲存行程到資料庫
            plan_data = {
                "itinerary": enhanced_itinerary,
                "places": nearby_places,
                "origin": origin_location
            }
            
            db.save_trip_plan(
                user_id=user_id,
                plan_name=f"行程規劃 {datetime.now().strftime('%Y-%m-%d')}",
                plan_type="short",
                start_date=datetime.now(),
                end_date=None,
                plan_data=plan_data
            )
            
            return enhanced_itinerary
    except Exception as e:
        print(f"Maps integration error: {e}")
        return base_itinerary
```

---

## 資料庫設定

### 本地開發（SQLite）

不需額外設定，首次執行時會自動建立 `bot_data.db`

### 生產環境（PostgreSQL）

#### 在 Zeabur 設定

1. 登入 Zeabur Dashboard
2. 選擇專案 → Add Service → Database → PostgreSQL
3. 複製 DATABASE_URL
4. 在環境變數中設定：
   ```
   DATABASE_URL=postgresql://username:password@host:port/dbname
   ```

#### 手動建立資料庫

如果使用其他 PostgreSQL 服務：

```bash
# 連接到 PostgreSQL
psql -U your_user -d your_database

# 執行建表語句（database.py 會自動執行，這裡僅供參考）
CREATE TABLE reminders (...);
CREATE TABLE trip_plans (...);
```

---

## 排程器設定

### 工作原理

`scheduler.py` 使用 APScheduler 每分鐘檢查一次待發送的提醒。

### 整合到 main.py

```python
if __name__ == "__main__":
    # 初始化資料庫（自動執行）
    from database import db
    
    # 初始化排程器
    from scheduler import init_scheduler
    reminder_scheduler = init_scheduler(channel_access_token)
    
    # 啟動 Flask
    port = int(os.environ.get("PORT", 5000))
    try:
        app.run(host="0.0.0.0", port=port)
    finally:
        # 關閉排程器
        if reminder_scheduler:
            reminder_scheduler.stop()
```

### 測試排程器

```python
# 新增一個測試提醒（2分鐘後）
from datetime import datetime, timedelta
from database import db

test_time = datetime.now() + timedelta(minutes=2)
db.add_reminder(
    user_id="YOUR_LINE_USER_ID",
    reminder_text="測試提醒！",
    reminder_time=test_time
)

print(f"已設定測試提醒，將於 {test_time} 發送")
```

---

## 完整環境變數

更新 `.env` 檔案：

```bash
# LINE Bot
LINE_CHANNEL_ACCESS_TOKEN=your_line_token
LINE_CHANNEL_SECRET=your_line_secret

# Google AI
GEMINI_API_KEY=your_gemini_key
GOOGLE_CLOUD_PROJECT=your_gcp_project_id
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account-key.json

# Google Maps
GOOGLE_MAPS_API_KEY=your_maps_api_key

# 圖片上傳
IMGBB_API_KEY=your_imgbb_key

# 資料庫
DATABASE_URL=postgresql://user:pass@host:port/dbname
# 或使用 SQLite（本地）:
# DATABASE_URL=sqlite:///bot_data.db

# 伺服器
PORT=5000
```

---

## 費用估算

| 服務 | 免費額度 | 超出費用 |
|------|---------|---------|
| Google Maps Geocoding | 每月 $200 額度 | $5/1000 次 |
| Google Maps Directions | 每月 $200 額度 | $5/1000 次 |
| Google Maps Places | 每月 $200 額度 | $17/1000 次 |
| PostgreSQL (Zeabur) | 免費方案 1GB | $5/月起 |

> 💡 **提示**：Google Maps 每月有 $200 免費額度，足夠小型應用使用。

---

## 故障排除

### 提醒沒有發送

1. 檢查排程器是否啟動：
   ```python
   print(reminder_scheduler.is_running)  # 應該是 True
   ```

2. 檢查資料庫連接：
   ```python
   from database import db
   pending = db.get_pending_reminders()
   print(pending)
   ```

3. 檢查 LINE Push Message 權限

### Maps API 錯誤

1. 確認 API 金鑰是否正確
2. 檢查 Google Cloud Console 是否啟用相關 API
3. 查看配額是否用完

### 資料庫連接失敗

1. 檢查 DATABASE_URL 格式
2. PostgreSQL 確認防火牆設定
3. 檢查連接憑證

---

## 下一步

1. ✅ 安裝新依賴：`pip install -r requirements.txt`
2. ✅ 設定環境變數
3. ✅ 測試資料庫連接
4. ✅ 整合到 main.py
5. ✅ 部署到 Zeabur
6. ✅ 測試提醒和 Maps 功能

祝你實作順利！🚀
