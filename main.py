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
    # 不設定 system_instruction 或設定為純粹的助理
    system_instruction="You are a helpful AI assistant focused on data processing and JSON generation. Do not include any conversational filler. Output strict structured data.",
)

UPLOAD_FOLDER = "static"

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
# 儲存每個用戶上傳的圖片
user_images = {}
# 儲存每個用戶最後一次生圖的 Prompt
user_last_image_prompt = {} 
# 儲存每個用戶的圖片生成狀態
user_image_generation_state = {}  # 'idle', 'waiting_for_prompt', 'generating'
# 儲存每個用戶的長輩圖製作狀態
user_meme_state = {}
# 儲存每個用戶的行程規劃狀態
user_trip_plans = {}

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


# ======================
# 連結查證功能
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
        'cna.com.tw',    # 中央社
        'pts.org.tw',    # 公視
        'udn.com',       # 聯合新聞網
        'ltn.com.tw',    # 自由
        'chinatimes.com',# 中時
        'ettoday.net',   # ETtoday
        'storm.mg',      # 風傳媒
        'setn.com',      # 三立新聞
        'tvbs.com.tw',   # TVBS
        'nownews.com',   # 今日新聞
        'rti.org.tw',    # 中央廣播電台
        'bcc.com.tw',    # 中國廣播公司
    ]
    return any(td in domain.lower() for td in trusted_domains)

def check_safe_domain(domain):
    """檢查是否為常見安全網域"""
    safe_domains = [
        # 搜尋引擎與平台
        'google.com', 'youtube.com', 'facebook.com', 'instagram.com',
        'twitter.com', 'x.com', 'linkedin.com',
        # 政府機關
        'gov.tw', 'edu.tw', 'org.tw',
        # 常見服務
        'yahoo.com', 'microsoft.com', 'apple.com', 'line.me',
        'shopee.tw', 'pchome.com.tw', 'momo.com', 'books.com.tw',
        # 銀行金融
        'cathaybc.com.tw', 'esunbank.com', 'bot.com.tw', 'chinatrust.com.tw',
    ]
    return any(sd in domain.lower() for sd in safe_domains)

def get_domain_age(url):
    """查詢網域年齡（天數）"""
    try:
        import whois
        from datetime import datetime
        domain = extract_domain(url)
        if not domain:
            return None
        w = whois.whois(domain)
        creation_date = w.creation_date
        if isinstance(creation_date, list):
            creation_date = creation_date[0]
        if creation_date:
            age = (datetime.now() - creation_date).days
            return age
        return None
    except Exception as e:
        print(f"Domain age check error: {e}")
        return None

def quick_safety_check(url):
    """
    快速安全檢查
    返回: {'level': 'safe'|'warning'|'danger', 'risks': [...], 'is_trusted': bool, 'is_scam_like': bool}
    """
    risks = []
    domain = extract_domain(url)
    
    if not domain:
        return {'level': 'warning', 'risks': ['無法解析網域'], 'is_trusted': False, 'is_scam_like': False}
    
    is_trusted = check_trusted_media(domain)
    is_safe = check_safe_domain(domain)
    
    # 如果是可信媒體或安全網域，直接標記為安全
    if is_trusted or is_safe:
        return {
            'level': 'safe',
            'risks': [],
            'is_trusted': is_trusted,
            'is_scam_like': False
        }
    
    # 對於其他網域，進行風險評估
    domain_age = get_domain_age(url)
    is_new_domain = False
    if domain_age is not None:
        if domain_age < 30:  # 改為 30 天（原本 90 天太嚴格）
            risks.append(f"網域很新 ({domain_age} 天)")
            is_new_domain = True
        elif domain_age < 90:
            risks.append(f"網域較新 ({domain_age} 天)")
    
    scam_keywords = ['震驚', '必看', '不可思議', '驚人', '免費送', '限時', '賺錢', '投資必看']
    has_scam_keywords = any(kw in url for kw in scam_keywords)
    if has_scam_keywords:
        risks.append("網址包含可疑關鍵字")
    
    is_scam_like = is_new_domain and has_scam_keywords
    
    if is_scam_like or len(risks) >= 2:
        level = 'danger'
    elif len(risks) > 0:
        level = 'warning'
    else:
        level = 'safe'
    
    return {
        'level': level,
        'risks': risks,
        'is_trusted': is_trusted,
        'is_scam_like': is_scam_like
    }

def format_verification_result(safety_check, url):
    """格式化查證結果"""
    domain = extract_domain(url)
    
    if safety_check['level'] == 'danger':
        risks_text = '\n'.join(['• ' + risk for risk in safety_check['risks']])
        return f"""🚨 等等！這個連結看起來有點可疑：

⚠️ 風險提示：
{risks_text}

💡 建議：
1️⃣ 🔍 查證 - 深度分析內容是否為詐騙
2️⃣ 📖 閱讀 - 我幫你摘要內容（謹慎使用）

請回覆「查證」或「閱讀」"""
    
    elif safety_check['level'] == 'warning':
        risks_text = '\n'.join(['• ' + risk for risk in safety_check['risks']])
        return f"""⚠️ 提醒！這個網站有以下特徵：
{risks_text}

💡 您想要：
1️⃣ 🔍 查證 - 檢查是否為詐騙
2️⃣ 📖 閱讀 - 摘要內容

請回覆「查證」或「閱讀」"""
    
    else:
        # 安全連結，直接提供選項
        return """收到連結！

您想要：
1️⃣ 📖 閱讀 - 幫您摘要內容
2️⃣ 🔍 查證 - 檢查內容可信度

請回覆「閱讀」或「查證」"""

def fetch_webpage_content(url):
    """抓取網頁內容"""
    try:
        from bs4 import BeautifulSoup
        import requests
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        if len(text) > 5000:
            text = text[:5000] + "..."
        
        return text
    except Exception as e:
        print(f"Fetch webpage error: {e}")
        return None

def summarize_content(content, user_id):
    """使用 Gemini 摘要網頁內容"""
    try:
        prompt = f"""
請幫我這位長輩讀懂這個網頁，用溫暖的口吻告訴他：

{content}

請用這樣的格式回應：

📖 **內容摘要**

（用3-5句話解釋重點）

💡 **我的建議**

（告訴長輩這內容是否可信，有什麼要注意的）
"""
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Summarize error: {e}")
        return "抱歉，我無法讀懂這個網頁內容，請稍後再試！"


# ======================
# 新聞查詢功能
# ======================

def fetch_latest_news():
    """抓取最新新聞(使用 RSS)"""
    try:
        import feedparser
        from datetime import datetime, timedelta
        
        if news_cache['data'] and news_cache['timestamp']:
            if datetime.now() - news_cache['timestamp'] < timedelta(minutes=5):
                return news_cache['data']
        
        feeds = [
            'https://www.cna.com.tw/rss/headline.xml',
        ]
        
        news_items = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:15]:  # 增加到 15 則，確保有足夠新聞供挑選
                    news_items.append({
                        'title': entry.title,
                        'summary': entry.get('summary', ''),
                        'link': entry.link,
                        'published': entry.get('published', '')
                    })
            except Exception as e:
                print(f"Feed parse error for {feed_url}: {e}")
                continue
        
        news_cache['data'] = news_items
        news_cache['timestamp'] = datetime.now()
        
        return news_items
    except Exception as e:
        print(f"Fetch news error: {e}")
        return []

def detect_news_intent(text):
    """檢測是否想要查詢新聞"""
    keywords = ['新聞', '消息', '發生', '今天', '最近', '時事', '頭條']
    return any(keyword in text for keyword in keywords)

def generate_news_summary():
    """生成新聞摘要"""
    news_items = fetch_latest_news()
    
    if not news_items:
        return "抱歉，目前無法取得新聞資訊，請稍後再試！"
    
    try:
        # 格式化新聞內容，包含網址
        news_text = "\n\n".join([
            f"標題: {item['title']}\n內容: {item['summary']}\n連結: {item['link']}"
            for item in news_items[:10]
        ])
        
        prompt = f"""
請從這些新聞中，挑選最重要的 7 則新聞，並按照以下格式輸出。

重要要求：
1. 必須輸出完整的 7 則新聞
2. 每則摘要控制在 80-100 字，要有足夠的內容細節
3. 每則新聞「必須」包含完整的來源連結網址
4. 不要省略任何一則新聞

新聞資料：
{news_text}

輸出格式（嚴格遵守）：
📰 今日新聞摘要

1️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址

2️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址

3️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址

4️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址

5️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址

6️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址

7️⃣ 【標題】
   摘要內容（80-100字，包含重要細節）
   🔗 來源：完整網址
"""
        response = model_functional.generate_content(prompt)  # 使用功能性模型，避免講笑話
        return response.text + "\n\n💡 想聽語音播報？回覆「語音」即可"
    except Exception as e:
        print(f"News summary error: {e}")
        return "抱歉，無法整理新聞資訊，請稍後再試！"

def generate_news_audio(text, user_id):
    """生成新聞語音"""
    try:
        from google.cloud import texttospeech
        
        client = texttospeech.TextToSpeechClient()
        
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="zh-TW",
            name="cmn-TW-Wavenet-A"
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        audio_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_news.mp3")
        with open(audio_path, 'wb') as f:
            f.write(response.audio_content)
        
        return audio_path
    except Exception as e:
        print(f"TTS error: {e}")
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

3️⃣ ⏰ 設定提醒
👉 請說：「提醒我明天8點吃藥」
   或「10分鐘後叫我關火」
👉 輸入「刪除提醒」可清除所有待辦

4️⃣ 🗺️ 行程規劃
👉 請說：「規劃宜蘭一日遊」

5️⃣ 🔗 連結查證
👉 貼上任何連結，我會幫你：
   • 📖 摘要內容
   • 🔍 查證是否可信

6️⃣ 📰 新聞快報
👉 請說：「看新聞」或「今日新聞」

7️⃣ 💬 聊天解悶
👉 隨時都可以跟我聊天喔！

⚠️ 貼心小提醒：
• 輸入「取消」可停止目前動作
• 生成期間約15秒請勿傳訊
• 記憶維持七天，輸入「清除記憶」可重置"""

# ======================
# 連結查證與新聞功能
# ======================

# 儲存用戶待處理的連結
user_link_pending = {}
# 儲存新聞快取 (避免重複抓取)
news_cache = {'data': None, 'timestamp': None}
# 儲存用戶的新聞內容 (用於語音播報)
user_news_cache = {}

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
    try:
        import whois
        from datetime import datetime
        
        domain = extract_domain(url)
        if not domain:
            return None
        
        w = whois.whois(domain)
        
        # whois 回傳的 creation_date 可能是 datetime 或 list
        creation_date = w.creation_date
        if isinstance(creation_date, list):
            creation_date = creation_date[0]
        
        if creation_date:
            age = (datetime.now() - creation_date).days
            return age
        
        return None
    except Exception as e:
        print(f"Domain age check error: {e}")
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
        return f"""⚠️ 等等！我發現這個連結有點可疑：

{''.join(['• ' + risk + '\\n' for risk in safety_check['risks']])}
💡 建議先不要點開！

你是想：
1️⃣ 🔍 查證這個連結是否為詐騙
2️⃣ 📖 還是要我幫你讀內容

請告訴我你的需求！"""
    
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
        
        response = requests.get(url, headers=headers, timeout=10)
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
以下是一則網頁內容，請進行「精簡但深入」的閱讀整理，
目標是讓長輩能快速掌握重點，但內容要有價值，不要只寫空泛的廢話。

內容：
{content[:4000]}

請用以下格式回答（語氣保持專業、親切但嚴肅，絕對不要講笑話）：
📰 深度閱讀整理

【核心重點】
(請列出 3 點真正的內容精華，言之有物)

【詳細內容】
(針對內容進行精簡扼要的解說，保留重要數據或建議，但不要太長)

【貼心提醒】
(針對內容給予實際建議或注意事項)
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
            'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFZxYUdjU0FucG9MV1JsWm1GMWJIUUtP?hl=zh-TW&gl=TW&ceid=TW:zh-Hant',  # Google News 台灣
            'https://www.cna.com.tw/rss/headline.xml',  # 中央社頭條
            'https://udn.com/rssfeed/news/2/6638?ch=news',  # 聯合新聞網生活
        ]
        
        news_items = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:  # 增加到 10 則，確保有足夠新聞供挑選
                    news_items.append({
                        'title': entry.title,
                        'summary': entry.get('summary', ''),
                        'link': entry.link,
                        'published': entry.get('published', '')
                    })
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
        # 使用更多的新聞項目 (前 30 則) 給 AI 挑選
        # 為了確保連結正確，我們建立索引映射
        indexed_news = []
        for i, item in enumerate(news_items[:30], 1):
             indexed_news.append(f"[{i}] 標題: {item['title']}\n內容: {item['summary']}")
        
        news_text = "\n\n".join(indexed_news)
        
        prompt = f"""
以下是今天的新聞，請挑選最重要的 7 則，
用長輩容易理解的方式摘要，每則 80 字內。

{news_text}

CRITICAL INSTRUCTION:
請直接回傳你選擇的新聞 ID，以及摘要。
絕對不要自己編造連結。

輸出格式（嚴格遵守）：
📰 今日新聞摘要

1️⃣ [ID] 【標題】
   摘要內容（80-100字，包含重要細節）

... (請列出完整 7 則) ...

7️⃣ [ID] 【標題】
   摘要內容（80-100字，包含重要細節）
"""
        response = model_functional.generate_content(prompt)
        
        final_text = response.text.strip()
        
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
        
        client = texttospeech.TextToSpeechClient()
        
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


def generate_image_with_imagen(prompt, user_id):
    """使用 Imagen 3 生成圖片
    
    Returns:
        tuple: (成功與否, 圖片路徑或錯誤訊息)
        - 成功: (True, image_path)
        - 失敗: (False, error_message)
    """
    try:
        # 初始化 Vertex AI
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = "us-central1"
        
        aiplatform.init(project=project_id, location=location)
        
        # 使用 Imagen 3 生成圖片
        from vertexai.preview.vision_models import ImageGenerationModel
        import time
        
        imagen_model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
        
        # 優化提示詞（加入品質關鍵字）
        enhanced_prompt = f"{prompt}, high quality, detailed, vibrant colors"
        
        # Retry logic for 429/503 errors
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries + 1):
            try:
                images = imagen_model.generate_images(
                    prompt=enhanced_prompt,
                    number_of_images=1,
                    aspect_ratio="1:1",
                )
                
                if not images:
                    raise ValueError("API returned no images (possibly due to safety filters)")
                
                # 儲存圖片
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                image_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_generated.png")
                images[0].save(location=image_path)
                return (True, image_path)

            except Exception as e:
                error_str = str(e)
                # 只有在遇到暫時性錯誤時才重試 (429 Resource Exhausted, 503 Service Unavailable, 500 Internal Error)
                is_retryable = any(code in error_str for code in ["429", "503", "500", "ResourceExhausted", "ServiceUnavailable"])
                
                if is_retryable and attempt < max_retries:
                    print(f"API Error (Attempt {attempt+1}/{max_retries}): {error_str}. Retrying in {retry_delay}s...")
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
    win_paths = {
        'msjh': "C:\\Windows\\Fonts\\msjh.ttc",
        'heiti': "C:\\Windows\\Fonts\\msjh.ttc",
        'kaiti': "C:\\Windows\\Fonts\\kaiu.ttf",
        'ming': "C:\\Windows\\Fonts\\mingliu.ttc"
    }
    
    # 如果是 Windows 且檔案存在，直接回傳
    if os.name == 'nt':
        win_path = win_paths.get(font_type)
        if win_path and os.path.exists(win_path):
            return win_path

    # Linux/Cloud 環境：使用 Free Google Fonts (TTF)
    # 使用 NotoSerifTC (楷體/明體替代品) 和 NotoSansTC (黑體替代品)
    cloud_font_map = {
        'kaiti': 'NotoSerifTC-Regular.otf', # PIL 對 OTF 支援有時有問題，嘗試如果 OTF 失敗下載 TTF
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
    # 注意：Google Fonts repo 結構可能會變
    # 暫時改用更穩定的 CDN 或確保 URL 正確
    # 這裡嘗試使用 Noto Sans TC 的 Variable Font (ttf) 如果可能，或是直接用 OTF
    # 經過檢查 GitHub google/fonts，NotoSansTC 目錄下通常是 .otf
    
    urls = {
        'NotoSansTC-Bold.otf': "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf", # 改用 Variable TTF
        'NotoSansTC-Regular.otf': "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf",
        'NotoSerifTC-Regular.otf': "https://github.com/google/fonts/raw/main/ofl/notoseriftc/NotoSerifTC%5Bwght%5D.ttf" # 改用 Variable TTF
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

def create_meme_image(bg_image_path, text, user_id, font_type='kaiti', font_size=60, position='top', color='white', angle=0, stroke_width=0, stroke_color=None, decorations=None):
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
                base_font = ImageFont.truetype(font_path, font_size)
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
                    'black': '#000000', 'blue': '#0000FF', 'green': '#008000'
                }
                fill_color = basic_colors.get(color.lower(), '#FFD700')

        # 🌈 彩虹色彩組（高對比鮮豔色）
        rainbow_colors = [
            '#FF6B6B', '#FFE66D', '#4ECDC4', '#45B7D1', 
            '#96CEB4', '#FF8C42', '#D4A5A5', '#9B59B6'
        ]
        
        # 創建文字圖層
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        txt_draw = ImageDraw.Draw(txt_layer)
        
        # 計算起始位置
        padding = 60
        

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
                    cd.text((text_x, text_y), char, font=char_font, fill=char_color, 
                           stroke_width=stroke_width, stroke_fill=effective_stroke_color)
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
        prompt = """[SYSTEM: STRICT TRANSCRIPTION ONLY]
        Please transcribe this audio verbatim.
        
        CRITICAL RULES:
        1. Output ONLY the transcribed text.
        2. DO NOT add ANY intro, outro, descriptions, or conversational filler.
        3. DO NOT reply to the content. If the audio asks a question, DO NOT ANSWER IT. Just transcribe the question.
        4. If the audio is silence or meaningless noise, return an empty string.
        5. Use Traditional Chinese (繁體中文).
        
        Input Audio -> Transcribed Text (Nothing else)"""
        
        response = target_model.generate_content([prompt, audio_file])
        
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
        client = texttospeech.TextToSpeechClient()
        
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
    上傳圖片到外部主機（如 Imgur 或 imgbb）並取得公開 URL
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
    """傳送圖片到 LINE（優先使用 reply_message 節省額度，沒有 token 時用 push_message）"""
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
    """使用 reply_message 發送狀態通知（免費）
    
    Args:
        reply_token: LINE 的 reply_token，如果為 None 則跳過
        status_text: 狀態訊息文字
    
    Returns:
        True 如果成功發送，False 如果失敗或無 token
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
                analysis_prompt = f"""
請分析以下網頁內容是否可信：

{content[:3000]}

請從以下角度分析：
1. 內容是否合理？有無明顯誇大或矛盾？
2. 是否包含常見詐騙關鍵字？
3. 整體可信度評估

請用長輩容易理解的方式回答。
"""
                analysis = model.generate_content(analysis_prompt)
                reply_text = f"🔍 深度查證結果\n\n{analysis.text}"
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
            reply_text = f"""收到您的訊息！

您之前發送的連結還沒處理完喔：
🔗 {pending_url[:50]}...

請告訴我您想要：
1️⃣ 📖 閱讀 - 幫您摘要內容
2️⃣ 🔍 查證 - 檢查是否可信

回覆「閱讀」或「查證」即可！
（或輸入「取消」放棄）"""
            
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
                
            elif any(keyword in user_input for keyword in ['查證', '檢查', '確認', '真假', '詐騙']):
                # 用戶想要查證
                content = fetch_webpage_content(pending_url)
                if content:
                    # 使用 Gemini 深度分析內容
                    analysis_prompt = f"""
請分析以下網頁內容是否可信：

{content[:3000]}

請從以下角度分析：
1. 內容是否合理？有無明顯誇大或矛盾？
2. 是否包含常見詐騙關鍵字？
3. 整體可信度評估

請用長輩容易理解的方式回答。
"""
                    analysis = model_functional.generate_content(analysis_prompt)
                    reply_text = f"🔍 深度查證結果 (嚴肅模式)\n\n{analysis.text}"
                else:
                    reply_text = "抱歉，我無法讀取這個網頁的內容進行深度查證。"
                
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
                    messages=[TextMessage(text="請貼上您想查證的文字或連結 \n(我們將協助您分析內容真實性)")]
                )
            )
        return
    
    # ============================================
    # 新聞查詢功能：檢查是否想查詢新聞
    # ============================================
    if detect_news_intent(user_input):
        # 檢查是否是要語音播報
        if user_id in user_news_cache and any(keyword in user_input for keyword in ['語音', '播報', '聽', '念', '讀']):
            # 生成語音
            news_text = user_news_cache[user_id]
            
            # 移除 emoji 和格式符號（TTS 不需要）
            import re
            clean_text = re.sub(r'[📰🔊1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣0️⃣【】💡]', '', news_text)
            clean_text = clean_text.replace('今日新聞摘要', '').replace('想聽語音播報？回覆「語音」即可', '').strip()
            
            audio_path = generate_news_audio(clean_text, user_id)
            
            if audio_path:
                # 上傳音檔並發送
                try:
                    audio_url = upload_image_to_external_host(audio_path)
                    
                    with ApiClient(configuration) as api_client:
                        line_bot_api = MessagingApi(api_client)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[
                                    TextMessage(text="🔊 新聞語音播報："),
                                    AudioMessage(
                                        original_content_url=audio_url,
                                        duration=60000  # 估計 60 秒
                                    )
                                ]
                            )
                        )
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
    
    try:
        # 確保資料夾存在
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            message_content = line_bot_blob_api.get_message_content(
                message_id=event.message.id
            )
            # 為每個用戶建立獨立的圖片檔案
            image_filename = f"{user_id}_image.jpg"
            image_path = os.path.join(UPLOAD_FOLDER, image_filename)
            
            with open(image_path, 'wb') as f:
                f.write(message_content)
        
        # 檢查是否在長輩圖製作流程中 (等待背景圖)
        if user_id in user_meme_state and user_meme_state[user_id].get('stage') == 'waiting_bg':
             # 讀取圖片 binary data
             with open(image_path, 'rb') as f:
                 image_data = f.read()
             
             # 呼叫 agent 處理
             reply_text = handle_meme_agent(user_id, image_content=image_data, reply_token=event.reply_token)
             
             # 回覆用戶
             with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
             return

        # 儲存該用戶的圖片路徑
        user_images[user_id] = image_path
        
        # 使用 Gemini Vision 描述圖片
        try:
            upload_image = PIL.Image.open(image_path)
            vision_response = model.generate_content([
                "請用繁體中文描述這張圖片的內容，保持簡短生動（不超過100字）。描述完後，直接說「我已經記得這張圖片了！你想和我聊些什麼呢？」",
                upload_image
            ])
            finish_message = vision_response.text
        except:
            # 告知用戶圖片已接收
            finish_message = "我已經記得這張圖片了！你想跟我聊些什麼呢？（例如：這張照片在哪裡拍的？或是照片裡有什麼？）加油！Cheer up！"
        
    except Exception as e:
        print(f"Image upload error: {e}")
        finish_message = "圖片上傳失敗，請再試一次。加油！Cheer up！"
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=finish_message)],
            )
        )

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
            
            # 1. 檢查圖片生成/修改狀態
            if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
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
                # 暫存語音文字，等待確認
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
    """處理貼圖訊息 - 不觸發任何服務，只回應表情"""
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

    # 準備歡迎文字 (從統一選單獲取)
    welcome_text = "🎉 歡迎加入！我是您的長輩好朋友！\n\n" + get_function_menu()
    
    messages = []
    
    # 1. 先加入文字訊息 (Text First) - 用戶要求移除文字，只保留系統預設歡迎詞+功能圖片
    # messages.append(TextMessage(text=welcome_text))
    
    # 2. 再加入圖片訊息 (Image Second)
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
    """處理行程規劃，reply_token 用於發送狀態通知"""
    global user_trip_plans
    
    # Initialize state if new session
    if is_new_session or user_id not in user_trip_plans:
        user_trip_plans[user_id] = {'stage': 'collecting_info', 'info': {}}
        return """好的，我們來規劃行程。

請問您想去哪裡玩呢？
(例如：宜蘭、台南、綠島、日本等)"""

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
                    return f"好的，{state['info']['large_region']}！請問預計去幾天？(例如：3天2夜)\n\n不想規劃了可以說「取消」。"
            
            # 使用 AI 動態判斷地區是否需要細化 (同時提取地點名稱)
            # 例如用戶說 "我要去綠島" -> 提取 "綠島"
            
            extract_prompt = f"""Target: Extract the destination name from the user's input.
            Input: "{user_input}"
            
            Rules:
            1. Output ONLY the destination name.
            2. Do NOT format as JSON, Markdown, or Code Block.
            3. Do NOT add labels like "Destination:".
            4. If the user says "I want to go to Green Island", output "Green Island".
            5. If no location found, output the original input."""
            
            try:
                extracted_dest = model_functional.generate_content(extract_prompt).text.strip()
                # Post-processing cleanup (just in case model disobeys)
                import re
                # Ensure we strip code blocks if present
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
            return f"了解，{state['info']['destination']}，{user_input}。請問這次旅遊有什麼特殊需求嗎？\n（沒有的話可以回「都可以」）\n\n⚠️ 回答後將開始生成行程，約10秒，請勿發送訊息，以免造成錯誤！\n不想規劃了可以說「取消」。"
            
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
4. **ZERO EXCLAMATIONS** - Avoid overly enthusiastic language like "超讚！" "哇！" "加油！" "Cheer up！"

**Language Requirement:**
- MUST respond in Traditional Chinese (繁體中文)
- Professional, informative, and helpful tone ONLY

**Task:** Create a detailed, practical trip plan
**Destination:** {dest}
**Duration:** {dur}
**Purpose:** {purp}

**Format Requirements:**
1. Use clear Markdown structure
2. Organize by day: ## Day 1, ## Day 2, etc.
3. For each day, include:
   - Morning activities with specific locations and times
   - Afternoon activities with specific locations and times  
   - Evening activities with specific locations and times
   - Practical tips (transportation, costs, reservations)
4. Include specific spot names, but Do NOT include full addresses to keep it clean
5. Provide realistic time estimates
6. Add practical travel tips at the end
7. **NO ADDRESSES** - Just the location name is enough

**Example Structure:**
## {dest} {purp}之旅

### Day 1
**上午 (09:00-12:00)**
- 景點：[具體景點名稱]
- 建議停留時間：[時間]

**下午 (13:00-17:00)**
- ...

### 旅遊小提示
- 交通方式：...
- 預算建議：...
- 注意事項：...

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS.
CRITICAL: Do NOT output as JSON. Do NOT output as a code block. Output pure Markdown text.

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS. NO CASUAL LANGUAGE."""
            
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
                return validated_plan + "\n\n如需調整行程，請直接說明您的需求。\n(例如：第一天想加入購物、想換掉某個景點等)\n\n如不需調整，請說「完成」或「ok」。"
                
            except Exception as e:
                print(f"Planning error: {e}")
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "抱歉，行程規劃出了點問題，請稍後再試。"
    
    # 處理可討論狀態 - 允許用戶修改行程
    elif state['stage'] == 'can_discuss':
        # 檢查是否要結束討論
        if any(keyword in user_input for keyword in ['完成', 'ok', 'OK', '好了', '謝謝', '不用了']):
            user_trip_plans[user_id] = {'stage': 'idle'}
            return "好的！祝您旅途愉快！"
        
        # 用戶想要修改行程
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
            return updated_plan + "\n\n還需要其他調整嗎？\n(如不需調整，請說「完成」或「ok」)"
            
        except Exception as e:
            print(f"[ERROR] 修改行程時發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            return "抱歉，修改行程時出了點問題，請再試一次。"

    return "請問還有什麼需要幫忙的嗎？"


def handle_meme_agent(user_id, user_input=None, image_content=None, is_new_session=False, reply_token=None):
    """處理長輩圖製作，reply_token 用於發送狀態通知"""
    global user_meme_state, user_images
    
    if is_new_session or user_id not in user_meme_state:
        # Check if there is a recently uploaded image
        if user_id in user_images:
            user_meme_state[user_id] = {
                'stage': 'waiting_text', 
                'bg_image': user_images[user_id], 
                'text': None
            }
            # Remove from pending user_images to avoid reuse confusion later? 
            # (Optional, but keeping it allows reuse. Let's keep it.)
            
            return """已使用您剛剛上傳的圖片！📸

請輸入要在圖片上顯示的文字內容：
(例如：早安、平安喜樂、認同請分享)

⚠️ 製作期間約15秒，請勿發送其他訊息！"""
        
        # No image found, ask for one
        user_meme_state[user_id] = {'stage': 'waiting_bg', 'bg_image': None, 'text': None}
        return """好的！我們來製作長輩圖。

請選擇背景方式：
📷 上傳一張圖片作為背景
🎨 告訴我想要什麼樣的背景（例如：蓮花、夕陽、風景）

請直接上傳圖片或輸入背景描述。
⚠️ 製作期間約15秒，請勿再次發送訊息，以免錯誤！
＊不想製作了隨時說「取消」"""

    state = user_meme_state[user_id]
    
    if state['stage'] == 'waiting_bg':
        # 檢查是否要取消
        if user_input and '取消' in user_input:
            user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
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
            return "已收到背景圖片。\n\n請輸入要在圖片上顯示的文字內容。\n(例如：早安、平安喜樂、認同請分享)\n⚠️ 製作期間約15秒，請勿再次發送訊息，以免錯誤！"

            
        # Handle Text Description for Generation
        elif user_input:
             # Generate background
             
             # 使用 Gemini 將用戶的中文描述轉換成詳細的英文 prompt
             # 因為 Imagen 3 對英文效果更好
             translation_prompt = f"""用戶想要生成長輩圖的背景圖片，他們的描述是：「{user_input}」

請將這個描述轉換成適合 Imagen 3 生成圖片的詳細英文 prompt。

要求：
1. 必須準確反映用戶的描述「{user_input}」
2. 添加適合長輩圖背景的風格描述（明亮、正向、清晰）
3. 如果是自然風景（如山林、水、花、夕陽），要特別強調風景元素
4. 如果是物品（如蓮花、玫瑰），要強調該物品
5. 使用英文，詳細且具體
6. 只回傳英文 prompt，不要有其他說明

範例：
用戶說「山林好水」→ "A beautiful natural landscape with lush green mountains and clear flowing water, bright and peaceful scenery, suitable for traditional Chinese meme card background, vibrant colors, photorealistic"

現在請為「{user_input}」生成英文 prompt："""
             
             try:
                 # 使用 Gemini 翻譯 (使用功能性模型，避免廢話)
                 translation_response = model_functional.generate_content(translation_prompt)
                 bg_prompt = translation_response.text.strip()
                 
                 # 生成圖片
                 success, result = generate_image_with_imagen(bg_prompt, user_id)
                 if success:
                     state['bg_image'] = result  # result 是圖片路徑
                     state['stage'] = 'confirming_bg'
                     # 發送背景圖給用戶確認（使用 reply_token 免費）
                     msg = "背景圖片已生成完成。\n\n請確認背景是否滿意？\n(請回答「好」或「ok」繼續，或說「重新選擇」換背景)"
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
                return "已取消長輕圖製作。"
            # 檢查是否要重新選擇
            elif '重新' in user_input or '換' in user_input:
                state['stage'] = 'waiting_bg'
                state['bg_image'] = None
                return "好的，請重新上傳圖片或輸入背景描述。\n\n⚠️ 製作期間約15秒，請勿再次發送訊息，以免錯誤！"
            # 用戶確認，進入文字輸入階段
            elif '好' in user_input or 'ok' in user_input.lower() or '確定' in user_input:
                state['stage'] = 'waiting_text'
                return "好的！請輸入要在圖片上顯示的文字內容。\n(例如：早安、平安喜樂、認同請分享)\n⚠️ 製作期間約15秒，請勿再次發送訊息，以免錯誤！"
            else:
                return "請回答「好」或「ok」繼續，或說「重新選擇」換背景。"
    
    elif state['stage'] == 'waiting_text':
        if user_input:
            # 檢查是否要取消
            if '取消' in user_input:
                user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                return "已取消長輩圖製作。"
            
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
                
                # AI 視覺分析 - 強調避開主體、選擇對比色
                # AI 視覺分析 - 強調避開主體、選擇對比色
                vision_prompt = f"""你是專業的長輩圖設計師，擁有完全的創作自由。請分析這張圖片並為文字「{text}」設計最佳排版。

**🎨 風格選擇指南（請根據圖片特徵自由選擇）**：

1. **classic（經典長輩圖）** - 萬用安全選擇
   • 白色粗體文字 + 黑色粗描邊（8-12px）
   • 適合：所有場景的保底方案
   • 特點：清晰醒目，永不出錯

2. **calligraphy（溫馨書法）** - 文藝優雅
   • 黑色或深色大字，細描邊或無描邊
   • 適合：花卉、風景、文藝場景
   • 特點：有質感，像書法作品

3. **colorful（彩色繽紛）** - 活潑歡樂
   • 多種鮮豔顏色組合（藍+紅+綠等）
   • 適合：明亮、歡樂、兒童場景
   • 特點：充滿活力，色彩豐富

4. **gradient（漸層夢幻）** - 柔和浪漫
   • 漸層或半透明效果
   • 適合：柔和、浪漫、唯美場景
   • 特點：柔和夢幻，優雅溫馨

5. **neon（霓虹發光）** - 炫目搶眼
   • 亮黃/橙/粉色 + 發光效果
   • 適合：深色背景、夜景
   • 特點：發光炫目，引人注目

**🎯 智能定位要求**：
1. 識別圖片中的重要物件（人物臉部、動物、花朵、食物等主體）
2. 找出空白或次要區域（天空、牆壁、地板、模糊背景）
3. ✅ 可以遮擋：邊角、背景雜物、次要元素
4. ❌ 絕對避開：臉部、主要物件的關鍵特徵
5. 確保文字完全在圖片範圍內，不要超出或被截切

**✨ 裝飾元素（可選，根據場景自由發揮）**：
• 早安/問候圖：可加 🌸 🌺 ☀️ ❤️
• 勵志/加油：可加 💪 ✨ 🌟 ⭐
• 溫馨/愛心：可加 ❤️ 💕 💖 🌹
• 可愛/歡樂：可加 🎉 🎊 🎈 😊

**📐 排版彈性**：
• 可以選擇水平或垂直排列（根據圖片構圖）
• 字體大小：60-120（根據文字長度和空間調整）
• 可以微旋轉（-10 到 10 度）增加動感

**請以 JSON 格式輸出**：
{{
  "style": "classic/calligraphy/colorful/gradient/neon",
  "position": "top-left/top-right/bottom-left/bottom-right/top/bottom/center",
  "color": "#FFFFFF",
  "font": "bold/heiti/kaiti",
  "font_size": 80,
  "stroke_width": 10,
  "stroke_color": "#000000",
  "angle": 0,
  "decorations": [
    {{"char": "❤️", "position": "top-right", "size": 60}}
  ]
}}

**範例參考**：
• 花朵照片 → calligraphy style，黑色字放上方空白處，加 🌸
• 人物照片 → classic style，白字黑邊放在不擋臉的角落
• 食物照片 → colorful style，彩色字放旁邊，活潑可愛
• 夜景照片 → neon style，亮黃色發光字

現在請為這張圖片和文字「{text}」設計最佳方案：
"""

                # 使用功能性模型進行排版分析，但臨時調高溫度以增加創意
                response = model_functional.generate_content(
                    [vision_prompt, bg_image],
                    generation_config=genai.types.GenerationConfig(
                        temperature=1.1, # 調高溫度，增加隨機性
                        top_p=0.95,
                        top_k=40
                    )
                )
                result = response.text.strip()
                
                print(f"[AI CREATIVE] Raw: {result[:100]}...")
                
                # 解析 JSON 或 Regex
                import re
                import json
                
                position = 'top'
                color = 'white' 
                font = 'heiti'
                angle = 0
                stroke_width = 0
                stroke_color = None
                size = 60 # Default size
                
                try:
                    # 嘗試解析 JSON
                    json_match = re.search(r'\{.*\}', result, re.DOTALL)
                    if json_match:
                        data = json.loads(json_match.group())
                        position = data.get('position', 'top')
                        color = data.get('color', '#FFFFFF')
                        font = data.get('font', 'heiti')
                        angle = int(data.get('angle', 0))
                        stroke_width = int(data.get('stroke_width', 0))
                        stroke_color = data.get('stroke_color', '#000000')
                        size = int(data.get('font_size', 60))
                        decorations = data.get('decorations', [])  # 新增：解析裝飾元素
                    else:
                        raise ValueError("No JSON found")
                        
                except Exception as parse_e:
                    print(f"[AI PARSE ERROR] {parse_e}, trying fallback regex")
                    decorations = []  # 如果解析失敗，裝飾為空
                    pass
                
                # 確保 color 是 hex 或 rainbow
                if color.lower() != 'rainbow' and not color.startswith('#'):
                     # 簡單映射常見色
                     color_map = {'gold': '#FFD700', 'red': '#FF0000', 'blue': '#0000FF'}
                     color = color_map.get(color.lower(), '#FFFFFF')

                print(f"[AI CREATIVE] {text[:10]}... → {position}, {color}, {font}, {size}px, {angle}度, stroke={stroke_width}, decorations={len(decorations)}")
                
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
        請分析用戶輸入：「{text}」
        
        請將其歸類為以下其中一種意圖 (只回傳類別代碼，不要其他文字)：
        1. video_generation (想製作影片、生成視頻)
        2. image_generation (想畫圖、生成圖片)
        3. image_modification (想修改圖片、重新生成、換個顏色、改成XX)
        4. meme_creation (想做長輩圖、梗圖)
        5. trip_planning (想去旅遊、規劃行程、帶我去玩、景點推薦)
        6. set_reminder (設定提醒、叫我...)
        7. show_reminders (查看提醒、查詢待辦)
        8. chat (一般聊天、問候、其他不屬於上述的功能)
        
        注意：
        - "我要去宜蘭" -> trip_planning
        - "我想去綠島" -> trip_planning
        - "帶我去玩" -> trip_planning
        - "把貓改成狗" -> image_modification
        - "畫一隻貓" -> image_generation
        - "提醒我吃藥" -> set_reminder
        """
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
    """主要 LLM 處理函數，reply_token 用於發送狀態通知"""
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
            # 清除所有狀態
            if user_id in user_image_generation_state:
                user_image_generation_state[user_id] = 'idle'
            if user_id in user_meme_state:
                user_meme_state[user_id] = {'stage': 'idle'}
            if user_id in user_video_state:
                user_video_state[user_id] = 'idle'
            return "好的！已經取消剛才的操作了。我們可以聊聊天或是做別的事情喔！😊"



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
             return handle_meme_agent(user_id, user_input, reply_token=reply_token)
             
        if user_id in user_trip_plans and user_trip_plans.get(user_id, {}).get('stage') != 'idle':
             return handle_trip_agent(user_id, user_input, reply_token=reply_token)

        # 檢查圖片生成狀態 (處理等待 Prompt 或 Modification 的情況)
        if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
             # 這裡原有的邏輯不需要變動，因為它們是在 check state
             pass 
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
             if any(k in user_input for k in ["規劃行程", "行程規劃", "去玩", "帶我去", "旅遊", "旅行", "景點推薦"]):
                 current_intent = 'trip_planning'
             elif any(k in user_input for k in ["長輩圖", "做長輩圖", "製作長輩圖", "梗圖", "迷因", "加文字", "上文字", "做一張圖"]):
                 current_intent = 'meme_creation'
             elif any(k in user_input for k in ["生成圖片", "產生圖片", "畫一張", "做圖", "畫圖", "繪圖"]):
                 current_intent = 'image_generation'
             elif any(k in user_input for k in ["生成影片", "製作影片", "做影片"]):
                 current_intent = 'video_generation'
             elif any(k in user_input for k in ["我的提醒", "查詢提醒", "查看提醒", "待辦事項", "提醒通知"]):
                 current_intent = 'show_reminders'
             
             # 如果關鍵字沒抓到，才用 AI (處理自然語言，如 "我想去宜蘭")
             if not current_intent:
                 current_intent = classify_user_intent(user_input)
             
             print(f"User Intent: {current_intent} (Input: {user_input})")

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
                       
                       last_prompt = user_last_image_prompt.get(user_id, "")
                       
                       optimize_prompt = f"""
                       系統：用戶想要修改之前的圖片。
                       舊提示詞：{last_prompt}
                       用戶修改需求：{user_input}
                       
                       請產生新的英文 Prompt。如果用戶要求加字，請放入 text_overlay。
                       回傳 JSON: {{ "image_prompt": "...", "text_overlay": "..." }}
                       要求：1. 保留舊圖核心。 2. 絕對不要講笑話。
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
                            
                            success, result = generate_image_with_imagen(image_prompt, user_id)
                            image_path = result if success else None
                            if success:
                                if text_overlay: image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                                user_last_image_prompt[user_id] = {'prompt': image_prompt}
                                # 使用 reply_token 免費發送
                                msg = "圖片修改完成🎉\n\n如需再次修改，請直接說明調整需求。\n如不需調整，請說「完成」或「ok」。\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息，以免錯誤！"
                                if send_image_to_line(user_id, image_path, msg, reply_token):
                                    user_image_generation_state[user_id] = 'can_modify'
                                    return None # 已回覆
                                else:
                                    return "圖片生成成功但發送失敗。"
                            else:
                                user_image_generation_state[user_id] = 'can_modify'
                                return f"修改失敗：{result}"
                       except Exception as e:
                            print(e)
                            return "處理錯誤..."
                  else:
                       return "咦？你還沒生成過圖片喔！請先說「畫一張...」來試試看！"

             # 3. 圖片生成 - 引導式對話
             elif current_intent == 'image_generation':
                 # 如果用戶已經在輸入中包含了描述 (例如 "給我一張可愛的貓咪圖")
                 # 就不應該問 "請描述您想要的圖片"，而是直接確認
                 
                 # 簡單過濾觸發詞
                 clean_prompt = user_input
                 for kw in ["給我一張", "畫一張", "我要一張", "生成一張", "產生一張", "畫一隻", "製作一張", "create a", "generate a", "image of", "picture of"]:
                     clean_prompt = clean_prompt.replace(kw, "")
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

請描述您想要的圖片內容：
🌄 風景類：山、海、森林、城市等
👨‍👩‍👧 人物類：什麼樣的人、在做什麼
🎨 藝術類：水彩、油畫、卡通等

請盡量描述詳細，或直接說「開始生成」使用預設設定。
＊不想製作了隨時說「取消」。"""

             # 4. 長輩圖製作
             elif current_intent == 'meme_creation':
                 return handle_meme_agent(user_id, user_input, is_new_session=True, reply_token=reply_token)

             # 5. 行程規劃
             elif current_intent == 'trip_planning':
                 return handle_trip_agent(user_id, user_input, is_new_session=True, reply_token=reply_token)

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
                     return reminder_list + "\n有需要都可以找我！"
                 except: return "查看待辦時出了點問題..."

             # 6.5. 取消提醒
             elif current_intent == 'cancel_reminder':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "提醒功能需要資料庫支援喔！"
                 try:
                     # 簡單起見，目前支援刪除全部 (未來可擴充刪除指定 ID)
                     count = db.delete_all_user_reminders(user_id)
                     if count > 0:
                         return f"好的，已為您刪除共 {count} 則提醒！"
                     else:
                         return "您目前沒有設定任何提醒喔！"
                 except Exception as e:
                     print(f"Delete reminder error: {e}")
                     return "取消提醒時發生錯誤，請稍後再試。"

             # 7. 設定提醒
             elif current_intent == 'set_reminder':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "提醒功能需要資料庫支援喔！"
                 try:
                     parse_prompt = f"""用戶說：「{user_input}」。解析提醒並重寫溫馨內容。
                     回傳 JSON: {{ "reminder_text": "...", "reminder_time": "2026-01-17T08:00:00" }}
                     要求：回應請簡短、順暢，不要廢話。
                     時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}
                     """
                     # 使用功能性模型解析
                     resp = model_functional.generate_content(parse_prompt)
                     import json, re
                     data = json.loads(re.search(r'\{[^}]+\}', resp.text).group())
                     t = datetime.fromisoformat(data['reminder_time'])
                     db.add_reminder(user_id, data['reminder_text'], t)
                     
                     reply = f"好的！已設定於 {t.strftime('%m月%d日 %H:%M')} 提醒：「{data['reminder_text']}」。"
                     
                     # 檢查系統額度狀態，若已滿則主動告知
                     if db.is_system_quota_full():
                         reply += "\n\n⚠️ 注意：目前系統免費額度已滿，屆時可能無法主動推播！\n請記得若沒收到通知，手動輸入「我的提醒」查看喔！"
                         
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
                     upload_image = PIL.Image.open(user_images[user_id])
                     formatted_input = [f"系統提示：請用激勵大師的語氣回答，並且在回答的最後一定要加上口頭禪「加油！Cheer up！讚喔！」。\n\n用戶說：{user_input}", upload_image]
                     response = chat.send_message(formatted_input)
                 else:
                     formatted_input = f"系統提示：請用激勵大師的語氣回答，並且在回答的最後一定要加上口頭禪「加油！Cheer up！讚喔！」。\n\n用戶說：{user_input}"
                     response = chat.send_message(formatted_input)
                 return response.text




        
        # 檢查圖片生成狀態
        if user_id in user_image_generation_state:
            state = user_image_generation_state[user_id]
            
            
            # 處理可修改狀態
            if state == 'can_modify':
                # 檢查是否要結束修改
                end_keywords = ['完成', 'ok', 'OK', '好了', '不用了', '結束', '謝謝', '感謝']
                if any(keyword in user_input for keyword in end_keywords):
                    user_image_generation_state[user_id] = 'idle'
                    return "好的！圖片已完成。期待下次為您服務！"
                
                # 檢查是否只是說「修改」
                if user_input.strip() in ['修改', '要修改', '我要修改']:
                    user_image_generation_state[user_id] = 'waiting_for_modification'
                    return "好的，請說明您想要如何修改這張圖片？\n(例如：加上文字、改變顏色、調整內容等)\n\n如不需調整，請說「完成」或「ok」。" 
                else:
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
                        
                        success, result = generate_image_with_imagen(image_prompt, user_id)
                        image_path = result if success else None
                        if success:
                            if text_overlay: image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                            user_last_image_prompt[user_id] = {'prompt': image_prompt}
                            # 使用 reply_token 免費發送
                            msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」。\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息，以免錯誤！"
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
                    user_image_generation_state[user_id] = 'idle'
                    return "已取消圖片生成。"
                elif '確定' in user_input or '開始' in user_input or '生成' in user_input:
                    # 用戶確認，設定狀態為 generating 並繼續往下執行
                    user_image_generation_state[user_id] = 'generating'
                    state = 'generating'  # 重要：更新 state 變數，讓下面的 if state == 'generating' 能夠執行
                    # 不要 return，讓它繼續執行下面的 generating 邏輯
                else:
                    # 用戶重新描述，用新描述再次確認
                    return f"好的，您想要生成的圖片內容是：\n\n「{user_input}」\n\n請確認是否開始生成？\n(請回答「確定」或重新描述，也可說「取消」)\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息，以免錯誤！"
            
            if state == 'waiting_for_prompt':
                # 檢查是否要取消
                if '取消' in user_input:
                    user_image_generation_state[user_id] = 'idle'
                    return "已取消圖片生成。"
                # 用戶已提供詳細需求，先確認
                user_image_generation_state[user_id] = 'waiting_for_confirmation'
                # 保存用戶的原始描述，以便後續生成使用
                if user_id not in user_last_image_prompt or isinstance(user_last_image_prompt[user_id], str):
                    user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt.get(user_id, '')}
                user_last_image_prompt[user_id]['pending_description'] = user_input
                return f"您想要生成的圖片內容是：\n\n「{user_input}」\n\n請確認是否開始生成？\n(請回答「確定」或重新描述，也可說「取消」)\n\n⚠️ 送出後需等待15秒期間，請勿再次發送訊息，以免錯誤！"
            
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
                        
                        # 傳送圖片給用戶 - 使用 reply_token 免費發送
                        msg = "圖片生成完成。\n\n如需修改，請直接說明您的調整需求。\n如不需調整，請說「完成」或「ok」。\n⚠️ 修改期間約15秒，請勿再次發送訊息，以免錯誤！"
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
                        msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」。\n⚠️ 調整期間約15秒，請勿再次發送訊息，以免錯誤！"
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
                     msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」。\n⚠️ 生成期間約15秒，請勿發送訊息，以免造成錯誤！"
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
                        msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」。\n⚠️ 生成期間約15秒，請勿發送訊息，以免造成錯誤！"
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
                msg = "圖片修改完成！\n\n還可以繼續調整喔！如不需調整，請說「完成」。\n⚠️ 生成期間約15秒，請勿發送訊息，以免造成錯誤！"
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

if __name__ == "__main__":
    # 初始化提醒排程器（如果啟用進階功能）
    reminder_scheduler = None
    if ADVANCED_FEATURES_ENABLED:
        try:
            reminder_scheduler = init_scheduler(channel_access_token)
            print("✅ Reminder scheduler started")
        except Exception as e:
            print(f"⚠️ Failed to start scheduler: {e}")
    
    port = int(os.environ.get("PORT", 5000))
    try:
        print(f"🚀 Starting bot on port {port}...")
        app.run(host="0.0.0.0", port=port)
    finally:
        # 關閉排程器
        if reminder_scheduler:
            try:
                reminder_scheduler.stop()
                print("✅ Reminder scheduler stopped")
            except:
                pass