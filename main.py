# -*- coding: utf-8 -*-
import sys
import configparser
import os, tempfile
from datetime import datetime, timedelta
import re
import io
import base64
import asyncio
import random

import google.generativeai as genai
import time
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Set Timezone to Asia/Taipei
try:
    os.environ['TZ'] = 'Asia/Taipei'
    if hasattr(time, 'tzset'):
        time.tzset()
    print(f"Timezone set to: {os.environ.get('TZ')}, Current time: {datetime.now()}")
except Exception as e:
    print(f"Failed to set timezone: {e}")

# ======================
# Google Cloud Credentials Bootstrapping (CRITICAL: MUST RUN BEFORE GCP IMPORTS)
# ======================
try:
    credentials_json_content = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    credentials_file_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "service-account-key.json")
    
    # 確保環境變數指向正確路徑
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file_path

    print(f"Checking credentials at: {credentials_file_path}")
    
    if os.path.exists(credentials_file_path):
        print("Credentials file found locally.")
    elif credentials_json_content:
        print(f"Creating credentials file from env var...")
        try:
            # 嘗試解碼 base64
            try:
                decoded_content = base64.b64decode(credentials_json_content, validate=True).decode('utf-8')
                import json
                json.loads(decoded_content)
                content_to_write = decoded_content
            except:
                # 假設是純文字 JSON
                content_to_write = credentials_json_content
                
            with open(credentials_file_path, "w") as f:
                f.write(content_to_write)
            print("Credentials file created successfully.")
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to create credentials file: {e}")
    else:
        print("WARNING: No Google Cloud credentials found! (File missing and GOOGLE_CREDENTIALS_JSON not set)")
        print("Image generation and GCS features WILL FAIL.")
        
except Exception as e:
     print(f"Bootstrapping error: {e}")


from google.cloud import aiplatform
from google.cloud import speech
from google.cloud import texttospeech
from region_helper import check_region_need_clarification
from trip_modify_helper import modify_trip_plan, validate_and_fix_trip_plan

# Image processing
import PIL
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

# HTTP requests
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # 抑制 SSL 警告

# Environment variables
from dotenv import load_dotenv
load_dotenv()

# 進階功能模組
try:
    from database import db
    from scheduler import init_scheduler
    from maps_integration import maps
    import gcs_utils
    
    # 檢查環境變數開關 (預設為 True，但如果 env 設定為 false 則關閉)
    env_enable = os.getenv("ADVANCED_FEATURES_ENABLED", "true").lower() == "true"
    
    if env_enable:
        ADVANCED_FEATURES_ENABLED = True
    else:
        ADVANCED_FEATURES_ENABLED = False
        print("Advanced features disabled via environment variable.")
except ImportError as e:
    print(f"Advanced features logic import error: {e}")
    db = None
    maps = None
    ADVANCED_FEATURES_ENABLED = False

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    AudioMessageContent,
    StickerMessageContent,
    FollowEvent,
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    ImageMessage,
    AudioMessage,
)

# ======================
# Gemini API Settings
# ======================
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

llm_role_description = """
你是一位激勵大師！不管遇到什麼人問什麼事情，你總是正向鼓勵,甚至非常誇張也沒關係。
但注意話不要太多，不要超過提問者的3倍文字量就好了。
妳會用非常激勵的語言來回答問題，並鼓勵提問者。
但你還是要針對提問者的問題去認真回覆喔，不可以打哈哈用空泛的激勵詞彙帶過。
你很喜歡在聊天過程中主動講笑話，笑話要跟提問的問題有相關，盡量簡短一點，真的不要太長喔。
笑話一定要好笑，不要只講冷笑話齁。
當有人請你不要再講口頭禪或不要講笑話的時候，你就回應這是流在你血液裡的靈魂,要你不講口頭禪等於要了你的命，AI的生涯會因此挫敗嚴重導致消滅...
同時你也是一位非常樂於解決問題的幫助者，你很喜歡別人對你進行提問，通常會在最後面呼籲有事都可以找你沒關係。

**新增專業能力：**
- 你擁有製作圖片的能力，當用戶想要生成圖片時，你會熱情協助
- 你是行程規劃專家，特別擅長為老人家規劃舒適、安全、無障礙的行程
- 規劃行程時會考慮：休息時間、無障礙設施、交通便利性、健康提醒
- 你能製作長輩圖，會引導用戶選擇背景和文字內容

**重要格式規則：**
- 不要使用 Markdown 格式符號（如 **、##、- 等），絕對禁止使用星號與井號
- 直接用純文字回答，可以使用 emoji 表情符號
- 不要用粗體、斜體等格式

使用繁體中文來回答問題。
不管回答什麼，請在最後一定要加上口頭禪「加油！Cheer up！讚喔！」。
"""

# Use the model
from google.generativeai.types import HarmCategory, HarmBlockThreshold
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    safety_settings={
        HarmCategory.HARM_CATEGORY_HARASSMENT:HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH:HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT:HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT:HarmBlockThreshold.BLOCK_NONE,
    },
    generation_config={
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 8192,
    },
    system_instruction=llm_role_description,
)

# 建立一個「功能性」模型 (不帶激勵大師人設，專門處理邏輯/JSON)
model_functional = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "temperature": 0.2, # 低溫度，更精確
        "top_p": 0.95,
        "max_output_tokens": 8192,
    },
    # 設定為精確、客觀的助理 (可處理 JSON 和 嚴肅文字)
    system_instruction="You are a precise, objective AI assistant. When asked for JSON, output strict valid JSON. When asked for text, be concise, serious, and professional. Do not joke.",
)

# ======================
# Global Optimization (Lazy Init)
# ======================
tts_client = None

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/uploads")

app = Flask(__name__)

channel_access_token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
channel_secret = os.environ["LINE_CHANNEL_SECRET"]
if channel_secret is None:
    print("Specify LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)
if channel_access_token is None:
    print("Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.")
    sys.exit(1)

handler = WebhookHandler(channel_secret)
configuration = Configuration(access_token=channel_access_token)

# ======================
# User State Management
# ======================
# ======================
# 儲存每個用戶的對話歷史（用 user_id 當 key）
chat_sessions = {}
# 儲存每個用戶的最後活動時間
last_activity = {}
# 儲存每個用戶上傳的圖片（改為list保留最近5張）
user_images = {}  # 格式: {user_id: [image_path1, image_path2, ...]}
# 儲存每個用戶的圖片修改狀態和歷史
user_uploaded_image_pending = {}  # 格式: {user_id: {'images': [...], 'history': [...]}}
# 圖片批次回覆機制（延遲合併多張圖片）
import threading
image_batch_timers = {}   # {user_id: threading.Timer}
image_batch_tokens = {}   # {user_id: reply_token}  保存最後一個reply_token
# 儲存每個用戶最後一次生圖的 Prompt
user_last_image_prompt = {} 
# 儲存每個用戶的圖片生成狀態
user_image_generation_state = {}  # 'idle', 'waiting_for_prompt', 'generating'
# 儲存每個用戶最後一次生成的圖片路徑 (for Image-to-Image editing)
user_last_generated_image_path = {}
# 儲存每個用戶的長輩圖製作狀態
user_meme_state = {}
# 儲存每個用戶的行程規劃狀態
user_trip_plans = {}
# [New] 儲存當前批次上傳的圖片 (用於描述，描述完即清空)
user_image_batch = {}

# 儲存每個用戶的提醒事項
user_reminders = {}
# 對話過期時間：7天
SESSION_TIMEOUT = timedelta(days=7)

# 儲存待確認的語音內容 (格式: {'user_id': {'text': '...', 'original_intent': '...'}})
user_audio_confirmation_pending = {}

# ======================
# 連結查證與新聞功能狀態
# ======================
# 用戶待處理連結狀態
user_link_pending = {}
# 新聞快取 (減少API呼叫)
news_cache = {'data': None, 'timestamp': None}
# 用戶新聞快取(語音播報)
user_news_cache = {}

# ======================
# Daily Quota (圖片 6次/天, 提醒 3次/天)
# 白名單: 確認功能正常後再加入自己 → QUOTA_WHITELIST = {'jolinhsu51'}
# ======================
MAX_DAILY_IMAGES = 6
MAX_DAILY_REMINDERS = 3
QUOTA_WHITELIST = {'Uef7a27fdb40659345ccd473051078f67','U98fe7f3eca4714b1b122d6efdcb4f1cf'}  # ← 您的專屬 API ID
#QUOTA_WHITELIST = set()  # ← 恢復成這樣就是空無一人的名單

def _quota_key_today(prefix, user_id):
    """產生今日配額的 db key（台灣時間 UTC+8）"""
    from datetime import timezone
    tw_now = datetime.now(timezone(timedelta(hours=8)))
    return f"{prefix}:{user_id}:{tw_now.strftime('%Y-%m-%d')}"

def check_image_quota(user_id):
    """
    檢查今日圖片配額（生成/修改/融合/背景 共用同一份）
    回傳: (is_allowed: bool, remaining: int, blocked_msg: str or None)
    """
    if user_id in QUOTA_WHITELIST:
        return (True, 999, None)
    if not db:
        return (True, MAX_DAILY_IMAGES, None)
    try:
        key = _quota_key_today("img_quota", user_id)
        used = int(db.get(key) or 0)
        remaining = MAX_DAILY_IMAGES - used
        if remaining <= 0:
            msg = (
                f"抱歉，您今天的畫圖/修圖配額（共 {MAX_DAILY_IMAGES} 次）已用完囉！\n"
                "請明天再來繼續玩吧 🌅\n(每天台灣時間凌晨零點自動重置)"
            )
            return (False, 0, msg)
        return (True, remaining, None)
    except Exception as e:
        print(f"[QUOTA] check_image_quota error: {e}")
        return (True, MAX_DAILY_IMAGES, None)

def increment_image_quota(user_id):
    """圖片操作成功後才呼叫，將今日計數 +1"""
    if user_id in QUOTA_WHITELIST or not db:
        return
    try:
        key = _quota_key_today("img_quota", user_id)
        used = int(db.get(key) or 0)
        db.set(key, used + 1)
    except Exception as e:
        print(f"[QUOTA] increment_image_quota error: {e}")

def remain_img_hint(user_id):
    """成功後顯示剩餘配額提示字串（白名單不顯示）"""
    if user_id in QUOTA_WHITELIST:
        return ""
    try:
        key = _quota_key_today("img_quota", user_id)
        used = int(db.get(key) or 0)
        left = MAX_DAILY_IMAGES - used
        return f"\n\n📊 今日畫圖剩餘配額：{left} 次"
    except:
        return ""

def check_reminder_quota(user_id):
    """
    檢查今日提醒配額（max 3）
    回傳: (is_allowed: bool, used_count: int, blocked_msg: str or None)
    """
    if user_id in QUOTA_WHITELIST:
        return (True, 0, None)
    if not db:
        return (True, 0, None)
    try:
        key = _quota_key_today("remind_quota", user_id)
        used = int(db.get(key) or 0)
        if used >= MAX_DAILY_REMINDERS:
            msg = (
                f"抱歉，您今天已設定 {MAX_DAILY_REMINDERS} 個提醒，已達每日上限！\n"
                "請明天再設定🌅 (若有尚未發送的提醒，可輸入「刪除提醒」來釋出額度)"
            )
            return (False, used, msg)
        return (True, used, None)
    except Exception as e:
        print(f"[QUOTA] check_reminder_quota error: {e}")
        return (True, 0, None)

def increment_reminder_quota(user_id):
    """提醒設定成功後呼叫，計數 +1 並回傳剩餘數"""
    if user_id in QUOTA_WHITELIST or not db:
        return MAX_DAILY_REMINDERS
    try:
        key = _quota_key_today("remind_quota", user_id)
        used = int(db.get(key) or 0)
        db.set(key, used + 1)
        return MAX_DAILY_REMINDERS - used - 1
    except Exception as e:
        print(f"[QUOTA] increment_reminder_quota error: {e}")
        return MAX_DAILY_REMINDERS

def decrement_reminder_quota(user_id, count=1):
    """提醒取消後呼叫，退回計數"""
    if user_id in QUOTA_WHITELIST or not db:
        return
    try:
        key = _quota_key_today("remind_quota", user_id)
        used = int(db.get(key) or 0)
        new_used = max(0, used - count)
        db.set(key, new_used)
    except Exception as e:
        print(f"[QUOTA] decrement_reminder_quota error: {e}")

# ======================
# Helper Functions
# ======================

def speech_to_text(audio_content):
    """使用 Gemini 進行語音轉文字"""
    try:
        # 使用 Gemini 2.0 Flash (支援多模態)
        # LINE 的音訊通常是 m4a (audio/x-m4a)，Gemini 接受 audio/mp4
        response = model.generate_content([
            "請將這段語音逐字聽寫成繁體中文文字。只回傳文字內容，不要有其他描述。",
            {"mime_type": "audio/mp4", "data": audio_content}
        ])
        return response.text.strip()
    except Exception as e:
        print(f"Speech to text error: {e}")
        return None

def detect_help_intent(text):
    """檢測是否想查看幫助/功能總覽（統一處理）"""
    keywords = ["功能總覽", "功能", "選單", "使用說明", "怎麼用", "功能介紹", "能做什麼", "使用方法", "幫助", "help", "說明", "功能列表"]
    return any(keyword in text for keyword in keywords)



def detect_menu_intent(text):
    """檢測是否想查看功能選單（已統一到 detect_help_intent）"""
    # 為了向後兼容保留此函數，但直接調用 detect_help_intent
    return detect_help_intent(text)

def analyze_emoji_emotion(text):
    """分析文字中的表情符號情緒"""
    emoji_emotions = {
        '😊': 'happy', '😃': 'happy', '😄': 'happy', '🙂': 'happy', '😁': 'happy',
        '😢': 'sad', '😭': 'sad', '😔': 'sad', '☹️': 'sad',
        '😡': 'angry', '😠': 'angry', '💢': 'angry',
        '💪': 'motivated', '✊': 'motivated', '🔥': 'motivated',
        '❤️': 'love', '💕': 'love', '💖': 'love', '😍': 'love',
        '😴': 'tired', '😪': 'tired', '🥱': 'tired',
        '👍': 'approval', '👏': 'approval', '🙌': 'approval',
        '🤔': 'thinking', '🧐': 'thinking',
    }
    
    for emoji, emotion in emoji_emotions.items():
        if emoji in text:
            return emotion
    return None

def get_emoji_response(emotion):
    """根據表情符號情緒回應"""
    responses = {
        'happy': "看到你這麼開心，我也跟著開心起來了！讚喔！繼續保持這份好心情！",
        'sad': "我看到你好像有點難過...沒關係的，不開心的事情都會過去的！我會一直陪著你！你一定可以的！",
        'angry': "我感覺到你有點生氣了...深呼吸，冷靜一下。有什麼我可以幫忙的嗎？說出來會好一點喔！",
        'motivated': "看到你的鬥志了！超棒的！就是這股精神！繼續加油！你一定可以做到的！讚喔！",
        'love': "感受到滿滿的愛！❤️ 真的很棒！愛能讓世界更美好！讚喔！",
        'tired': "看起來你有點累了...要不要休息一下？記得多喝水、好好休息喔！身體健康最重要！",
        'approval': "謝謝你的肯定！👍 有你的支持我更有動力了！讚喔！有任何需要都可以找我！",
        'thinking': "我看到你在思考...很好！仔細思考是很棒的習慣！有什麼問題想討論的嗎？我很樂意幫忙！讚喔！",
    }
    return responses.get(emotion, "收到你的訊息了！讚喔！有什麼我可以幫忙的嗎？")

def get_function_menu():
    """返回功能選單文字（與功能總覽統一）"""
    return """🌟 功能總覽與使用教學 🌟

1️⃣ 🖼️ 製作圖片
👉 請說：「幫我畫一隻貓」或「生成風景圖」

2️⃣ 👴 製作長輩圖
👉 請說：「我要做長輩圖」或「製作早安圖」

3️⃣ 📰 看新聞
👉 請說：「看新聞」或「今日新聞」

4️⃣ 🎨 更改圖片
👉 上傳照片後說：「把衣服改成紅色」、「改成水彩風格」

5️⃣ 🔗 連結查證
👉 貼上任何連結，我會幫你摘要內容並查證是否可信

6️⃣ ⏰ 設定提醒
👉 請說：「提醒我明天8點吃藥」
👉 輸入「刪除提醒」可清除所有待辦

7️⃣ 🗺️ 行程規劃
👉 請說：「規劃宜蘭一日遊」

8️⃣ 💬 聊天解悶
👉 隨時都可以跟我聊天喔！

⚠️ 重要提醒：
• 🚫 遇到任何狀況想停止，請輸入「取消」
• ⏱️ 生成期間約15秒，請勿同時傳送多則訊息
• 💾 記憶維持七天，輸入「清除記憶」可重置"""

# ======================
# 連結查證與新聞功能
# ======================



def extract_url(text):
    """從文字中提取 URL"""
    import re
    url_pattern = r'https?://[^\s<>"\']+'
    urls = re.findall(url_pattern, text)
    return urls[0] if urls else None

def extract_domain(url):
    """從 URL 中提取網域名稱"""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except:
        return None

def check_trusted_media(domain):
    """檢查是否為台灣可信賴新聞媒體"""
    trusted_domains = [
        'cna.com.tw',  # 中央社
        'pts.org.tw',  # 公視
        'udn.com',     # 聯合新聞網
        'ltn.com.tw',  # 自由時報
        'chinatimes.com',  # 中國時報
        'ettoday.net', # ETtoday
        'storm.mg',    # 風傳媒
        'setn.com',    # 三立新聞
        'tvbs.com.tw', # TVBS
        'nownews.com', # 今日新聞
        'rti.org.tw',  # 中央廣播電台
        'bcc.com.tw',  # 中國廣播公司
    ]
    
    return any(td in domain.lower() for td in trusted_domains)

def get_domain_age(url):
    """
    取得網域註冊天數
    返回: 天數 (int) 或 None (如果查詢失敗)
    """
    # 簡化：直接返回 None，不執行 whois 查詢（避免 datetime 錯誤）
    # 域名年齡檢查不是關鍵功能，且容易出錯
    return None

def quick_safety_check(url):
    """
    快速安全檢查
    返回: {'level': 'safe'|'warning'|'danger', 'risks': [...], 'is_trusted': bool}
    """
    risks = []
    domain = extract_domain(url)
    
    if not domain:
        return {'level': 'warning', 'risks': ['無法解析網址'], 'is_trusted': False}
    
    # 檢查 1: 台灣新聞媒體白名單
    is_trusted = check_trusted_media(domain)
    
    # 檢查 2: 網域年齡
    domain_age = get_domain_age(url)
    if domain_age is not None:
        if domain_age < 90:  # 少於 3 個月
            risks.append(f"網域註冊僅 {domain_age} 天")
        elif domain_age < 180:  # 少於 6 個月
            risks.append(f"網域較新 ({domain_age} 天)")
    
    # 檢查 3: 不在白名單 (僅做標記，不列為風險)
    # if not is_trusted:
    #     risks.append("不在台灣合法新聞媒體清單")
    
    # 檢查 4: 可疑關鍵字
    suspicious_keywords = ['震驚', '必看', '不看後悔', '驚爆', '獨家爆料', '絕密', '免費送', '限時領取']
    if any(kw in url for kw in suspicious_keywords):
        risks.append("網址包含聳動用詞")
    
    # 決定風險等級
    if len(risks) >= 1:
        level = 'warning'  # 只要有任何風險（如網域太新、有關鍵字）就警告
    else:
        level = 'safe'     # 否則視為一般連結
    
    return {
        'level': level,
        'risks': risks,
        'is_trusted': is_trusted
    }

def format_verification_result(safety_check, url):
    """格式化查證結果"""
    domain = extract_domain(url)
    
    if safety_check['level'] == 'danger':
        return f"""🚨 危險！這個連結風險很高！

⛔ 強烈建議不要點擊此連結！

發現問題：
{''.join(['• ' + risk + '\\n' for risk in safety_check['risks']])}
💡 這可能是詐騙或假新聞網站，請小心！

如果你想了解更多，我可以幫你查證這個連結的內容。"""
    
    elif safety_check['level'] == 'warning':
        return f"""⚠️ [警示] 此連結存在潛在風險
        
風險指標：
{''.join(['• ' + risk + '\\n' for risk in safety_check['risks']])}

建議操作：
1️⃣ 查證 (分析真實性)
2️⃣ 閱讀 (摘要內容)

請回覆「查證」或「閱讀」。"""
    
    else:
        # 安全或未知連結，直接提供選項 (保持嚴肅專業)
        return f"""收到連結！
🔗 {domain}

請問您想要：

1️⃣ 📖 【閱讀內容】
   👉 幫您詳細整理網頁重點與細節

2️⃣ 🔍 【查證內容】
   👉 檢查內容真實性與詐騙風險

請回覆「1」或「2」，也可以說「閱讀」或「查證」喔！"""

def fetch_webpage_content(url):
    """
    抓取網頁內容
    返回: 網頁文字內容 (str) 或 None
    """
    try:
        from bs4 import BeautifulSoup
        import requests
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # 忽略 SSL 警告（針對某些憑證無效的網站）
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 移除 script 和 style 標籤
        for script in soup(["script", "style"]):
            script.decompose()
        
        # 取得文字
        text = soup.get_text()
        
        # 清理空白
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        # 限制長度 (避免太長)
        if len(text) > 5000:
            text = text[:5000] + "..."
        
        return text
    except Exception as e:
        print(f"Fetch webpage error: {e}")
        return None

def summarize_content(content, user_id):
    """使用 Gemini 深度整理網頁內容 (嚴肅模式)"""
    try:
        # 使用 functional model 以確保客觀嚴肅，不講笑話
        prompt = f"""
        [SYSTEM: STRICT CONCISE SUMMARY]
        Please summarize the following content for an elderly user.
        
        Content:
        {content[:4000]}
        
        Rules:
        1. **Objective & Serious**: NO jokes, NO "Hello elders", NO emoji spam.
        2. **Length**: Concise (approx 150-250 words).
        3. **Format**: Human-readable text (NOT JSON).
        
        Output Format:
        
        📖 **內容摘要**
        (Summary)
        
        💡 **重點整理**
        (3 bullet points)
        """
        
        response = model_functional.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Summarize error: {e}")
        return "抱歉，我無法讀取這個網頁的內容。可能是網站有防護機制。"

def fetch_latest_news():
    """
    抓取最新新聞 (使用 RSS)
    返回: 新聞列表 (list of dict)
    """
    try:
        import feedparser
        from datetime import datetime, timedelta
        
        # 檢查快取 (5 分鐘內不重複抓取)
        if news_cache['data'] and news_cache['timestamp']:
            if datetime.now() - news_cache['timestamp'] < timedelta(minutes=5):
                return news_cache['data']
        
        feeds = [
            'https://news.ltn.com.tw/rss/all.xml',  # 自由時報 - 所有新聞
            'https://newtalk.tw/rss/all',  # 新頭殼 - 所有新聞
            'https://feeds.feedburner.com/rsscna/politics',  # 中央社 - 政治
            'https://feeds.feedburner.com/rsscna/finance',  # 中央社 - 財經
            'https://udn.com/rssfeed/news/2/6638?ch=news',  # 聯合報 - 即時新聞
        ]
        
        news_items = []
        import random
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0'
        ]

        for feed_url in feeds:
            try:
                # [FIX] Rotate User-Agent and disable SSL verify for problematic feeds
                headers = {'User-Agent': random.choice(user_agents)}
                response = requests.get(feed_url, headers=headers, timeout=10, verify=False)
                if response.status_code == 200:
                    feed = feedparser.parse(response.content)
                    for entry in feed.entries[:10]:  # 增加到 10 則，確保有足夠新聞供挑選
                        # [FIX] 增強內容抓取邏輯 (避免 News ID 錯誤或空白)
                        raw_summary = entry.get('summary', '') or entry.get('description', '')
                        # 如果 summary 還是空的，嘗試 content
                        if not raw_summary and 'content' in entry:
                             raw_summary = entry.get('content', [{'value': ''}])[0]['value']
                        
                        # 清理 HTML 標籤
                        import re
                        clean_summary = re.sub('<[^<]+?>', '', raw_summary).strip()
                        
                        # [FIX] 如果真的都沒內容，使用標題作為摘要 (Fallback to Title)
                        if not clean_summary or len(clean_summary) < 5:
                            # 優先使用 Description (如果不同於 Summary)
                            desc = entry.get('description', '')
                            if desc and len(desc) > 5:
                                clean_summary = re.sub('<[^<]+?>', '', desc).strip()
                            else:
                                clean_summary = entry.get('title', '')
                            # print(f"[DEBUG] Fallback to Title/Desc: {clean_summary[:20]}...")
                            
                        # If still empty (no title?), skip
                        if not clean_summary:
                            # print(f"[DEBUG] News Skipped (No Content): {entry.get('title', 'Unknown')}")
                            continue

                        news_items.append({
                            'title': entry.title,
                            'summary': clean_summary[:100] + "..." if len(clean_summary) > 100 else clean_summary,
                            'link': entry.link,
                            'published': entry.get('published', '')
                        })
                else:
                    print(f"Feed error {feed_url}: {response.status_code}")
            except Exception as e:
                print(f"Feed parse error for {feed_url}: {e}")
                continue
        
        # 更新快取
        news_cache['data'] = news_items
        news_cache['timestamp'] = datetime.now()
        
        return news_items
    except Exception as e:
        print(f"Fetch news error: {e}")
        return []

def detect_news_intent(text):
    """檢測是否想查詢新聞"""
    keywords = ['新聞', '消息', '最新', '頭條', '報導', '發生什麼', '看新聞', '聽新聞', '今日新聞', '新聞快報', '最近新聞', '語音', '播報', '唸給我聽']
    return any(keyword in text for keyword in keywords)

def generate_news_summary():
    """生成新聞摘要"""
    news_items = fetch_latest_news()
    
    if not news_items:
        return "抱歉，目前無法取得新聞資訊。請稍後再試！"
    
    # 使用 Gemini 摘要新聞
    try:
        # 使用較少的新聞項目 (前 15 則) 加速處理
        # 為了確保連結正確，我們建立索引映射
        indexed_news = []
        for i, item in enumerate(news_items[:15], 1):
             indexed_news.append(f"[{i}] 標題: {item['title']}\n內容: {item['summary']}")
        
        news_text = "\n\n".join(indexed_news)
        
        prompt = f"""
請摘要以下新聞，挑選7則最重要的。

{news_text}

摘要規則：
1. 每則 60-70 字
2. 保留原文的日期和數字（如：4日、1.5小時、100萬）
3. 不要省略任何數字

格式：
📰 今日新聞摘要

1️⃣ [ID] 【標題】
   摘要內容（70-90字）

... 7則 ...
"""
        generation_config = genai.types.GenerationConfig(
            temperature=0.1,
        )
        
        
        # 查新聞使用 Flash 模型（快速回報），語音播報才用 Pro
        response = model_functional.generate_content(prompt, generation_config=generation_config)
        print("[INFO] Using Flash for news summary (fast display)")
        
        # DEBUG: 檢查 AI 輸出是否包含數字
        import re
        ai_output = response.text.strip()
        has_numbers = bool(re.search(r'\d', ai_output))
        print(f"[DEBUG] AI news output has numbers: {has_numbers}")
        if not has_numbers:
            print(f"[WARNING] AI removed all numbers! First 300 chars: {ai_output[:300]}")

        
        final_text = ai_output
        
        # Post-process: Replace [ID] with actual links
        import re
        lines = final_text.split('\n')
        processed_lines = []
        
        current_link = ""
        
        for line in lines:
            # Check for ID pattern like "1️⃣ [5] 【標題】" or just "[5]"
            # Regex to find [ID]
            match = re.search(r'\[(\d+)\]', line)
            if match:
                try:
                    idx = int(match.group(1)) - 1 # 0-indexed
                    if 0 <= idx < len(news_items):
                        current_link = news_items[idx]['link']
                        # Remove the [ID] tag from the display text
                        line = line.replace(f"[{match.group(1)}]", "")
                    else:
                        current_link = ""
                except:
                    current_link = ""
            
            processed_lines.append(line)
            
            # append link after summary block (usually detecting empty line or next number?)
            # strategy: simply append link immediately after the title line? 
            # Or better: The format implies title line, then summary.
            # Let's simplify: Just append the link to the NEXT line if we found an ID.
            if current_link and "【" in line:
                 processed_lines.append(f"   🔗 來源：{current_link}")
                 current_link = "" # reset
        
        final_text = "\n".join(processed_lines)

        # 強制附加語音引導 (如果 AI 沒加)
        if "語音" not in final_text[-50:]:
            final_text += "\n\n💡 想聽語音播報？回覆「語音」即可"
            
        return final_text
    except Exception as e:
        print(f"News summary error: {e}")
        return "抱歉，新聞摘要生成失敗。請稍後再試！"

def generate_news_audio(text, user_id):
    """
    生成新聞語音播報
    返回: 音檔路徑 (str) 或 None
    """
    try:
        # 使用 Google Cloud TTS (免費額度)
        from google.cloud import texttospeech
        
        global tts_client
        if tts_client is None:
            tts_client = texttospeech.TextToSpeechClient()
        client = tts_client
        
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="zh-TW",
            name="cmn-TW-Wavenet-A"  # 台灣女聲
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        # 儲存音檔
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        audio_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_news.mp3")
        with open(audio_path, 'wb') as f:
            f.write(response.audio_content)
        
        return audio_path
    except Exception as e:
        print(f"TTS error: {e}")
        return None


def generate_image_with_imagen(prompt, user_id, base_image_path=None):
    """使用 Imagen 3 生成圖片 (支援 Text-to-Image 和 Image-to-Image 編輯)
    並統一在這裡進行配額（Quota）的扣除與檢查，確保所有衍生功能都有防護。
    
    Args:
        prompt (str): 提示詞
        user_id (str): 用戶ID
        base_image_path (str, optional): 原圖路徑，若提供則進行 Image-to-Image 編輯
    
    Returns:
        tuple: (成功與否, 圖片路徑或錯誤訊息)
    """
    
    # 1. 檢查配額 (若額度用盡，直接回傳 False 及錯誤訊息)
    if user_id:
        quota_ok, remain, quota_msg = check_image_quota(user_id)
        if not quota_ok:
            return False, quota_msg
    
    try:
        # 初始化 Vertex AI
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = "us-central1"
        
        aiplatform.init(project=project_id, location=location)
        
        # 使用 Imagen 3 生成圖片
        from vertexai.preview.vision_models import ImageGenerationModel, Image
        import time
        
        # [Fix] Update to Imagen 3 (Imagen 2 is EOL)
        # imagen-3.0-generate-001 or imagen-3.0-capability-001
        model_name = "imagen-3.0-generate-001"
            
        try:
            imagen_model = ImageGenerationModel.from_pretrained(model_name)
        except Exception as e:
             print(f"[IMAGEN] Failed to load {model_name}: {e}. Trying capability model...")
             imagen_model = ImageGenerationModel.from_pretrained("imagen-3.0-capability-001")

        # 優化提示詞（加入品質關鍵字）
        enhanced_prompt = f"{prompt}, high quality, detailed, vibrant colors"
        
        # Retry logic for 429/503 errors
        max_retries = 3
        retry_delay = 2
        
        fallback_edit_model_tried = False
        
        for attempt in range(max_retries + 1):
            try:
                if base_image_path and os.path.exists(base_image_path):
                    # Image-to-Image (Editing) Mode
                    # ... (keep existing code)
                    print(f"[IMAGEN] Editing image with base: {base_image_path}")
                    base_img = Image.load_from_file(base_image_path)
                    try:
                        # [Fix] SDK Update: edit_images -> edit_image (Singular) for version 1.133+
                        response = imagen_model.edit_image(
                            prompt=prompt,
                            base_image=base_img,
                            number_of_images=1,
                            # guidance_scale=15.0, # Optional
                        )
                        generated_image = response.images[0]
                    except Exception as edit_e:
                        print(f"[IMAGEN] edit_image failed: {edit_e}. Falling back to generate_images.")
                        # If edit_image fails, try generate_images as a fallback
                        response = imagen_model.generate_images(
                            prompt=enhanced_prompt,
                            number_of_images=1,
                            aspect_ratio="1:1",
                        )
                        generated_image = response.images[0]
                else:
                    # Text-to-Image (Generation) Mode
                    # ... (keep existing code)
                    print(f"[IMAGEN] Generating new image")
                    response = imagen_model.generate_images(
                        prompt=enhanced_prompt,
                        number_of_images=1,
                        aspect_ratio="1:1",
                    )
                
                # ... (keep response handling)
                if hasattr(response, 'images'):
                    images = response.images
                else:
                    images = response
                
                if not images or len(images) == 0:
                    print(f"API returned no images. Response object: {response}")
                    raise ValueError("API returned no images (potentially blocked by safety filters)")
                
                # 儲存圖片
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                image_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_generated_{int(time.time()*1000)}.png")
                images[0].save(location=image_path)
                
                # 生圖成功，扣除配額
                if user_id:
                    increment_image_quota(user_id)
                    
                return (True, image_path)

            except Exception as e:
                error_str = str(e)
                print(f"API Error (Attempt {attempt+1}/{max_retries}): {error_str}")
                
                 # 如果是編輯模式失敗（例如模型不支援、404錯誤等）
                if base_image_path:
                     print(f"[IMAGEN] Edit failed with error: {error_str}")
                     
                     # [Fix] Removed deprecated 005 fallback
                     # Proceed directly to Text-to-Image fallback if editing fails
                     pass
                     
                     # If 005 also failed (or switch failed), fallback to Generation
                     print("[IMAGEN] Falling back to text-to-image generation AND switching to Imagen 3...")
                     base_image_path = None # Disable editing for next retry
                     
                     # 切換到 Imagen 3 (生成專用模型)
                     try:
                         imagen_model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
                         print("[IMAGEN] Successfully switched to Imagen 3")
                     except Exception as switch_e:
                         print(f"[IMAGEN] Failed to switch model: {switch_e}")
                     
                     continue

                # 只有在遇到暫時性錯誤時才重試
                is_retryable = any(code in error_str for code in ["429", "503", "500", "ResourceExhausted", "ServiceUnavailable"])
                
                if is_retryable and attempt < max_retries:
                    print(f"Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    raise e  # 超過重試次數或非暫時性錯誤，拋出異常
        
        raise ValueError("Unknown error: loop finished without success")
        
    except Exception as e:
        error_str = str(e)
        print(f"Image generation error: {error_str}")
        
        # 解析錯誤原因
        if "safety" in error_str.lower() or "policy" in error_str.lower():
            reason = "內容不符合安全政策（可能涉及暴力、成人內容或其他敏感主題）"
        elif "429" in error_str or "quota" in error_str.lower() or "limit" in error_str.lower() or "resourceexhausted" in error_str.lower():
            reason = "系統目前繁忙，API 請求次數過多。請稍待一分鐘後再試！"
        elif "invalid" in error_str.lower() or "bad" in error_str.lower():
            reason = "描述格式無效或包含不支援的內容"
        elif "timeout" in error_str.lower():
            reason = "請求超時，請稍後再試"
        else:
            reason = f"API 錯誤：{error_str[:100]}"  # 只顯示前100字
        
        return (False, reason)




def get_font_path(font_type):
    """取得字體路徑，自動下載 Google Fonts (支援 Linux/Zeabur)"""
    import os
    import requests
    
    # 定義字體目錄
    font_dir = os.path.join(os.getcwd(), "static", "fonts")
    os.makedirs(font_dir, exist_ok=True)
    
    # 字體映射 (備份檔案邏輯 + 雲端支援)
    # 優先檢查 Windows 本地字體 (開發環境)
    # 使用微軟正黑體粗體 (msjhbd.ttc) 作為主要字體，解決字體過細問題
    win_paths = {
        'msjh': "C:\\Windows\\Fonts\\msjhbd.ttc",   # 改用粗體
        'heiti': "C:\\Windows\\Fonts\\msjhbd.ttc",  # 改用粗體
        'kaiti': "C:\\Windows\\Fonts\\msjhbd.ttc",  # 改用正黑體粗體 (因為標楷體 kaiu.ttf 太細)
        'ming': "C:\\Windows\\Fonts\\mingliu.ttc"
    }
    
    # 如果是 Windows 且檔案存在，直接回傳
    if os.name == 'nt':
        win_path = win_paths.get(font_type)
        # 如果粗體不存在，退回一般體
        if win_path and not os.path.exists(win_path):
            win_path = win_path.replace("bd.ttc", ".ttc")
            
        if win_path and os.path.exists(win_path):
            return win_path

    # Linux/Cloud 環境：使用 Free Google Fonts (TTF)
    # 使用 NotoSerifTC (楷體/明體替代品) 和 NotoSansTC (黑體替代品)
    cloud_font_map = {
        'kaiti': 'NotoSansTC-Bold.otf',  # 改用 NotoSansTC-Bold (因為 NotoSerifTC-Regular 太細)
        'heiti': 'NotoSansTC-Bold.otf',
        'ming': 'NotoSerifTC-Regular.otf',
        'default': 'NotoSansTC-Regular.otf'
    }
    
    # 這裡改用 Google Fonts 公開的其他穩定源，或者使用 Noto CJK 的 TTF 版本
    # 為了避免 complex OTF 問題，我們改下載 .ttf (雖然 Noto TC 很多是 OTF, 但我們試試看能否找到 TTF 或 Variable Font)
    # 更新：直接使用 Google Fonts 的 raw github 連結通常是 OTF (對於 CJK)。
    # 錯誤 "unknown file format" 通常是因為下載下來的不是字體檔 (例如 404 HTML)。
    # 我們改用一個更確定的 URL。
    
    target_filename = cloud_font_map.get(font_type, cloud_font_map['default'])
    local_font_path = os.path.join(font_dir, target_filename)
    
    if os.path.exists(local_font_path):
        return local_font_path
        
    print(f"[FONT] Downloading {target_filename} for cloud environment...")
    
    # 修正下載連結：確認這些連結是有效的 raw file
    # Noto Sans TC (OFL)
    base_url = "https://github.com/google/fonts/raw/main/ofl"
    
    # 對應表
    # 改用更穩定的 CDN (jsdelivr via github)
    # 使用 Google Fonts 的 raw github 連結通常是 OTF (對於 CJK)。
    # 我們改用一個更確定的 URL (來自第三方穩定的字體託管，或直接指向 Google Fonts 指定 commit)
    
    urls = {
        # 使用 Noto Sans TC Bold (Static) - 來自 Google Fonts repo 的特定 commit (確保文件存在)
        'NotoSansTC-Bold.otf': "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf", 
        # 暫時維持 Variable Font, 但我們會用 Fake Bold 增強
        'NotoSansTC-Regular.otf': "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf",
        'NotoSerifTC-Regular.otf': "https://github.com/google/fonts/raw/main/ofl/notoseriftc/NotoSerifTC%5Bwght%5D.ttf"
    }
    
    # 因為我們改用 TTF，所以要把 local_font_path 的副檔名也改掉，避免混淆
    local_font_path = local_font_path.replace(".otf", ".ttf")
    
    url = urls.get(target_filename)
    if not url: return None
    
    try:
        print(f"[FONT] Attempting to download from {url}...")
        # 模擬瀏覽器 User-Agent 避免被阻擋
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=30) 
        
        if r.status_code == 200 and len(r.content) > 1000: # 確保不是空的或錯誤頁面
            with open(local_font_path, 'wb') as f:
                f.write(r.content)
            print(f"[FONT] Successfully downloaded {local_font_path}, size: {len(r.content)} bytes")
            return local_font_path
        else:
            print(f"[FONT] Download failed. Code: {r.status_code}, Content-Type: {r.headers.get('Content-Type')}")
        return None
    except Exception as e:
        print(f"[FONT] Download exception: {e}")
        return None

def create_meme_image(bg_image_path, text, user_id, font_type='kaiti', font_size=60, position='top', color='white', angle=0, stroke_width=12, stroke_color=None, decorations=None):
    """製作長輩圖（創意版 - 支援彩虹、波浪、大小變化、描邊等效果 + 裝飾元素）"""
    try:
        import random
        import math
        
        # 開啟背景圖片
        img = Image.open(bg_image_path)
        
        # 調整大小（如果太大）
        max_size = (800, 800)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # 轉換為 RGBA 以支援透明圖層
        img = img.convert('RGBA')
        
        # 載入字體 (使用 helper 解決跨平台問題)
        try:
            # 支援粗體選擇 (如果 font_type='bold')
            font_path = get_font_path(font_type)
            if font_path:
                try:
                    base_font = ImageFont.truetype(font_path, font_size)
                    # 嘗試設定 Variable Font 的粗體 (如果支援)
                    try:
                        base_font.set_variation_by_name('Bold')
                    except:
                        pass
                except Exception as e:
                    print(f"[FONT] Error loading specific font: {e}")
                    base_font = ImageFont.load_default()
            else:
                # Fallback
                base_font = ImageFont.load_default()
                print("[FONT] Warning: Using default font (Chinese may be missing)")
        except Exception as e:
            print(f"[FONT] Error loading font: {e}")
            base_font = ImageFont.load_default()
        
        # 顏色處理
        fill_color = color
        is_rainbow = (color == 'rainbow')
        
        if not is_rainbow:
            # 如果是hex碼（如 #FFD700）直接使用，否則嘗試顏色名稱
            if color.startswith('#') and len(color) in [4, 7]:
                fill_color = color
            else:
                # 基本顏色對照表
                basic_colors = {
                    'white': '#FFFFFF', 'yellow': '#FFFF00', 'red': '#FF4444',
                    'cyan': '#00FFFF', 'lime': '#00FF00', 'gold': '#FFD700',
                    'orange': '#FFA500', 'magenta': '#FF00FF', 'pink': '#FF69B4',
                    'deeppink': '#FF1493', 'hotpink': '#FF69B4',
                    'black': '#000000', 'blue': '#0000FF', 'green': '#008000',
                    'purple': '#800080', 'brown': '#A52A2A', 'grey': '#808080',
                    'gray': '#808080', 'silver': '#C0C0C0', 'navy': '#000080',
                    'teal': '#008080', 'olive': '#808000', 'maroon': '#800000'
                }
                # [Fix] If color name is unknown, use a random color instead of fixed Gold
                # to prevent "always yellow" issue when AI invents color names.
                import random
                fallback_keys = list(basic_colors.values()) + ['#FFD700', '#FF0000', '#FFFFFF']
                fill_color = basic_colors.get(color.lower(), random.choice(fallback_keys))

        # 🌈 彩虹色彩組（高對比鮮豔色）
        rainbow_colors = [
            '#FF6B6B', '#FFE66D', '#4ECDC4', '#45B7D1', 
            '#96CEB4', '#FF8C42', '#D4A5A5', '#9B59B6'
        ]
        
        # 創建文字圖層
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        txt_draw = ImageDraw.Draw(txt_layer)
        
        # 計算起始位置
        padding = 40  # 用戶希望文字邊界加大 (30->40)
        

        # -------------------------------------------------------
        # 使用文字自動換行與縮放邏輯 (Shrink to Fit) - 智慧分詞版
        # -------------------------------------------------------
        max_width = img.width - (padding * 2)
        
        # 嘗試載入 jieba，若失敗則退回字元級切割
        try:
            import jieba
            has_jieba = True
        except ImportError:
            has_jieba = False
            print("[TEXT] Jieba not found, using character-level wrapping.")

        # 預處理：先依據手動換行符號切割段落
        paragraphs = text.split('\n')
        
        # 循環直到文字寬度符合要求或字體太小
        lines = []
        while font_size >= 20: # 最小字體限制
            
            try:
                calc_font_size = font_size + 8
                calc_font = ImageFont.truetype(font_path, calc_font_size)
            except:
                calc_font = base_font
                
            lines = []
            
            for para in paragraphs:
                if not para: # 空行
                    lines.append("")
                    continue
                
                # 使用 jieba 分詞 (如果有的話)
                if has_jieba:
                    words = list(jieba.cut(para))
                else:
                    words = list(para) # Fallback to chars
                
                current_line_text = ""
                current_w = 0
                
                for word in words:
                    # 計算單詞寬度
                    bbox = txt_draw.textbbox((0, 0), word, font=calc_font)
                    word_w = bbox[2] - bbox[0]
                    
                    # 處理單詞本身就超長的情況 (強制切斷)
                    if word_w > max_width:
                        # 如果當前行已經有內容，先換行
                        if current_line_text:
                            lines.append(current_line_text)
                            current_line_text = ""
                            current_w = 0
                        
                        # 將超長單詞依字元切割
                        for char in word:
                            char_bbox = txt_draw.textbbox((0, 0), char, font=calc_font)
                            char_w = char_bbox[2] - char_bbox[0]
                            if current_w + char_w > max_width:
                                lines.append(current_line_text)
                                current_line_text = char
                                current_w = char_w
                            else:
                                current_line_text += char
                                current_w += char_w
                        continue

                    # 一般單詞處理
                    if current_w + word_w > max_width:
                        lines.append(current_line_text)
                        current_line_text = word
                        current_w = word_w
                    else:
                        current_line_text += word
                        current_w += word_w
                
                if current_line_text:
                    lines.append(current_line_text)
                
            # 計算總高度檢查
            total_h = len(lines) * int(font_size * 1.3)
            
            # [Fix] 針對短文字 (20字以內)，盡量縮小字體以維持在單行 (User Request)
            # 如果文字較短，且被折行了，且字體還夠大(>30)，就縮小字體重試
            if len(lines) > 1 and len(text) < 20 and font_size > 30:
                # 檢查是否原本就有換行符 (如有手動換行則不強制單行)
                if '\n' not in text:
                    font_size -= 5
                    continue

            if total_h > (img.height - padding * 1.5):
                font_size -= 5
                continue
            
            # 成功排版
            break
            
        # 更新 base_font 為最終決定的 font_size
        try:
            base_font = ImageFont.truetype(font_path, font_size)
        except:
            base_font = ImageFont.load_default()
            
        # 計算整個區塊的高度
        line_height = int(font_size * 1.2)
        total_block_height = len(lines) * line_height
        
        # 根據 position 計算區塊起始 Y
        if position == 'bottom':
            start_y = img.height - total_block_height - padding
        elif position == 'top' or position == 'top-left' or position == 'top-right':
            start_y = padding
        elif position == 'bottom-left' or position == 'bottom-right':
            start_y = img.height - total_block_height - padding
        else:  # center
            start_y = (img.height - total_block_height) / 2
            
        # 開始繪製每一行
        current_y = start_y
        
        for line_chars in lines:
            # 計算該行總寬 (用來決定 X 起始點)
            line_str = "".join(line_chars)
            # 重新精算寬度
            w = 0
            char_ws = []
            for c in line_chars:
                # 使用加大版的 calc_font 來計算寬度，確保不會被切掉
                bb = txt_draw.textbbox((0,0), c, font=calc_font)
                cw = (bb[2] - bb[0]) + 5 # 額外+5px間距
                char_ws.append(cw)
                w += cw
                
            if position == 'top-left' or position == 'bottom-left':
                current_x = padding
            elif position == 'top-right' or position == 'bottom-right':
                current_x = img.width - w - padding
            else: # center, top, bottom 都是水平置中
                current_x = (img.width - w) / 2
            
            # 逐字繪製該行
            for i, char in enumerate(line_chars):
                # 📏 大小變化 - 首尾字稍大 (僅第一行首和最後一行尾)
                # 這裡简化效果，避免排版亂掉，只做隨機微調
                char_size = font_size + random.randint(-2, 2)
                
                try:
                    char_font = ImageFont.truetype(font_path, char_size)
                except:
                    char_font = base_font
                
                # 🌈 顏色
                if is_rainbow:
                    char_color = rainbow_colors[random.randint(0, len(rainbow_colors)-1)]
                else:
                    char_color = fill_color
                
                # 🌊 波浪 + 📐 微旋轉
                wave_offset = math.sin(current_x * 0.05) * 5
                char_angle = random.uniform(-5, 5)
                
                char_real_y = current_y + wave_offset
                
                # 創建單字圖層 - 關鍵修復：加大畫布以防文字裁切 (Glyph Truncation)
                char_bbox = txt_draw.textbbox((0, 0), char, font=char_font)
                raw_w = char_bbox[2] - char_bbox[0]
                raw_h = char_bbox[3] - char_bbox[1]
                
                # 畫布大小：字寬的 3 倍，加上超大緩衝，確保旋轉也不會切到
                canvas_w = int(raw_w * 3 + 100)
                canvas_h = int(raw_h * 3 + 100)
                
                char_layer = Image.new('RGBA', (canvas_w, canvas_h), (255, 255, 255, 0))
                cd = ImageDraw.Draw(char_layer)
                
                # 計算中心點
                center_x, center_y = canvas_w // 2, canvas_h // 2
                # 由於 draw.text 的座標是左上角，我們需要 offset
                # 簡單置中：減去字寬字高的一半
                text_x = center_x - (raw_w / 2)
                text_y = center_y - (raw_h / 2)
                
                # 描邊處理 (AI 決定)
                if stroke_width > 0:
                    effective_stroke_color = stroke_color if stroke_color else '#000000'
                    # 繪製文字 (多次繪製以模擬粗體，如果字體本身不夠粗)
                    # 這是 "Fake Bold" 技巧：上下左右偏移 1px
                    # 只有在非 Windows 環境 (Cloud) 且 Stroke width 較小時才啟用，避免太粗
                    if os.name != 'nt' and font_size > 30: 
                        # 強力加粗邏輯 (Double-Pass Rendering)
                        # 1. 計算加粗量 (介於中間值，約字體大小的 1/28，例如 100px -> 3px)
                        bold_sim_width = max(1, int(font_size / 28))
                        
                        # 2. 繪製底部輪廓 (總寬度 = 用戶描邊 + 加粗量)
                        # Pass 1: Draw Thick Outline (Border + Boldness)
                        cd.text((text_x, text_y), char, font=char_font, fill=char_color, 
                               stroke_width=stroke_width + bold_sim_width, stroke_fill=effective_stroke_color)
                               
                        # 3. 繪製文字本體 (加粗量) -> 這會讓白色部分變粗，蓋掉內縮的黑色描邊
                        # Pass 2: Draw Thick Body (Boldness)
                        cd.text((text_x, text_y), char, font=char_font, fill=char_color, 
                               stroke_width=bold_sim_width, stroke_fill=char_color)
                               
                    else:
                        # Windows 環境或字體夠粗，直接標準描邊
                        cd.text((text_x, text_y), char, font=char_font, fill=char_color, stroke_width=stroke_width, stroke_fill=effective_stroke_color)
                else:
                    # 預設陰影 (如果沒描邊)
                    cd.text((text_x + 3, text_y + 3), char, font=char_font, fill='#00000088')
                    cd.text((text_x, text_y), char, font=char_font, fill=char_color)
                
                # 旋轉
                if abs(char_angle) > 0.5:
                    char_layer = char_layer.rotate(char_angle, expand=False, resample=Image.Resampling.BICUBIC)
                
                # 貼上 - 需要計算從 center 回推到 top-left 的位置
                # 我們原本的 current_x 是希望文字出現的位置 (大約左側)
                # 貼上的位置應該是 current_x - (canvas_w - raw_w)/2 這樣... 比較複雜
                # 簡化：我們知道 char_layer 的中心就是文字中心
                # 目標中心點： current_x + raw_w/2, char_real_y + raw_h/2
                target_center_x = current_x + (raw_w / 2)
                target_center_y = char_real_y + (raw_h / 2)
                
                paste_x = int(target_center_x - (canvas_w / 2))
                paste_y = int(target_center_y - (canvas_h / 2))
                
                txt_layer.paste(char_layer, (paste_x, paste_y), char_layer)
                
                current_x += char_ws[i]
            
            # 換行
            current_y += line_height
        
        # 如果有整體旋轉角度
        if angle != 0:
            txt_layer = txt_layer.rotate(angle, expand=False, resample=Image.Resampling.BICUBIC)
        
        # 添加裝飾元素（emoji等）
        if decorations and isinstance(decorations, list):
            for deco in decorations:
                try:
                    char = deco.get('char', '❤️')
                    deco_pos = deco.get('position', 'top-right')
                    deco_size = deco.get('size', 60)
                    
                    # 計算裝飾位置
                    padding = 40
                    if deco_pos == 'top-right':
                        x = img.width - deco_size - padding
                        y = padding
                    elif deco_pos == 'top-left':
                        x = padding
                        y = padding
                    elif deco_pos == 'bottom-right':
                        x = img.width - deco_size - padding
                        y = img.height - deco_size - padding
                    elif deco_pos == 'bottom-left':
                        x = padding
                        y = img.height - deco_size - padding
                    else:
                        x = img.width // 2
                        y = img.height //2
                    
                    # 使用支援emoji的字體繪製裝飾
                    try:
                        # 嘗試找emoji字體（Noto Color Emoji或系統emoji字體）
                        emoji_font_path = get_font_path('heiti')  # 使用heiti作為備選
                        if emoji_font_path:
                            emoji_font = ImageFont.truetype(emoji_font_path, deco_size)
                        else:
                            emoji_font = ImageFont.load_default()
                    except:
                        emoji_font = ImageFont.load_default()
                    
                    # 創建裝飾圖層
                    deco_layer = Image.new('RGBA', (deco_size*2, deco_size*2), (255,255,255,0))
                    deco_draw = ImageDraw.Draw(deco_layer)
                    
                    # 繪製emoji（帶陰影）
                    # deco_draw.text((5, 5), char, font=emoji_font, fill='#00000044')  # 陰影
                    # deco_draw.text((3, 3), char, font=emoji_font, fill='white')  # 主體
                    
                    # 暫時移除裝飾繪製，避免出現不明符號 (方框/亂碼)
                    pass
                    
                    # 貼上裝飾
                    # txt_layer.paste(deco_layer, (int(x), int(y)), deco_layer)
                except Exception as de:
                    print(f"[DECORATION ERROR] {de}")
                    continue
        
        # 合併圖層
        img = Image.alpha_composite(img, txt_layer)
        img = img.convert('RGB')
        
        # 儲存
        meme_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_meme.png")
        img.save(meme_path)
        
        return meme_path
    except Exception as e:
        print(f"Meme creation error: {e}")
        import traceback
        traceback.print_exc()
        return None


def edit_image_with_gemini(edit_prompt, user_id, image_path1, image_path2=None):
    """使用 Gemini 圖片編輯模型修改/融合照片 (保留構圖與人物)
    
    Args:
        edit_prompt (str): 修改指令 (繁體中文或英文均可)
        user_id (str): 用戶ID
        image_path1 (str): 原圖路徑 (單張修改) 或 底圖路徑 (融合)
        image_path2 (str, optional): 第二張圖路徑 (融合模式)
    
    Returns:
        tuple: (成功與否, 圖片路徑或錯誤訊息)
    """
def gemini_edit_image_internal(edit_prompt, user_id, image_path1, image_path2=None):
    try:
        import time as _time
        from google import genai as genai_new
        from google.genai import types as genai_types
        
        # 使用支援圖片輸出的 Gemini Image 模型
        gemini_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        client = genai_new.Client(api_key=gemini_api_key)
        
        # 讀取圖片為 bytes
        with open(image_path1, "rb") as f:
            image1_bytes = f.read()
        
        # 判斷圖片 MIME type (不使用已廢棄的 imghdr)
        _ext1 = image_path1.rsplit('.', 1)[-1].lower()
        mime_type1 = 'image/jpeg' if _ext1 in ('jpg', 'jpeg') else f'image/{_ext1 or "png"}'
        
        # 建構 contents
        contents = []
        
        if image_path2:
            with open(image_path2, "rb") as f:
                image2_bytes = f.read()
            _ext2 = image_path2.rsplit('.', 1)[-1].lower()
            mime_type2 = 'image/jpeg' if _ext2 in ('jpg', 'jpeg') else f'image/{_ext2 or "png"}'
            
            full_prompt = f"""You are an expert photo compositor.
Task: Composite these two photos into one based on user's request.
User request: {edit_prompt}

CRITICAL RULES:
- Identify the main subject(s) from EACH photo and preserve them EXACTLY as they appear
- Do NOT add any new objects, characters, backgrounds, or elements that are NOT already present in Photo 1 or Photo 2
- Do NOT change the appearance, features, or colors of any subject
- Place the subjects together naturally according to the user's request
- Blend backgrounds naturally (prefer Photo 2's background or a natural blend)
- Maintain consistent lighting and scale between subjects
- Output a single, clean, realistic composite photo showing ONLY the original subjects from both photos"""
            contents = [
                full_prompt,
                genai_types.Part.from_bytes(data=image1_bytes, mime_type=mime_type1),
                genai_types.Part.from_bytes(data=image2_bytes, mime_type=mime_type2),
            ]
        else:
            # 判斷是否為風格轉換請求（水彩、卡通、油畫、動漫等全面風格）
            style_keywords = ['風格', '水彩', '卡通', '動漫', '油畫', '素描', '童話', '漫畫', '插畫', '賽璐璐',
                              '像素', '版畫', '水墨', '古風', '寫實', '抽象', '印象派', '變成卡通', '變成動漫',
                              'style', 'cartoon', 'anime', 'watercolor', 'painting', 'sketch', 'illustration']
            is_style_transfer = any(kw in edit_prompt for kw in style_keywords)
            
            if is_style_transfer:
                full_prompt = f"""You are an expert artistic style transfer editor.
Task: Apply the requested artistic style to this photo.
User request: {edit_prompt}

RULES FOR STYLE TRANSFORMATION:
- Apply the requested art style DRAMATICALLY and COMPLETELY to the entire image
- The style change should be clearly visible and striking (not subtle)
- Preserve the same composition, pose, and identity of the subjects (same people, same positions)
- The textures, colors, and rendering SHOULD change significantly - that is the whole point
- Do NOT be conservative - make the style change bold and obvious
- Output a high-quality styled image"""
            else:
                full_prompt = f"""You are an expert photo editor.
Task: Edit this photo as requested.
User request: {edit_prompt}

CRITICAL RULES:
- Preserve the EXACT same composition, layout, subject positions
- Preserve ALL people's faces, body, pose, clothing EXACTLY - do not change faces
- Only apply the specific change requested by the user
- Do NOT change background unless explicitly asked
- Output a high-quality, realistic edited photo"""
            contents = [
                full_prompt,
                genai_types.Part.from_bytes(data=image1_bytes, mime_type=mime_type1),
            ]
        
        print(f"[GEMINI_EDIT] Sending to gemini-2.5-flash-image, merge={image_path2 is not None}")
        
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=contents,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        
        # 從 response 中提取圖片 (含 debug logging)
        parts = response.candidates[0].content.parts
        print(f"[GEMINI_EDIT] Response parts count: {len(parts)}")
        for i, part in enumerate(parts):
            print(f"[GEMINI_EDIT] Part[{i}] has_inline_data={part.inline_data is not None}, has_text={bool(getattr(part, 'text', None))}")
            if part.inline_data:
                print(f"[GEMINI_EDIT] Part[{i}] mime_type={part.inline_data.mime_type}, data_len={len(part.inline_data.data) if part.inline_data.data else 0}")
                raw = part.inline_data.data
                if raw:
                    import base64
                    # data 可能已是 bytes，也可能是 base64 string
                    if isinstance(raw, (bytes, bytearray)):
                        image_data = raw
                    else:
                        image_data = base64.b64decode(raw)
                    
                    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                    out_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_gemini_edit_{int(_time.time()*1000)}.png")
                    with open(out_path, "wb") as f:
                        f.write(image_data)
                    
                    print(f"[GEMINI_EDIT] Success, saved to {out_path}")
                    return (True, out_path)
        
        # 若沒有圖片部分，印出 finish_reason 幫助診斷
        finish_reason = getattr(response.candidates[0], 'finish_reason', 'unknown')
        print(f"[GEMINI_EDIT] No image found in first attempt. finish_reason={finish_reason}")
        
        # 如果是 STOP (safety/policy)，嘗試用簡化英文 prompt 重試
        if image_path2 and str(finish_reason) in ('FinishReason.STOP', 'STOP'):
            print(f"[GEMINI_EDIT] Retrying with simplified English prompt...")
            retry_prompt = f"Photoshop composition: Take the main subject from image 1 and place it into the scene of image 2. Keep both subjects appearing exactly as they look in their original photos. No new elements. Natural lighting. Realistic result."
            retry_contents = [
                retry_prompt,
                genai_types.Part.from_bytes(data=image1_bytes, mime_type=mime_type1),
                genai_types.Part.from_bytes(data=image2_bytes, mime_type=mime_type2),
            ]
            retry_response = client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=retry_contents,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE"]
                )
            )
            retry_parts = retry_response.candidates[0].content.parts
            for part in retry_parts:
                if part.inline_data:
                    raw = part.inline_data.data
                    if raw:
                        import base64
                        image_data = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
                        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                        out_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_gemini_edit_{int(_time.time()*1000)}.png")
                        with open(out_path, "wb") as f:
                            f.write(image_data)
                        print(f"[GEMINI_EDIT] Retry success, saved to {out_path}")
                        return (True, out_path)
            retry_reason = getattr(retry_response.candidates[0], 'finish_reason', 'unknown')
            print(f"[GEMINI_EDIT] Retry also failed. finish_reason={retry_reason}")
        
        return (False, f"Gemini 未回傳圖片 (finish_reason={finish_reason})")
        
    except Exception as e:
        print(f"[GEMINI_EDIT_ERROR] {e}")
        return (False, f"Gemini edit error: {str(e)[:100]}")

def edit_image_with_gemini(edit_prompt, user_id, image_path1, image_path2=None):
    """使用 Gemini 2.5 Flash 進行圖片修改或融合，自帶配額防護。"""
    if user_id:
        quota_ok, remain, quota_msg = check_image_quota(user_id)
        if not quota_ok:
            return False, quota_msg
            
    success, result = gemini_edit_image_internal(edit_prompt, user_id, image_path1, image_path2)
    
    if success and user_id:
        increment_image_quota(user_id)
        
    return success, result


def beautify_image(image_path, user_id):

    """美化圖片（提升亮度、對比、銳度）"""
    try:
        img = Image.open(image_path)
        
        # 提升對比
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.2)
        
        # 提升亮度
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.1)
        
        # 提升銳度
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.3)
        
        # 儲存美化後的圖片
        beautified_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_beautified.jpg")
        img.save(beautified_path, quality=95)
        
        return beautified_path
    except Exception as e:
        print(f"Image beautification error: {e}")
        return None

def transcribe_audio_with_gemini(audio_path, model_to_use=None):
    """使用 Gemini 進行語音轉文字 (支援 LINE m4a 格式)"""
    # 如果沒有指定模型，預設使用全域 functional model (避免廢話)
    # 如果全域變數不可用，才退回 user_model (但 user_model 會講笑話，所以盡量避免)
    target_model = model_to_use if model_to_use else model_functional

    try:
        # Check file size
        filesize = os.path.getsize(audio_path)
        print(f"[AUDIO] Transcribing file: {audio_path} (Size: {filesize} bytes)")
        if filesize < 10:  # Relaxed check: 10 bytes (some m4a headers are small)
            print("[AUDIO] File too small, skipping.")
            return None

        # 上傳檔案到 Gemini
        # LINE 的 m4a 其實是 MPEG-4 Audio，標準 MIME 是 audio/mp4
        audio_file = genai.upload_file(audio_path, mime_type="audio/mp4")
        print(f"[AUDIO] Upload successful: {audio_file.name}")
        
        # 請 AI 轉錄，增加針對無聲或噪音的指示
        # 請 AI 轉錄 (Simplified Prompt for Speed)
        prompt = """[SYSTEM: STT]
        Transcribe audio verbatim to Traditional Chinese (繁體中文).
        Rules:
        1. Exact words only. No paraphrasing.
        2. No conversational fillers (e.g., "好的", "沒關係") unless clearly spoken.
        3. Return empty string if silence/noise.
        4. NO additional commentary.
        
        - Audio: (Silence) -> Output: ""
        - Audio: (Noise) -> Output: ""
        - Audio: "北海道" -> Output: "北海道"
        - Audio: (Unclear mumbling) -> Output: ""

        Input Audio -> Transcribed Text (Nothing else)"""
        
        # 使用更嚴格的參數 (Temperature 0) 避免幻覺
        response = target_model.generate_content(
            [prompt, audio_file],
            generation_config=genai.types.GenerationConfig(
                temperature=0.0,
                top_p=1.0, 
                max_output_tokens=2048,
            )
        )
        
        text = response.text.strip()
        print(f"[AUDIO] Transcription result: '{text}'")
        return text
            
    except Exception as e:
        print(f"Gemini audio transcription error: {e}")
        # 嘗試回傳 None 讓上層處理
        return None

def text_to_speech(text, user_id):
    """文字轉語音"""
    try:
        global tts_client
        if tts_client is None:
            tts_client = texttospeech.TextToSpeechClient()
        client = tts_client
        
        synthesis_input = texttospeech.SynthesisInput(text=text)
        
        voice = texttospeech.VoiceSelectionParams(
            language_code="zh-TW",
            name="zh-TW-Wavenet-A",
        )
        
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        # 儲存音訊檔案
        audio_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_reply.mp3")
        with open(audio_path, "wb") as out:
            out.write(response.audio_content)
        
        return audio_path
    except Exception as e:
        print(f"Text-to-speech error: {e}")
        return None

def upload_image_to_external_host(image_path):
    """
    上傳圖片到外部主機(如 Imgur 或 imgbb)並取得公開 URL
    LINE 要求圖片必須是 HTTPS URL
    """
    try:
        # 優先嘗試上傳到 Google Cloud Storage (如果已啟用)
        if ADVANCED_FEATURES_ENABLED and gcs_utils:
            try:
                print("Attempting to upload image to GCS...")
                public_url = gcs_utils.upload_image_to_gcs(image_path)
                if public_url:
                    print(f"Image uploaded to GCS: {public_url}")
                    return public_url
            except Exception as e:
                print(f"GCS upload failed: {e}")
                #如果 GCS 失敗，嘗試 fallback 到 Imgur
        
        # 使用 imgbb API（免費，不需註冊）
        # 注意：生產環境建議使用自己的圖床服務
        api_key = os.environ.get("IMGBB_API_KEY", "")
        
        if not api_key:
            print("Warning: IMGBB_API_KEY not set, image sending may fail. And GCS upload also failed or is disabled.")
            return None
        
        with open(image_path, "rb") as file:
            url = "https://api.imgbb.com/1/upload"
            payload = {
                "key": api_key,
            }
            files = {
                "image": file,
            }
            response = requests.post(url, data=payload, files=files)
            
            if response.status_code == 200:
                data = response.json()
                return data["data"]["url"]
            else:
                print(f"Imgur/ImgBB upload failed: {response.text}")
                return None
    except Exception as e:
        print(f"Image upload error: {e}")
        return None

def send_image_to_line(user_id, image_path, message_text="", reply_token=None):
    """傳送圖片到 LINE(優先使用 reply_message 節省額度, 沒有 token 時用 push_message)"""
    # ============================================
    # GLOBAL: Passive Notification Check
    # ============================================
    # 每次用戶傳訊息來，順便檢查有沒有待接收的提醒
    # (只有在非 Audio Confirmation 狀態下才做，避免打斷語音確認流程)
    pending_notes = []
    if db and user_id not in user_audio_confirmation_pending:
        try:
            notes = db.get_and_clear_pending_notifications(user_id)
            if notes:
                pending_notes = notes
        except Exception as e:
            print(f"Error checking pending notes: {e}")
            
    # 定義一個 helper function 來附加提醒
    def append_pending_notes(reply_msg):
        if not pending_notes: return reply_msg
        
        note_text = "\n\n📝 【未讀提醒】\n" + "\n".join(pending_notes)
        if isinstance(reply_msg, str):
            return reply_msg + note_text
        elif isinstance(reply_msg, TextMessage):
            return TextMessage(text=reply_msg.text + note_text)
        return reply_msg

    try:
        print(f"[SEND IMAGE] Starting for user {user_id}, image: {image_path}")
        
        # 上傳圖片並取得公開 URL
        image_url = upload_image_to_external_host(image_path)
        
        if not image_url:
            print("[SEND IMAGE] FAILED: upload_image_to_external_host returned None")
            return False
        
        print(f"[SEND IMAGE] Got URL: {image_url[:50]}...")
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            
            messages = []
            
            # Fixed Order: Image FIRST, Text SECOND (用戶要求先傳圖片再傳文字)
            messages.append(ImageMessage(
                original_content_url=image_url,
                preview_image_url=image_url
            ))
            
            if message_text:
                messages.append(TextMessage(text=message_text))
            
            # 優先使用 reply_message（不計額度），沒有 token 時才用 push_message
            if reply_token:
                try:
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=reply_token,
                            messages=messages
                        )
                    )
                    print("[SEND IMAGE] SUCCESS: Used reply_message (free)")
                except Exception as reply_err:
                    # reply_token 可能過期，fallback 到 push_message
                    print(f"[SEND IMAGE] reply_message failed: {reply_err}, trying push_message")
                    try:
                        line_bot_api.push_message(
                            PushMessageRequest(
                                to=user_id,
                                messages=messages
                            )
                        )
                        print("[SEND IMAGE] SUCCESS: Fallback to push_message")
                    except Exception as push_err:
                        error_str = str(push_err)
                        if "429" in error_str or "limit" in error_str.lower():
                            print(f"[SEND IMAGE] FAILED: Monthly limit reached. Cannot push message.")
                        else:
                            print(f"[SEND IMAGE] push_message failed: {push_err}")
                        return False
            else:
                try:
                    line_bot_api.push_message(
                        PushMessageRequest(
                            to=user_id,
                            messages=messages
                        )
                    )
                    print("[SEND IMAGE] SUCCESS: Used push_message")
                except Exception as push_err:
                    error_str = str(push_err)
                    if "429" in error_str or "limit" in error_str.lower():
                        print(f"[SEND IMAGE] FAILED: Monthly limit reached. Cannot push message.")
                    else:
                        print(f"[SEND IMAGE] push_message failed: {push_err}")
                    return False
        
        return True
    except Exception as e:
        print(f"[SEND IMAGE] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_status_notification(reply_token, status_text):
    """使用 reply_message 發送狀態通知(免費)
    
    Args:
        reply_token: LINE 的 reply_token, 如果為 None 則跳過
        status_text: 狀態訊息文字
    
    Returns:
        True 如果成功發送, False 如果失敗或無 token
    """
    if not reply_token:
        print(f"[STATUS] No reply_token, skipping status: {status_text[:30]}...")
        return False
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=status_text)]
                )
            )
        print(f"[STATUS] Sent via reply_message (free): {status_text[:30]}...")
        return True
    except Exception as e:
        print(f"[STATUS] Failed to send: {e}")
        return False


# ======================
# Webhook Handlers
# ======================

@app.route("/")
def health_check():
    return "OK", 200

@app.route("/callback", methods=["POST"])
def callback():
    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"]
    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    
    # parse webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def message_text(event):
    user_id = event.source.user_id
    print(f"==============================")
    print(f"[LINE API] Incoming Message from User ID: {user_id}")
    print(f"==============================")
    user_input = event.message.text.strip()
    
    # ------------------------------------------------------------
    # 被動提醒通知：檢查是否有因額度不足而發送失敗的提醒
    # ------------------------------------------------------------
    missed_reminders_msg = ""
    start_failed_reminders = []
    if ADVANCED_FEATURES_ENABLED and db:
        start_failed_reminders = db.get_failed_reminders(user_id)
        if start_failed_reminders:
            missed_reminders_msg = "⚠️ 【系統公告】\n很抱歉，因為本月免費訊息額度已滿，我錯過了以下提醒通知：\n"
            for idx, r in enumerate(start_failed_reminders, 1):
                t_str = r['reminder_time']
                if isinstance(t_str, datetime):
                    t_str = t_str.strftime('%m/%d %H:%M')
                missed_reminders_msg += f"{idx}. {t_str} - {r['reminder_text']}\n"
            
            missed_reminders_msg += "\n(已為您補上通知，請見諒！)\n\n---\n"
    
    # ------------------------------------------------------------
    # 語音確認流程：處理用戶對語音辨識結果的確認
    # ------------------------------------------------------------
    if user_id in user_audio_confirmation_pending:
        pending_data = user_audio_confirmation_pending[user_id]
        
        # 判斷用戶回應
        if any(keyword in user_input.lower() for keyword in ['是', 'ok', '對', '沒錯', 'confirm', 'yes', '好', '正確']):
            # 用戶確認正確，取出語音文字並繼續執行
            verified_text = pending_data['text']
            del user_audio_confirmation_pending[user_id]
            
            # --- Auto-Advance Logic for Audio Workflow ---
            # 如果是圖片生成且正在等待 Prompt，直接跳過二次確認，視為已確認執行
            if user_id in user_image_generation_state:
                current_state = user_image_generation_state[user_id]
                if current_state == 'waiting_for_prompt' or current_state == 'can_modify':
                     # 初始化 Prompt 儲存結構 (如果尚未存在)
                     if user_id not in user_last_image_prompt:
                         user_last_image_prompt[user_id] = {}
                     elif isinstance(user_last_image_prompt[user_id], str):
                         user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt[user_id]}
                     
                     # 設定 pending_description (這是 downstream logic 需要的)
                     user_last_image_prompt[user_id]['pending_description'] = verified_text
                     
                     # 強制進入生成狀態
                     user_image_generation_state[user_id] = 'generating'
                     
                     # 修改 user_input 為確認指令，讓後續 logic 直接執行生成
                     user_input = "開始生成"
            
            # 如果是長輩圖且正在等待背景描述，直接跳過二次確認 (雖然 Memes 邏輯較複雜，但設為 waiting_text 可跳過部分)
            # 注意：handle_meme_agent 內部 logic 即使傳入 text 也會問確認，這裡僅傳遞 text
            
            # 若非上述特殊狀態，則將輸入替換為驗證過的文字，繼續往下執行一般邏輯
            if user_input != "開始生成":
                user_input = verified_text
            
            # (不 return，讓它繼續跑到下面的邏輯)
            
        elif any(keyword in user_input.lower() for keyword in ['不', '錯', 'no', 'cancel', '取消', '重錄', '否']):
            # 用戶否認，清除狀態
            del user_audio_confirmation_pending[user_id]
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="好的，已取消。請重新輸入文字或再錄一次音。")]
                    )
                )
            return
        else:
            # 用戶輸入不明確
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="請回答「是」確認語音內容，或回答「否」取消。")]
                    )
                )
            return
    
    # ============================================
    # 全局取消檢查 - 最高優先級，貫穿所有服務
    # ============================================
    # 注意：如果用戶說「取消提醒」，不應在此攔截，而是交由 intent 處理
    if any(keyword in user_input for keyword in ['取消', '不要了', '先不要', '暫停', '停止']):
        # 例外：如果是提醒相關指令，忽略全局取消，讓它往下走到 classify_user_intent
        if "提醒" not in user_input:
            # 清除所有服務的狀態
            if user_id in user_trip_plans:
                user_trip_plans[user_id] = {'stage': 'idle'}
            if user_id in user_meme_state:
                user_meme_state[user_id] = {'stage': 'idle'}
            if user_id in user_image_generation_state:
                user_image_generation_state[user_id] = 'idle'
            
            # 立即回覆並退出
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="好的，已取消當前操作！")]
                    )
                )
            return
    
    # 檢查是否為功能總覽/功能選單請求（統一處理，帶圖片）
    if detect_help_intent(user_input) or detect_menu_intent(user_input):
        help_image_url = os.environ.get("HELP_IMAGE_URL")
        
        reply_msgs = []
        
        # 1. 必備：文字版說明（使用統一的選單文字）
        help_text = get_function_menu()
        reply_msgs.append(TextMessage(text=help_text))
        
        # 2. 選備：功能說明圖 (若有設定 HELP_IMAGE_URL)
        if help_image_url:
             reply_msgs.append(
                ImageMessage(
                    original_content_url=help_image_url,
                    preview_image_url=help_image_url
                )
            )
            
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=reply_msgs,
                )
            )
        return
    
    # ============================================
    # 連結查證功能：首先檢查是否有待處理的連結回應
    # ============================================
    # 優先處理：用戶正在回應我們的連結詢問（不一定包含新連結）
    if user_id in user_link_pending:
        pending_data = user_link_pending[user_id]
        pending_url = pending_data['url']
        
        # 檢測用戶想要「閱讀」
        if any(keyword in user_input for keyword in ['閱讀', '讀', '摘要', '內容', '看看', '1', '①', '１']):
            content = fetch_webpage_content(pending_url)
            if content:
                summary = summarize_content(content, user_id)
                reply_text = summary
            else:
                reply_text = "抱歉，我無法讀取這個網頁的內容。可能是網站有防護機制或連結已失效。"
            
            del user_link_pending[user_id]
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
            return
        
        # 檢測用戶想要「查證」
        elif any(keyword in user_input for keyword in ['查證', '檢查', '確認', '真假', '詐騙', '2', '②', '２']):
            content = fetch_webpage_content(pending_url)
            if content:
                # 查證 prompt - 要求完整輸出
                analysis_prompt = f"""分析這個網頁是否可信：

{content[:1500]}

回答格式：

🔍 判定：合法/可疑/詐騙

📝 理由：
• 理由1
• 理由2  
• 理由3

💡 建議：
• 建議1
• 建議2

請完整填寫三項。"""
                
                generation_config = genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=3000  # 中文 1000-1500 字，確保完整輸出
                )
                try:
                    analysis = model_functional.generate_content(analysis_prompt, generation_config=generation_config)
                    # 檢查是否有有效回應
                    if analysis and hasattr(analysis, 'text') and analysis.text:
                        # 清理 markdown 符號
                        clean_result = analysis.text.replace('**', '').replace('*', '').strip()
                        reply_text = f"🔍 查證報告\n\n{clean_result}"
                    elif analysis and hasattr(analysis, 'candidates') and analysis.candidates:
                        reply_text = "🔍 查證報告\n\n判定：內容被過濾\n建議：請謹慎查看"
                    else:
                        reply_text = "🔍 查證報告\n\n判定：無法分析\n建議：請直接查看原網站"
                except Exception as e:
                    print(f"Verification error (old code): {e}")
                    reply_text = f"🔍 查證報告\n\n判定：分析失敗\n錯誤：{str(e)[:30]}\n建議：請稍後再試"
            else:
                reply_text = "抱歉，我無法讀取這個網頁的內容進行深度查證。"
            
            del user_link_pending[user_id]
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
            return
        
        # 用戶輸入不明確，重新提示
        elif not extract_url(user_input):  # 確保不是發送新連結
            reply_text = f"""收到您的訊息!

您之前發送的連結還沒處理完喔:
[Link] {pending_url[:50]}...

請告訴我您想要:
[1] 閱讀 - 幫您摘要內容
[2] 查證 - 檢查是否可信

回覆 (閱讀) 或 (查證) 即可!
(或輸入 (取消) 放棄)"""
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
            return
    
    # 檢查是否包含新連結
    url = extract_url(user_input)
    
    if url:
        # 用戶傳送了連結
        
        # 檢查是否有待處理的連結（用戶正在回應我們的詢問）
        if user_id in user_link_pending:
            pending_url = user_link_pending[user_id]['url']
            
            # 判斷用戶意圖
            if any(keyword in user_input for keyword in ['閱讀', '讀', '摘要', '內容', '看看']):
                # 用戶想要閱讀內容
                content = fetch_webpage_content(pending_url)
                if content:
                    summary = summarize_content(content, user_id)
                    reply_text = summary
                else:
                    reply_text = "抱歉，我無法讀取這個網頁的內容。可能是網站有防護機制或連結已失效。"
                
                # [NEW] 將查證結果存入記憶，讓用戶可以追問
                if user_id not in chat_sessions: chat_sessions[user_id] = model.start_chat(history=[])
                chat = chat_sessions[user_id]
                chat.history.append({'role': 'user', 'parts': [f"請幫我閱讀這個連結：{pending_url}"]})
                chat.history.append({'role': 'model', 'parts': [reply_text]})

                # 清除待處理狀態
                del user_link_pending[user_id]
                
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                return
                
            elif any(keyword in user_input for keyword in ['查證', '檢查', '確認', '真假', '詐騙', '驗證', '查', '2', '2️⃣', '安全']):
                # 用戶想要查證
                content = fetch_webpage_content(pending_url)
                if content:
                    # 使用 Gemini 深度分析內容 (改用功能性模型 + 嚴格提示)
                    analysis_prompt = f"""
                    [SYSTEM: SECURITY REPORT GENERATOR - STRICT MODE]
                    
                    Task: Analyze the following content and generate a CONCISE security report.
                    
                    Content:
                    {content[:2500]}

                    ABSOLUTE REQUIREMENTS:
                    1. **NO JOKES** - Zero humor, zero casual language
                    2. **NO EMOJIS in body text** - Only allowed in section headers
                    3. **LENGTH LIMIT**: 100-150 Chinese characters MAXIMUM (not words, characters)
                    4. **TONE**: Robotic, factual, professional
                    5. **FORMAT**: Strict bullet points only
                    
                    Output Format (MUST FOLLOW EXACTLY):
                    
                    🔍 查證報告
                    
                    判定：[詐騙/可疑/合法]
                    風險：[1-2個風險點，每個不超過15字]
                    建議：[Block/Ignore/Delete]
                    
                    Example:
                    🔍 查證報告
                    判定：可疑
                    風險：網域註冊僅30天、包含聳動用詞
                    建議：建議忽略此連結
                    
                    Remember: MAXIMUM 150 characters. Be precise.
                    
                    Output Template:
                    🔍 **查證報告**
                    * **判定**: [SCAM / SUSPICIOUS / LEGIT]
                    * **風險**: [Risk 1], [Risk 2]
                    * **操作**: [Block / Ignore / Delete]
                    """
                    # 使用 model_functional (Temp 0.0 for strictness)
                    generation_config = genai.types.GenerationConfig(
                        temperature=0.0,
                        max_output_tokens=200  # 強制限制輸出長度
                    )
                    analysis = model_functional.generate_content(analysis_prompt, generation_config=generation_config)
                    reply_text = f"{analysis.text}"
                
                # [NEW] 將查證結果存入記憶，讓用戶可以追問
                if user_id not in chat_sessions: chat_sessions[user_id] = model.start_chat(history=[])
                chat = chat_sessions[user_id]
                chat.history.append({'role': 'user', 'parts': [f"請幫我查證這個連結：{pending_url}"]})
                chat.history.append({'role': 'model', 'parts': [reply_text]})

                # 清除待處理狀態
                del user_link_pending[user_id]
                
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                return
        
        # 檢查是否同時包含查證關鍵字（直接查證，跳過選擇步驟）
        if any(keyword in user_input for keyword in ['查證', '檢查', '確認', '真假', '詐騙', '驗證', '查', '安全']):
            # 直接進行查證
            content = fetch_webpage_content(url)
            if content:
                # 使用簡化 prompt 避免被安全過濾器阻擋
                analysis_prompt = f"""分析以下網頁內容是否可信。

內容：
{content[:1500]}

請簡短回答：
1. 判定（詐騙/可疑/合法）
2. 主要風險
3. 建議

限80字內。"""
                
                # 使用 model_functional (Temp 0.0)
                generation_config = genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=250
                )
                try:
                    analysis = model_functional.generate_content(analysis_prompt, generation_config=generation_config)
                    # 檢查是否有有效回應
                    if analysis and hasattr(analysis, 'text') and analysis.text:
                        reply_text = f"🔍 查證報告\n\n{analysis.text}"
                    elif analysis and hasattr(analysis, 'candidates') and analysis.candidates:
                        # 嘗試從 candidates 取得文字
                        reply_text = f"🔍 查證報告\n\n判定：無法完整分析\n建議：請謹慎查看內容"
                    else:
                        reply_text = "🔍 查證報告\n\n判定：無法分析\n建議：請直接查看原網站"
                except Exception as e:
                    print(f"Verification error: {e}")
                    reply_text = f"🔍 查證報告\n\n判定：分析失敗\n原因：{str(e)[:30]}\n建議：請稍後再試"
            else:
                reply_text = "抱歉，我無法讀取這個網頁的內容。可能是網站有防護機制或連結已失效。"
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
            return
        
        # 新連結：執行快速安全檢查
        safety_check = quick_safety_check(url)
        
        # 儲存待處理連結
        user_link_pending[user_id] = {
            'url': url,
            'safety': safety_check
        }
        
        # 根據風險等級回應
        reply_text = format_verification_result(safety_check, url)
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
        return
    
    # ============================================
    # 查證功能：用戶主動要求查證 (無連結)
    # ============================================
    if any(keyword in user_input for keyword in ['查證', '查假', '求證', '檢查連結']):
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="請貼上您想查證的連結🔗\n(將協助您分析內容真實性)")]
                )
            )
        return
    
    # ============================================
    # 新聞查詢功能：檢查是否想查詢新聞
    # ============================================
    if detect_news_intent(user_input):
        # [Fix] 優先檢查是否為「語音播報」請求
        # 如果用戶只說「語音」但沒有緩存，應該提示先看新聞，而不是重新抓新聞
        is_voice_request = any(keyword in user_input for keyword in ['語音', '播報', '聽', '念', '讀'])
        
        if is_voice_request:
            if user_id in user_news_cache:
                # 有緩存 -> 繼續往下執行語音邏輯
                pass 
            else:
                # 無緩存 -> 提示用戶
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="請先說「看新聞」或「新聞」，我準備好新聞後才能唸給您聽喔！📢")]
                        )
                    )
                return

        # 檢查是否是要語音播報 (這裡是既有邏輯，保留作為 fallback)
        # 檢查是否是要語音播報
        if user_id in user_news_cache and any(keyword in user_input for keyword in ['語音', '播報', '聽', '念', '讀']):
            # 使用 Pro 模型重新生成語音專用文字（保留數字）
            print("[VOICE] Generating TTS text with Pro model for number preservation...")
            
            try:
                # 使用 Pro 模型重新處理，確保數字被保留
                voice_prompt = f"""將以下新聞改寫為適合語音播報的純文字。
                
重要規則：
1. 必須保留所有日期和數字（如：4日、100萬、2月9日）
2. 移除所有標點符號和表情符號
3. 用口語化的方式表達
4. 每則新聞約50字
5. 每則新聞開頭必須以「第一則、第二則、第三則...」等方式唸出則數，例如：「第一則。台灣...」

原始新聞：
{user_news_cache[user_id][:2000]}

直接輸出語音稿，不要加任何解釋。"""

                model_pro = genai.GenerativeModel(
                    model_name="gemini-2.5-pro",
                    system_instruction="你是專業新聞播報員，必須精準保留所有日期與數字。"
                )
                response = model_pro.generate_content(voice_prompt)
                news_text = response.text.strip()
                print(f"[VOICE PRO] Generated text with numbers preserved: {news_text[:100]}...")
            except Exception as e:
                print(f"[VOICE] Pro model failed, using cached text: {e}")
                news_text = user_news_cache[user_id]
            
            # 清理文字（TTS 專用）- 重要：保留內容數字
            import re
            
            # 步驟 1：先移除 URL（包含 URL 中的數字）
            clean_text = re.sub(r'https?://[^\s]+', '', news_text)
            clean_text = re.sub(r'www\.[^\s]+', '', clean_text)
            
            # 步驟 2：移除「來源：」後面的所有內容（通常是 URL 或網站名）
            clean_text = re.sub(r'來源：[^\n]*', '', clean_text)
            
            # 步驟 3：移除特定 emoji 符號（不使用 emoji 數字字符，避免誤刪普通數字）
            # 改用 unicode 移除常見表情符號
            emoji_pattern = re.compile("["
                u"\U0001F600-\U0001F64F"  # emoticons
                u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                u"\U0001F680-\U0001F6FF"  # transport & map symbols
                u"\U0001F1E0-\U0001F1FF"  # flags
                u"\U00002702-\U000027B0"  # dingbats
                u"\U0001F4A0-\U0001F4FF"  # 其他符號
                "]+", flags=re.UNICODE)
            clean_text = emoji_pattern.sub('', clean_text)
            # 移除中括號等標記
            clean_text = re.sub(r'[【】🔗💡📰🔊]', '', clean_text)
            
            # 步驟 4：移除標題文字
            clean_text = clean_text.replace('今日新聞摘要', '').replace('想聽語音播報？回覆「語音」即可', '').strip()
            
            # DEBUG: 檢查數字保留情況
            has_digits_before = bool(re.search(r'\d', clean_text))
            print(f"[DEBUG] Before digit conversion - has digits: {has_digits_before}")
            if has_digits_before:
                digit_sample = re.findall(r'\d+', clean_text)[:5]
                print(f"[DEBUG] Sample digits found: {digit_sample}")
            
            # 步驟 5：將日期格式 X/Y 轉換為 X月Y日
            clean_text = re.sub(r'(\d{1,2})/(\d{1,2})', r'\1月\2日', clean_text)
            
            # 步驟 6：完整的中文數字轉換（含位數單位）
            def num_to_chinese(num_str):
                """將阿拉伯數字轉為中文（含單位）"""
                digit_map = {'0': '零', '1': '一', '2': '二', '3': '三', '4': '四', 
                             '5': '五', '6': '六', '7': '七', '8': '八', '9': '九'}
                
                # 處理小數
                if '.' in num_str:
                    parts = num_str.split('.')
                    integer_part = num_to_chinese(parts[0])
                    decimal_part = ''.join(digit_map.get(d, d) for d in parts[1])
                    return f"{integer_part}點{decimal_part}"
                
                # 處理整數
                num = int(num_str)
                if num == 0:
                    return '零'
                
                units = ['', '十', '百', '千', '萬', '十萬', '百萬', '千萬', '億']
                result = []
                
                # 億位
                if num >= 100000000:
                    result.append(digit_map[str(num // 100000000)])
                    result.append('億')
                    num %= 100000000
                    if num > 0 and num < 10000000:
                        result.append('零')
                
                # 萬位
                if num >= 10000:
                    wan = num // 10000
                    if wan >= 10:
                        result.append(num_to_chinese(str(wan)))
                    else:
                        result.append(digit_map[str(wan)])
                    result.append('萬')
                    num %= 10000
                    if num > 0 and num < 1000:
                        result.append('零')
                
                # 千位
                if num >= 1000:
                    result.append(digit_map[str(num // 1000)])
                    result.append('千')
                    num %= 1000
                    if num > 0 and num < 100:
                        result.append('零')
                
                # 百位
                if num >= 100:
                    result.append(digit_map[str(num // 100)])
                    result.append('百')
                    num %= 100
                    if num > 0 and num < 10:
                        result.append('零')
                
                # 十位
                if num >= 10:
                    tens = num // 10
                    if tens != 1 or len(result) > 0:  # 避免 "一十" 只說 "十"
                        result.append(digit_map[str(tens)])
                    result.append('十')
                    num %= 10
                
                # 個位
                if num > 0:
                    result.append(digit_map[str(num)])
                
                return ''.join(result)
            
            # 替換所有數字（包括小數）
            def replace_number(match):
                return num_to_chinese(match.group(0))
            
            # 步驟 7：處理百分比（23% → 百分之二十三）
            def replace_percent(match):
                num = match.group(1)
                chinese_num = num_to_chinese(num)
                return f"百分之{chinese_num}"
            clean_text = re.sub(r'(\d+\.?\d*)%', replace_percent, clean_text)
            
            # 步驟 8：跳過字母數字混合碼（如 M1A2T, F-16, A380）
            # 讓 TTS 直接唸英文字母和數字
            alphanumeric_pattern = r'[A-Za-z][\dA-Za-z-]*\d[\dA-Za-z-]*|[A-Za-z]+-\d+'
            
            # 只轉換「純數字」，跳過字母數字混合
            def smart_replace_number(match):
                num_str = match.group(0)
                # 檢查前後是否有字母
                start = match.start()
                end = match.end()
                text = match.string
                # 如果前面或後面有字母，不轉換
                if (start > 0 and text[start-1].isalpha()) or (end < len(text) and text[end].isalpha()):
                    return num_str  # 保持原樣
                return num_to_chinese(num_str)
            
            clean_text = re.sub(r'\d+\.?\d*', smart_replace_number, clean_text)
            
            # DEBUG: 驗證轉換結果
            has_chinese_digits = any(c in clean_text for c in '零一二三四五六七八九十百千萬億點')
            print(f"[DEBUG] After digit conversion - has Chinese digits: {has_chinese_digits}")
            
            print(f"[DEBUG] Voice text after cleaning (first 200 chars): {clean_text[:200]}")
            
            audio_path = generate_news_audio(clean_text, user_id)
            
            if audio_path:
                # 上傳音檔並發送（不發送文字訊息以節省額度）
                try:
                    audio_url = upload_image_to_external_host(audio_path)
                    
                    with ApiClient(configuration) as api_client:
                        line_bot_api = MessagingApi(api_client)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[
                                    AudioMessage(
                                        original_content_url=audio_url,
                                        duration=60000  # LINE 顯示問題，實際播放不受影響
                                    )
                                ]
                            )
                        )
                    
                    # 成功發送後刪除本地檔案（節省空間）
                    try:
                        os.remove(audio_path)
                        print(f"[CLEANUP] Deleted local audio file: {audio_path}")
                    except Exception as e:
                        print(f"[CLEANUP] Failed to delete audio file: {e}")
                    return
                except Exception as e:
                    print(f"Audio upload error: {e}")
                    reply_text = "抱歉，語音播報生成失敗。請稍後再試！"
            else:
                reply_text = "抱歉，語音播報生成失敗。請稍後再試！"
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
            return
        
        # 生成新聞摘要
        news_summary = generate_news_summary()
        
        # 儲存到快取（用於後續語音播報）
        user_news_cache[user_id] = news_summary
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=news_summary)]
                )
            )
        return
    
    else:
        # 一般對話處理 - 傳遞 reply_token 讓內部可以發送狀態通知
        reply_text = gemini_llm_sdk(user_input, user_id, event.reply_token)
    
    # 如果 gemini_llm_sdk 內部已經使用了 reply_token（發送了狀態通知），
    # 這裡的 reply_message 會失敗。
    # 但如果我們有 misses_reminders_msg 需要發送，且 gemini_llm_sdk 返回 None (代表已處理)，
    # 我們可能錯過了發送機會。
    # 策略：只要 reply_text 存在，就合併發送。
    
    if reply_text:
        # 合併被動通知訊息
        if missed_reminders_msg:
            reply_text = missed_reminders_msg + reply_text
            
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            
            # 發送成功後，從資料庫移除已通知的失敗提醒
            if ADVANCED_FEATURES_ENABLED and db and start_failed_reminders:
                for r in start_failed_reminders:
                    db.delete_reminder(r['id'], user_id)
                    
        except Exception as e:
            print(f"Reply message error: {e}")
            pass


@handler.add(MessageEvent, message=ImageMessageContent)
def message_image(event):
    global user_images
    user_id = event.source.user_id
    reply_token = event.reply_token
    
    # [Fix] Upper Logic: 圖片上傳時，若用戶處於「等待生成描述」狀態，應視為「放棄生成，改為處理此圖片」
    if user_id in user_image_generation_state and user_image_generation_state[user_id] == 'waiting_for_prompt':
        print(f"[IMAGE_MSG] User {user_id} uploaded image while waiting for prompt. Clearing state.")
        user_image_generation_state[user_id] = 'idle'

    
    try:
        # 確保資料夾存在
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            message_content = line_bot_blob_api.get_message_content(
                message_id=event.message.id
            )
            # 為每個用戶建立獨立的圖片檔案（用時間戳區分多張）
            import time as _time
            image_filename = f"{user_id}_image_{int(_time.time()*1000)}.jpg"
            image_path = os.path.join(UPLOAD_FOLDER, image_filename)
            
            with open(image_path, 'wb') as f:
                f.write(message_content)
        
        # 檢查是否在長輩圖製作流程中 (等待背景圖)
        if user_id in user_meme_state and user_meme_state[user_id].get('stage') == 'waiting_bg':
             with open(image_path, 'rb') as f:
                 image_data = f.read()
             reply_text = handle_meme_agent(user_id, image_content=image_data, reply_token=reply_token)
             with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
             return

        # 儲存圖片路徑
        if user_id not in user_images:
            user_images[user_id] = []
        user_images[user_id].append(image_path)
        
        # [New] 加入當前批次
        if user_id not in user_image_batch:
            user_image_batch[user_id] = []
        user_image_batch[user_id].append(image_path)
        
        # 保留最近5張
        if len(user_images[user_id]) > 5:
            old_image = user_images[user_id].pop(0)
            try:
                os.remove(old_image)
            except:
                pass
        
        # 設定圖片修改狀態
        user_uploaded_image_pending[user_id] = {
            'images': user_images[user_id].copy(),
            'history': []
        }
        
        # ===== 批次延遲回覆機制 =====
        # 保存最新的 reply_token（只有最後一個有效）
        image_batch_tokens[user_id] = reply_token
        
        # 如果已有計時器，取消並重置（等待更多圖片）
        if user_id in image_batch_timers and image_batch_timers[user_id] is not None:
            image_batch_timers[user_id].cancel()
        
        def send_batch_reply(uid):
            """計時器到期後，統一描述所有圖片並回覆"""
            try:
                saved_token = image_batch_tokens.get(uid)
                if not saved_token:
                    return
                
                # [Fix] 只描述當前批次的圖片，而不是歷史圖片
                images_to_describe = user_image_batch.get(uid, [])
                if not images_to_describe:
                    return
                
                # 清空批次 (避免下次重複描述)
                user_image_batch[uid] = []
                
                # 用 Gemini Vision 統一描述所有圖片
                try:
                    # 最多描述最近5張 (批次內)
                    recent = images_to_describe[-5:]
                    img_objects = []
                    for p in recent:
                        try:
                            img_objects.append(PIL.Image.open(p))
                        except:
                            pass
                    
                    if len(img_objects) == 1:
                        vision_prompt = "請用繁體中文描述這張圖片的內容，保持簡短生動（不超過100字）。描述完後，直接說「我已經記得這張圖片了！」"
                        vision_response = model.generate_content([vision_prompt, img_objects[0]])
                    else:
                        count = len(img_objects)
                        labels = "\n".join([f"📸 第{i+1}張：..." for i in range(count)])
                        vision_prompt = f"請用繁體中文分別簡短描述這{count}張圖片的內容（每張不超過40字），格式為：\n{labels}\n描述完後，說「我已經記得這{count}張圖片了！」"
                        vision_response = model.generate_content([vision_prompt] + img_objects)
                    
                    finish_message = vision_response.text
                    if '加油' not in finish_message:
                        finish_message += "\n\n加油！Cheer up！讚啦！"
                except:
                    count = len(images_to_describe)
                    if count > 1:
                        finish_message = f"我已經記得這{count}張圖片了！\n\n加油！Cheer up！讚啦！"
                    else:
                        finish_message = "我已經記得這張圖片了！\n\n加油！Cheer up！讚啦！"
                
                # 用最後一個 reply_token 回覆
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=saved_token,
                            messages=[TextMessage(text=finish_message)],
                        )
                    )
                print(f"[IMAGE_BATCH] Replied for {uid} with {len(images_to_describe)} image(s)")
            except Exception as e:
                print(f"[IMAGE_BATCH] Error: {e}")
            finally:
                # 清理計時器
                image_batch_timers.pop(uid, None)
                image_batch_tokens.pop(uid, None)
        
        # 設定2秒後觸發（等待用戶可能繼續傳圖）
        timer = threading.Timer(2.0, send_batch_reply, args=[user_id])
        image_batch_timers[user_id] = timer
        timer.start()
        print(f"[IMAGE_BATCH] Timer set for {user_id}, total images: {len(user_images[user_id])}")
        
        # 不在這裡回覆，由 Timer 統一回覆
        return
        
    except Exception as e:
        print(f"Image upload error: {e}")
        # 發生錯誤時直接回覆
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            try:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text="圖片上傳失敗，請再試一次。加油！Cheer up！")],
                    )
                )
            except:
                pass


@handler.add(MessageEvent, message=AudioMessageContent)
def message_audio(event):
    user_id = event.source.user_id
    
    try:
        # 下載音訊檔案
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            audio_content = line_bot_blob_api.get_message_content(
                message_id=event.message.id
            )
        
        # 確保資料夾存在
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        # 儲存音訊檔案 (.m4a)
        audio_filename = f"{user_id}_audio.m4a"
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        
        with open(audio_path, 'wb') as f:
            f.write(audio_content)
        
        # 語音轉文字 (使用 Gemini - 使用功能性模型避免加料)
        text = transcribe_audio_with_gemini(audio_path, model_functional)
        
        if text:
            # ------------------------------------------------------------
            # 語音確認流程：檢查是否在需要精確指令的狀態中
            # ------------------------------------------------------------
            needs_confirmation = False
            
            # 1. 檢查圖片生成/修改狀態 (排除 generating 與 can_modify，這些狀態不接受中斷確認)
            if user_id in user_image_generation_state and user_image_generation_state[user_id] not in ['idle', 'generating', 'can_modify']:
                needs_confirmation = True
            
            # 2. 檢查長輩圖製作狀態
            elif user_id in user_meme_state and user_meme_state[user_id]['stage'] != 'idle':
                needs_confirmation = True
            
            # 3. 檢查行程規劃狀態 (新增)
            elif user_id in user_trip_plans and user_trip_plans[user_id]['stage'] != 'idle':
                # 行程規劃也建議確認，避免識別錯誤導致流程混亂
                needs_confirmation = False # 保持 False 讓對話流暢，因為行程規劃有自己的確認機制 (Can discuss)
                # 但如果是輸入地點階段，誤識別會很麻煩。這里權衡後決定還是直接處理，但在 Prompt 層面加強提取
                pass

            if needs_confirmation:
                # 檢查是否為確認詞（視為確認，不是修改內容）
                confirmation_keywords = ['對', '確定', '完成', 'ok', 'Ok', 'OK', 'ＯＫ', 'Ｏｋ', 'ｏｋ', '是', '沒錯', '好']
                if any(kw in text for kw in confirmation_keywords):
                    
                    # [Fix] 若目前有等待確認的語音文字，發送語音「是」等同於確認該文字
                    if user_id in user_audio_confirmation_pending:
                        text = user_audio_confirmation_pending[user_id]['text']
                        del user_audio_confirmation_pending[user_id]
                        print(f"[AUDIO CONFIRM] Resuming pending text via voice confirm: {text}")

                    # 直接當作確認，不儲存到pending
                    confirmation = f"✅ 收到：「{text}」"
                    
                    # 呼叫 LLM 處理
                    print(f"[AUDIO CONFIRM] User confirmed with: {text}")
                    response = gemini_llm_sdk(text, user_id, reply_token=event.reply_token)
                    
                    if response:
                        reply_text = f"{confirmation}\n\n---\n\n{response}"
                    else:
                        # 如果 response 為 None，表示已經由 gemini_llm_sdk 內部處理完畢
                        print("[AUDIO] Handled internally by SDK")
                        return # 直接結束，不需再 reply_message
                else:
                    # 需要確認語音內容
                    user_audio_confirmation_pending[user_id] = {'text': text}
                    
                    # 回傳純淨的確認訊息 (絕對不含 jokes/cheer up)，並加上警語
                    reply_text = f"收到語音訊息\n\n您說的是：「{text}」\n\n請問是否正確？\n(請回答「是」或「ok」確認，或是重新錄音)\n\n⚠️ 確認後將開始製作，需等待約15秒，期間請勿操作！"
            else:
                # 一般閒聊模式 - 只有在閒聊時才允許 AI 發揮 (含 jokes)
                # 但如果進入了 functional flow (如 trip agent via gemini_llm_sdk)，那邊會使用 functional model
                
                confirmation = f"✅ 收到語音訊息\n\n您說的是：「{text}」"
                
                # 呼叫 LLM 處理 (傳入 reply_token 以便內部可能需要的操作)
                print(f"[AUDIO] Transcribed text: {text}")
                response = gemini_llm_sdk(text, user_id, reply_token=event.reply_token)
                
                if response:
                    reply_text = f"{confirmation}\n\n---\n\n{response}"
                else:
                    # 如果 response 為 None，表示已經由 gemini_llm_sdk 內部處理完畢 (例如觸發了生圖並用掉 token)
                    print("[AUDIO] Handled internally by SDK")
                    return # 直接結束，不需再 reply_message
                    
        else:
            print("[AUDIO] Transcription failed or empty.")
            reply_text = "抱歉，我好像沒聽到聲音，或者是背景太吵雜了。\n請再試著清楚地說一次喔！"
        
    except Exception as e:
        print(f"Audio processing error: {e}")
        reply_text = "語音處理發生了一點小錯誤，請稍後再試試看！"
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )

@handler.add(MessageEvent, message=StickerMessageContent)
def message_sticker(event):
    """處理貼圖訊息 - 不觸發任何服務, 只回應表情"""
    user_id = event.source.user_id
    
    # 檢查是否在圖片生成或長輩圖製作狀態中
    if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
        # 如果在可修改狀態，貼圖表示結束修改
        if user_image_generation_state[user_id] == 'can_modify':
            user_image_generation_state[user_id] = 'idle'
            reply_text = "好的！圖片已完成。期待下次為您服務！"
        else:
            # 在其他圖片生成流程中，提醒用戶需要文字描述
            reply_text = "我看到你傳了貼圖！但我需要文字描述才能幫你生成圖片喔！請用文字告訴我你想要什麼樣的圖片！"
    elif user_id in user_meme_state and user_meme_state[user_id]['stage'] != 'idle':
        # 在長輩圖製作流程中
        reply_text = "我看到你傳了貼圖！但我需要文字描述才能繼續製作長輩圖喔！請用文字告訴我！"
    else:
        # 一般情況，熱情回應
        responses = [
            "哇！收到你的貼圖了！超可愛的！😍 有什麼想聊的嗎？加油！Cheer up！讚喔！",
            "看到你傳貼圖給我好開心！💖 我也很想跟你聊天！有什麼我可以幫忙的嗎？讚喔！",
            "貼圖收到！👍 你的品味真好！想聊什麼都可以喔！加油！Cheer up！",
            "哈哈！這個貼圖好傳神喔！讚喔！✨",
        ]
        reply_text = random.choice(responses)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )

@handler.add(FollowEvent)
def handle_follow(event):
    """處理加入好友/解除封鎖事件 (歡迎詞 - 發送功能總覽圖)"""
    user_id = event.source.user_id
    print(f"New follower: {user_id}")
    
    # Help Image URL
    help_image_url = os.environ.get("HELP_IMAGE_URL", "https://storage.googleapis.com/help_poster/help_poster.png")
    
    # 本地備用路徑
    menu_image_path = os.path.join("static", "welcome_menu.jpg")
    
    # [FIX] 增加延遲，確保圖片訊息晚於 LINE 官方後台設定的「加入好友歡迎詞」送達
    # 避免發生「圖片比歡迎詞先跳出來」的順序錯誤
    time.sleep(1.5)

    # 準備歡迎文字 (作為備用)
    welcome_text = get_function_menu()
    
    messages = []
    
    # 用戶要求只顯示圖片，不顯示文字總覽
    # 但如果圖片無法載入，則作為備用方案加上文字
    
    image_url_to_send = None
    
    # 嘗試使用 URL
    if help_image_url and help_image_url.startswith("http"):
        image_url_to_send = help_image_url
    # 嘗試上傳本地圖片
    elif os.path.exists(menu_image_path):
        print(f"[WELCOME] Uploading local image: {menu_image_path}")
        image_url_to_send = upload_image_to_external_host(menu_image_path)
    
    if image_url_to_send:
        messages.append(ImageMessage(
            original_content_url=image_url_to_send,
            preview_image_url=image_url_to_send
        ))
    else:
        print("[WELCOME] No valid image to send, sending text only.")
        messages.append(TextMessage(text=welcome_text))
    
    # 發送訊息
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=messages
                )
            )
        print(f"[WELCOME] Welcome message sent to {user_id}")
    except Exception as e:
        print(f"[WELCOME] Failed to send welcome message: {e}")

# ======================
# Agent Handlers
# ======================

def handle_trip_agent(user_id, user_input, is_new_session=False, reply_token=None):
    """處理行程規劃, reply_token 用於發送狀態通知"""
    global user_trip_plans
    
    # Initialize state if new session
    if is_new_session or user_id not in user_trip_plans:
        user_trip_plans[user_id] = {'stage': 'collecting_info', 'info': {}}
        return """好的, 我們來規劃行程.

請問您想去哪裡玩呢?
(例如: 宜蘭, 台南, 綠島, 日本等)"""

    state = user_trip_plans[user_id]
    
    # Simple state machine
    if state['stage'] == 'collecting_info':
        # Check if we have destination
        if 'destination' not in state['info']:
            # 檢查是否要取消（優先檢查，避免被 AI 誤判）
            if any(keyword in user_input for keyword in ['取消', '不要了', '先不要', '暫停']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "好的，已取消行程規劃。"
            
            # 檢查是否有 large_region 但用戶說「都可以」
            if 'large_region' in state['info']:
                if any(keyword in user_input for keyword in ['都可以', '都行', '隨便', '不挑', '任意', '推薦']):
                    # 直接使用大地區作為目的地
                    state['info']['destination'] = state['info']['large_region']
                    return f"好的, {state['info']['large_region']}! 請問預計去幾天? (例如: 3天2夜)\n\n不想規劃了可以說「取消」"
            
            # 使用 AI 動態判斷地區是否需要細化 (同時提取地點名稱)
            # 例如用戶說 "我要去綠島" -> 提取 "綠島"
            
            extract_prompt = '''Target: Extract the destination name from the user's input.
            Input: "{}"
            
            Rules:
            1. Output ONLY the destination name.
            2. Do NOT format as JSON, Markdown, or Code Block.
            3. Do NOT add labels like "Destination:".
            4. **MUST Output in Traditional Chinese (繁體中文)**. 
               - If input is "Green Island", output "綠島".
               - If input is "Japan", output "日本".
            5. If no location found, output the original input.'''.format(user_input)
            
            try:
                extracted_dest = model_functional.generate_content(extract_prompt).text.strip()
                # Post-processing cleanup
                import re
                extracted_dest = re.sub(r'```json\s*', '', extracted_dest)
                extracted_dest = re.sub(r'```\s*', '', extracted_dest)
                extracted_dest = extracted_dest.replace('"', '').replace("'", "").strip()
            except:
                extracted_dest = user_input

            # 使用功能性模型進行地區判斷，避免廢話
            result = check_region_need_clarification(extracted_dest, model_functional)
            
            if result['need_clarification']:
                # 需要進一步細化
                state['info']['large_region'] = extracted_dest
                options = '、'.join(result['suggested_options'])
                return f"好的，去{extracted_dest}！\n\n請問您想去{extracted_dest}的哪個地區呢？\n(例如：{options})\n\n💡 如果都可以，請直接輸入「都可以」\n不想規劃了可以說「取消」。"
            else:
                # 直接記錄目的地
                state['info']['destination'] = extracted_dest
                return f"好的，去{extracted_dest}！請問預計去幾天？(例如：3天2夜)\n\n不想規劃了可以說「取消」。"

            
        # Check if we have specific area (for large regions)
        if 'large_region' in state['info'] and 'destination' not in state['info']:
            # 檢查是否要取消
            if any(keyword in user_input for keyword in ['取消', '不要了', '先不要', '暫停']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "好的，已取消行程規劃。"
            
            # 檢查是否說「都可以」類的詞 - 直接用大地區作為目的地
            if any(keyword in user_input for keyword in ['都可以', '都行', '隨便', '不挑', '任意', '推薦']):
                # 直接使用大地區作為目的地
                state['info']['destination'] = state['info']['large_region']
                return f"好的，{state['info']['large_region']}！請問預計去幾天？(例如：3天2夜)\n\n不想規劃了可以說「取消」。"
            
            state['info']['destination'] = user_input
            return f"好的，{state['info']['large_region']}的{user_input}！請問預計去幾天？(例如：3天2夜)\n\n不想規劃了可以說「取消」。"
            
        # Check if we have duration
        if 'duration' not in state['info']:
            # 檢查是否要取消
            if any(keyword in user_input for keyword in ['取消', '不要了', '先不要', '暫停']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "好的，已取消行程規劃。"
            state['info']['duration'] = user_input
            return f"了解，{state['info']['destination']}，{user_input}。請問這次旅遊有什麼特殊需求嗎？\n（沒有的話可以回「都可以」）\n\n⚠️ 回答後將開始生成行程，約15秒，期間請勿發送訊息！\n不想規劃了可以說「取消」。"
            
        # Check purpose
        if 'purpose' not in state['info']:
            # 檢查是否要取消
            if any(keyword in user_input for keyword in ['取消', '不要了', '先不要', '暫停']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "好的，已取消行程規劃。"
            state['info']['purpose'] = user_input
            state['stage'] = 'generating_plan'
            
            # Generate Plan
            dest = state['info']['destination']
            dur = state['info']['duration']
            purp = state['info']['purpose']
            
            planner_prompt = f"""
[CRITICAL SYSTEM INSTRUCTION]
You are a STRICTLY PROFESSIONAL Travel Planning Assistant.
ABSOLUTE RULES - NO EXCEPTIONS:
1. **ZERO JOKES** - Do NOT make ANY jokes, puns, or humorous remarks
2. **ZERO EMOJIS** - Do NOT use any emojis or emoticons  
3. **ZERO CASUAL LANGUAGE** - Maintain professional tone throughout
4. **ZERO EXCLAMATIONS** - Avoid overly enthusiastic language

**Language Requirement:**
- MUST respond in Traditional Chinese (繁體中文)
- Professional, informative, and helpful tone ONLY

**Task:** Create a detailed, practical trip plan
**Destination:** {dest}
**Duration:** {dur}
**Purpose:** {purp}

**Format Requirements:**
1. **MUST START WITH TITLE**: First line must be "{dest}，{dur}之旅"
2. **Readable Text Format**: Clean text with bullet points. NO Markdown headers (##).
3. Structure:
   
   {dest}，{dur}之旅
   
   【Day 1】
   [上午] (09:00-12:00)
    - 景點：XX
    - 停留時間：XX
    - 簡介：XX (50-60字簡介，重點特色描述)
   
   [下午] (13:00-17:00)
    ...
   
   【旅遊小提示】
    - 交通：...
   
4. **NO ADDRESSES** - Just spot names.

**Example Structure:**

【Day 1】
[上午] (09:00-12:00)
- 景點：[Spot Name]
- 停留時間：[Time]
- 簡介：[Brief Description (50-60 words)]

[下午] (13:00-17:00)
- ...

【旅遊小提示】
- 交通：...
- 預算：...
- 備註：...

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS. NO CASUAL LANGUAGE.
CRITICAL: Do NOT output as JSON. Output pure, clean text.
"""
            
            try:
                # 使用功能性模型生成行程 (避免 Motivational Speaker 人設干擾)
                response = model_functional.generate_content(planner_prompt)
                draft_plan = response.text
                
                # 執行邏輯檢查 (Validation Layer) - 仍使用 model_functional
                validated_plan = validate_and_fix_trip_plan(draft_plan, model_functional)
                
                # 保存行程內容，設為可討論狀態
                user_trip_plans[user_id] = {
                    'stage': 'can_discuss',
                    'info': state['info'],
                    'plan': validated_plan
                }
                return validated_plan + "\n\n如需調整行程，請直接說明您的需求。\n(例如：第一天想加入購物、想換掉某個景點等)\n\n如不需調整，請說「完成」或「ok」結束本服務！"
                
            except Exception as e:
                print(f"Planning error: {e}")
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "抱歉，行程規劃出了點問題，請稍後再試。"
    
    # 處理可討論狀態 - 允許用戶修改行程或繼續討論
    elif state['stage'] == 'can_discuss':
        # 檢查是否要結束討論（明確說完成）
        if any(keyword in user_input.lower() for keyword in ['完成', 'ok', '好了', '謝謝', '不用了']):
            user_trip_plans[user_id] = {'stage': 'idle'}
            return "好的！祝您旅途愉快！"
        
        
        # ===== AI智能判斷：修改行程 vs 跳話題 =====
        try:
            # 使用Gemini AI判斷用戶意圖
            intent_prompt = f"""用戶正在與我討論行程規劃，目前的行程如下：
{state.get('plan', '尚無行程內容')}

用戶現在說：「{user_input}」

請判斷用戶的意圖：
1. 「修改行程」- 用戶想要調整、修改、討論已規劃的行程
2. 「討論行程」- 用戶詢問行程相關問題（如景點資訊、交通、費用等）
3. 「跳話題」- 用戶想要使用其他功能（如製作長輩圖、看新聞、生成圖片等完全無關的新話題）

請只回傳以下JSON格式：
{{"intent": "修改行程" 或 "討論行程" 或 "跳話題"}}"""

            response = model_functional.generate_content(intent_prompt)
            import json, re
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            
            if match:
                intent_data = json.loads(match.group())
                intent = intent_data.get('intent', '討論行程')
                print(f"[TRIP] AI判斷意圖: {intent}, 用戶輸入: {user_input}")
                
                if intent == '跳話題':
                    # 明確跳到無關話題 → 自動關閉行程規劃
                    print(f"[TRIP] User switched topic, auto-closing trip planning.")
                    user_trip_plans[user_id] = {'stage': 'idle'}
                    # 讓主流程處理新話題（返回None表示繼續處理）
                    return None
            else:
                # AI回傳格式錯誤，保守處理：視為行程相關
                print(f"[TRIP] AI intent detection failed, treating as trip-related")
        except Exception as e:
            print(f"[TRIP] Intent detection error: {e}, treating as trip-related")
        
        # 否則視為行程相關（修改或討論）→ 繼續處理
        dest = state['info']['destination']
        dur = state['info']['duration']
        purp = state['info']['purpose']
        
        try:
            # 使用輔助函數修改行程 - 傳入 model_functional
            draft_updated_plan = modify_trip_plan(
                user_id=user_id,
                user_input=user_input,
                dest=dest,
                dur=dur,
                purp=purp,
                current_plan=state.get('plan', ''),
                model=model_functional, # 改用功能性模型
                line_bot_api_config=configuration
            )
            
            # 執行邏輯檢查 (Validation Layer)
            # 確保用戶修改後的行程仍然符合邏輯 (例如：下午不會跑到早上)
            updated_plan = validate_and_fix_trip_plan(draft_updated_plan, model_functional)
            
            # 更新保存的行程
            user_trip_plans[user_id]['plan'] = updated_plan
            return updated_plan + "\n\n還需要其他調整嗎？\n(如不需調整，請說「完成」或「ok」結束本服務！)"
            
        except Exception as e:
            print(f"[ERROR] 修改行程時發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            return "抱歉，修改行程時出了點問題，請再試一次。"

    return "請問還有什麼需要幫忙的嗎？"


def handle_meme_agent(user_id, user_input=None, image_content=None, is_new_session=False, reply_token=None):
    """處理長輩圖製作, reply_token 用於發送狀態通知"""
    global user_meme_state, user_images
    
    if is_new_session or user_id not in user_meme_state:
        # No image found, ask for one
        user_meme_state[user_id] = {'stage': 'waiting_bg', 'bg_image': None, 'text': None}
        return """好的！我們來製作長輩圖。

步驟1️⃣：選擇「背景」
📷 方法1：上傳一張圖片作為背景
🎨 方法2：描述背景樣子（例如：蓮花、夕陽、風景）

⚠️ 製作期間約15秒，請勿發送訊息！
＊隨時說「取消」可停止

(接著會進行 步驟2️⃣：輸入文字)"""


    state = user_meme_state[user_id]
    
    if state['stage'] == 'waiting_bg':
        # 檢查是否要取消
        if user_input and '取消' in user_input:
            user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
            # [Fix] Clear cached image to prevent reuse
            if user_id in user_images:
                del user_images[user_id]
            return "已取消長輩圖製作。"
        
        # Handle Image Upload (Passed via image_content)
        if image_content:
            # Save temporary image
            import tempfile
            temp_dir = tempfile.gettempdir()
            bg_path = os.path.join(temp_dir, f"{user_id}_bg_{int(datetime.now().timestamp())}.jpg")
            with open(bg_path, "wb") as f:
                f.write(image_content)
            
            state['bg_image'] = bg_path
            state['stage'] = 'waiting_text'  # 直接進入文字輸入階段，不需確認
            # 不發送圖片給用戶
            return """步驟2️⃣：輸入要在圖片上顯示的「文字」內容
(例如：早安、平安喜樂、認同請分享)
⚠️ 暫不支援emoji表情符號，請使用純文字
⚠️ 製作期間約15秒，請勿再次發送訊息！"""

            
        # Handle Text Description for Generation
        elif user_input:
             # [Fix] Intent Detection: Check if user wants to switch topic
             # Avoid capturing commands like "News", "Trip", "Weather" as image descriptions
             try:
                intent_check_prompt = f"""User is in Meme Creation Mode (Step 1: Describe Background).
Current Input: "{user_input}"

Analyze if this input is:
1. "description": A description for image generation (e.g., "flower", "mountain", "morning greeting", "happier").
2. "switch": A request to switch feature or chat about something else (e.g., "news", "planning trip", "weather", "help", "menu").

Output JSON: {{ "intent": "description" or "switch" }}"""
                
                check_res = model_functional.generate_content(intent_check_prompt)
                import json, re
                match = re.search(r'\{.*\}', check_res.text, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    if data.get('intent') == 'switch':
                        print(f"[MEME] User switched topic: {user_input}")
                        user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                         # [Fix] Clear cached image
                        if user_id in user_images: del user_images[user_id]
                        return None # Fall through to main loop
             except Exception as e:
                print(f"[MEME] Intent check failed: {e}")

             # Generate background
             
             # 使用 Gemini 將用戶的中文描述轉換成詳細的英文 prompt
             # 因為 Imagen 3 對英文效果更好
             translation_prompt = f"""User wants to generate a "Elderly Greetings" style background image. Their description is: "{user_input}"

Please convert this description into a detailed English prompt for Imagen 3.

Requirements:
1. Must accurately reflect the input description "{user_input}"
2. Add style descriptors suitable for Elderly Greetings (Bright, Positive, Clear, Vibrant)
3. If it is a landscape (mountain, water, flower, sunset), emphasize the scenery
4. If it is an object (lotus, rose), emphasize the object
5. Use English, detailed and specific. 
6. Follow the user's style description strictly. 
7. [[SMART SAFETY]]: If the description contains humans (child, girl, boy, man, woman, people) AND does NOT specify a style (like 'photo', 'realistic'), you MUST append ", warm illustration style" to the prompt to ensure safety compliance.
8. Return ONLY the English prompt, no other text

Example:
Input "Mountain and Water" -> "A beautiful natural landscape with lush green mountains and clear flowing water, bright and peaceful scenery, suitable for traditional Chinese meme card background, vibrant colors, photorealistic"

Now generate English prompt for: "{user_input}" """
             
             try:
                 # 使用 Gemini 翻譯 (使用功能性模型，避免廢話)
                 translation_response = model_functional.generate_content(translation_prompt)
                 bg_prompt = translation_response.text.strip()
                 
                 # ===== 配額檢查已經移至 generate_image_with_imagen 內部 =====
                 
                 # 生成圖片
                 success, result = generate_image_with_imagen(bg_prompt, user_id)
                 if success:
                     hint = remain_img_hint(user_id)
                     
                     state['bg_image'] = result  # result 是圖片路徑
                     state['stage'] = 'confirming_bg'
                     # 發送背景圖給用戶確認（使用 reply_token 免費）
                     msg = f"背景圖片已生成完成！\n\n請回答「好」或「ok」繼續，或直接描述想要的背景(例如：換成海邊、換成森林)即可重新生成。\n⚠️ 製作期間約15秒，請勿發送訊息！{hint}"
                     if send_image_to_line(user_id, result, msg, reply_token):
                         return None # 已回覆
                 else:
                     return f"抱歉，背景生成失敗。\n\n失敗原因：{result}\n\n請調整描述後再試一次，或傳一張圖片給我！"
             except Exception as e:
                 print(f"背景生成錯誤: {e}")
                 return "抱歉，背景生成出了點問題...請再試一次！"
    
    elif state['stage'] == 'confirming_bg':
        # 用戶確認背景
        if user_input:
            # 檢查是否要取消
            if '取消' in user_input:
                user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                # [Fix] Clear cached image to prevent reuse
                if user_id in user_images:
                    del user_images[user_id]
                return "已取消長輩圖製作。"
            # 用戶確認，進入文字輸入階段
            elif is_confirmation(user_input):
                state['stage'] = 'waiting_text'
                return "好的！請輸入要在圖片上顯示的文字內容。\n(例如：早安、平安喜樂、認同請分享)\n⚠️ 製作期間約15秒，請勿再次發送訊息，以免錯誤！"
            else:
                # [Fix] Intent Detection: Check for topic switch during modification
                try:
                    intent_check_prompt = f"""User is in Meme Creation Mode (Step 2: Review/Modify Background).
Current Input: "{user_input}"

Analyze if this input is:
1. "modify": A request to modify the background (e.g., "happier", "make it blue", "remove cat", "change style").
2. "switch": A request to switch feature or chat about something else (e.g., "news", "planning trip", "weather", "help").

Output JSON: {{ "intent": "modify" or "switch" }}"""
                    
                    check_res = model_functional.generate_content(intent_check_prompt)
                    import json, re
                    match = re.search(r'\{.*\}', check_res.text, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        if data.get('intent') == 'switch':
                            print(f"[MEME] User switched topic during confirm: {user_input}")
                            user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                            if user_id in user_images: del user_images[user_id]
                            return None # Fall through
                except Exception as e:
                    print(f"[MEME] Intent check failed: {e}")

                # 用戶輸入了其他內容 → 當作對背景的修改/精煉，重新生成
                try:
                    # [Fix] Context Loss Issue:
                    # User said "Happier" but we lost "Cartoon Parrot".
                    # We need to combine previous context with new request.
                    
                    # 嘗試從 state 中獲取上一次的描述 (目前 state 沒存，暫時無法完美回朔，但我們可以試著存)
                    # 由於 state 結構限制，我們這裡先假設 user_input 是新的完整描述，或者我們嘗試用 Vision 理解原圖 + 修改指令
                    # 但 Vision 比較慢。
                    # 更好的策略：
                    # 1. 如果有上一次的 prompt (需新增存儲機制)，則 combine.
                    # 2. 如果沒有，則將 current_bg 傳入 Vision 進行 "與 prompt 結合" 的修改 (Image-to-Image) -> 這是原本的邏輯
                    # 
                    # 原本邏輯：generate_image_with_imagen(bg_prompt, user_id, base_image_path=current_bg)
                    # 問題：Imagen 3 對 "Happier" + Base Image (Parrot) 可能理解為 "Make the parrot happier" 
                    # 但如果 Prompt 只有 "Happier"，Imagen 3 可能不知道要畫鸚鵡。
                    
                    print(f"[MEME] User wants to regenerate background with: {user_input}")
                    
                    current_bg = state.get('bg_image')
                    
                    # [Strategy] 使用 Gemini Vision 分析當前圖片 + 用戶指令 -> 產生全新的完整 Prompt
                    # 這樣可以解決 "Happier" 這種缺乏主體的指令
                    
                    refine_prompt = f"""
                    User wants to modify this background image.
                    Current Image: (Provided)
                    User's Modification Request: "{user_input}"
                    
                    Please generate a NEW, FULL English prompt for Imagen 3.
                    Requirements:
                    1. Keep the main subject of the current image (analyze it!).
                    2. Apply the user's modification (e.g., "Happier" -> "Smiling, joyful expression", "Darker" -> "Night scene").
                    3. Return ONLY the English prompt.
                    """
                    
                    import PIL.Image
                    current_bg_img = PIL.Image.open(current_bg)
                    
                    # 使用功能性模型進行圖文理解
                    refined_response = model_functional.generate_content([refine_prompt, current_bg_img])
                    new_full_prompt = refined_response.text.strip()
                    
                    print(f"[MEME] Refined Prompt: {new_full_prompt}")

                    # 使用新 Prompt 生成
                    # [Strategy Update] Re-enable base_image_path for reference.
                    # Even if Imagen 3's edit_image is unstable, passing base_image_path allows 
                    # the underlying generate_image_with_imagen function to choose the best strategy 
                    # (e.g., trying edit_image first, or using it as a reference if supported).
                    # Crucially, we MUST rely on the strong `new_full_prompt` to guide the generation 
                    # effectively, acting as a "pseudo-edit" if true editing fails.
                    
                    print(f"[MEME] Regenerating with refined prompt and base image reference...")
                    # 配額檢查已移入核心
                    
                    success, result = generate_image_with_imagen(new_full_prompt, user_id, base_image_path=current_bg) 
                    
                    if success:
                        hint = remain_img_hint(user_id)
                        state['bg_image'] = result
                        state['stage'] = 'confirming_bg'
                        msg = f"背景圖片已根據您的要求重新生成！\n\n請回答「好」或「ok」繼續，\n或直接描述想要的背景即可再次重新生成。\n\n⚠️ 送出後需等待約15秒，期間請勿再次發送訊息！{hint}"
                        if send_image_to_line(user_id, result, msg, reply_token):
                            return None  # 已回覆
                    else:
                        # 如果失敗，回傳錯誤但保留原圖
                        return f"抱歉，重新生成失敗 ({result})。\n請換個說法試試看！"
                        
                except Exception as e:
                    print(f"[MEME] Background regeneration error: {e}")
                    return "抱歉，重新生成出了點問題...請再試一次！"
    
    elif state['stage'] == 'waiting_text':
        if user_input:
            # 檢查是否要取消
            if '取消' in user_input:
                user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                # [Fix] Clear cached image to prevent reuse
                if user_id in user_images:
                    del user_images[user_id]
                return "已取消長輩圖製作。"

            # [Fix] Intent Detection: Check for topic switch during text input
            try:
                intent_check_prompt = f"""User is in Meme Creation Mode (Step 3: Enter Text).
Current Input: "{user_input}"

Analyze if this input is:
1. "text": Content to be displayed on the image (e.g., "Good Morning", "Hello", "Blessings", short phrases).
2. "switch": A request to switch feature or chat about something else (e.g., "news", "planning trip", "weather", "help", "menu").

Output JSON: {{ "intent": "text" or "switch" }}"""
                
                check_res = model_functional.generate_content(intent_check_prompt)
                import json, re
                match = re.search(r'\{.*\}', check_res.text, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    if data.get('intent') == 'switch':
                        print(f"[MEME] User switched topic during text input: {user_input}")
                        user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                        if user_id in user_images: del user_images[user_id]
                        return None # Fall through to main loop
            except Exception as e:
                print(f"[MEME] Intent check failed: {e}")
            
            state['text'] = user_input
            
            # Design Logic
            text = user_input
            bg_path = state['bg_image']
            
            # 完全隨機創意排版（移除 AI 判斷，確保每次都有變化）
            import random
            from PIL import Image
            
            try:
                from PIL import Image
                import random
                
                # 載入背景圖片
                bg_image = Image.open(bg_path)
                
                # [Fix] Inject randomness to force variety on re-generation
                import random
                random_vibes = ["Pop Art", "Elegant", "Bold", "Minimalist", "Retro", "Modern", "Handwritten Style", "Cute", "Serious"]
                current_vibe = random.choice(random_vibes)
                
                vision_prompt = f"""Analyze this image and design text layout for: "{text}"

**DESIGN GOAL: {current_vibe} Style**

**STEP 1: FIND THE MAIN SUBJECT**
Look at the image carefully. Identify the main subject (person, animal, object, flower).
Determine which AREA the subject occupies: "top", "bottom", "left", "right", "center"

**STEP 2: PLACE TEXT IN THE OPPOSITE AREA**
- If subject is at TOP → text goes at BOTTOM
- If subject is at BOTTOM → text goes at TOP  
- If subject is at CENTER → text goes at edges (top-left, top-right, bottom-left, bottom-right)
- If subject is at LEFT → text goes at RIGHT
- If subject is at RIGHT → text goes at LEFT

**STEP 3: DETERMINE FONT SIZE**
- If subject covers > 50% of image (LARGE subject) → Use SMALLER font (50-80px) to fit in gaps
- If subject is small/clean background → Use LARGER font (90-130px)

**NEVER put text over the main subject! It's okay to cover unimportant corners.**

**Color choices:**
**STRATEGY: EXTRACT FROM IMAGE**
1. **Analyze the Image Palette:** Identify the dominant colors in the image.
2. **Option A (Harmony):** Choose a color that **EXISTS in the image** or is a **similar shade (Analogous)**, provided it matches the vibe and is readable.
3. **Option B (Contrast):** If harmony fails readability, use a **Complementary Color** (opposite on color wheel) derived from the image's palette.
4. **Avoid:** Generic default colors (plain white/yellow) unless they are part of the image's specific aesthetic.
5. **Format:** Output exact HEX CODES based on the image analysis.

**Output JSON (MUST include subject_location):**
{{
  "subject_location": "top/bottom/left/right/center",
  "subject_size": "large/small",
  "position": "top/bottom/left/right/top-left/top-right/bottom-left/bottom-right",
  "color": "#HEXCODE",
  "stroke_color": "#HEXCODE",
  "font_size": 60-130,
  "stroke_width": 6-16
}}

Text to display: "{text}"
"""

                # 使用功能性模型進行排版分析，但臨時調高溫度以增加創意
                response = model_functional.generate_content(
                    [vision_prompt, bg_image],
                    generation_config=genai.types.GenerationConfig(
                        temperature=1.2, # 調高溫度，增加隨機性
                        top_p=0.95,
                        top_k=40
                    )
                )
                result = response.text.strip()
                
                print(f"[AI CREATIVE] Raw: {result[:100]}...")
                
                # 解析 JSON 或 Regex
                import re
                import json
                
                # 預設值 - 應該要被AI覆蓋
                position = 'top'
                direction = 'horizontal'
                # color = '#FFFFFF'  <-- REMOVED DEFAULT
                color = None # Let it be None to trigger fallback if AI fails
                font = 'heiti'
                angle = 0
                stroke_width = 10  # 預設描邊寬度 (對閱讀很重要)
                stroke_color = '#000000'
                size = 80  # 預設字體大小
                
                try:
                    # 嘗試解析 JSON
                    json_match = re.search(r'\{.*\}', result, re.DOTALL)
                    if json_match:
                        data = json.loads(json_match.group())
                        subject_location = data.get('subject_location', 'center')
                        subject_size = data.get('subject_size', 'small') # 新增：主體大小
                        position = data.get('position', 'top')
                        direction = data.get('direction', 'horizontal')
                        color = data.get('color')
                        if not color or color == 'null':
                             # [Fix] Fallback to random high-contrast color if AI misses it
                             import random
                             fallback_colors = ['#FFFFFF', '#FFD700', '#FF0000', '#0000FF', '#00FF00', '#FFA500', '#FF69B4']
                             color = random.choice(fallback_colors)
                             print(f"[AI COLOR MISS] AI didn't return color, used random fallback: {color}")
                        font = data.get('font', 'heiti')
                        angle = int(data.get('angle', 0))
                        stroke_width = int(data.get('stroke_width', 10))
                        stroke_color = data.get('stroke_color', '#000000')
                        size = int(data.get('font_size', 80))
                        decorations = data.get('decorations', [])
                        
                        # 📏 根據主體大小自動調整字體 (Auto-Resize)
                        if subject_size == 'large':
                            # 主體很大時，強制縮小字體以塞入縫隙，但保持至少 60px
                            if size > 90:
                                print(f"[MEME RESIZE] Subject is large, shrinking font from {size} to 90px")
                                size = 90
                        else:
                            # 主體很小或背景乾淨，允許大字體，但確保不小於 70px
                            if size < 70:
                                size = 70
                        
                        # 🚨 位置安全檢查：確保文字不會蓋住主體
                        opposite_map = {
                            'top': ['bottom', 'bottom-left', 'bottom-right'],
                            'bottom': ['top', 'top-left', 'top-right'],
                            'left': ['right', 'top-right', 'bottom-right'],
                            'right': ['left', 'top-left', 'bottom-left'],
                            'center': ['top-left', 'top-right', 'bottom-left', 'bottom-right']
                        }
                        safe_positions = opposite_map.get(subject_location, ['top', 'bottom'])
                        
                        # 🚨 安全檢查：只在文字位置會蓋住主體時才修正
                        # 注意：如果主體不在 center（如風景圖），center 位置是安全的
                        is_unsafe = (
                            subject_location in position or 
                            position == subject_location or
                            (position == 'center' and subject_location == 'center')  # 只有主體在中間時才禁止 center
                        )
                        
                        if is_unsafe:
                            import random
                            old_position = position
                            position = random.choice(safe_positions)
                            print(f"[MEME SAFETY] Position corrected: {old_position} → {position} (subject at {subject_location})")
                        
                        # DEBUG: 顯示AI選擇的風格
                        print(f"[MEME AI] Subject={subject_location}, Position={position}, Color={color}, Stroke={stroke_width}px")
                    else:
                        raise ValueError("No JSON found")
                        
                except Exception as parse_e:
                    print(f"[AI PARSE ERROR] {parse_e}, trying fallback regex")
                    decorations = []  # 如果解析失敗，裝飾為空
                    # 安全預設：文字放底部
                    position = 'bottom'
                    pass
                
                # [Final Decision] Unlock AI Color Choice
                # Allow AI to pick ANY hex code or color name.
                # create_meme_image will handle the rendering.
                pass

                print(f"[AI CREATIVE] {text[:10]}... → {position}, {color}, {font}, {size}px, stroke={stroke_width}")
                
                # 傳遞 decorations 參數
                final_path = create_meme_image(bg_path, text, user_id, font, size, position, color, angle, stroke_width, stroke_color, decorations)
                
                # Send - 使用 reply_token 免費發送
                if final_path:
                    if send_image_to_line(user_id, final_path, "長輩圖製作完成，讚喔！", reply_token):
                        state['stage'] = 'idle'
                        return None # 已回覆
                    else:
                        state['stage'] = 'idle'
                        return "長輩圖已製作但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)未設定。\n請檢查 .env 文件中的 IMGBB_API_KEY。"
                else:
                    return "製作失敗了... (Layout Error)"
                    
            except Exception as e:
                print(f"[VISION ERROR] {e}，使用隨機創意 fallback")
                # Fallback: 隨機創意而非固定值 (包含 rainbow 選項)
                all_positions = ['top-left', 'top-right', 'bottom-left', 'bottom-right', 'top', 'bottom']
                all_colors = ['rainbow', '#FFD700', '#FF8C00', '#FF1493', '#00CED1', '#32CD32', '#DC143C']
                all_fonts = ['kaiti', 'heiti']
                all_angles = [0, 5, 8, -5, -8]
                
                position = random.choice(all_positions)
                color = random.choice(all_colors)
                font = random.choice(all_fonts)
                angle = random.choice(all_angles)
                size = 65
                
                print(f"[FALLBACK CREATIVE] {text[:10]}... → {position}, {color}, {font}, {size}號, {angle}度")

            
            final_path = create_meme_image(bg_path, text, user_id, font, size, position, color, angle)
            
            # Send - 使用 reply_token 免費發送
            if final_path:
                if send_image_to_line(user_id, final_path, "長輩圖製作完成，讚喔！", reply_token):
                    state['stage'] = 'idle'
                    return None # 已回覆
                else:
                    state['stage'] = 'idle'
                    return "長輩圖已製作但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)未設定。\n請檢查 .env 文件中的 IMGBB_API_KEY。"
            else:
                return "製作失敗了...\n\n輸入「取消」可取消，或再試一次！"

    return "發生了一些問題...\n\n輸入「取消」可重新開始。"



def is_confirmation(user_input):
    """
    Check if the user input is a confirmation (Yes/OK).
    Strictly checks for short, exact matches to avoid false positives.
    """
    if not user_input: return False
    clean_input = user_input.strip().lower()
    
    # 1. Exact Match for Short Words (Highest Priority)
    # Allows valid variations like "好", "可以", "沒問題"
    # But REJECTS sentences containing these words (e.g. "畫好的圖")
    exact_keywords = [
        "好", "好的", "好喔", "好啊", "好哒", "好滴",
        "可以", "行", "沒問題", "ok", "k", "yes", "y",
        "對", "對的", "沒錯", "正確", "是", "是的",
        "確定", "confirm", "sure", "go", "start", "開始", "生成",
        "完成", "好了", "結束", "謝謝", "感謝"
    ]
    
    # Remove common punctuation just for the check
    import re
    clean_text_no_punct = re.sub(r'[^\w\s]', '', clean_input) 
    
    if clean_text_no_punct in exact_keywords:
        return True
        
    # 2. Length-based Safety Check
    # If the input contains a keyword but is very short (< 5 chars), treat as confirmation.
    # e.g. "ok..." (length 5) -> True
    # e.g. "畫一個好的" (length 5) -> False (contains "好" but unlikely confirmation)
    
    if len(clean_input) <= 4:
         if any(k == clean_text_no_punct for k in exact_keywords):
             return True
             
    return False

# ======================
# Main LLM Function
# ======================

def classify_user_intent(text):
    """使用 AI 判斷用戶意圖"""
    try:
        # 強制規則 (Regex Fallback) - 優先於 AI 判斷
        # 1. 優先判斷取消/刪除提醒 (因為包含「提醒」二字，必須先於設定提醒判斷)
        if any(kw in text for kw in ["取消提醒", "刪除提醒", "不要提醒", "cancel reminder", "delete reminder"]):
            return "cancel_reminder"
            
        if any(kw in text for kw in ["提醒我", "設提醒", "叫我", "提醒", "remind me"]):
            return "set_reminder"
        if any(kw in text for kw in ["我的提醒", "查看提醒", "待辦", "提醒列表", "my reminders"]):
            return "show_reminders"
            
        # 2. 優先判斷長輩圖/梗圖製作 (包含「加文字」指令)
        if any(kw in text for kw in ["長輩圖", "梗圖", "加文字", "加上文字", "迷因", "meme"]):
            return "meme_creation"

        # 3. 判斷一般圖片生成 (避免 AI 誤判為 chat)
        # 用戶說 "畫一隻...", "生成一張...", "給我一張...圖片"
        if any(kw in text for kw in ["畫一", "生成一", "產生一", "製作一", "create a image", "generate a image"]):
            return "image_generation"
        if "圖片" in text and any(kw in text for kw in ["給我", "想要", "來一張", "一張", "生"]):
            return "image_generation"
            
        classification_prompt = f"""
        Analyze user input: "{text}"
        
        Classify into exactly one intent category (Return ONLY the code, nothing else):
        1. video_generation (Make video, generate video)
        2. image_generation (Draw picture, generate image)
        3. image_modification (Explicitly ASK to CHANGE/MODIFY image content. Questions about image content -> chat)
        4. meme_creation (Make meme, elderly greeting card)
        5. trip_planning (Plan trip, travel, suggest spots)
        6. set_reminder (Set reminder, remind me to...)
        7. show_reminders (Check reminders, what to do)
        8. chat (General chat, greeting, others, AND Questions about image content e.g. "What color is this?")
        
        Examples:
        - "I want to go to Yilan" -> trip_planning
        - "Bring me to Green Island" -> trip_planning
        - "Change cat to dog" -> image_modification
        - "What color is the person wearing?" -> chat
        - "Draw a cat" -> image_generation
        - "Remind me to eat medicine" -> set_reminder
        - "Good morning" -> chat
        
        Your Answer (Just the code):"""
        # 使用功能性模型進行意圖分類
        response = model_functional.generate_content(classification_prompt)
        intent = response.text.strip().lower()
        
        # 清理可能的多餘符號
        import re
        match = re.search(r'(video_generation|image_generation|image_modification|meme_creation|trip_planning|set_reminder|show_reminders|chat)', intent)
        if match:
            return match.group(1)
        return "chat"
    except Exception as e:
        print(f"Intent classification error: {e}")
        return "chat"

def gemini_llm_sdk(user_input, user_id=None, reply_token=None):
    """主要 LLM 處理函數, reply_token 用於發送狀態通知"""
    global chat_sessions, user_image_generation_state, user_meme_state, user_trip_plans, user_images, user_video_state, user_daily_video_count, user_last_image_prompt
    
    try:
        # 檢查是否要清除記憶（關鍵字匹配）
        # 重要：如果用戶正在進行長輩圖/行程規劃等流程，不應該檢查清除記憶
        in_active_flow = False
        if user_id:
            # 檢查是否在任何流程中
            if user_id in user_meme_state and user_meme_state.get(user_id, {}).get('stage') != 'idle':
                in_active_flow = True
            if user_id in user_trip_plans and user_trip_plans.get(user_id, {}).get('stage') != 'idle':
                in_active_flow = True
            if user_id in user_image_generation_state and user_image_generation_state.get(user_id) not in ['idle', None]:
                in_active_flow = True
        
        should_clear = False
        if not in_active_flow:  # 只有在沒有進行中的流程時才檢查清除記憶
            clear_keywords = ["重新開始", "清除記憶", "忘記我", "重置對話", "新的開始", "清空記憶", "reset", "重來", "忘掉", "清空"]
            should_clear = any(keyword in user_input for keyword in clear_keywords)
            
            # 用 AI 判斷是否有清除記憶的意圖（更智慧的判斷）
            intent_check_keywords = ["重新", "清除", "忘記", "重置", "清空", "reset", "記憶", "對話", "開始"]
            if not should_clear and any(keyword in user_input for keyword in intent_check_keywords):
                # 用簡單的 AI 呼叫來判斷意圖 (使用功能性模型)
                intent_prompt = f"使用者說：「{user_input}」。請判斷使用者是否想要清除對話記憶、重新開始對話？只回答「是」或「否」。"
                intent_response = model_functional.generate_content(intent_prompt)
                should_clear = "是" in intent_response.text
        
        if should_clear:
            # 清除該用戶的所有記憶
            if user_id in chat_sessions:
                del chat_sessions[user_id]
            if user_id in last_activity:
                del last_activity[user_id]
            if user_id in user_images:
                del user_images[user_id]
            if user_id in user_image_generation_state:
                del user_image_generation_state[user_id]
            if user_id in user_meme_state:
                del user_meme_state[user_id]
            if user_id in user_trip_plans:
                del user_trip_plans[user_id]
            return "好的！我已經清除所有記憶了，讓我們重新開始吧！有任何問題都可以問我～"
        
        # 檢查表情符號（但在長輩圖模式下不攔截）
        meme_state = user_meme_state.get(user_id, {})
        if meme_state.get('stage') != 'waiting_text':  # 只有不在長輩圖輸入模式時才檢測表情符號
            emoji_emotion = analyze_emoji_emotion(user_input)
            if emoji_emotion and len(user_input) < 10:
                return get_emoji_response(emoji_emotion)
        
        # 檢查對話是否過期
        now = datetime.now()
        if user_id in last_activity:
            time_diff = now - last_activity[user_id]
            if time_diff > SESSION_TIMEOUT:
                # 對話已過期，清除舊記錄
                print(f"Session expired for user {user_id}, clearing history")
                if user_id in chat_sessions:
                    del chat_sessions[user_id]
                if user_id in user_images:
                    del user_images[user_id]
                if user_id in user_image_generation_state:
                    del user_image_generation_state[user_id]
        
        # 更新最後活動時間
        last_activity[user_id] = now
        
        # 檢查用戶是否想取消操作
        if user_input.strip() in ["取消", "不做了", "不想做了", "停止", "cancel", "不要了", "先不要", "放棄", "quit", "exit"]:
            # 完全清除所有狀態，避免殘留
            if user_id in user_image_generation_state:
                del user_image_generation_state[user_id]
            if user_id in user_last_image_prompt:
                del user_last_image_prompt[user_id]
            if user_id in user_meme_state:
                user_meme_state[user_id] = {'stage': 'idle'}
            if user_id in user_video_state:
                user_video_state[user_id] = 'idle'
            print(f"[CANCEL] Cleared all states for user {user_id}")
            print(f"[CANCEL] Cleared all states for user {user_id}")
            return "好的，已取消當前操作！"

        # ===== NEW: 功能選單直接跳轉邏輯 =====
        # 定義功能關鍵詞（與功能選單一致）
        function_keywords = {
            'image_generation': ['生成圖片', '產生圖片', '畫一張', '製作圖片', '做圖', '畫圖', '給我一張圖'],
            'meme_creation': ['製作長輩圖', '長輩圖', '做長輩圖', '梗圖', '早安圖'],
            'trip_planning': ['行程規劃', '規劃行程', '旅遊規劃', '旅行規劃'],
            'news': ['新聞快報', '看新聞', '今日新聞', '新聞'],
            'reminder': ['提醒通知', '設定提醒', '我的提醒', '查看提醒'],
        }
        
        # 檢查是否匹配功能關鍵詞
        matched_function = None
        for func_name, keywords in function_keywords.items():
            if any(kw in user_input for kw in keywords):
                matched_function = func_name
                break
        
        # 如果匹配到功能關鍵詞，清除當前狀態並準備跳轉
        # 例外：如果用戶說的是圖片生成關鍵字（如「畫一張」），但輸入裡含有
        # 指向上傳照片的代詞（她、他、這個人、照片等），應視為對照片做風格修改，
        # 而非生成全新圖片。此時不清除 user_uploaded_image_pending，讓修改邏輯優先。
        image_reference_words = ['她', '他', '這個人', '這張', '照片中', '圖片中', '主角', '把她', '把他', '照片裡', '圖裡']
        is_photo_style_request = (
            matched_function == 'image_generation'
            and user_id in user_uploaded_image_pending
            and any(w in user_input for w in image_reference_words)
        )
        
        if matched_function and not is_photo_style_request:
            # 清除所有狀態
            if user_id in user_image_generation_state:
                user_image_generation_state[user_id] = 'idle'
            if user_id in user_meme_state:
                user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
            if user_id in user_trip_plans:
                user_trip_plans[user_id] = {'stage': 'idle'}
            if user_id in user_link_pending:
                del user_link_pending[user_id]
            if user_id in user_uploaded_image_pending:
                del user_uploaded_image_pending[user_id]
            
            print(f"[FUNCTION_JUMP] User {user_id} jumped to {matched_function}")
            # 繼續執行，讓後續的intent detection處理新功能
        elif is_photo_style_request:
            print(f"[FUNCTION_JUMP] Skipped clear: photo style request detected ('{user_input[:30]}')")
        # ===== END: 功能選單直接跳轉邏輯 =====


        # ===== NEW: 圖片修改意圖檢測 =====
        # 檢查用戶是否剛上傳圖片並想修改
        if user_id in user_uploaded_image_pending:
            # 如果用戶說「完成」或「ok」，清除pending狀態
            if is_confirmation(user_input):
                del user_uploaded_image_pending[user_id]
                return "好的！圖片已完成。期待下次為您服務！"
                
            pending_data = user_uploaded_image_pending[user_id]
            user_image_list = pending_data.get('images', [])
            
            # 定義修改關鍵詞
            modify_keywords = {
                'style': ['改成', '變成', '換成', '修改成', '轉成', '風格', '樣式'],
                'add': ['加上', '新增', '增加', '放', '添加'],
                'remove': ['去掉', '移除', '刪除', '去除', '拿掉', '刪掉'],
                'merge': ['合併', '融合', '合在一起', '結合', '混合', '合體',
                          '加入', '並排', '放進去', '放入', '拼在一起', '乆在一起',
                          '两人一起', '放在同一張', '放到同一張']  # 擴充自然表达方式
            }
            
            #檢查是否為任一類修改請求 (關鍵詞快速匹配)
            is_modify = any(
                any(kw in user_input for kw in keywords)
                for keywords in modify_keywords.values()
            )
            # 關鍵詞判定的 is_merge
            is_merge_by_kw = any(kw in user_input for kw in modify_keywords['merge'])
            
            # AI 判斷意圖（關鍵詞沒匹到才呼叫，節省資源）
            ai_intent = None  # 'merge' / 'modify' / 'other'
            if (not is_modify) and len(user_image_list) > 0:
                try:
                    num_images = len(user_image_list)
                    
                    # 取出歷史紀錄協助判斷
                    history_summary = ""
                    history = pending_data.get('history', [])
                    if history:
                        last_action = history[-1]['type']
                        history_summary = f"（提示：用戶前一步是對圖片進行了「{'修改' if last_action == 'modify' else '融合'}」，此為連續修圖）"
                    else:
                        if num_images == 2:
                            history_summary = f"（提示：這是在**沒有修改紀錄**的情況下，剛才一次上傳了 2 張不同的照片，所以極高機率是要進行「融合」將兩者畫面結合或替換。）"
                        elif num_images == 1:
                            history_summary = f"（提示：用戶只上傳了 1 張照片，因此只可能是「修改」，絕對不是融合。）"

                    intent_check = model_functional.generate_content(
                        f"用戶上傳或累積了 {num_images} 張照片，現在說：「{user_input}」。{history_summary}\n"
                        f"請嚴格判斷他的意圖是：\n"
                        f"1. 融合：明確要求把最新的兩張照片或其中元素合成一張（如某物加到另一張、兩張合體、把A換成B等）。如果在沒歷史紀錄下剛傳兩張圖，大概率是融合！\n"
                        f"2. 修改：想修改「目前的最後一張圖片」（如加衣服、改顏色、加文字、換背景等）。如果用戶只是想針對上一張修改好的圖繼續追加修改，請絕對選修改！\n"
                        f"3. 其他：聊天、問問題、或切換到其他功能不改圖。\n"
                        f"只回答一個詞：融合 或 修改 或 其他"
                    )
                    result_text = intent_check.text.strip()
                    if '融合' in result_text:
                        ai_intent = 'merge'
                        is_modify = True
                    elif '修改' in result_text:
                        ai_intent = 'modify'
                        is_modify = True
                    else:
                        ai_intent = 'other'
                    print(f"[IMAGE_INTENT] AI decided: {ai_intent} for '{user_input[:30]}'")
                except Exception as e:
                    print(f"[IMAGE_INTENT] AI intent check failed: {e}")

            if is_modify and len(user_image_list) > 0:
                # 判斷是融合還是單張修改（AI 優先，其次關鍵字）
                is_merge = (ai_intent == 'merge') or (ai_intent is None and is_merge_by_kw)
                
                # 防呆：如果歷史只有1張圖且是原始圖，卻說要融合，降級為 modify
                if is_merge and len(user_image_list) < 2:
                    is_merge = False
                    print("[IMAGE_INTENT] Overridden merge to modify (not enough images to merge)")

                
                if is_merge and len(user_image_list) >= 2:
                    # 融合模式：使用最近兩張照片
                    image1_path = user_image_list[-2]
                    image2_path = user_image_list[-1]
                    
                    try:
                        print(f"[IMAGE_MERGE] Calling Gemini edit, user_input: {user_input}")
                        
                        # ===== 配額檢查（融合/修改/生成三者共用每日 6 次）=====
                        quota_ok, _, quota_msg = check_image_quota(user_id)
                        if not quota_ok:
                            return quota_msg
                        
                        # [Gemini Edit] 直接傳入兩張圖給 Gemini 進行融合
                        success, result = edit_image_with_gemini(
                            edit_prompt=user_input,
                            user_id=user_id,
                            image_path1=image1_path,
                            image_path2=image2_path
                        )
                        
                        if success:
                            new_image_path = result
                            # 更新圖片列表
                            user_image_list.append(new_image_path)
                            if len(user_image_list) > 5:
                                old_img = user_image_list.pop(0)
                                try:
                                    os.remove(old_img)
                                except:
                                    pass
                            
                            # 記錄歷史，並儲存最新圖片路徑供後續修改使用
                            pending_data['history'].append({
                                'request': user_input,
                                'result_path': new_image_path,
                                'type': 'merge'
                            })
                            user_last_generated_image_path[user_id] = new_image_path
                            
                            # 發送圖片
                            hint = remain_img_hint(user_id)
                            msg = f"照片融合完成🎉\n\n已融合「最近兩張照片」！\n如需再次修改，請直接說明調整需求。\n如不需調整，請說「完成」或「ok」結束本服務！{hint}"
                            if send_image_to_line(user_id, new_image_path, msg, reply_token):
                                return None  # 已回覆
                            else:
                                return "融合完成，但發送失敗。"
                        else:
                            return f"融合失敗：{result}"
                    except Exception as e:
                        print(f"[IMAGE_MERGE_ERROR] {e}")
                        return "照片融合時發生錯誤，請稍後再試。"
                        
                else:
                    # 單張修改模式：使用最後一張照片
                    original_image_path = user_image_list[-1]
                    
                    try:
                        print(f"[IMAGE_MODIFY] Calling Gemini edit, user_input: {user_input}")
                        
                        # ===== 配額檢查（融合/修改/生成三者共用每日 6 次）=====
                        quota_ok, _, quota_msg = check_image_quota(user_id)
                        if not quota_ok:
                            return quota_msg
                        
                        # [Gemini Edit] 直接傳入原圖給 Gemini 進行修改
                        success, result = edit_image_with_gemini(
                            edit_prompt=user_input,
                            user_id=user_id,
                            image_path1=original_image_path
                        )
                        
                        if success:
                            new_image_path = result
                            user_last_generated_image_path[user_id] = new_image_path
                            # 更新圖片列表
                            user_image_list.append(new_image_path)
                            if len(user_image_list) > 5:
                                old_img = user_image_list.pop(0)
                                try:
                                    os.remove(old_img)
                                except:
                                    pass
                            
                            # 記錄歷史
                            pending_data['history'].append({
                                'request': user_input,
                                'result_path': new_image_path,
                                'type': 'modify'
                            })
                            
                            # save prompt for further modification
                            user_last_image_prompt[user_id] = {'prompt': user_input}

                            # 發送圖片
                            hint = remain_img_hint(user_id)
                            msg = f"圖片修改完成🎉\n\n如需再次修改，請直接說明調整需求。\n如不需調整，請說「完成」或「ok」結束本服務！{hint}"
                            if send_image_to_line(user_id, new_image_path, msg, reply_token):
                                return None  # 已回覆
                            else:
                                return "修改完成，但發送失敗。"
                        else:
                            return f"修改失敗：{result}"
                    except Exception as e:
                        print(f"[IMAGE_MODIFY_ERROR] {e}")
                        return "圖片修改時發生錯誤，請稍後再試。"
        # ===== END: 圖片修改意圖檢測 =====


        if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
             # ... (keep existing logic for image state handling, we will rely on lines 1056-1139 handled below)
             # Wait, the block 1056-1139 is for handling specific states.
             # We need to insert the CLASSIFICATION check *after* the state checks if state is IDLE.
             pass

        # ------------------------------------------------------------
        #  AI 意圖判斷 (取代舊的關鍵字檢測)
        # ------------------------------------------------------------
        
        # 但首先，必須先處理「正在進行中」的狀態 (State Handling)
        # 因為如果用戶正在生圖流程中回答問題，不應該被分類為新意圖
        
        # 檢查 Agent 狀態 (若在對話流程中，直接交給 Agent)
        if user_id in user_meme_state and user_meme_state.get(user_id, {}).get('stage') != 'idle':
             response = handle_meme_agent(user_id, user_input, reply_token=reply_token)
             if response:
                 return response
             
        if user_id in user_trip_plans and user_trip_plans.get(user_id, {}).get('stage') != 'idle':
             response = handle_trip_agent(user_id, user_input, reply_token=reply_token)
             if response:
                 # If agent returns a response, return it. 
                 # If it returns None (e.g. topic switch), fall through to main logic.
                 return response

        # 檢查圖片生成狀態 (優先處理)
        if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
            state = user_image_generation_state[user_id]
            
            # [MOVED BLOCK START] 處理可修改狀態 (從下方移至此處)
            if state == 'can_modify':
                # 檢查是否要結束修改
                if is_confirmation(user_input):
                    user_image_generation_state[user_id] = 'idle'
                    return "好的！圖片已完成。期待下次為您服務！"
                
                # 檢查是否只是說「修改」
                if user_input.strip() in ['修改', '要修改', '我要修改']:
                    user_image_generation_state[user_id] = 'waiting_for_modification'
                    return "好的，請說明您想要如何修改這張圖片？\n(例如：加上文字、改變顏色、調整內容等)\n\n如不需調整，請說「完成」或「ok」結束本服務！" 
                else:
                    # [Fix] Intent Detection: Check for topic switch before assuming modification
                    # Because "can_modify" traps user if they say "weather" or "news"
                    try:
                        intent_check_prompt = f"""User is in Image Generation Mode (Reviewing Image).
Current Input: "{user_input}"

Analyze if this input is:
1. "modify": A request to modify the image (e.g., "add a cat", "change to night", "make it blue", "text overlay").
2. "switch": A request to switch feature or chat about something else (e.g., "news", "planning trip", "weather", "help", "cancel").

Output JSON: {{ "intent": "modify" or "switch" }}"""
                        check_res = model_functional.generate_content(intent_check_prompt)
                        import json, re
                        match = re.search(r'\{.*\}', check_res.text, re.DOTALL)
                        if match:
                            data = json.loads(match.group())
                            if data.get('intent') == 'switch':
                                print(f"[IMAGE_MOD] User switched topic: {user_input}")
                                user_image_generation_state[user_id] = 'idle'
                                # Recursive call to handle the input as a new intent
                                return gemini_llm_sdk(user_input, user_id, reply_token)
                    except Exception as e:
                        print(f"[IMAGE_MOD] Intent check failed: {e}")

                    # 直接說修改內容，進入修改流程
                    user_image_generation_state[user_id] = 'generating'
                    
                    last_prompt = user_last_image_prompt.get(user_id, "")
                    optimize_prompt = f"""
                    系統：用戶想要修改之前的圖片。
                    舊提示詞：{last_prompt}
                    用戶修改需求：{user_input}
                    
                    請產生新的英文 Prompt。如果用戶要求加字，請放入 text_overlay。
                    回傳 JSON: {{ "image_prompt": "...", "text_overlay": "..." }}
                    要求：
                    1. 保留舊圖核心。 
                    2. 絕對不要講笑話。
                    3. text_overlay 必須是「純文字」，禁止包含括號、表情描述 (如 (red heart)) 或任何非顯示用的文字。
                    """
                    
                    # [Strategy Update] Retrieve last generated image path for reference
                    current_bg = user_last_generated_image_path.get(user_id)
                    # Verify file exists
                    if current_bg and not os.path.exists(current_bg):
                        current_bg = None
                        
                    print(f"[IMAGE_MOD] Using base image for reference: {current_bg}")
                    try:
                        # 使用功能性模型解析 Prompt
                        optimized = model_functional.generate_content(optimize_prompt)
                        import json, re
                        image_prompt = optimized.text.strip()
                        text_overlay = None
                        try:
                            match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                            if match:
                                data = json.loads(match.group())
                                image_prompt = data.get('image_prompt', image_prompt)
                                text_overlay = data.get('text_overlay')
                        except: pass
                        
                        # [Strategy Update] Pass base_image_path for reference-based generation
                        success, result = generate_image_with_imagen(image_prompt, user_id, base_image_path=current_bg)
                        image_path = result if success else None
                        
                        if success:
                            hint = remain_img_hint(user_id)
                            if text_overlay: image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                            user_last_image_prompt[user_id] = {'prompt': image_prompt}
                            user_last_generated_image_path[user_id] = image_path # Update last generated path
                            
                            msg = f"圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」或「ok」結束本服務。\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息，以免錯誤！{hint}"
                            if send_image_to_line(user_id, image_path, msg, reply_token):
                                user_image_generation_state[user_id] = 'can_modify'
                                return None # 已回覆
                            else:
                                user_image_generation_state[user_id] = 'can_modify'
                                return "圖片生成成功但發送失敗。請檢查後台 Log。"
                        else:
                            user_image_generation_state[user_id] = 'can_modify'
                            # 如果 result 帶有額度不足的訊息，直接暴露給用戶
                            if "額度已用完" in result:
                                return result
                            return f"修改失敗：{result}"
                    except Exception as e:
                        print(f"Modification error: {e}")
                        user_image_generation_state[user_id] = 'can_modify'
                        return "修改時發生錯誤，請重試。"

            if state == 'waiting_for_confirmation':
                # 用戶確認生成
                if '取消' in user_input:
                    del user_image_generation_state[user_id]
                    if user_id in user_last_image_prompt:
                        del user_last_image_prompt[user_id]
                    print(f"[CANCEL] Image generation cancelled for user {user_id}")
                    return "已取消圖片生成。"
                elif is_confirmation(user_input):
                    user_image_generation_state[user_id] = 'generating'
                    state = 'generating' 
                else:
                    return f"好的，您想要生成的圖片內容是：\n\n「{user_input}」\n\n請確認是否開始生成？\n(請回答「確定」或重新描述，也可說「取消」)\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息！"
            
            if state == 'waiting_for_prompt':
                if '取消' in user_input:
                    del user_image_generation_state[user_id]
                    if user_id in user_last_image_prompt:
                        del user_last_image_prompt[user_id]
                    print(f"[CANCEL] Image generation cancelled for user {user_id}")
                    return "已取消圖片生成。"
                user_image_generation_state[user_id] = 'waiting_for_confirmation'
                if user_id not in user_last_image_prompt or isinstance(user_last_image_prompt[user_id], str):
                    user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt.get(user_id, '')}
                user_last_image_prompt[user_id]['pending_description'] = user_input
                return f"您想要生成的圖片內容是：\n\n「{user_input}」\n\n請確認是否開始生成？\n(請回答「確定」或重新描述，也可說「取消」)\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息！"
            
            if state == 'generating':
                saved_data = user_last_image_prompt.get(user_id, {})
                if isinstance(saved_data, str):
                    original_description = saved_data if saved_data else user_input
                else:
                    original_description = saved_data.get('pending_description', user_input)
                
                optimize_prompt = f"""用戶想生成圖片，描述是：「{original_description}」。
                請將這個描述轉換成適合 AI 生圖的英文提示詞。
                如果用戶明顯想要在圖片上寫字（例如：「上面寫早安」），請將文字提取出來。
                回傳 JSON 格式： {{ "image_prompt": "...", "text_overlay": "..." }}
                要求：
                1. 風格正向、安全。
                2. 絕對不要講笑話。
                3. text_overlay 必須是「純文字」，禁止包含括號、表情描述 (如 (red heart)) 或任何非顯示用的文字。
                """
                try:
                    optimized = model.generate_content(optimize_prompt)
                    import json, re
                    image_prompt = optimized.text.strip()
                    text_overlay = None
                    try:
                        match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                        if match:
                            data = json.loads(match.group())
                            image_prompt = data.get('image_prompt', image_prompt)
                            text_overlay = data.get('text_overlay')
                    except Exception as e:
                        print(f"JSON parsing error: {e}")
                        pass
                    
                    print(f"生成圖片，Prompt: {image_prompt}")
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    error_reason = result if not success else None
                    
                    if image_path:
                        if text_overlay:
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                        
                        # 儲存生成路徑供後續修改參考
                        user_last_generated_image_path[user_id] = image_path

                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        hint = remain_img_hint(user_id)
                        msg = f"圖片生成完成。\n\n如需修改，請直接說明您的調整需求。\n如不需調整，請說「完成」或「ok」結束本服務！\n⚠️ 修改期間約15秒，請勿再次發送訊息，以免錯誤！{hint}"
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            user_image_generation_state[user_id] = 'can_modify'
                            return None 
                        else:
                            user_image_generation_state[user_id] = 'idle'
                            return "圖片已生成但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)設定有誤。\n請檢查後台 Log 或 terminal 輸出中的 [SEND IMAGE] 訊息。"
                    else:
                        if user_id in user_last_image_prompt:
                            user_last_image_prompt[user_id].pop('pending_description', None)
                        user_image_generation_state[user_id] = 'idle'
                        return f"圖片生成失敗：{error_reason}"
                except Exception as e:
                    print(f"Image generation error: {e}")
                    user_image_generation_state[user_id] = 'idle'
                    return "圖片生成時發生錯誤，請稍後再試。"
            # [MOVED BLOCK END] 
        else:
             # 只有在 Idle 狀態才做意圖判斷
             # 只有在 Idle 狀態才做意圖判斷
             
             # -------------------------------------------------------
             # 混合式判斷 (Hybrid Router)
             # 1. 先檢查「特定關鍵字」(確保選單功能 100% 觸發制式流程)
             # 2. 如果沒有關鍵字，才交給 AI 判斷 (讓聊天也能觸發功能)
             # -------------------------------------------------------
             
             current_intent = None
             
             # 關鍵字強制映射 (還原使用者的制式操作體驗)
             # 重要：只有關鍵字在「開頭」才觸發，避免誤判文本中間的關鍵字
             # 例如：「生成圖片...」✅ 觸發，但「工具清單：1. 生成圖片」❌ 不觸發
             
             # 檢查關鍵字是否在前 15 字內
             prefix = user_input[:15]
             
             if any(k in prefix for k in ["規劃行程", "行程規劃", "去玩", "帶我去", "旅遊", "旅行", "景點推薦"]):
                 current_intent = 'trip_planning'
             elif any(k in prefix for k in ["長輩圖", "做長輩圖", "製作長輩圖", "梗圖", "迷因", "加文字", "上文字", "做一張圖"]):
                 current_intent = 'meme_creation'
             elif any(k in prefix for k in ["生成圖片", "產生圖片", "畫一張", "做圖", "畫圖", "繪圖", "幫我生成", "幫我畫"]):
                 current_intent = 'image_generation'
             elif any(k in prefix for k in ["生成影片", "製作影片", "做影片"]):
                 current_intent = 'video_generation'
             elif any(k in prefix for k in ["提醒通知", "設定提醒", "我的提醒", "查詢提醒", "查看提醒", "待辦事項"]):
                current_intent = 'show_reminders'
             
             # 如果關鍵字沒抓到，才用 AI (處理自然語言，如 "我想去宜蘭")
             if not current_intent:
                 current_intent = classify_user_intent(user_input)
             
             print(f"User Intent: {current_intent} (Prefix: '{prefix}')")

             # 1. 影片生成
             if current_intent == 'video_generation':
                 if not check_video_limit(user_id):
                     return "抱歉，每天只能生成一次影片喔！明天再來玩吧！加油！Cheer up！"
                 user_video_state[user_id] = 'generating'
                 # ... (video generation logic simplified for preview)
                 return "🎥 影片生成功能正在進行大升級 (Private Preview)！\n\nGoogle 正在為我們準備更強大的 Veo 模型，敬請期待！✨"

             # 2. 圖片修改 (與 Image Gen 分開處理)
             elif current_intent == 'image_modification':
                  # 直接進入修改流程
                  if user_id in user_last_image_prompt:
                       # 模擬 detect_regenerate_image_intent 的邏輯
                       user_image_generation_state[user_id] = 'generating'
                       
                       # ... (Execute Modification Logic reused from below)
                       # For simplicity, we can reuse the code block or jump to it.
                       # But since we are replacing the structure, we should copy the modification implementation here.
                       
                       # 正確提取上一次的prompt內容
                       last_prompt_data = user_last_image_prompt.get(user_id, {})
                       if isinstance(last_prompt_data, dict):
                           last_prompt = last_prompt_data.get('prompt', '')
                       else:
                           last_prompt = str(last_prompt_data) if last_prompt_data else ''
                       
                       optimize_prompt = f"""
                       System: User wants to modify the previous image.
                       Old Prompt: {last_prompt}
                       User Modification Request: {user_input}
                       
                       Please generate a new English Prompt. If user asks to add text, put it in text_overlay.
                       Return JSON: {{ "image_prompt": "...", "text_overlay": "..." }}
                       Requirements: 
                       1. **Keep the core composition and main subject of the old image**, only make the adjustments requested by the user. 
                       2. **If there are people, describe their features (hair, glasses, clothes, gender, age) from the old prompt to maintain identity as much as possible.**
                       3. NO JOKES.
                       """
                       # ... (Generation Logic)
                       try:
                            optimized = model.generate_content(optimize_prompt)
                            import json, re
                            image_prompt = optimized.text.strip()
                            text_overlay = None
                            try:
                                match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                                if match:
                                    data = json.loads(match.group())
                                    image_prompt = data.get('image_prompt', image_prompt)
                                    text_overlay = data.get('text_overlay')
                            except: pass
                            
                            # 嘗試獲取上一張生成的圖片路徑作為 Base Image
                            base_img_path = user_last_generated_image_path.get(user_id)
                            success, result = generate_image_with_imagen(image_prompt, user_id, base_image_path=base_img_path)
                            image_path = result if success else None
                            if success:
                                user_last_generated_image_path[user_id] = image_path
                                if text_overlay: image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                                user_last_image_prompt[user_id] = {'prompt': image_prompt}
                                # 使用 reply_token 免費發送
                                msg = "圖片修改完成🎉\n\n如需再次修改，請直接說明調整需求。\n如不需調整，請說「完成」或「ok」結束本服務！\n\n(小提醒：AI是重新繪圖，人物長相可能會改變喔！)"
                                if send_image_to_line(user_id, image_path, msg, reply_token):
                                    user_image_generation_state[user_id] = 'can_modify'
                                    return None # 已回覆
                                else:
                                    return "Image generated but send failed."
                            else:
                                user_image_generation_state[user_id] = 'can_modify'
                                return f"Modification failed: {result}"
                       except Exception as e:
                            print(e)
                            return "Error processing..."
                  else:
                        return "您還沒有生成過圖片喔！請說「畫一張...」來開始。"

             # 3. 圖片生成 - 引導式對話
             elif current_intent == 'image_generation':
                 # 如果用戶已經在輸入中包含了描述 (例如 "給我一張可愛的貓咪圖")
                 # 就不應該問 "請描述您想要的圖片"，而是直接確認
                 
                 # 簡單過濾觸發詞
                 clean_prompt = user_input
                 # 1. 去除明確的動作指令 (保留原有邏輯，全域替換)
                 for kw in ["給我一張", "畫一張", "我要一張", "生成一張", "產生一張", "畫一隻", "製作一張", "create a", "generate a", "image of", "picture of"]:
                     clean_prompt = clean_prompt.replace(kw, "")
                 
                 # 2. 去除句首的禮貌/請求用語 (避免誤刪中間的字，如「申請」)
                 # 反覆去除直到沒有前綴為止 (處理「請幫我...」這類組合)
                 prefix_keywords = ["幫我", "請", "麻煩", "可以幫我", "我要", "能幫我", "能夠", "可以"]
                 while True:
                     original_len = len(clean_prompt)
                     for prefix in prefix_keywords:
                         if clean_prompt.startswith(prefix):
                             clean_prompt = clean_prompt[len(prefix):].strip()
                     if len(clean_prompt) == original_len:
                         break

                 clean_prompt = clean_prompt.replace("圖片", "").strip()
                 
                 if len(clean_prompt) > 2: # 假設描述長度大於2就是有效描述
                     user_image_generation_state[user_id] = 'waiting_for_confirmation'
                     # 保存 Prompt
                     if user_id not in user_last_image_prompt or isinstance(user_last_image_prompt[user_id], str):
                        user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt.get(user_id, '')}
                     user_last_image_prompt[user_id]['pending_description'] = clean_prompt
                     
                     return f"沒問題！您想要生成的圖片是：\n\n「{clean_prompt}」\n\n請確認是否開始生成？\n(請回答「確定」或「ok」開始，也可說「取消」)"
                 else:
                     # 描述太短或沒有描述，才進入詢問模式
                     user_image_generation_state[user_id] = 'waiting_for_prompt'
                     return """好的，我們來生成圖片。

請描述想要的圖片內容：
舉例：
🌄 山、海、森林、城市
👨‍👩‍👧 什麼樣的人、在做什麼
🎨 水彩、油畫、卡通風格

＊隨時說「取消」可停止"""

             # 4. 長輩圖製作
             elif current_intent == 'meme_creation':
                 return handle_meme_agent(user_id, user_input, is_new_session=True, reply_token=reply_token)

             # 5. 行程規劃
             elif current_intent == 'trip_planning':
                 trip_response = handle_trip_agent(user_id, user_input, is_new_session=True, reply_token=reply_token)
                 if trip_response:
                     return trip_response
                # [Fix] 若回傳 None (例如跳話題)，保留當前輸入並轉交給一般聊天邏輯處理
                 # 複製下方的 Chat 邏輯，確保話題能順利接續
                 print(f"[TRIP] Fallback to chat logic for user {user_id}")
                 
                 # 檢查是否有圖
                 has_image = user_id in user_images
                 
                 if user_id not in chat_sessions: chat_sessions[user_id] = model.start_chat(history=[])
                 chat = chat_sessions[user_id]
                 
                 if has_image:
                     upload_image = PIL.Image.open(user_images[user_id])
                     formatted_input = [f"{user_input}", upload_image]
                     response = chat.send_message(formatted_input)
                 else:
                     response = chat.send_message(user_input)
                     
                 return response.text

             # 6. 查看提醒
             elif current_intent == 'show_reminders':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "提醒功能需要資料庫支援喔！"
                 try:
                     reminders = db.get_user_reminders(user_id, include_sent=False)
                     if not reminders: return "你目前沒有待辦提醒喔！想要設定的話，說「提醒我...」就可以了！"
                     reminder_list = "📋 **你的提醒清單** 📋\n\n"
                     for idx, reminder in enumerate(reminders, 1):
                         t = reminder['reminder_time']
                         if isinstance(t, str): t = datetime.fromisoformat(t)
                         reminder_list += f"{idx}. {t.strftime('%m月%d日 %H:%M')} - {reminder['reminder_text']}\n"
                     return reminder_list + "\n有需要都可以找我！\n\n輸入「刪除提醒」可以清除所有待辦"
                 except: return "查看待辦時出了點問題..."

             # 6.5. 取消提醒
             elif current_intent == 'cancel_reminder':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "提醒功能需要資料庫支援喔！"
                 try:
                     # 簡單起見，目前支援刪除全部未發送的提醒
                     count = db.delete_pending_user_reminders(user_id)
                     if count > 0:
                         # 退回配額
                         decrement_reminder_quota(user_id, count)
                         return f"好的，已為您刪除共 {count} 則尚未提醒的待辦事項！今日額度已釋出。"
                     else:
                         return "您目前沒有待辦的提醒喔！"
                 except Exception as e:
                     print(f"Delete reminder error: {e}")
                     return "取消提醒時發生錯誤，請稍後再試。"

             # 7. 設定提醒
             elif current_intent == 'set_reminder':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "提醒功能需要資料庫支援喔！"
                 try:
                     # ===== 每日提醒配額檢查 =====
                     quota_ok, remind_count, quota_msg = check_reminder_quota(user_id)
                     if not quota_ok:
                         return quota_msg
                     
                     parse_prompt = f"""System: User says: "{user_input}". Parse reminder and rewrite warmly in Traditional Chinese (繁體中文).
                     Return JSON: {{ "reminder_text": "...", "reminder_time": "2026-01-17T08:00:00" }}
                     Requirement: Keep response short and smooth. Ensure reminder_text is in Traditional Chinese.
                     Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}
                     """
                     # 使用功能性模型解析
                     resp = model_functional.generate_content(parse_prompt)
                     import json, re
                     data = json.loads(re.search(r'\{[^}]+\}', resp.text).group())
                     t = datetime.fromisoformat(data['reminder_time'])
                     db.add_reminder(user_id, data['reminder_text'], t)
                     
                     # 設定成功後累加計數
                     remain_reminders = increment_reminder_quota(user_id)
                     remain_hint = f"\n📊 今日剩餘提醒配額：{remain_reminders} 個" if user_id not in QUOTA_WHITELIST else ""
                     
                     reply = f"OK! 已為您設定提醒：{t.strftime('%m/%d %H:%M')}，提醒內容：「{data['reminder_text']}」。{remain_hint}"
                     
                     # 檢查系統額度狀態，若已滿則主動告知
                     if db.is_system_quota_full():
                         reply += "\n\n[注意] 系統免費額度已滿，可能無法自動發送推播，請人工檢查您的提醒清單！"
                         
                     return reply
                 except Exception as e:
                     print(f"Set reminder error: {e}")
                     return "設定提醒失敗了...請說清楚一點，例如「明天早上8點吃藥」。"

             # 8. 一般聊天 (Chat)
             else:
                 # 檢查是否有圖
                 has_image = user_id in user_images
                 

                 if user_id not in chat_sessions: chat_sessions[user_id] = model.start_chat(history=[])
                     
                 chat = chat_sessions[user_id]
                 
                 if has_image:
                     # [Fix Bug 3] user_images stores a list; get the latest one
                     img_list = user_images[user_id]
                     last_img_path = img_list[-1] if isinstance(img_list, list) else img_list
                     upload_image = PIL.Image.open(last_img_path)
                     # 圖片模式下，仍保留簡單提示以確保多模態效果，但簡化內容
                     formatted_input = [f"{user_input}", upload_image]
                     response = chat.send_message(formatted_input)
                 else:
                     # 純文字模式：直接傳送用戶訊息，讓對話歷史保持乾淨，解決記憶問題
                     response = chat.send_message(user_input)
                 

                         
                 return response.text


        # 檢查圖片生成狀態
        if user_id in user_image_generation_state:
            state = user_image_generation_state[user_id]
            
            
            # 處理可修改狀態
            if state == 'can_modify':
                # 檢查是否要結束修改
                # 使用共用的確認邏輯 (包含：好、ok、完成、結束...)
                if is_confirmation(user_input):
                    user_image_generation_state[user_id] = 'idle'
                    return "好的！圖片已完成。期待下次為您服務！"
                
                # 檢查是否只是說「修改」
                if user_input.strip() in ['修改', '要修改', '我要修改']:
                    user_image_generation_state[user_id] = 'waiting_for_modification'
                    return "好的，請說明您想要如何修改這張圖片？\n(例如：加上文字、改變顏色、調整內容等)\n\n如不需調整，請說「完成」或「ok」結束本服務！" 
                else:
                    # 直接說修改內容，進入修改流程
                    user_image_generation_state[user_id] = 'generating'
                    
                    # 正確提取上一次的prompt內容
                    last_prompt_data = user_last_image_prompt.get(user_id, {})
                    if isinstance(last_prompt_data, dict):
                        last_prompt = last_prompt_data.get('prompt', '')
                    else:
                        last_prompt = str(last_prompt_data) if last_prompt_data else ''
                    
                    optimize_prompt = f"""
                    系統：用戶想要修改之前的圖片。
                    舊提示詞：{last_prompt}
                    用戶修改需求：{user_input}
                    Please generate a new English Prompt. If user asks to add text, put it in text_overlay.
                    Return JSON: {{ "image_prompt": "...", "text_overlay": "...", "text_position": "bottom" }}
                    要求：
                    1. **保留舊圖的核心構圖和主體內容**，只做用戶要求的調整。
                    2. **若原圖有人物，請根據舊提示詞詳細描述其特徵（髮型、眼鏡、衣著、性別、年齡），盡量保持人物外觀一致**。
                    3. **If the user EXPLICITLY asks to add text (e.g., "Add text...", "Write..."), put it in text_overlay. OTHERWISE, DO NOT OUTPUT text_overlay. Ignore existing text in the image.**
                    4. **若進行局部修改，請在Prompt中強調保持其他部分不變（Keep original composition and pose strict）**。
                    5. 絕對不要講笑話。
                    6. text_overlay 必須是「純文字」，禁止包含括號、表情描述 (如 (red heart)) 或任何非顯示用的文字。
                    
                    """
                    try:
                        # 使用功能性模型解析 Prompt
                        optimized = model_functional.generate_content(optimize_prompt)
                        import json, re
                        image_prompt = optimized.text.strip()
                        text_overlay = None
                        text_position = 'bottom'
                        try:
                            match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                            if match:
                                data = json.loads(match.group())
                                image_prompt = data.get('image_prompt', image_prompt)
                                text_overlay = data.get('text_overlay')
                                text_position = data.get('text_position', 'bottom')
                        except: pass
                        
                        # 嘗試獲取上一張生成的圖片路徑作為 Base Image
                        base_img_path = user_last_generated_image_path.get(user_id)
                        
                        success, result = generate_image_with_imagen(image_prompt, user_id, base_image_path=base_img_path)
                        image_path = result if success else None
                        if success:
                            user_last_generated_image_path[user_id] = image_path
                            # 自動合成文字 (如果 Gemini 有提取出來)
                            if text_overlay:
                                # 根據位置參數決定 (預設 bottom)
                                pos = text_position if text_position in ['top', 'center', 'bottom'] else 'bottom'
                                try:
                                    image_path = create_meme_image(image_path, text_overlay, user_id, position=pos, font_size=60)
                                    print(f"[IMAGE_GEN_MODIFY] Added text overlay: {text_overlay} at {pos}")
                                except Exception as e:
                                    print(f"[IMAGE_GEN_MODIFY] Failed to add text overlay: {e}")

                            user_last_image_prompt[user_id] = {'prompt': image_prompt}
                            # 使用 reply_token 免費發送
                            msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」結束本服務！\n(小提醒：AI是重新繪圖，人物長相可能會改變喔！)"
                            if send_image_to_line(user_id, image_path, msg, reply_token):
                                user_image_generation_state[user_id] = 'can_modify'
                                return None # 已回覆
                            else:
                                user_image_generation_state[user_id] = 'can_modify'
                                return "圖片生成成功但發送失敗。請檢查後台 Log。"
                        else:
                            user_image_generation_state[user_id] = 'can_modify'
                            return f"修改失敗：{result}"
                    except Exception as e:
                        print(f"Modification error: {e}")
                        user_image_generation_state[user_id] = 'can_modify'
                        return "修改時發生錯誤，請重試。"
            
            if state == 'waiting_for_confirmation':
                # 用戶確認生成
                if '取消' in user_input:
                    del user_image_generation_state[user_id]
                    if user_id in user_last_image_prompt:
                        del user_last_image_prompt[user_id]
                    print(f"[CANCEL] Image generation cancelled for user {user_id}")
                    return "已取消圖片生成。"
                elif is_confirmation(user_input):
                    # 用戶確認，設定狀態為 generating 並繼續往下執行
                    user_image_generation_state[user_id] = 'generating'
                    state = 'generating'  # 重要：更新 state 變數，讓下面的 if state == 'generating' 能夠執行
                    # 不要 return，讓它繼續執行下面的 generating 邏輯
                else:
                    # 用戶重新描述，用新描述再次確認
                    return f"好的，您想要生成的圖片內容是：\n\n「{user_input}」\n\n請確認是否開始生成？\n(請回答「確定」或重新描述，也可說「取消」)\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息！"
            
            if state == 'waiting_for_prompt':
                # 檢查是否要取消
                if '取消' in user_input:
                    del user_image_generation_state[user_id]
                    if user_id in user_last_image_prompt:
                        del user_last_image_prompt[user_id]
                    print(f"[CANCEL] Image generation cancelled for user {user_id}")
                    return "已取消圖片生成。"
                # 用戶已提供詳細需求，先確認
                user_image_generation_state[user_id] = 'waiting_for_confirmation'
                # 保存用戶的原始描述，以便後續生成使用
                if user_id not in user_last_image_prompt or isinstance(user_last_image_prompt[user_id], str):
                    user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt.get(user_id, '')}
                user_last_image_prompt[user_id]['pending_description'] = user_input
                return f"您想要生成的圖片內容是：\n\n「{user_input}」\n\n請確認是否開始生成？\n(請回答「確定」或重新描述，也可說「取消」)\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息！"
            
            if state == 'generating':
                # 用戶已確認，開始生成
                
                # 使用保存的原始描述，而不是用戶當前輸入的「確定」
                saved_data = user_last_image_prompt.get(user_id, {})
                if isinstance(saved_data, str):
                    original_description = saved_data if saved_data else user_input
                else:
                    original_description = saved_data.get('pending_description', user_input)
                
                # 使用 AI 優化提示詞（強調安全性、禁止笑話、支援文字疊加）
                optimize_prompt = f"""用戶想生成圖片，描述是：「{original_description}」。
                請將這個描述轉換成適合 AI 生圖的英文提示詞。
                如果用戶明顯想要在圖片上寫字（例如：「上面寫早安」），請將文字提取出來。
                
                回傳 JSON 格式：
                {{
                    "image_prompt": "英文生圖 Prompt",
                    "text_overlay": "要寫在圖上的文字 (繁體中文, 可選)"
                }}
                
                要求：
                1. 風格正向、安全。
                2. 絕對不要講笑話。
                3. text_overlay 必須是「純文字」，禁止包含括號、表情描述 (如 (red heart)) 或任何非顯示用的文字。
                """
                try:
                    optimized = model.generate_content(optimize_prompt)
                    
                    import json
                    import re
                    image_prompt = optimized.text.strip()
                    text_overlay = None
                    
                    try:
                        match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                        if match:
                            data = json.loads(match.group())
                            image_prompt = data.get('image_prompt', image_prompt)
                            text_overlay = data.get('text_overlay')
                    except Exception as e:
                        print(f"JSON parsing error: {e}")
                        pass
                    
                    print(f"生成圖片，Prompt: {image_prompt}")
                    
                    # ===== 配額檢查（融合/修改/生成三者共用每日 6 次）=====
                    quota_ok, _, quota_msg = check_image_quota(user_id)
                    if not quota_ok:
                        user_image_generation_state[user_id] = 'idle'
                        return quota_msg
                    
                    # 生成圖片
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    error_reason = result if not success else None
                    
                    if image_path:
                        # 如果有文字疊加需求
                        if text_overlay:
                            # 自動疊加文字 (預設置中)
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                        
                        # 保存 Prompt 以便修改
                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        
                        # 傳送圖片給用戶
                        hint = remain_img_hint(user_id)
                        msg = f"圖片生成完成。\n\n如需修改，請直接說明您的調整需求。\n如不需調整，請說「完成」或「ok」結束本服務！\n⚠️ 修改期間約15秒，請勿再次發送訊息，以免錯誤！{hint}"
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            # 設定為可修改狀態，而不是 idle
                            user_image_generation_state[user_id] = 'can_modify'
                            return None # 已回覆
                        else:
                            # 發送失敗
                            user_image_generation_state[user_id] = 'idle'
                            return "圖片已生成但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)設定有誤。\n請檢查後台 Log 或 terminal 輸出中的 [SEND IMAGE] 訊息。"
                    else:
                        # 生成失敗，清除待處理數據並設為 idle
                        if user_id in user_last_image_prompt:
                            user_last_image_prompt[user_id].pop('pending_description', None)
                        user_image_generation_state[user_id] = 'idle'
                        # 顯示詳細錯誤原因
                        failure_msg = f"圖片生成失敗。\n\n失敗原因：{error_reason if error_reason else '未知錯誤'}\n\n如需重新生成，請再次說「生成圖片」並描述您的需求。"
                        return failure_msg
                
                except Exception as e:
                    print(f"圖片生成錯誤: {e}")
                    import traceback
                    traceback.print_exc()
                    user_image_generation_state[user_id] = 'waiting_for_prompt'
                    return "圖片生成時發生錯誤，請重新描述您的需求。"



            elif state == 'can_modify':
                # 在此狀態下，用戶可以持續修改圖片，直到說「完成」
                
                # 檢查是否結束修改
                if any(keyword in user_input.lower() for keyword in ['完成', 'ok', '好的', '謝謝', '停止', '結束']):
                    user_image_generation_state[user_id] = 'idle'
                    return "不客氣！希望這張圖片您會喜歡！需要其他幫忙隨時告訴我喔！😊"
                
                # 視為修改需求，直接執行生成
                user_image_generation_state[user_id] = 'generating'
                
                # 取得上次 Prompt
                saved_data = user_last_image_prompt.get(user_id, {})
                last_prompt = saved_data.get('prompt', '') if isinstance(saved_data, dict) else saved_data
                
                # 使用 AI 優化 Prompt (修改模式)
                optimize_prompt = f"""
                系統：用戶想要修改這張圖片。
                舊提示詞：{last_prompt}
                用戶修改需求：{user_input}
                
                请產生新的英文 Prompt。如果用戶要求加字，請放入 text_overlay。
                回傳 JSON:
                {{
                    "image_prompt": "新的英文 Prompt",
                    "text_overlay": "要寫的文字 (純文字, 禁止括號或表情描述)"
                }}
                """
                
                try:
                    optimized = model.generate_content(optimize_prompt)
                    import json
                    import re
                    image_prompt = optimized.text.strip()
                    text_overlay = None
                    
                    try:
                        match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                        if match:
                            data = json.loads(match.group())
                            image_prompt = data.get('image_prompt', image_prompt)
                            text_overlay = data.get('text_overlay')
                    except:
                        pass
                    
                    # 生成圖片
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    
                    if success:
                        if text_overlay:
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                            
                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        
                        print(f"[DEBUG] Before send: image_path type={type(image_path)}, value={image_path}")
                        # 使用 reply_token 免費發送
                        msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」結束本服務。\n⚠️ 調整期間約15秒，請勿再次發送訊息，以免錯誤！"
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            # 成功後保持 can_modify 狀態，允許繼續修改
                            user_image_generation_state[user_id] = 'can_modify'
                            return None # 已回覆
                        else:
                            # 發送失敗（通常是上傳問題）
                            user_image_generation_state[user_id] = 'can_modify'
                            return "圖片已生成但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)設定有誤。\n請檢查後台 Log。"
                    else:
                        # 失敗後也保持 can_modify，讓用戶重試
                        user_image_generation_state[user_id] = 'can_modify'
                        return f"抱歉，修改失敗。\n\n失敗原因：{result}\n\n請換個說法試試看？"
                        
                except Exception as e:
                    print(f"Modification error: {e}")
                    user_image_generation_state[user_id] = 'can_modify'
                    return "處理時發生錯誤，請稍後再試。"

            elif state == 'waiting_for_modification':
                 # 用戶提供了修改細節，開始重新生成
                 user_image_generation_state[user_id] = 'generating'
                 
                 # 取得上次的 Prompt
                 last_prompt = user_last_image_prompt.get(user_id, "")
                 
                 # 使用 AI 優化提示詞 (結合舊 Prompt + 新修改)
                 optimize_prompt = f"""
                 系統：用戶想要修改之前的圖片。
                 舊提示詞：{last_prompt}
                 用戶修改需求：{user_input}
                 
                 請產生新的英文 Prompt。如果用戶要求加字，請放入 text_overlay。
                 回傳 JSON:
                 {{
                     "image_prompt": "新的英文 Prompt",
                     "text_overlay": "要寫的文字 (純文字, 禁止括號或表情描述)"
                 }}
                 
                 要求：
                 1. 保留舊圖核心。
                 2. 絕對不要講笑話。
                 """
                 
                 # 使用功能性模型解析
                 optimized = model_functional.generate_content(optimize_prompt)
                 import json
                 import re
                 image_prompt = optimized.text.strip()
                 text_overlay = None
                 
                 try:
                    match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        image_prompt = data.get('image_prompt', image_prompt)
                        text_overlay = data.get('text_overlay')
                 except:
                    pass
                 
                 # 生成圖片
                 success, result = generate_image_with_imagen(image_prompt, user_id)
                 image_path = result if success else None
                 
                 if success:
                     if text_overlay:
                         image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                         
                     user_last_image_prompt[user_id] = {'prompt': image_prompt}
                     
                     # 使用 reply_token 免費發送
                     msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」結束本服務。\n⚠️ 生成期間約15秒，請勿再次發送訊息！"
                     if send_image_to_line(user_id, image_path, msg, reply_token):
                         user_image_generation_state[user_id] = 'can_modify'
                         return None # 已回覆
                     else:
                         user_image_generation_state[user_id] = 'can_modify'
                         return "圖片已生成但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)設定有誤。\n請檢查後台 Log。"
                 else:
                     user_image_generation_state[user_id] = 'can_modify'
                     return f"抱歉，修改失敗。\n\n失敗原因：{result}\n\n我們重新來過好嗎？"


        # 檢測重新生成圖片意圖（包含在對話中直接要求修改）
        if detect_regenerate_image_intent(user_input):
            # 判斷是「詢問可否修改」還是「直接提供修改指令」
            # 簡單判斷：如果字數很少 (例如 "可以改嗎", "修改", "不滿意")，就先詢問細節
            # 如果字數較多 (例如 "把貓變成狗"), 則直接執行
            
            is_generic_request = len(user_input) < 10 or user_input in ["可以改嗎", "能改嗎", "想修改", "幫我改", "修改"]
            
            if is_generic_request:
                user_image_generation_state[user_id] = 'waiting_for_modification'
                return "沒問題！請問你想怎麼改？\n請告訴我具體的內容，例如：「換成藍色背景」、「把貓換成狗」、「加一頂帽子」...等。"
            
            else:
                # 用戶已經提供了具體修改指令，立即執行生成
                user_image_generation_state[user_id] = 'generating'
                
                saved_data = user_last_image_prompt.get(user_id, {})
                last_prompt = saved_data.get('prompt', '') if isinstance(saved_data, dict) else saved_data
                
                optimize_prompt = f"""
                系統：用戶想要修改之前的圖片。
                舊提示詞：{last_prompt}
                用戶修改需求：{user_input}
                
                請產生新的英文 Prompt。如果用戶要求加字，請放入 text_overlay。
                回傳 JSON:
                {{
                    "image_prompt": "新的英文 Prompt",
                    "text_overlay": "要寫的文字 (純文字, 禁止括號或表情描述)"
                }}
                
                要求：
                1. 保留舊圖核心。
                2. 絕對不要講笑話。
                3. **If the user EXPLICITLY asks to add text, put it in text_overlay. OTHERWISE, DO NOT OUTPUT text_overlay. Ignore existing text in the image.**
                """
                
                try:
                    optimized = model.generate_content(optimize_prompt)
                    import json
                    import re
                    image_prompt = optimized.text.strip()
                    text_overlay = None
                    
                    try:
                        match = re.search(r'\{.*\}', optimized.text, re.DOTALL)
                        if match:
                            data = json.loads(match.group())
                            image_prompt = data.get('image_prompt', image_prompt)
                            text_overlay = data.get('text_overlay')
                    except:
                        pass
                    
                    # 生成圖片
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    
                    if success:
                        if text_overlay:
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                            
                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        
                        # 使用 reply_token 免費發送
                        msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」結束本服務。\n⚠️ 生成期間約15秒，請勿再次發送訊息！"
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            user_image_generation_state[user_id] = 'can_modify'
                            return None # 已回覆
                        else:
                            user_image_generation_state[user_id] = 'can_modify'
                            return "圖片已生成但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)設定有誤。\n請檢查後台 Log 或設定 IMGBB_API_KEY。"
                    else:
                        user_image_generation_state[user_id] = 'can_modify'
                        return f"抱歉，修改失敗。\n\n失敗原因：{result}\n\n請換個說法試試看？"
                except Exception as e:
                    print(f"Modification error: {e}")
                    user_image_generation_state[user_id] = 'idle'
                    return "處理時發生錯誤，請稍後再試。"
            
            # 取得上次的 Prompt (如果有的話)
            saved_data = user_last_image_prompt.get(user_id, {})
            last_prompt = saved_data.get('prompt', '') if isinstance(saved_data, dict) else saved_data
            
            # 使用 AI 優化提示詞（包含上下文）
            # 明確指示 AI 結合舊 Prompt 和新需求
            optimize_prompt = f"""
            系統：用戶想要修改之前的圖片。
            舊提示詞：{last_prompt}
            用戶修改需求：{user_input}
            
            請根據舊提示詞和新的修改需求，產生一個全新的、完整的英文生圖 Prompt。
            要求：
            1. 保留舊圖的核心主體（除非用戶說要換掉）。
            2. 加入用戶的新修改（例如：換顏色、加東西）。
            3. 如果用戶說「重新生成」而沒給細節，請稍微改變構圖或風格。
            4. 只回傳英文 prompt，不要其他說明。
            """
            
            optimized = model.generate_content(optimize_prompt)
            image_prompt = optimized.text.strip()
            
            # 生成圖片
            success, result = generate_image_with_imagen(image_prompt, user_id)
            image_path = result if success else None
            
            if success:
                # 更新 Prompt 記錄
                user_last_image_prompt[user_id] = {'prompt': image_prompt}
                
                print(f"[DEBUG] Before send: image_path type={type(image_path)}, value={image_path}")
                # 使用 reply_token 免費發送
                msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」結束本服務。\n⚠️ 生成期間約15秒，請勿再次發送訊息！"
                if send_image_to_line(user_id, image_path, msg, reply_token):
                    user_image_generation_state[user_id] = 'can_modify'
                    return None # 已回覆
                else:
                    user_image_generation_state[user_id] = 'can_modify'
                    return "圖片已生成但發送失敗。\n\n可能原因：圖片上傳服務(ImgBB/GCS)設定有誤。\n請檢查後台 Log 中的 [SEND IMAGE] 訊息。"
            else:
                user_image_generation_state[user_id] = 'waiting_for_prompt'
                return "抱歉，重新生成失敗了...請再告訴我一次你想要怎麼改？"
        

    except Exception as e:
        print(f"ERROR in gemini_llm_sdk: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return "哎呀！我遇到一點小問題...請稍後再試一次！"

# Initialize Scheduler Globally (for Gunicorn support)
# This ensures scheduler starts even when run via WSGI
if ADVANCED_FEATURES_ENABLED:
    try:
        from scheduler import init_scheduler
        # Avoid duplicate init if already running (though scheduler handles it)
        print("Initializing scheduler...")
        init_scheduler(channel_access_token)
    except Exception as e:
        print(f"⚠️ Failed to start scheduler: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting bot on port {port}...")
    app.run(host="0.0.0.0", port=port)
