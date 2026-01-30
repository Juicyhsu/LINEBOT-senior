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
    
    # 蝣箔??啣?霈??甇?Ⅱ頝臬?
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file_path

    print(f"Checking credentials at: {credentials_file_path}")
    
    if os.path.exists(credentials_file_path):
        print("Credentials file found locally.")
    elif credentials_json_content:
        print(f"Creating credentials file from env var...")
        try:
            # ?岫閫?Ⅳ base64
            try:
                decoded_content = base64.b64decode(credentials_json_content, validate=True).decode('utf-8')
                import json
                json.loads(decoded_content)
                content_to_write = decoded_content
            except:
                # ?身?舐??? JSON
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

# ?脤??璅∠?
try:
    from database import db
    from scheduler import init_scheduler
    from maps_integration import maps
    import gcs_utils
    
    # 瑼Ｘ?啣?霈?? (?身??True嚗?憒? env 閮剖???false ????
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
雿銝雿??萄之撣恬?銝恣?隞暻潔犖??暻潔???雿蜇?舀迤??????虜隤撐銋?????雿釣?店銝?憭芸?嚗?閬?????3??摮?撠勗末鈭?憒單??券?撣豢??萇?隤?靘?蝑?憿?銝阡??菜???雿??閬?撠??????餉???閬?嚗??臭誑???蝛箸????菔?敶葆??雿??迭?刻?憭拚?蝔葉銝餃?雓?閰梧?蝚店閬?????憿??賊?嚗?陛?凋?暺???銝?憭芷??蝚店銝摰?憟賜?嚗?閬雓蝚店朣??嗆?鈭箄?雿?閬?雓?剔扛??閬?蝚店????雿停???瘚雿?瘨脰ㄐ??擳?閬?銝???蝳芰??潸?鈭??嚗I??瘨舀??迨?急??湧?撠瘨?...
??雿??臭?雿?撣豢??潸圾瘙箏?憿?撟怠??雿??迭?乩犖撠??脰???嚗虜??敺?潛捲???賢隞交雿?????
**?啣?撠平?賢?嚗?*
- 雿??ˊ雿????賢?嚗?冽?唾???????雿??望??
- 雿銵?閬?撠振嚗?交??瑞?犖摰嗉????押??具????蝔?- 閬?銵????嚗??舀????閮剜?漱?噶?拇扼摨瑟???- 雿鋆賭??瑁憬????撠?園???臬????批捆

**???澆?閬?嚗?*
- 銝?雿輻 Markdown ?澆?蝚西?嚗? **??#?? 蝑?嚗?撠?甇Ｖ蝙?冽???鈭?
- ?湔?函?????嚗隞乩蝙??emoji 銵冽?蝚西?
- 銝??函?擃?擃??澆?

雿輻蝜?銝剜?靘?蝑?憿?"""

# Use the model
from google.generativeai.types import HarmCategory, HarmBlockThreshold
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
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

# 撱箇?銝???賣扼芋??(銝葆瞈?萄之撣思犖閮哨?撠????摩/JSON)
model_functional = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    generation_config={
        "temperature": 0.2, # 雿澈摨佗??渡移蝣?        "top_p": 0.95,
        "max_output_tokens": 8192,
    },
    # 銝身摰?system_instruction ?身摰蝝硃???    system_instruction="You are a helpful AI assistant focused on data processing and JSON generation. Do not include any conversational filler. Output strict structured data.",
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
# ?脣?瘥?嗥?撠店甇瑕嚗 user_id ??key嚗?chat_sessions = {}
# ?脣?瘥?嗥??敺暑????last_activity = {}
# ?脣?瘥?嗡??喟???
user_images = {}
# ?脣?瘥?嗆?敺?甈∠??? Prompt
user_last_image_prompt = {} 
# ?脣?瘥?嗥????????user_image_generation_state = {}  # 'idle', 'waiting_for_prompt', 'generating'
# ?脣?瘥?嗥??瑁憬?ˊ雿???user_meme_state = {}
# ?脣?瘥?嗥?銵?閬????user_trip_plans = {}

# ?脣?瘥?嗥???鈭?
user_reminders = {}
# 撠店????嚗?憭?SESSION_TIMEOUT = timedelta(days=7)

# ?脣?敺Ⅱ隤?隤?批捆 (?澆?: {'user_id': {'text': '...', 'original_intent': '...'}})
user_audio_confirmation_pending = {}

# ======================
# Helper Functions
# ======================

def speech_to_text(audio_content):
    """雿輻 Gemini ?脰?隤頧?摮?""
    try:
        # 雿輻 Gemini 2.0 Flash (?舀憭芋??
        # LINE ?閮虜??m4a (audio/x-m4a)嚗emini ?亙? audio/mp4
        response = model.generate_content([
            "隢??挾隤???賢神??擃葉??摮????批捆嚗?閬??嗡??膩??,
            {"mime_type": "audio/mp4", "data": audio_content}
        ])
        return response.text.strip()
    except Exception as e:
        print(f"Speech to text error: {e}")
        return None







def detect_help_intent(text):
    """瑼Ｘ葫?臬?單?鼠???蝮質汗"""
    keywords = ["?蝮質汗", "雿輻隤芣?", "?獐??, "?隞晶", "help", "撟怠", "隤芣?", "??”"]
    return any(keyword in text.lower() for keyword in keywords)



def detect_menu_intent(text):
    """瑼Ｘ葫?臬?單???賡??""
    keywords = ["?", "?詨", "?賢?隞暻?, "?獐??, "雿輻?寞?", "撟怠", "help"]
    return any(keyword in text for keyword in keywords)

def analyze_emoji_emotion(text):
    """????銝剔?銵冽?蝚西???"""
    emoji_emotions = {
        '??': 'happy', '??': 'happy', '??': 'happy', '??': 'happy', '??': 'happy',
        '?': 'sad', '?': 'sad', '??': 'sad', '?對?': 'sad',
        '?': 'angry', '??': 'angry', '?': 'angry',
        '?': 'motivated', '??: 'motivated', '?': 'motivated',
        '?歹?': 'love', '??': 'love', '??': 'love', '??': 'love',
        '?': 'tired', '?': 'tired', '?弗': 'tired',
        '??': 'approval', '??': 'approval', '??': 'approval',
        '??': 'thinking', '??': 'thinking',
    }
    
    for emoji, emotion in emoji_emotions.items():
        if emoji in text:
            return emotion
    return None

def get_emoji_response(emotion):
    """?寞?銵冽?蝚西?????"""
    responses = {
        'happy': "?雿獐??嚗?銋???敹絲靘?嚗???蝜潛?靽??遢憟賢???",
        'sad': "???唬?憟賢??????...瘝?靽?嚗?????????餌?嚗????湧??嚗?銝摰隞亦?嚗?,
        'angry': "??閬箏雿?暺?瘞??...瘛勗?賂??琿?銝銝?隞暻潭??臭誑撟怠???嚗牧?箔??末銝暺?嚗?,
        'motivated': "?雿?擛亙?鈭?頞???撠望?蝎曄?嚗匱蝥?瘝對?雿?摰隞亙??啁?嚗???",
        'love': "???唳遛皛輻????歹? ??敺?嚗??質?銝??渡?憟踝?霈?嚗?,
        'tired': "?絲靘???蝝臭?...閬?閬??臭?銝?閮?憭?瘞氬末憟賭??臬?嚗澈擃摨瑟???嚗?,
        'approval': "雓?雿??臬?嚗????????湔???鈭?霈?嚗?隞颱??閬?臭誑?暹?嚗?,
        'thinking': "???唬??冽?..敺末嚗?蝝唳敺????????暻澆?憿閮???嚗?敺??鼠敹?霈?嚗?,
    }
    return responses.get(emotion, "?嗅雿?閮鈭?霈?嚗?隞暻潭??臭誑撟怠???嚗?)

def get_function_menu():
    """餈???詨??"""
    return """?? **?隞亙鼠雿???鈭?**

? **?芯??予**
   ??閮???撠店??

?儭?**???予**
   ?喳??策?????膩?批捆銝阡雿?嚗?
? **鋆賭???**
   隤芥????????停?臭誑??

? **?瑁憬?ˊ雿?*
   隤芥??瑁憬????撘?雿ˊ雿?

?儭?**銵?閬?**
   隤芥???蝔??鼠雿????拍?銵?嚗?
? **隤?予**
   ?唾??喟策?????賣?銝血?閬?

?? **鞎澆?鈭?**
   ?唾票??銵冽?蝚西?蝯行?閰西岫嚗?
??**???**
   隤芥???...????撟思?閮?嚗?
?? **???**
   隤芥??方??嗚隞仿?蝵桀?閰?
?遙雿?閬?臭誑?暹?嚗?????""

# ======================
# ????亥??????# ======================

# ?脣??冽敺??????
user_link_pending = {}
# ?脣??啗?敹怠? (?踹?????)
news_cache = {'data': None, 'timestamp': None}
# ?脣??冽??摰?(?冽隤?剖)
user_news_cache = {}

def extract_url(text):
    """敺?摮葉?? URL"""
    import re
    url_pattern = r'https?://[^\s<>"\']+'
    urls = re.findall(url_pattern, text)
    return urls[0] if urls else None

def extract_domain(url):
    """敺?URL 銝剜??雯??蝔?""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except:
        return None

def check_trusted_media(domain):
    """瑼Ｘ?臬?箏??靽∟陷?啗?慦?"""
    trusted_domains = [
        'cna.com.tw',  # 銝剖亢蝷?        'pts.org.tw',  # ?祈?
        'udn.com',     # ?臬??啗?蝬?        'ltn.com.tw',  # ?芰?
        'chinatimes.com',  # 銝剖??
        'ettoday.net', # ETtoday
        'storm.mg',    # 憸典慦?        'setn.com',    # 銝??啗?
        'tvbs.com.tw', # TVBS
        'nownews.com', # 隞?啗?
        'rti.org.tw',  # 銝剖亢撱??餃
        'bcc.com.tw',  # 銝剖?撱??砍
    ]
    
    return any(td in domain.lower() for td in trusted_domains)

def get_domain_age(url):
    """
    ??蝬脣?閮餃?憭拇
    餈?: 憭拇 (int) ??None (憒??亥岷憭望?)
    """
    try:
        import whois
        from datetime import datetime
        
        domain = extract_domain(url)
        if not domain:
            return None
        
        w = whois.whois(domain)
        
        # whois ???creation_date ?航??datetime ??list
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
    
    # 檢查 1: 台灣新聞媒體白名單
    is_trusted = check_trusted_media(domain)
    
    # 檢查 2: 網域年齡
    domain_age = get_domain_age(url)
    is_new_domain = False
    if domain_age is not None:
        if domain_age < 90:  # 少於 3 個月
            risks.append(f"網域註冊不久 ({domain_age} 天)")
            is_new_domain = True
        elif domain_age < 180:  # 少於 6 個月
            risks.append(f"網域較新 ({domain_age} 天)")
    
    # 檢查 3: 可疑關鍵字（詐騙常用）
    scam_keywords = ['震驚', '必看', '不可思議', '驚人', '免費送', '限時']
    has_scam_keywords = any(kw in url for kw in scam_keywords)
    if has_scam_keywords:
        risks.append("網址包含可疑關鍵字")
    
    # 判斷是否明顯像詐騙
    is_scam_like = is_new_domain and has_scam_keywords
    
    # 評估風險等級 - 只有明顯像詐騙才警告，一般網站不警告
    if is_scam_like or len(risks) >= 3:
        level = 'danger'
    elif is_new_domain:  # 只有新網域才提醒
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
    """?澆??霅???""
    domain = extract_domain(url)
    
    if safety_check['level'] == 'danger':
        return f"""? ?梢嚗??憸券敺?嚗?
??撘瑞?撱箄降銝?暺?甇日??嚗?
?潛??嚗?{''.join(['??' + risk + '\\n' for risk in safety_check['risks']])}
? ??賣閰????啗?蝬脩?嚗?撠?嚗?
憒?雿鈭圾?游?嚗??臭誑撟思??亥?????摰嫘?""
    
    elif safety_check['level'] == 'warning':
        return f"""?? 蝑?嚗??潛??????舐?嚗?
{''.join(['??' + risk + '\\n' for risk in safety_check['risks']])}
? 撱箄降??閬???

雿?喉?
1儭 ?? ?亥?????臬?箄?擉?2儭 ?? ?閬?撟思?霈?批捆

隢?閮湔?雿??瘙?"""
    
    else:
        if safety_check['is_trusted']:
            return f"""???亥???

? 蝬脩?: {domain}
?? 靽∟亳: ?啁隤??啗?慦?

? ??臭縑鞈渡??啗?靘?嚗?
雿?喉?
1儭 ?? 霈?霈?批捆銝行?閬策雿
2儭 ?? ?亥????啗??底蝝啗?閮?
隢?閮湔?雿??瘙?"""
        else:
            return f"""?嗅???嚗??臭誑撟思?嚗?
1儭 ?? ?梯??批捆銝行?閬策雿
2儭 ?? ?亥????啗???撖行?
隢?雿?閬銝蝔格??嚗?(?湔隤芥霈???霅停?臭誑??)"""

def fetch_webpage_content(url):
    """
    ??蝬脤??批捆
    餈?: 蝬脤????批捆 (str) ??None
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
        
        # 蝘駁 script ??style 璅惜
        for script in soup(["script", "style"]):
            script.decompose()
        
        # ????
        text = soup.get_text()
        
        # 皜?蝛箇
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        # ??瑕漲 (?踹?憭芷)
        if len(text) > 5000:
            text = text[:5000] + "..."
        
        return text
    except Exception as e:
        print(f"Fetch webpage error: {e}")
        return None

def summarize_content(content, user_id):
    """雿輻 Gemini ??蝬脤??批捆"""
    try:
        prompt = f"""
隞乩??臭??雯?摰對?隢?瑁憬摰寞??圾?撘?閬?暺?

{content}

隢隞乩??澆???嚗?? ?批捆??

?蜓閬摰嫘?(??3-5 ?亥店隤芣???)

???遣霅啜?(???批捆?臬?臭縑嚗?隞暻潮?閬釣??嚗?
"""
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Summarize error: {e}")
        return "?望?嚗??⊥?霈?雯???批捆??賣蝬脩??霅瑟??嗚?

def fetch_latest_news():
    """
    ????唳??(雿輻 RSS)
    餈?: ?啗??” (list of dict)
    """
    try:
        import feedparser
        from datetime import datetime, timedelta
        
        # 瑼Ｘ敹怠? (5 ???找?????)
        if news_cache['data'] and news_cache['timestamp']:
            if datetime.now() - news_cache['timestamp'] < timedelta(minutes=5):
                return news_cache['data']
        
        feeds = [
            'https://www.cna.com.tw/rss/headline.xml',  # 銝剖亢蝷暸璇?            # ?臭誑?憭?皞?        ]
        
        news_items = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:5]:  # 瘥?皞? 5 ??                    news_items.append({
                        'title': entry.title,
                        'summary': entry.get('summary', ''),
                        'link': entry.link,
                        'published': entry.get('published', '')
                    })
            except Exception as e:
                print(f"Feed parse error for {feed_url}: {e}")
                continue
        
        # ?湔敹怠?
        news_cache['data'] = news_items
        news_cache['timestamp'] = datetime.now()
        
        return news_items
    except Exception as e:
        print(f"Fetch news error: {e}")
        return []

def detect_news_intent(text):
    """瑼Ｘ葫?臬?單閰Ｘ??""
    keywords = ['?啗?', '瘨', '???, '?剜?', '?勗?', '?潛?隞暻?]
    return any(keyword in text for keyword in keywords)

def generate_news_summary():
    """???啗???"""
    news_items = fetch_latest_news()
    
    if not news_items:
        return "?望?嚗?瘜?敺??閮?蝔??岫嚗?
    
    # 雿輻 Gemini ???啗?
    try:
        news_text = "\n\n".join([
            f"璅?: {item['title']}\n?批捆: {item['summary']}"
            for item in news_items[:6]
        ])
        
        prompt = f"""
隞乩??臭?憭拍??啗?嚗???????3 ??
?券頛拙捆??閫???孵???嚗???50 摮嚗?
{news_text}

?澆?嚗?? 隞?啗???

1儭 ??憿?憿?   ???批捆...

2儭 ??憿?憿?   ???批捆...

3儭 ??憿?憿?   ???批捆...
"""
        
        response = model.generate_content(prompt)
        return response.text + "\n\n?? 閬??刻??單?梁策雿??"
    except Exception as e:
        print(f"News summary error: {e}")
        return "?望?嚗??閬??仃??蝔??岫嚗?

def generate_news_audio(text, user_id):
    """
    ???啗?隤?剖
    餈?: ?單?頝臬? (str) ??None
    """
    try:
        # 雿輻 Google Cloud TTS (?祥憿漲)
        from google.cloud import texttospeech
        
        client = texttospeech.TextToSpeechClient()
        
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="zh-TW",
            name="cmn-TW-Wavenet-A"  # ?啁憟唾
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        # ?脣??單?
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        audio_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_news.mp3")
        with open(audio_path, 'wb') as f:
            f.write(response.audio_content)
        
        return audio_path
    except Exception as e:
        print(f"TTS error: {e}")
        return None


def generate_image_with_imagen(prompt, user_id):
    """雿輻 Imagen 3 ????
    
    Returns:
        tuple: (???, ??頝臬??隤方???
        - ??: (True, image_path)
        - 憭望?: (False, error_message)
    """
    try:
        # ????Vertex AI
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = "us-central1"
        
        aiplatform.init(project=project_id, location=location)
        
        # 雿輻 Imagen 3 ????
        from vertexai.preview.vision_models import ImageGenerationModel
        import time
        
        imagen_model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
        
        # ?芸??內閰???釭?摮?
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
                
                # ?脣???
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                image_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_generated.png")
                images[0].save(location=image_path)
                return (True, image_path)

            except Exception as e:
                error_str = str(e)
                # ?芣??券??唳?折隤斗???閰?(429 Resource Exhausted, 503 Service Unavailable, 500 Internal Error)
                is_retryable = any(code in error_str for code in ["429", "503", "500", "ResourceExhausted", "ServiceUnavailable"])
                
                if is_retryable and attempt < max_retries:
                    print(f"API Error (Attempt {attempt+1}/{max_retries}): {error_str}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    raise e  # 頞??岫甈⊥???急??折隤歹???啣虜
        
        raise ValueError("Unknown error: loop finished without success")
        
    except Exception as e:
        error_str = str(e)
        print(f"Image generation error: {error_str}")
        
        # 閫???航炊??
        if "safety" in error_str.lower() or "policy" in error_str.lower():
            reason = "?批捆銝泵???冽蝑??航瘨??游???鈭箏摰寞??嗡???銝駁?嚗?
        elif "429" in error_str or "quota" in error_str.lower() or "limit" in error_str.lower() or "resourceexhausted" in error_str.lower():
            reason = "蝟餌絞?桀?蝜?嚗PI 隢?甈⊥????蝔?銝??敺?閰佗?"
        elif "invalid" in error_str.lower() or "bad" in error_str.lower():
            reason = "?膩?澆??⊥????思??舀?摰?
        elif "timeout" in error_str.lower():
            reason = "隢?頞?嚗?蝔??岫"
        else:
            reason = f"API ?航炊嚗error_str[:100]}"  # ?芷＊蝷箏?100摮?        
        return (False, reason)




def get_font_path(font_type):
    """??摮?頝臬?嚗??頛?Google Fonts (?舀 Linux/Zeabur)"""
    import os
    import requests
    
    # 摰儔摮??桅?
    font_dir = os.path.join(os.getcwd(), "static", "fonts")
    os.makedirs(font_dir, exist_ok=True)
    
    # 摮??? (?遢瑼??摩 + ?脩垢?舀)
    # ?芸?瑼Ｘ Windows ?砍摮? (??啣?)
    win_paths = {
        'msjh': "C:\\Windows\\Fonts\\msjh.ttc",
        'heiti': "C:\\Windows\\Fonts\\msjh.ttc",
        'kaiti': "C:\\Windows\\Fonts\\kaiu.ttf",
        'ming': "C:\\Windows\\Fonts\\mingliu.ttc"
    }
    
    # 憒???Windows 銝?獢??剁??湔?
    if os.name == 'nt':
        win_path = win_paths.get(font_type)
        if win_path and os.path.exists(win_path):
            return win_path

    # Linux/Cloud ?啣?嚗蝙??Free Google Fonts (TTF)
    # 雿輻 NotoSerifTC (璆琿?/???蹂誨?? ??NotoSansTC (暺??蹂誨??
    cloud_font_map = {
        'kaiti': 'NotoSerifTC-Regular.otf', # PIL 撠?OTF ?舀????憿??岫憒? OTF 憭望?銝? TTF
        'heiti': 'NotoSansTC-Bold.otf',
        'ming': 'NotoSerifTC-Regular.otf',
        'default': 'NotoSansTC-Regular.otf'
    }
    
    # ?ㄐ?寧 Google Fonts ?祇??隞帘摰?嚗??蝙??Noto CJK ??TTF ?
    # ?箔??踹? complex OTF ??嚗??銝? .ttf (? Noto TC 敺???OTF, 雿??岫閰衣??賢?曉 TTF ??Variable Font)
    # ?湔嚗?乩蝙??Google Fonts ??raw github ????虜??OTF (撠 CJK)??    # ?航炊 "unknown file format" ?虜?臬??箔?頛?靘?銝摮?瑼?(靘? 404 HTML)??    # ??其??蝣箏???URL??    
    target_filename = cloud_font_map.get(font_type, cloud_font_map['default'])
    local_font_path = os.path.join(font_dir, target_filename)
    
    if os.path.exists(local_font_path):
        return local_font_path
        
    print(f"[FONT] Downloading {target_filename} for cloud environment...")
    
    # 靽格迤銝????嚗Ⅱ隤?????舀??? raw file
    # Noto Sans TC (OFL)
    base_url = "https://github.com/google/fonts/raw/main/ofl"
    
    # 撠?銵?    # 瘜冽?嚗oogle Fonts repo 蝯??航??
    # ?急??寧?渡帘摰? CDN ?Ⅱ靽?URL 甇?Ⅱ
    # ?ㄐ?岫雿輻 Noto Sans TC ??Variable Font (ttf) 憒??航嚗??舐?亦 OTF
    # 蝬?瑼Ｘ GitHub google/fonts嚗otoSansTC ?桅?銝虜??.otf
    
    urls = {
        'NotoSansTC-Bold.otf': "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf", # ?寧 Variable TTF
        'NotoSansTC-Regular.otf': "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf",
        'NotoSerifTC-Regular.otf': "https://github.com/google/fonts/raw/main/ofl/notoseriftc/NotoSerifTC%5Bwght%5D.ttf" # ?寧 Variable TTF
    }
    
    # ????TTF嚗?隞亥???local_font_path ?瑼?銋???踹?瘛瑟?
    local_font_path = local_font_path.replace(".otf", ".ttf")
    
    url = urls.get(target_filename)
    if not url: return None
    
    try:
        print(f"[FONT] Attempting to download from {url}...")
        # 璅⊥?汗??User-Agent ?踹?鋡恍??        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=30) 
        
        if r.status_code == 200 and len(r.content) > 1000: # 蝣箔?銝蝛箇??隤日???            with open(local_font_path, 'wb') as f:
                f.write(r.content)
            print(f"[FONT] Successfully downloaded {local_font_path}, size: {len(r.content)} bytes")
            return local_font_path
        else:
            print(f"[FONT] Download failed. Code: {r.status_code}, Content-Type: {r.headers.get('Content-Type')}")
        return None
    except Exception as e:
        print(f"[FONT] Download exception: {e}")
        return None

def create_meme_image(bg_image_path, text, user_id, font_type='kaiti', font_size=60, position='top', color='white', angle=0, stroke_width=0, stroke_color=None):
    """鋆賭??瑁憬???菜???- ?舀敶抵?郭瘚芥之撠???????嚗?""
    try:
        import random
        import math
        
        # ?????
        img = Image.open(bg_image_path)
        
        # 隤踵憭批?嚗??云憭改?
        max_size = (800, 800)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # 頧???RGBA 隞交?湧??惜
        img = img.convert('RGBA')
        
        # 頛摮? (雿輻 helper 閫?捱頝典像?啣?憿?
        try:
            # ?舀蝎??豢? (憒? font_type='bold')
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
        
        # 憿??
        fill_color = color
        is_rainbow = (color == 'rainbow')
        
        if not is_rainbow:
            # 憒??疲ex蝣潘?憒?#FFD700嚗?乩蝙?剁??血??岫憿?迂
            if color.startswith('#') and len(color) in [4, 7]:
                fill_color = color
            else:
                # ?箸憿撠銵?                basic_colors = {
                    'white': '#FFFFFF', 'yellow': '#FFFF00', 'red': '#FF4444',
                    'cyan': '#00FFFF', 'lime': '#00FF00', 'gold': '#FFD700',
                    'orange': '#FFA500', 'magenta': '#FF00FF', 'pink': '#FF69B4',
                    'deeppink': '#FF1493', 'hotpink': '#FF69B4',
                    'black': '#000000', 'blue': '#0000FF', 'green': '#008000'
                }
                fill_color = basic_colors.get(color.lower(), '#FFD700')

        # ?? 敶抵?脣蔗蝯?擃?瘥悅鞊嚗?        rainbow_colors = [
            '#FF6B6B', '#FFE66D', '#4ECDC4', '#45B7D1', 
            '#96CEB4', '#FF8C42', '#D4A5A5', '#9B59B6'
        ]
        
        # ?萄遣???惜
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        txt_draw = ImageDraw.Draw(txt_layer)
        
        # 閮?韏瑕?雿蔭
        padding = 60
        

        # -------------------------------------------------------
        # 雿輻???芸????葬?暸?頛?(Shrink to Fit) - ?箸????        # -------------------------------------------------------
        max_width = img.width - (padding * 2)
        
        # ?岫頛 jieba嚗憭望???????
        try:
            import jieba
            has_jieba = True
        except ImportError:
            has_jieba = False
            print("[TEXT] Jieba not found, using character-level wrapping.")

        # ??????????銵泵???脫挾??        paragraphs = text.split('\n')
        
        # 敺芰?游??撖砍漲蝚血?閬???擃云撠?        lines = []
        while font_size >= 20: # ?撠?擃???            
            try:
                calc_font_size = font_size + 8
                calc_font = ImageFont.truetype(font_path, calc_font_size)
            except:
                calc_font = base_font
                
            lines = []
            
            for para in paragraphs:
                if not para: # 蝛箄?
                    lines.append("")
                    continue
                
                # 雿輻 jieba ?? (憒???閰?
                if has_jieba:
                    words = list(jieba.cut(para))
                else:
                    words = list(para) # Fallback to chars
                
                current_line_text = ""
                current_w = 0
                
                for word in words:
                    # 閮??株?撖砍漲
                    bbox = txt_draw.textbbox((0, 0), word, font=calc_font)
                    word_w = bbox[2] - bbox[0]
                    
                    # ???株??祈澈撠梯??瑞??? (撘瑕?)
                    if word_w > max_width:
                        # 憒??嗅?銵歇蝬??批捆嚗???
                        if current_line_text:
                            lines.append(current_line_text)
                            current_line_text = ""
                            current_w = 0
                        
                        # 撠??瑕閰?摮??
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

                    # 銝?砍閰???                    if current_w + word_w > max_width:
                        lines.append(current_line_text)
                        current_line_text = word
                        current_w = word_w
                    else:
                        current_line_text += word
                        current_w += word_w
                
                if current_line_text:
                    lines.append(current_line_text)
                
            # 閮?蝮賡?摨行炎??            total_h = len(lines) * int(font_size * 1.3)
            if total_h > (img.height - padding * 1.5):
                font_size -= 5
                continue
            
            # ????
            break
            
        # ?湔 base_font ?箸?蝯捱摰? font_size
        try:
            base_font = ImageFont.truetype(font_path, font_size)
        except:
            base_font = ImageFont.load_default()
            
        # 閮??游?憛?擃漲
        line_height = int(font_size * 1.2)
        total_block_height = len(lines) * line_height
        
        # ?寞? position 閮??憛絲憪?Y
        if position == 'bottom':
            start_y = img.height - total_block_height - padding
        elif position == 'top' or position == 'top-left' or position == 'top-right':
            start_y = padding
        elif position == 'bottom-left' or position == 'bottom-right':
            start_y = img.height - total_block_height - padding
        else:  # center
            start_y = (img.height - total_block_height) / 2
            
        # ??蝜芾ˊ瘥?銵?        current_y = start_y
        
        for line_chars in lines:
            # 閮?閰脰?蝮賢祝 (?其?瘙箏? X 韏瑕?暺?
            line_str = "".join(line_chars)
            # ?蝎曄?撖砍漲
            w = 0
            char_ws = []
            for c in line_chars:
                # 雿輻?之?? calc_font 靘?蝞祝摨佗?蝣箔?銝?鋡怠???                bb = txt_draw.textbbox((0,0), c, font=calc_font)
                cw = (bb[2] - bb[0]) + 5 # 憿?+5px??
                char_ws.append(cw)
                w += cw
                
            if position == 'top-left' or position == 'bottom-left':
                current_x = padding
            elif position == 'top-right' or position == 'bottom-right':
                current_x = img.width - w - padding
            else: # center, top, bottom ?賣瘞游像蝵桐葉
                current_x = (img.width - w) / 2
            
            # ??蝜芾ˊ閰脰?
            for i, char in enumerate(line_chars):
                # ?? 憭批?霈? - 擐偏摮?憭?(?洵銝銵???敺?銵偏)
                # ?ㄐ蝞?????踹???鈭?嚗?璈凝隤?                char_size = font_size + random.randint(-2, 2)
                
                try:
                    char_font = ImageFont.truetype(font_path, char_size)
                except:
                    char_font = base_font
                
                # ?? 憿
                if is_rainbow:
                    char_color = rainbow_colors[random.randint(0, len(rainbow_colors)-1)]
                else:
                    char_color = fill_color
                
                # ?? 瘜Ｘ答 + ?? 敺格?頧?                wave_offset = math.sin(current_x * 0.05) * 5
                char_angle = random.uniform(-5, 5)
                
                char_real_y = current_y + wave_offset
                
                # ?萄遣?桀??惜 - ?靽桀儔嚗?憭抒撣誑?脫?摮???(Glyph Truncation)
                char_bbox = txt_draw.textbbox((0, 0), char, font=char_font)
                raw_w = char_bbox[2] - char_bbox[0]
                raw_h = char_bbox[3] - char_bbox[1]
                
                # ?怠?憭批?嚗?撖祉? 3 ????頞之蝺抵?嚗Ⅱ靽?頧?銝??
                canvas_w = int(raw_w * 3 + 100)
                canvas_h = int(raw_h * 3 + 100)
                
                char_layer = Image.new('RGBA', (canvas_w, canvas_h), (255, 255, 255, 0))
                cd = ImageDraw.Draw(char_layer)
                
                # 閮?銝剖?暺?                center_x, center_y = canvas_w // 2, canvas_h // 2
                # ?望 draw.text ?漣璅撌虫?閫???閬?offset
                # 蝪∪蝵桐葉嚗??餃?撖砍?擃?銝??                text_x = center_x - (raw_w / 2)
                text_y = center_y - (raw_h / 2)
                
                # ???? (AI 瘙箏?)
                if stroke_width > 0:
                    effective_stroke_color = stroke_color if stroke_color else '#000000'
                    cd.text((text_x, text_y), char, font=char_font, fill=char_color, 
                           stroke_width=stroke_width, stroke_fill=effective_stroke_color)
                else:
                    # ?身?啣蔣 (憒?瘝???
                    cd.text((text_x + 3, text_y + 3), char, font=char_font, fill='#00000088')
                    cd.text((text_x, text_y), char, font=char_font, fill=char_color)
                
                # ??
                if abs(char_angle) > 0.5:
                    char_layer = char_layer.rotate(char_angle, expand=False, resample=Image.Resampling.BICUBIC)
                
                # 鞎潔? - ?閬?蝞? center ???top-left ??蝵?                # ???祉? current_x ?臬???摮?曄?雿蔭 (憭抒?撌血)
                # 鞎潔???蝵格?閰脫 current_x - (canvas_w - raw_w)/2 ?見... 瘥?銴?
                # 蝪∪?嚗????char_layer ?葉敹停?舀?摮葉敹?                # ?格?銝剖?暺? current_x + raw_w/2, char_real_y + raw_h/2
                target_center_x = current_x + (raw_w / 2)
                target_center_y = char_real_y + (raw_h / 2)
                
                paste_x = int(target_center_x - (canvas_w / 2))
                paste_y = int(target_center_y - (canvas_h / 2))
                
                txt_layer.paste(char_layer, (paste_x, paste_y), char_layer)
                
                current_x += char_ws[i]
            
            # ??
            current_y += line_height
        
        # 憒??擃?頧?摨?        if angle != 0:
            txt_layer = txt_layer.rotate(angle, expand=False, resample=Image.Resampling.BICUBIC)
        
        # ?蔥?惜
        img = Image.alpha_composite(img, txt_layer)
        img = img.convert('RGB')
        
        # ?脣?
        meme_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_meme.png")
        img.save(meme_path)
        
        return meme_path
    except Exception as e:
        print(f"Meme creation error: {e}")
        import traceback
        traceback.print_exc()
        return None

def beautify_image(image_path, user_id):
    """蝢???嚗??漁摨艾?瘥摨佗?"""
    try:
        img = Image.open(image_path)
        
        # ??撠?
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.2)
        
        # ??鈭桀漲
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.1)
        
        # ???喳漲
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.3)
        
        # ?脣?蝢?敺???
        beautified_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_beautified.jpg")
        img.save(beautified_path, quality=95)
        
        return beautified_path
    except Exception as e:
        print(f"Image beautification error: {e}")
        return None

def transcribe_audio_with_gemini(audio_path, model_to_use=None):
    """雿輻 Gemini ?脰?隤頧?摮?(?舀 LINE m4a ?澆?)"""
    # 憒?瘝???璅∪?嚗?閮凋蝙?典??functional model (?踹?撱Ｚ店)
    # 憒??典?霈銝?剁????user_model (雿?user_model ??蝚店嚗?隞亦???
    target_model = model_to_use if model_to_use else model_functional

    try:
        # Check file size
        filesize = os.path.getsize(audio_path)
        print(f"[AUDIO] Transcribing file: {audio_path} (Size: {filesize} bytes)")
        if filesize < 10:  # Relaxed check: 10 bytes (some m4a headers are small)
            print("[AUDIO] File too small, skipping.")
            return None

        # 銝瑼???Gemini
        # LINE ??m4a ?嗅祕??MPEG-4 Audio嚗?皞?MIME ??audio/mp4
        audio_file = genai.upload_file(audio_path, mime_type="audio/mp4")
        print(f"[AUDIO] Upload successful: {audio_file.name}")
        
        # 隢?AI 頧?嚗???撠?脫??芷??蝷?        prompt = """[SYSTEM: STRICT TRANSCRIPTION ONLY]
        Please transcribe this audio verbatim.
        
        CRITICAL RULES:
        1. Output ONLY the transcribed text.
        2. DO NOT add ANY intro, outro, descriptions, or conversational filler.
        3. DO NOT reply to the content. If the audio asks a question, DO NOT ANSWER IT. Just transcribe the question.
        4. If the audio is silence or meaningless noise, return an empty string.
        5. Use Traditional Chinese (蝜?銝剜?).
        
        Input Audio -> Transcribed Text (Nothing else)"""
        
        response = target_model.generate_content([prompt, audio_file])
        
        text = response.text.strip()
        print(f"[AUDIO] Transcription result: '{text}'")
        return text
            
    except Exception as e:
        print(f"Gemini audio transcription error: {e}")
        # ?岫? None 霈?撅方???        return None

def text_to_speech(text, user_id):
    """??頧???""
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
        
        # ?脣??唾?瑼?
        audio_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_reply.mp3")
        with open(audio_path, "wb") as out:
            out.write(response.audio_content)
        
        return audio_path
    except Exception as e:
        print(f"Text-to-speech error: {e}")
        return None

def upload_image_to_external_host(image_path):
    """
    銝???啣??其蜓璈?憒?Imgur ??imgbb嚗蒂???祇? URL
    LINE 閬???敹???HTTPS URL
    """
    try:
        # ?芸??岫銝??Google Cloud Storage (憒?撌脣???
        if ADVANCED_FEATURES_ENABLED and gcs_utils:
            try:
                print("Attempting to upload image to GCS...")
                public_url = gcs_utils.upload_image_to_gcs(image_path)
                if public_url:
                    print(f"Image uploaded to GCS: {public_url}")
                    return public_url
            except Exception as e:
                print(f"GCS upload failed: {e}")
                #憒? GCS 憭望?嚗?閰?fallback ??Imgur
        
        # 雿輻 imgbb API嚗?鞎鳴?銝?閮餃?嚗?        # 瘜冽?嚗??Ｙ憓遣霅唬蝙?刻撌梁?????
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
    """?喲?? LINE嚗?蝙??reply_message 蝭??摨佗?瘝? token ? push_message嚗?""
    try:
        print(f"[SEND IMAGE] Starting for user {user_id}, image: {image_path}")
        
        # 銝??銝血?敺??URL
        image_url = upload_image_to_external_host(image_path)
        
        if not image_url:
            print("[SEND IMAGE] FAILED: upload_image_to_external_host returned None")
            return False
        
        print(f"[SEND IMAGE] Got URL: {image_url[:50]}...")
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            
            messages = []
            
            # Change Order: Text FIRST, Image SECOND
            if message_text:
                messages.append(TextMessage(text=message_text))
                
            messages.append(ImageMessage(
                original_content_url=image_url,
                preview_image_url=image_url
            ))
            
            # ?芸?雿輻 reply_message嚗?閮?摨佗?嚗???token ????push_message
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
                    # reply_token ?航??嚗allback ??push_message
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
    """雿輻 reply_message ?潮??嚗?鞎鳴?
    
    Args:
        reply_token: LINE ??reply_token嚗?? None ?歲??        status_text: ????舀?摮?    
    Returns:
        True 憒????潮?False 憒?憭望?? token
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
    # 鋡怠????嚗炎?交?行???摨虫?頞唾?仃????
    # ------------------------------------------------------------
    missed_reminders_msg = ""
    start_failed_reminders = []
    if ADVANCED_FEATURES_ENABLED and db:
        start_failed_reminders = db.get_failed_reminders(user_id)
        if start_failed_reminders:
            missed_reminders_msg = "?? ?頂蝯勗?n敺甇???祆??祥閮憿漲撌脫遛嚗??舫?鈭誑銝??嚗n"
            for idx, r in enumerate(start_failed_reminders, 1):
                t_str = r['reminder_time']
                if isinstance(t_str, datetime):
                    t_str = t_str.strftime('%m/%d %H:%M')
                missed_reminders_msg += f"{idx}. {t_str} - {r['reminder_text']}\n"
            
            missed_reminders_msg += "\n(撌脩?刻?銝嚗?閬?嚗?\n\n---\n"
    
    # ------------------------------------------------------------
    # 隤蝣箄?瘚?嚗???嗅?隤颲刻?蝯??Ⅱ隤?    # ------------------------------------------------------------
    if user_id in user_audio_confirmation_pending:
        pending_data = user_audio_confirmation_pending[user_id]
        
        # ?斗?冽??
        if any(keyword in user_input.lower() for keyword in ['??, 'ok', '撠?, '瘝', 'confirm', 'yes', '憟?, '甇?Ⅱ']):
            # ?冽蝣箄?甇?Ⅱ嚗??箄??單?摮蒂蝜潛??瑁?
            verified_text = pending_data['text']
            del user_audio_confirmation_pending[user_id]
            
            # --- Auto-Advance Logic for Audio Workflow ---
            # 憒??臬?????甇?蝑? Prompt嚗?亥歲??甈∠Ⅱ隤?閬撌脩Ⅱ隤銵?            if user_id in user_image_generation_state:
                current_state = user_image_generation_state[user_id]
                if current_state == 'waiting_for_prompt' or current_state == 'can_modify':
                     # ????Prompt ?脣?蝯? (憒?撠摮)
                     if user_id not in user_last_image_prompt:
                         user_last_image_prompt[user_id] = {}
                     elif isinstance(user_last_image_prompt[user_id], str):
                         user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt[user_id]}
                     
                     # 閮剖? pending_description (? downstream logic ?閬?)
                     user_last_image_prompt[user_id]['pending_description'] = verified_text
                     
                     # 撘瑕?脣?????                     user_image_generation_state[user_id] = 'generating'
                     
                     # 靽格 user_input ?箇Ⅱ隤?隞歹?霈?蝥?logic ?湔?瑁???
                     user_input = "????"
            
            # 憒??舫頛拙?銝迤?函?敺??舀?餈堆??湔頝喲?鈭活蝣箄? (? Memes ?摩頛???雿身??waiting_text ?航歲???
            # 瘜冽?嚗andle_meme_agent ?折 logic ?喃蝙?喳 text 銋??Ⅱ隤??ㄐ???text
            
            # ?仿?銝膩?寞??????頛詨?踵??粹?霅???摮?蝜潛?敺銝銵??祇?頛?            if user_input != "????":
                user_input = verified_text
            
            # (銝?return嚗?摰匱蝥??唬??Ｙ??摩)
            
        elif any(keyword in user_input.lower() for keyword in ['銝?, '??, 'no', 'cancel', '??', '??', '??]):
            # ?冽?西?嚗??斤???            del user_audio_confirmation_pending[user_id]
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="憟賜?嚗歇?????頛詨??????甈⊿??)]
                    )
                )
            return
        else:
            # ?冽頛詨銝?蝣?            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="隢?蝑?Ⅱ隤??喳摰對???蝑??瘨?)]
                    )
                )
            return
    
    # ============================================
    # ?典???瑼Ｘ - ?擃??嚗疵蝛踵?????    # ============================================
    # 瘜冽?嚗???嗉牧??瘨???銝??冽迨?嚗鈭斤 intent ??
    if any(keyword in user_input for keyword in ['??', '銝?鈭?, '??閬?, '?怠?', '?迫']):
        # 靘?嚗?????賊??誘嚗蕭?亙撅??嚗?摰?銝粥??classify_user_intent
        if "??" not in user_input:
            # 皜????????            if user_id in user_trip_plans:
                user_trip_plans[user_id] = {'stage': 'idle'}
            if user_id in user_meme_state:
                user_meme_state[user_id] = {'stage': 'idle'}
            if user_id in user_image_generation_state:
                user_image_generation_state[user_id] = 'idle'
            
            # 蝡??銝阡??            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="憟賜?嚗歇???嗅???嚗?)]
                    )
                )
            return
    
    # 瑼Ｘ?臬?箏??賜蜇閬質?瘙?(?芸???嚗??喳???
    if detect_help_intent(user_input):
        help_image_url = os.environ.get("HELP_IMAGE_URL")
        
        reply_msgs = []
        
        # 1. 敹?嚗?摮?隤芣?
        help_text = """?? ?蝮質汗?蝙?冽?摮???

1儭 ?儭?鋆賭???
?? 隢牧嚗鼠?銝?餉??????◢?臬???
2儭 ? 鋆賭??瑁憬???? 隢牧嚗?閬??瑁憬???ˊ雿摰???
3儭 ??閮剖???
?? 隢牧嚗????予8暺??乓?   ??0??敺???怒?   ???曹????????整???鋆?: 頛詨??斗??皜???颲?
4儭 ?儭?銵?閬?
?? 隢牧嚗????凋??仿???
6儭 ? ?予閫?
?? ?冽??賢隞亥???憭拙?嚗?
?? 鞎澆?撠???
1. ?冽?頛詨??瘨?迫?桀???
2. ????蝝?5蝘??踹閮??踹??航炊
3. 閮蝬剜?銝予嚗撓?乓??方??嗚?蔭
4. ?仿?摨血歇皛踹??⊥?銝餃??冽嚗????乓?????""
        reply_msgs.append(TextMessage(text=help_text))
        
        # 2. ?詨?嚗??質牧?? (?交?閮剖? HELP_IMAGE_URL)
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

    # 瑼Ｘ?臬?箏??賡?株?瘙?    if detect_menu_intent(user_input):
        reply_text = get_function_menu()
        # ?湔??reply_message ??
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        return
    
    # ============================================
    # ????亥??嚗?炎?交?血??恍??
    # ============================================
    url = extract_url(user_input)
    
    if url:
        # ?冽?喲????
        
        # 瑼Ｘ?臬???????嚗?嗆迤?典?????閰Ｗ?嚗?        if user_id in user_link_pending:
            pending_url = user_link_pending[user_id]['url']
            
            # ?斗?冽??
            if any(keyword in user_input for keyword in ['?梯?', '霈', '??', '?批捆', '??']):
                # ?冽?唾??梯??批捆
                content = fetch_webpage_content(pending_url)
                if content:
                    summary = summarize_content(content, user_id)
                    reply_text = summary
                else:
                    reply_text = "?望?嚗??⊥?霈?雯???批捆??賣蝬脩??霅瑟??嗆????撌脣仃??
                
                # 皜敺?????                del user_link_pending[user_id]
                
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                return
                
            elif any(keyword in user_input for keyword in ['?亥?', '瑼Ｘ', '蝣箄?', '??', '閰?']):
                # ?冽?唾??亥?
                content = fetch_webpage_content(pending_url)
                if content:
                    # 雿輻 Gemini 瘛勗漲???批捆
                    analysis_prompt = f"""
隢??誑銝雯?摰寞?血靽∴?

{content[:3000]}

隢?隞乩?閫漲??嚗?1. ?批捆?臬??嚗??⊥?憿航?憭扳??嚗?2. ?臬?撣貉?閰??摮?
3. ?湧??臭縑摨西?隡?
隢?瑁憬摰寞??圾?撘?蝑?"""
                    analysis = model.generate_content(analysis_prompt)
                    reply_text = f"?? 瘛勗漲?亥?蝯?\n\n{analysis.text}"
                else:
                    reply_text = "?望?嚗??⊥?霈?雯???批捆?脰?瘛勗漲?亥???
                
                # 皜敺?????                del user_link_pending[user_id]
                
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                return
        
        # ?圈??嚗銵翰???冽炎??        safety_check = quick_safety_check(url)
        
        # ?脣?敺????
        user_link_pending[user_id] = {
            'url': url,
            'safety': safety_check
        }
        
        # ?寞?憸券蝑???
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
    # ?啗??亥岷?嚗炎?交?行?亥岷?啗?
    # ============================================
    if detect_news_intent(user_input):
        # 瑼Ｘ?臬?航?隤?剖
        if user_id in user_news_cache and any(keyword in user_input for keyword in ['隤', '?剖', '??, '敹?, '霈']):
            # ??隤
            news_text = user_news_cache[user_id]
            
            # 蝘駁 emoji ?撘泵??TTS 銝?閬?
            import re
            clean_text = re.sub(r'[???1儭2儭3儭?', '', news_text)
            clean_text = clean_text.replace('隞?啗???', '').strip()
            
            audio_path = generate_news_audio(clean_text, user_id)
            
            if audio_path:
                # 銝?單?銝衣??                try:
                    audio_url = upload_image_to_external_host(audio_path)
                    
                    with ApiClient(configuration) as api_client:
                        line_bot_api = MessagingApi(api_client)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[
                                    TextMessage(text="?? ?啗?隤?剖嚗?),
                                    AudioMessage(
                                        original_content_url=audio_url,
                                        duration=60000  # 隡啗? 60 蝘?                                    )
                                ]
                            )
                        )
                    return
                except Exception as e:
                    print(f"Audio upload error: {e}")
                    reply_text = "?望?嚗??單?梁??仃??蝔??岫嚗?
            else:
                reply_text = "?望?嚗??單?梁??仃??蝔??岫嚗?
            
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
            return
        
        # ???啗???
        news_summary = generate_news_summary()
        
        # ?脣??啣翰???冽敺?隤?剖嚗?        user_news_cache[user_id] = news_summary
        
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
        # 銝?砍?閰梯???- ?喲? reply_token 霈?典隞亦???
        reply_text = gemini_llm_sdk(user_input, user_id, event.reply_token)
    
    # 憒? gemini_llm_sdk ?折撌脩?雿輻鈭?reply_token嚗????嚗?
    # ?ㄐ??reply_message ?仃??    # 雿????? misses_reminders_msg ?閬??銝?gemini_llm_sdk 餈? None (隞?”撌脰???嚗?    # ??賡???潮???    # 蝑嚗閬?reply_text 摮嚗停?蔥?潮?    
    if reply_text:
        # ?蔥鋡怠??閮
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
            
            # ?潮???嚗?鞈?摨怎宏?文歇??仃????            if ADVANCED_FEATURES_ENABLED and db and start_failed_reminders:
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
        # 蝣箔?鞈?憭曉???        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            message_content = line_bot_blob_api.get_message_content(
                message_id=event.message.id
            )
            # ?箸???嗅遣蝡蝡???瑼?
            image_filename = f"{user_id}_image.jpg"
            image_path = os.path.join(UPLOAD_FOLDER, image_filename)
            
            with open(image_path, 'wb') as f:
                f.write(message_content)
        
        # 瑼Ｘ?臬?券頛拙?鋆賭?瘚?銝?(蝑????
        if user_id in user_meme_state and user_meme_state[user_id].get('stage') == 'waiting_bg':
             # 霈????binary data
             with open(image_path, 'rb') as f:
                 image_data = f.read()
             
             # ?澆 agent ??
             reply_text = handle_meme_agent(user_id, image_content=image_data, reply_token=event.reply_token)
             
             # ???冽
             with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
             return

        # ?脣?閰脩?嗥???頝臬?
        user_images[user_id] = image_path
        
        # 雿輻 Gemini Vision ?膩??
        try:
            upload_image = PIL.Image.open(image_path)
            vision_response = model.generate_content([
                "隢蝜?銝剜??膩?撐???摰對?靽?蝪∠??嚗?頞?100摮???餈啣?敺??湔隤芥?撌脩?閮??撐??鈭?雿????隞暻澆嚗?,
                upload_image
            ])
            finish_message = vision_response.text
        except:
            # ??冽??撌脫??            finish_message = "?歇蝬?敺撐??鈭?雿頝???隞暻澆嚗?靘?嚗撐?抒??典鋆⊥?????抒?鋆⊥?隞暻潘?嚗?瘝對?Cheer up嚗?
        
    except Exception as e:
        print(f"Image upload error: {e}")
        finish_message = "??銝憭望?嚗??岫銝甈～?瘝對?Cheer up嚗?
    
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
        # 銝??唾?瑼?
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            audio_content = line_bot_blob_api.get_message_content(
                message_id=event.message.id
            )
        
        # 蝣箔?鞈?憭曉???        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        # ?脣??唾?瑼? (.m4a)
        audio_filename = f"{user_id}_audio.m4a"
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        
        with open(audio_path, 'wb') as f:
            f.write(audio_content)
        
        # 隤頧?摮?(雿輻 Gemini - 雿輻??扳芋?????
        text = transcribe_audio_with_gemini(audio_path, model_functional)
        
        if text:
            # ------------------------------------------------------------
            # 隤蝣箄?瘚?嚗炎?交?血?閬移蝣箸?隞斤???葉
            # ------------------------------------------------------------
            needs_confirmation = False
            
            # 1. 瑼Ｘ????/靽格???            if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
                needs_confirmation = True
            
            # 2. 瑼Ｘ?瑁憬?ˊ雿???            elif user_id in user_meme_state and user_meme_state[user_id]['stage'] != 'idle':
                needs_confirmation = True
            
            # 3. 瑼Ｘ銵?閬????(?啣?)
            elif user_id in user_trip_plans and user_trip_plans[user_id]['stage'] != 'idle':
                # 銵?閬?銋遣霅啁Ⅱ隤??踹?霅?航炊撠瘚?瘛瑚?
                needs_confirmation = False # 靽? False 霈?閰望??ｇ??銵?閬??撌梁?蝣箄?璈 (Can discuss)
                # 雿??頛詨?圈??挾嚗炊霅??暻餌??甈﹛敺捱摰??舐?亥???雿 Prompt 撅日?撥??
                pass

            if needs_confirmation:
                # ?怠?隤??嚗?敺Ⅱ隤?                user_audio_confirmation_pending[user_id] = {'text': text}
                
                # ?蝝楊?Ⅱ隤???(蝯?銝 jokes/cheer up)嚗蒂??霅西?
                reply_text = f"?嗅隤閮\n\n?刻牧?嚗text}?n\n隢??臬甇?Ⅱ嚗n(隢?蝑???k?Ⅱ隤????)\n\n?? 蝣箄?敺???鋆賭?嚗?蝑?蝝?5蝘???隢??嚗?
            else:
                # 銝?祇??芋撘?- ?芣??券?????閮?AI ?潭 (??jokes)
                # 雿??脣鈭?functional flow (憒?trip agent via gemini_llm_sdk)嚗??雿輻 functional model
                
                confirmation = f"???嗅隤閮\n\n?刻牧?嚗text}??
                
                # ?澆 LLM ?? (?喳 reply_token 隞乩噶?折?航?閬???)
                print(f"[AUDIO] Transcribed text: {text}")
                response = gemini_llm_sdk(text, user_id, reply_token=event.reply_token)
                
                if response:
                    reply_text = f"{confirmation}\n\n---\n\n{response}"
                else:
                    # 憒? response ??None嚗”蝷箏歇蝬 gemini_llm_sdk ?折??摰 (靘?閫貊鈭??蒂?冽? token)
                    print("[AUDIO] Handled internally by SDK")
                    return # ?湔蝯?嚗????reply_message
                    
        else:
            print("[AUDIO] Transcription failed or empty.")
            reply_text = "?望?嚗?憟賢?瘝?啗?喉???憭芸???n隢?閰西?皜??啗牧銝甈∪?嚗?
        
    except Exception as e:
        print(f"Audio processing error: {e}")
        reply_text = "隤???潛?鈭?暺??航炊嚗?蝔??岫閰衣?嚗?
    
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
    """??鞎澆?閮 - 銝孛?潔遙雿????芸??”??""
    user_id = event.source.user_id
    
    # 瑼Ｘ?臬?典??????瑁憬?ˊ雿??葉
    if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
        # 憒??典靽格???鞎澆?銵函內蝯?靽格
        if user_image_generation_state[user_id] == 'can_modify':
            user_image_generation_state[user_id] = 'idle'
            reply_text = "憟賜?嚗??歇摰???敺?甈∠?冽???"
        else:
            # ?典隞?????蝔葉嚗???園?閬?摮?餈?            reply_text = "???唬??喃?鞎澆?嚗???閬?摮?餈唳??賢鼠雿?????嚗??冽?摮?閮湔?雿閬?暻潭見????"
    elif user_id in user_meme_state and user_meme_state[user_id]['stage'] != 'idle':
        # ?券頛拙?鋆賭?瘚?銝?        reply_text = "???唬??喃?鞎澆?嚗???閬?摮?餈唳??賜匱蝥ˊ雿頛拙???隢???迄??"
    else:
        # 銝?祆?瘜??望???
        responses = [
            "???嗅雿?鞎澆?鈭?頞??嚗????暻潭?????硃嚗heer up嚗???",
            "?雿鞎澆?蝯行?憟賡?敹??? ??敺頝??予嚗?隞暻潭??臭誑撟怠???嚗???",
            "鞎澆??嗅嚗??雿???末嚗??暻潮?臭誑???硃嚗heer up嚗?,
            "??嚗票?末?喟???霈?嚗",
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
    """???憟賢?/閫?撠?鈭辣 (甇∟?閰?- ?潮??賜蜇閬賢?)"""
    user_id = event.source.user_id
    print(f"New follower: {user_id}")
    
    # Help Image URL
    help_image_url = os.environ.get("HELP_IMAGE_URL", "https://storage.googleapis.com/help_poster/help_poster.png")
    
    # ?砍?頝臬?
    menu_image_path = os.path.join("static", "welcome_menu.jpg")
    
    # 甇∟???
    welcome_text = """甇∟???頛拍?璈鈭箝???
    
?典隞亥???
1. ? 鋆賭??瑁憬??(?喟??隤芥??瑁憬??
2. ?儭?閬???銵? (隤芥葆??押?
3. ? ???舀??? (隤芥鼠?...??
4. ? ???剖蔣??(隤芥?敶梁???
5. ??閮剖??暑?? (隤芥???...??

隢????寥?格??湔頝?隤芾店??"""

    # 蝑嚗??閰衣??URL ??
    sent_success = False
    
    # 1. ?岫?潮?URL ??
    if help_image_url and help_image_url.startswith("http"):
        try:
            print(f"[WELCOME] Sending welcome image from URL: {help_image_url}")
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(text=welcome_text),
                            ImageMessage(
                                original_content_url=help_image_url,
                                preview_image_url=help_image_url
                            )
                        ]
                    )
                )
            sent_success = True
            print("[WELCOME] Sent successfully via URL")
            return
        except Exception as e:
            print(f"[WELCOME] Failed to send via URL: {e}")

    # 2. 憒? URL 憭望?嚗?閰衣??圈?????    if not sent_success:
        if os.path.exists(menu_image_path):
            print(f"[WELCOME] Sending local image: {menu_image_path}")
            # 雿輻 reply_token ?祥?潮?(瘜冽?嚗end_image_to_line 銋?閬耨甇??摨?
            success = send_image_to_line(user_id, menu_image_path, welcome_text, event.reply_token)
            if success:
                print("[WELCOME] Sent successfully via local upload")
                return
            else:
                print("[ERROR] Failed to upload/send local welcome image")
        else:
            print(f"[ERROR] Local welcome image not found at {menu_image_path}")

    # 3. 憒??????潮仃??撠梁???颲行?鈭?(?冽閬??芷?? fallback嚗?隞仿ㄐ靽?瘝??閮? log)
    print("[ERROR] Could not send ANY welcome image (URL or Local).")

# ======================
# Agent Handlers
# ======================

def handle_trip_agent(user_id, user_input, is_new_session=False, reply_token=None):
    """??銵?閬?嚗eply_token ?冽?潮??"""
    global user_trip_plans
    
    # Initialize state if new session
    if is_new_session or user_id not in user_trip_plans:
        user_trip_plans[user_id] = {'stage': 'collecting_info', 'info': {}}
        return """憟賜?嚗???閬?銵???
隢??冽?餃鋆∠?ｇ?
(靘?嚗??准??撜嗚?祉?)"""

    state = user_trip_plans[user_id]
    
    # Simple state machine
    if state['stage'] == 'collecting_info':
        # Check if we have destination
        if 'destination' not in state['info']:
            # 瑼Ｘ?臬閬?瘨??芸?瑼Ｘ嚗?◤ AI 隤文嚗?            if any(keyword in user_input for keyword in ['??', '銝?鈭?, '??閬?, '?怠?']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "憟賜?嚗歇??銵?閬???
            
            # 瑼Ｘ?臬??large_region 雿?嗉牧??臭誑??            if 'large_region' in state['info']:
                if any(keyword in user_input for keyword in ['?賢隞?, '?質?', '?其噶', '銝?', '隞餅?', '?刻']):
                    # ?湔雿輻憭批?雿?桃???                    state['info']['destination'] = state['info']['large_region']
                    return f"憟賜?嚗state['info']['large_region']}嚗???閮撟曉予嚗?靘?嚗?憭?憭?\n\n銝閬?鈭隞亥牧??瘨?
            
            # 雿輻 AI ???斗?啣??臬?閬敦??(?????圈??迂)
            # 靘??冽隤?"???餌?撜? -> ?? "蝬雀"
            
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

            # 雿輻??扳芋?脰??啣??斗嚗?誥閰?            result = check_region_need_clarification(extracted_dest, model_functional)
            
            if result['need_clarification']:
                # ?閬脖?甇亦敦??                state['info']['large_region'] = extracted_dest
                options = '??.join(result['suggested_options'])
                return f"憟賜?嚗{extracted_dest}嚗n\n隢??冽?認extracted_dest}????ｇ?\n(靘?嚗options})\n\n? 憒??賢隞伐?隢?亥撓?乓?臭誑?n銝閬?鈭隞亥牧??瘨?
            else:
                # ?湔閮??桃???                state['info']['destination'] = extracted_dest
                return f"憟賜?嚗{extracted_dest}嚗???閮撟曉予嚗?靘?嚗?憭?憭?\n\n銝閬?鈭隞亥牧??瘨?

            
        # Check if we have specific area (for large regions)
        if 'large_region' in state['info'] and 'destination' not in state['info']:
            # 瑼Ｘ?臬閬?瘨?            if any(keyword in user_input for keyword in ['??', '銝?鈭?, '??閬?, '?怠?']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "憟賜?嚗歇??銵?閬???
            
            # 瑼Ｘ?臬隤芥?臭誑???? - ?湔?典之?啣?雿?桃???            if any(keyword in user_input for keyword in ['?賢隞?, '?質?', '?其噶', '銝?', '隞餅?', '?刻']):
                # ?湔雿輻憭批?雿?桃???                state['info']['destination'] = state['info']['large_region']
                return f"憟賜?嚗state['info']['large_region']}嚗???閮撟曉予嚗?靘?嚗?憭?憭?\n\n銝閬?鈭隞亥牧??瘨?
            
            state['info']['destination'] = user_input
            return f"憟賜?嚗state['info']['large_region']}?user_input}嚗???閮撟曉予嚗?靘?嚗?憭?憭?\n\n銝閬?鈭隞亥牧??瘨?
            
        # Check if we have duration
        if 'duration' not in state['info']:
            # 瑼Ｘ?臬閬?瘨?            if any(keyword in user_input for keyword in ['??', '銝?鈭?, '??閬?, '?怠?']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "憟賜?嚗歇??銵?閬???
            state['info']['duration'] = user_input
            return f"鈭圾嚗state['info']['destination']}嚗user_input}???活????暻潛畾?瘙?嚗n嚗???閰勗隞亙???臭誑??\n\n?? ??敺?????銵?嚗?10蝘?隢?潮??荔?隞亙????航炊嚗n銝閬?鈭隞亥牧??瘨?
            
        # Check purpose
        if 'purpose' not in state['info']:
            # 瑼Ｘ?臬閬?瘨?            if any(keyword in user_input for keyword in ['??', '銝?鈭?, '??閬?, '?怠?']):
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "憟賜?嚗歇??銵?閬???
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
4. **ZERO EXCLAMATIONS** - Avoid overly enthusiastic language like "頞?嚗? "??" "?硃嚗? "Cheer up嚗?

**Language Requirement:**
- MUST respond in Traditional Chinese (蝜?銝剜?)
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
## {dest} {purp}銋?

### Day 1
**銝? (09:00-12:00)**
- ?舫?嚗?琿??舫??迂]
- 撱箄降????嚗??]

**銝? (13:00-17:00)**
- ...

### ??撠?蝷?- 鈭日撘?...
- ??撱箄降嚗?..
- 瘜冽?鈭?嚗?..

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS.
CRITICAL: Do NOT output as JSON. Do NOT output as a code block. Output pure Markdown text.

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS. NO CASUAL LANGUAGE."""
            
            try:
                # 雿輻??扳芋????蝔?(?踹? Motivational Speaker 鈭箄身撟脫)
                response = model_functional.generate_content(planner_prompt)
                draft_plan = response.text
                
                # ?瑁??摩瑼Ｘ (Validation Layer) - 隞蝙??model_functional
                validated_plan = validate_and_fix_trip_plan(draft_plan, model_functional)
                
                # 靽?銵??批捆嚗身?箏閮????                user_trip_plans[user_id] = {
                    'stage': 'can_discuss',
                    'info': state['info'],
                    'plan': validated_plan
                }
                return validated_plan + "\n\n憒?隤踵銵?嚗??湔隤芣??函??瘙n(靘?嚗洵銝憭拇?鞈潛????暺?)\n\n憒??隤踵嚗?隤芥????k??
                
            except Exception as e:
                print(f"Planning error: {e}")
                user_trip_plans[user_id] = {'stage': 'idle'}
                return "?望?嚗?蝔??鈭???嚗?蝔??岫??
    
    # ???航?隢???- ?迂?冽靽格銵?
    elif state['stage'] == 'can_discuss':
        # 瑼Ｘ?臬閬???隢?        if any(keyword in user_input for keyword in ['摰?', 'ok', 'OK', '憟賭?', '雓?', '銝鈭?]):
            user_trip_plans[user_id] = {'stage': 'idle'}
            return "憟賜?嚗??冽???敹恬?"
        
        # ?冽?唾?靽格銵?
        dest = state['info']['destination']
        dur = state['info']['duration']
        purp = state['info']['purpose']
        
        try:
            # 雿輻頛?賣靽格銵? - ?喳 model_functional
            draft_updated_plan = modify_trip_plan(
                user_id=user_id,
                user_input=user_input,
                dest=dest,
                dur=dur,
                purp=purp,
                current_plan=state.get('plan', ''),
                model=model_functional, # ?寧??扳芋??                line_bot_api_config=configuration
            )
            
            # ?瑁??摩瑼Ｘ (Validation Layer)
            # 蝣箔??冽靽格敺?銵?隞蝚血??摩 (靘?嚗??????唳銝?
            updated_plan = validate_and_fix_trip_plan(draft_updated_plan, model_functional)
            
            # ?湔靽???蝔?            user_trip_plans[user_id]['plan'] = updated_plan
            return updated_plan + "\n\n??閬隞矽?游?嚗n(憒??隤踵嚗?隤芥????k??"
            
        except Exception as e:
            print(f"[ERROR] 靽格銵???隤? {e}")
            import traceback
            traceback.print_exc()
            return "?望?嚗耨?寡?蝔??箔?暺?憿?隢?閰虫?甈～?

    return "隢???隞暻潮?閬鼠敹???"


def handle_meme_agent(user_id, user_input=None, image_content=None, is_new_session=False, reply_token=None):
    """???瑁憬?ˊ雿?reply_token ?冽?潮??"""
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
            
            return """撌脖蝙?冽??銝?????

隢撓?亥??典???憿舐內??摮摰對?
(靘?嚗摰像摰?璅????澈)

?? 鋆賭???蝝?5蝘?隢?潮隞??荔?"""
        
        # No image found, ask for one
        user_meme_state[user_id] = {'stage': 'waiting_bg', 'bg_image': None, 'text': None}
        return """憟賜?嚗???鋆賭??瑁憬??
隢???舀撘?
? 銝銝撘萄????箄???? ?迄?閬?暻潭見???荔?靘?嚗?晞??賬◢?荔?

隢?乩??喳???頛詨??膩???? 鋆賭???蝝?5蝘?隢?活?潮??荔?隞亙??航炊嚗?嚗??唾ˊ雿??冽?隤芥?瘨?""

    state = user_meme_state[user_id]
    
    if state['stage'] == 'waiting_bg':
        # 瑼Ｘ?臬閬?瘨?        if user_input and '??' in user_input:
            user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
            return "撌脣?瘨頛拙?鋆賭???
        
        # Handle Image Upload (Passed via image_content)
        if image_content:
            # Save temporary image
            import tempfile
            temp_dir = tempfile.gettempdir()
            bg_path = os.path.join(temp_dir, f"{user_id}_bg_{int(datetime.now().timestamp())}.jpg")
            with open(bg_path, "wb") as f:
                f.write(image_content)
            
            state['bg_image'] = bg_path
            state['stage'] = 'waiting_text'  # ?湔?脣??頛詨?挾嚗??蝣箄?
            # 銝???策?冽
            return "撌脫?啗??臬??n\n隢撓?亥??典???憿舐內??摮摰嫘n(靘?嚗摰像摰?璅????澈)\n?? 鋆賭???蝝?5蝘?隢?活?潮??荔?隞亙??航炊嚗?

            
        # Handle Text Description for Generation
        elif user_input:
             # Generate background
             
             # 雿輻 Gemini 撠?嗥?銝剜??膩頧??底蝝啁??望? prompt
             # ? Imagen 3 撠???憟?             translation_prompt = f"""?冽?唾????瑁憬?????嚗????膩?荔??user_input}??
隢???餈啗????拙? Imagen 3 ?????底蝝啗??prompt??
閬?嚗?1. 敹?皞Ⅱ???冽??餈啜user_input}??2. 瘛餃??拙??瑁憬???舐?憸冽?膩嚗?鈭柴迤???堆?
3. 憒??航?園◢?荔?憒控?偌????踝?嚗??孵撘瑁矽憸冽??
4. 憒??舐??憒?晞?堆?嚗?撘瑁矽閰脩??5. 雿輻?望?嚗底蝝唬??琿?
6. ?芸??唾??prompt嚗?閬??嗡?隤芣?

蝭?嚗??冽隤芥控?末瘞氬? "A beautiful natural landscape with lush green mountains and clear flowing water, bright and peaceful scenery, suitable for traditional Chinese meme card background, vibrant colors, photorealistic"

?曉隢?user_input}?????prompt嚗?""
             
             try:
                 # 雿輻 Gemini 蝧餉陌 (雿輻??扳芋???踹?撱Ｚ店)
                 translation_response = model_functional.generate_content(translation_prompt)
                 bg_prompt = translation_response.text.strip()
                 
                 # ????
                 success, result = generate_image_with_imagen(bg_prompt, user_id)
                 if success:
                     state['bg_image'] = result  # result ?臬??楝敺?                     state['stage'] = 'confirming_bg'
                     # ?潮??臬?蝯衣?嗥Ⅱ隤?雿輻 reply_token ?祥嚗?                     msg = "???撌脩????n\n隢Ⅱ隤??舀?行遛??\n(隢?蝑末???k?匱蝥??牧???圈???)"
                     if send_image_to_line(user_id, result, msg, reply_token):
                         return None # 撌脣?閬?                 else:
                     return f"?望?嚗??舐??仃?n\n憭望???嚗result}\n\n隢矽?湔?餈啣??岫銝甈∴??銝撘萄??策??"
             except Exception as e:
                 print(f"????航炊: {e}")
                 return "?望?嚗??舐??鈭???...隢?閰虫?甈∴?"
    
    elif state['stage'] == 'confirming_bg':
        # ?冽蝣箄??
        if user_input:
            # 瑼Ｘ?臬閬?瘨?            if '??' in user_input:
                user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                return "撌脣?瘨頛?鋆賭???
            # 瑼Ｘ?臬閬??圈??            elif '?' in user_input or '?? in user_input:
                state['stage'] = 'waiting_bg'
                state['bg_image'] = None
                return "憟賜?嚗??銝???撓?亥??舀?餈啜n\n?? 鋆賭???蝝?5蝘?隢?活?潮??荔?隞亙??航炊嚗?
            # ?冽蝣箄?嚗脣??頛詨?挾
            elif '憟? in user_input or 'ok' in user_input.lower() or '蝣箏?' in user_input:
                state['stage'] = 'waiting_text'
                return "憟賜?嚗?頛詨閬??銝＊蝷箇????批捆?n(靘?嚗摰像摰?璅????澈)\n?? 鋆賭???蝝?5蝘?隢?活?潮??荔?隞亙??航炊嚗?
            else:
                return "隢?蝑末???k?匱蝥??牧???圈?????
    
    elif state['stage'] == 'waiting_text':
        if user_input:
            # 瑼Ｘ?臬閬?瘨?            if '??' in user_input:
                user_meme_state[user_id] = {'stage': 'idle', 'bg_image': None, 'text': None}
                return "撌脣?瘨頛拙?鋆賭???
            
            state['text'] = user_input
            
            # Design Logic
            text = user_input
            bg_path = state['bg_image']
            
            # 摰?冽??菜???嚗宏??AI ?斗嚗Ⅱ靽?甈⊿????
            import random
            from PIL import Image
            
            try:
                from PIL import Image
                import random
                
                # 頛???
                bg_image = Image.open(bg_path)
                
                # AI 閬死?? - 撘瑁矽?輸?銝駁????瘥
                # AI 閬死?? - 撘瑁矽?輸?銝駁????瘥
                vision_prompt = f"""雿?瑁憬???身閮蜇??????撐??嚗???text}?身閮?雿喟?閬死????
**閮剛??格?嚗?*
1. **摮??征??銵?(Critical Balance)**嚗?   - **???抒洵銝**嚗?擃之撠?雁? **50-90** 銋???   - **?迂閬?**嚗鈭雁??擃?憭改?**?臭誑閬?**??銝凋??????憒??﹝???賡??押芋蝟??荔???   - **蝯??輸?**嚗?犖???敹蜓擃敺萸蝯?銝???2. **擃?瘥漲**嚗Ⅱ靽?摮?銝??啣閬?3. **閮剛???*嚗?????捱摰?阡?閬???(Stroke)??   - 瘣餅?/銴?? -> 撱箄降???? (stroke_width: 3-5)
   - ?舐?/銋暹楊? -> ?舐???敦?? (stroke_width: 0-2)
4. **????銝敺?*嚗?   - 隢?????脰矽嚗?*憭扯?岫**銝????脩???(憒??寡??敶押?????   - 銝?蝮賣?賊???(#FFD700) ??脯?5. **摮??末**嚗?   - **?身隢蝙?函?擃?(bold/heiti)**嚗頛拙??虜?閬?擃?蝎???皜???   - ?日????虜?舐??除鞈迎??蝙?冽扑擃?(kaiti)??
**隢??喃?銵?JSON ?澆?嚗?*
{{
    "position": "top-left", 
    "color": "#FFD700", 
    "font": "kaiti", 
    "font_size": 60,
    "angle": 5,
    "stroke_width": 3,
    "stroke_color": "#000000"
}}

**?隤芣?嚗?*
- position: top-left, top-right, bottom-left, bottom-right, top, bottom
- color: ??憿 (Hex ??rainbow)
- font: heiti (?刻/蝎?), bold (?寧?), kaiti (??澆?◢??
- font_size: ?⊿?蝬剜? 50-90嚗????雿蔭???40
- angle: -10 ??10 (敺格?頧?????
- stroke_width: 0 (?? ??5 (璆萇?)
- stroke_color: ??憿
"""

                # 雿輻??扳芋?脰?????嚗??冽?隤輸?皞怠漲隞亙????                response = model_functional.generate_content(
                    [vision_prompt, bg_image],
                    generation_config=genai.types.GenerationConfig(
                        temperature=1.1, # 隤輸?皞怠漲嚗??璈?                        top_p=0.95,
                        top_k=40
                    )
                )
                result = response.text.strip()
                
                print(f"[AI CREATIVE] Raw: {result[:100]}...")
                
                # 閫?? JSON ??Regex
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
                    # ?岫閫?? JSON
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
                    else:
                        raise ValueError("No JSON found")
                        
                except Exception as parse_e:
                    print(f"[AI PARSE ERROR] {parse_e}, trying fallback regex")
                    pass
                
                # 蝣箔? color ??hex ??rainbow
                if color.lower() != 'rainbow' and not color.startswith('#'):
                     # 蝪∪??撣貉???                     color_map = {'gold': '#FFD700', 'red': '#FF0000', 'blue': '#0000FF'}
                     color = color_map.get(color.lower(), '#FFFFFF')

                print(f"[AI CREATIVE] {text[:10]}... ??{position}, {color}, {font}, {size}px, {angle}摨? stroke={stroke_width}")
                
                final_path = create_meme_image(bg_path, text, user_id, font, size, position, color, angle, stroke_width, stroke_color)
                
                # Send - 雿輻 reply_token ?祥?潮?                if final_path:
                    if send_image_to_line(user_id, final_path, "?瑁憬?ˊ雿???霈?嚗?, reply_token):
                        state['stage'] = 'idle'
                        return None # 撌脣?閬?                    else:
                        state['stage'] = 'idle'
                        return "?瑁憬?歇鋆賭?雿?仃?n\n?航??嚗????單???ImgBB/GCS)?芾身摰n隢炎??.env ?辣銝剔? IMGBB_API_KEY??
                else:
                    return "鋆賭?憭望?鈭?.. (Layout Error)"
                    
            except Exception as e:
                print(f"[VISION ERROR] {e}嚗蝙?券璈??fallback")
                # Fallback: ?冽??菜????箏???(? rainbow ?賊?)
                all_positions = ['top-left', 'top-right', 'bottom-left', 'bottom-right', 'top', 'bottom']
                all_colors = ['rainbow', '#FFD700', '#FF8C00', '#FF1493', '#00CED1', '#32CD32', '#DC143C']
                all_fonts = ['kaiti', 'heiti']
                all_angles = [0, 5, 8, -5, -8]
                
                position = random.choice(all_positions)
                color = random.choice(all_colors)
                font = random.choice(all_fonts)
                angle = random.choice(all_angles)
                size = 65
                
                print(f"[FALLBACK CREATIVE] {text[:10]}... ??{position}, {color}, {font}, {size}?? {angle}摨?)

            
            final_path = create_meme_image(bg_path, text, user_id, font, size, position, color, angle)
            
            # Send - 雿輻 reply_token ?祥?潮?            if final_path:
                if send_image_to_line(user_id, final_path, "?瑁憬?ˊ雿???霈?嚗?, reply_token):
                    state['stage'] = 'idle'
                    return None # 撌脣?閬?                else:
                    state['stage'] = 'idle'
                    return "?瑁憬?歇鋆賭?雿?仃?n\n?航??嚗????單???ImgBB/GCS)?芾身摰n隢炎??.env ?辣銝剔? IMGBB_API_KEY??
            else:
                return "鋆賭?憭望?鈭?..\n\n頛詨??瘨??嚗??岫銝甈∴?"

    return "?潛?鈭?鈭?憿?..\n\n頛詨??瘨?????


# ======================
# Main LLM Function
# ======================

def classify_user_intent(text):
    """雿輻 AI ?斗?冽??"""
    try:
        # 撘瑕閬? (Regex Fallback) - ?芸???AI ?斗
        # 1. ?芸??斗??/?芷?? (??????摮?敹??閮剖????斗)
        if any(kw in text for kw in ["????", "?芷??", "銝???", "cancel reminder", "delete reminder"]):
            return "cancel_reminder"
            
        if any(kw in text for kw in ["????, "閮剜???, "?急?", "??", "remind me"]):
            return "set_reminder"
        if any(kw in text for kw in ["????", "?亦???", "敺齒", "???”", "my reminders"]):
            return "show_reminders"
            
        # 2. ?芸??斗?瑁憬??璇?鋆賭? (???????隞?
        if any(kw in text for kw in ["?瑁憬??, "璇?", "??摮?, "????", "餈瑕?", "meme"]):
            return "meme_creation"

        # 3. ?斗銝?砍?????(?踹? AI 隤文??chat)
        # ?冽隤?"?思???..", "??銝撘?..", "蝯行?銝撘?..??"
        if any(kw in text for kw in ["?思?", "??銝", "?Ｙ?銝", "鋆賭?銝", "create a image", "generate a image"]):
            return "image_generation"
        if "??" in text and any(kw in text for kw in ["蝯行?", "?唾?", "靘?撘?, "銝撘?, "??]):
            return "image_generation"
            
        classification_prompt = f"""
        隢???嗉撓?伐??text}??        
        隢??嗆飛憿隞乩??嗡葉銝蝔格???(?芸??喲??乩誨蝣潘?銝??嗡???)嚗?        1. video_generation (?唾ˊ雿蔣??????
        2. image_generation (?喟??????
        3. image_modification (?喃耨?孵????啁??????脯?X)
        4. meme_creation (?喳??瑁憬????
        5. trip_planning (?喳??????蝔葆??押暺??
        6. set_reminder (閮剖??????..)
        7. show_reminders (?亦????閰Ｗ?颲?
        8. chat (銝?祈?憭押??隞?撅祆銝膩????
        
        瘜冽?嚗?        - "???餃??? -> trip_planning
        - "??餌?撜? -> trip_planning
        - "撣嗆??餌" -> trip_planning
        - "???寞??? -> image_modification
        - "?思??餉?" -> image_generation
        - "?????? -> set_reminder
        """
        # 雿輻??扳芋?脰?????
        response = model_functional.generate_content(classification_prompt)
        intent = response.text.strip().lower()
        
        # 皜??航??擗泵??        import re
        match = re.search(r'(video_generation|image_generation|image_modification|meme_creation|trip_planning|set_reminder|show_reminders|chat)', intent)
        if match:
            return match.group(1)
        return "chat"
    except Exception as e:
        print(f"Intent classification error: {e}")
        return "chat"

def gemini_llm_sdk(user_input, user_id=None, reply_token=None):
    """銝餉? LLM ???賣嚗eply_token ?冽?潮??"""
    global chat_sessions, user_image_generation_state, user_meme_state, user_trip_plans, user_images, user_video_state, user_daily_video_count, user_last_image_prompt
    
    try:
        # 瑼Ｘ?臬閬??方??塚??摮??
        # ??嚗???嗆迤?券脰??瑁憬??銵?閬?蝑?蝔?銝?閰脫炎?交??方???        in_active_flow = False
        if user_id:
            # 瑼Ｘ?臬?其遙雿?蝔葉
            if user_id in user_meme_state and user_meme_state.get(user_id, {}).get('stage') != 'idle':
                in_active_flow = True
            if user_id in user_trip_plans and user_trip_plans.get(user_id, {}).get('stage') != 'idle':
                in_active_flow = True
            if user_id in user_image_generation_state and user_image_generation_state.get(user_id) not in ['idle', None]:
                in_active_flow = True
        
        should_clear = False
        if not in_active_flow:  # ?芣??冽??脰?銝剔?瘚???瑼Ｘ皜閮
            clear_keywords = ["???", "皜閮", "敹???, "?蔭撠店", "?啁???", "皜征閮", "reset", "??", "敹?", "皜征"]
            should_clear = any(keyword in user_input for keyword in clear_keywords)
            
            # ??AI ?斗?臬???方??嗥???嚗?箸??瘀?
            intent_check_keywords = ["?", "皜", "敹?", "?蔭", "皜征", "reset", "閮", "撠店", "??"]
            if not should_clear and any(keyword in user_input for keyword in intent_check_keywords):
                # ?函陛?桃? AI ?澆靘?瑟???(雿輻??扳芋??
                intent_prompt = f"雿輻?牧嚗user_input}???斗雿輻??行閬??文?閰梯??嗚??圈?憪?閰梧??芸?蝑?????
                intent_response = model_functional.generate_content(intent_prompt)
                should_clear = "?? in intent_response.text
        
        if should_clear:
            # 皜閰脩?嗥??????            if user_id in chat_sessions:
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
            return "憟賜?嚗?撌脩?皜????嗡?嚗????圈?憪嚗?隞颱????賢隞亙???"
        
        # 瑼Ｘ銵冽?蝚西?嚗??券頛拙?璅∪?銝??嚗?        meme_state = user_meme_state.get(user_id, {})
        if meme_state.get('stage') != 'waiting_text':  # ?芣?銝?瑁憬?撓?交芋撘??炎皜祈”?泵??            emoji_emotion = analyze_emoji_emotion(user_input)
            if emoji_emotion and len(user_input) < 10:
                return get_emoji_response(emoji_emotion)
        
        # 瑼Ｘ撠店?臬??
        now = datetime.now()
        if user_id in last_activity:
            time_diff = now - last_activity[user_id]
            if time_diff > SESSION_TIMEOUT:
                # 撠店撌脤???皜????                print(f"Session expired for user {user_id}, clearing history")
                if user_id in chat_sessions:
                    del chat_sessions[user_id]
                if user_id in user_images:
                    del user_images[user_id]
                if user_id in user_image_generation_state:
                    del user_image_generation_state[user_id]
        
        # ?湔?敺暑????        last_activity[user_id] = now
        
        # 瑼Ｘ?冽?臬?喳?瘨?雿?        if user_input.strip() in ["??", "銝?鈭?, "銝??", "?迫", "cancel", "銝?鈭?, "??閬?, "?暹?", "quit", "exit"]:
            # 皜?????            if user_id in user_image_generation_state:
                user_image_generation_state[user_id] = 'idle'
            if user_id in user_meme_state:
                user_meme_state[user_id] = {'stage': 'idle'}
            if user_id in user_video_state:
                user_video_state[user_id] = 'idle'
            return "憟賜?嚗歇蝬?瘨?????鈭??隞亥??予??????嚗??



        if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
             # ... (keep existing logic for image state handling, we will rely on lines 1056-1139 handled below)
             # Wait, the block 1056-1139 is for handling specific states.
             # We need to insert the CLASSIFICATION check *after* the state checks if state is IDLE.
             pass

        # ------------------------------------------------------------
        #  AI ???斗 (?誨???摮炎皜?
        # ------------------------------------------------------------
        
        # 雿???敹????迤?券脰?銝准????(State Handling)
        # ?憒??冽甇???瘚?銝剖?蝑?憿?銝?閰脰◤???箸??
        
        # 瑼Ｘ Agent ???(?亙撠店瘚?銝哨??湔鈭斤策 Agent)
        if user_id in user_meme_state and user_meme_state.get(user_id, {}).get('stage') != 'idle':
             return handle_meme_agent(user_id, user_input, reply_token=reply_token)
             
        if user_id in user_trip_plans and user_trip_plans.get(user_id, {}).get('stage') != 'idle':
             return handle_trip_agent(user_id, user_input, reply_token=reply_token)

        # 瑼Ｘ???????(??蝑? Prompt ??Modification ??瘜?
        if user_id in user_image_generation_state and user_image_generation_state[user_id] != 'idle':
             # ?ㄐ????頛臭??閬????摰??check state
             pass 
        else:
             # ?芣???Idle ????????             # ?芣???Idle ????????             
             # -------------------------------------------------------
             # 瘛瑕?撘??(Hybrid Router)
             # 1. ?炎?乓摰??萄???蝣箔??詨? 100% 閫貊?嗅?瘚?)
             # 2. 憒?瘝??摮??漱蝯?AI ?斗 (霈?憭拐??質孛?澆???
             # -------------------------------------------------------
             
             current_intent = None
             
             # ?摮撥?嗆?撠?(??雿輻???嗅???擃?)
             if any(k in user_input for k in ["閬?銵?", "銵?閬?", "?餌", "撣嗆???, "??", "??", "?舫??刻"]):
                 current_intent = 'trip_planning'
             elif any(k in user_input for k in ["?瑁憬??, "?頛拙?", "鋆賭??瑁憬??, "璇?", "餈瑕?", "??摮?, "銝?摮?, "??撘萄?"]):
                 current_intent = 'meme_creation'
             elif any(k in user_input for k in ["????", "?Ｙ???", "?思?撘?, "??", "?怠?", "蝜芸?"]):
                 current_intent = 'image_generation'
             elif any(k in user_input for k in ["??敶梁?", "鋆賭?敶梁?", "?蔣??]):
                 current_intent = 'video_generation'
             elif any(k in user_input for k in ["????", "?亥岷??", "?亦???", "敺齒鈭?"]):
                 current_intent = 'show_reminders'
             
             # 憒??摮??嚗???AI (???芰隤?嚗? "??餃???)
             if not current_intent:
                 current_intent = classify_user_intent(user_input)
             
             print(f"User Intent: {current_intent} (Input: {user_input})")

             # 1. 敶梁???
             if current_intent == 'video_generation':
                 if not check_video_limit(user_id):
                     return "?望?嚗?憭拙?賜???甈∪蔣??嚗?憭拙?靘?改??硃嚗heer up嚗?
                 user_video_state[user_id] = 'generating'
                 # ... (video generation logic simplified for preview)
                 return "? 敶梁????甇??脰?憭批?蝝?(Private Preview)嚗n\nGoogle 甇??箸????撘瑕之??Veo 璅∪?嚗隢?敺???

             # 2. ??靽格 (??Image Gen ????)
             elif current_intent == 'image_modification':
                  # ?湔?脣靽格瘚?
                  if user_id in user_last_image_prompt:
                       # 璅⊥ detect_regenerate_image_intent ??頛?                       user_image_generation_state[user_id] = 'generating'
                       
                       # ... (Execute Modification Logic reused from below)
                       # For simplicity, we can reuse the code block or jump to it.
                       # But since we are replacing the structure, we should copy the modification implementation here.
                       
                       last_prompt = user_last_image_prompt.get(user_id, "")
                       
                       optimize_prompt = f"""
                       蝟餌絞嚗?嗆閬耨?嫣???????                       ??蝷箄?嚗last_prompt}
                       ?冽靽格?瘙?{user_input}
                       
                       隢????Prompt????嗉?瘙?摮?隢??text_overlay??                       ? JSON: {{ "image_prompt": "...", "text_overlay": "..." }}
                       閬?嚗?. 靽????詨???2. 蝯?銝?雓?閰晞?                       """
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
                                # 雿輻 reply_token ?祥?潮?                                msg = "??靽格摰???\n\n憒??活靽格嚗??湔隤芣?隤踵?瘙n憒??隤踵嚗?隤芥????k?n?? ?敺?蝑?15蝘???隢?活?潮??荔?隞亙??航炊嚗?
                                if send_image_to_line(user_id, image_path, msg, reply_token):
                                    user_image_generation_state[user_id] = 'can_modify'
                                    return None # 撌脣?閬?                                else:
                                    return "??????雿?仃??
                            else:
                                user_image_generation_state[user_id] = 'can_modify'
                                return f"靽格憭望?嚗result}"
                       except Exception as e:
                            print(e)
                            return "???航炊..."
                  else:
                       return "?佗?雿?瘝???????隢?隤芥銝撘?..??閰西岫??"

             # 3. ???? - 撘?撘?閰?             elif current_intent == 'image_generation':
                 # 憒??冽撌脩??刻撓?乩葉?鈭?餈?(靘? "蝯行?銝撘萄??鞎??)
                 # 撠曹??府??"隢?餈唳?唾?????嚗?湔蝣箄?
                 
                 # 蝪∪?蕪閫貊閰?                 clean_prompt = user_input
                 for kw in ["蝯行?銝撘?, "?思?撘?, "??銝撘?, "??銝撘?, "?Ｙ?銝撘?, "?思???, "鋆賭?銝撘?, "create a", "generate a", "image of", "picture of"]:
                     clean_prompt = clean_prompt.replace(kw, "")
                 clean_prompt = clean_prompt.replace("??", "").strip()
                 
                 if len(clean_prompt) > 2: # ?身?膩?瑕漲憭扳2撠望???膩
                     user_image_generation_state[user_id] = 'waiting_for_confirmation'
                     # 靽? Prompt
                     if user_id not in user_last_image_prompt or isinstance(user_last_image_prompt[user_id], str):
                        user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt.get(user_id, '')}
                     user_last_image_prompt[user_id]['pending_description'] = clean_prompt
                     
                     return f"瘝?憿??冽閬??????荔?\n\n?clean_prompt}?n\n隢Ⅱ隤?阡?憪???\n(隢?蝑Ⅱ摰??k??憪?銋隤芥?瘨?"
                 else:
                     # ?膩憭芰????餈堆??脣閰Ｗ?璅∪?
                     user_image_generation_state[user_id] = 'waiting_for_prompt'
                     return """憟賜?嚗?????????
隢?餈唳?唾????摰對?
?? 憸冽憿?撅晞絲?ㄝ??撣?
???抽??鈭箇憿?隞暻潭見?犖???暻?? ??憿?瘞游蔗?硃?怒??

隢??餈啗底蝝堆???亥牧??憪??蝙?券?閮剛身摰?嚗??唾ˊ雿??冽?隤芥?瘨?""

             # 4. ?瑁憬?ˊ雿?             elif current_intent == 'meme_creation':
                 return handle_meme_agent(user_id, user_input, is_new_session=True, reply_token=reply_token)

             # 5. 銵?閬?
             elif current_intent == 'trip_planning':
                 return handle_trip_agent(user_id, user_input, is_new_session=True, reply_token=reply_token)

             # 6. ?亦???
             elif current_intent == 'show_reminders':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "????閬??澈?舀??"
                 try:
                     reminders = db.get_user_reminders(user_id, include_sent=False)
                     if not reminders: return "雿????颲行???嚗閬身摰?閰梧?隤芥???...?停?臭誑鈭?"
                     reminder_list = "?? **雿???皜** ??\n\n"
                     for idx, reminder in enumerate(reminders, 1):
                         t = reminder['reminder_time']
                         if isinstance(t, str): t = datetime.fromisoformat(t)
                         reminder_list += f"{idx}. {t.strftime('%m??d??%H:%M')} - {reminder['reminder_text']}\n"
                     return reminder_list + "\n??閬?臭誑?暹?嚗?
                 except: return "?亦?敺齒?鈭???..."

             # 6.5. ????
             elif current_intent == 'cancel_reminder':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "????閬??澈?舀??"
                 try:
                     # 蝪∪韏瑁?嚗??游?文??(?芯??舀??斗?摰?ID)
                     count = db.delete_all_user_reminders(user_id)
                     if count > 0:
                         return f"憟賜?嚗歇?箸?芷??{count} ????"
                     else:
                         return "?函???身摰遙雿???嚗?
                 except Exception as e:
                     print(f"Delete reminder error: {e}")
                     return "??????隤歹?隢?敺?閰艾?

             # 7. 閮剖???
             elif current_intent == 'set_reminder':
                 if not ADVANCED_FEATURES_ENABLED or not db: return "????閬??澈?舀??"
                 try:
                     parse_prompt = f"""?冽隤迎??user_input}?圾???蒂?神皞恍成?批捆??                     ? JSON: {{ "reminder_text": "...", "reminder_time": "2026-01-17T08:00:00" }}
                     閬?嚗???蝪∠???ｇ?銝?撱Ｚ店??                     ??嚗datetime.now().strftime('%Y-%m-%d %H:%M')}
                     """
                     # 雿輻??扳芋?圾??                     resp = model_functional.generate_content(parse_prompt)
                     import json, re
                     data = json.loads(re.search(r'\{[^}]+\}', resp.text).group())
                     t = datetime.fromisoformat(data['reminder_time'])
                     db.add_reminder(user_id, data['reminder_text'], t)
                     
                     reply = f"憟賜?嚗歇閮剖???{t.strftime('%m??d??%H:%M')} ??嚗data['reminder_text']}??
                     
                     # 瑼Ｘ蝟餌絞憿漲????亙歇皛踹?銝餃??
                     if db.is_system_quota_full():
                         reply += "\n\n?? 瘜冽?嚗?頂蝯勗?鞎駁?摨血歇皛選?撅??航?⊥?銝餃??冽嚗n隢?敺瘝?圈嚗??撓?乓??????嚗?
                         
                     return reply
                 except Exception as e:
                     print(f"Set reminder error: {e}")
                     return "閮剖???憭望?鈭?..隢牧皜?銝暺?靘???憭拇銝?暺??乓?

             # 8. 銝?祈?憭?(Chat)
             else:
                 # 瑼Ｘ?臬??
                 has_image = user_id in user_images
                 if user_id not in chat_sessions: chat_sessions[user_id] = model.start_chat(history=[])
                 chat = chat_sessions[user_id]
                 
                 if has_image:
                     upload_image = PIL.Image.open(user_images[user_id])
                     formatted_input = [f"蝟餌絞?內嚗??冽??萄之撣怎?隤除??嚗蒂銝????敺?摰?????蝳芥?瘝對?Cheer up嚗????n\n?冽隤迎?{user_input}", upload_image]
                     response = chat.send_message(formatted_input)
                 else:
                     formatted_input = f"蝟餌絞?內嚗??冽??萄之撣怎?隤除??嚗蒂銝????敺?摰?????蝳芥?瘝對?Cheer up嚗????n\n?冽隤迎?{user_input}"
                     response = chat.send_message(formatted_input)
                 return response.text




        
        # 瑼Ｘ???????        if user_id in user_image_generation_state:
            state = user_image_generation_state[user_id]
            
            
            # ???臭耨?寧???            if state == 'can_modify':
                # 瑼Ｘ?臬閬??耨??                end_keywords = ['摰?', 'ok', 'OK', '憟賭?', '銝鈭?, '蝯?', '雓?', '??']
                if any(keyword in user_input for keyword in end_keywords):
                    user_image_generation_state[user_id] = 'idle'
                    return "憟賜?嚗??歇摰???敺?甈∠?冽???"
                
                # 瑼Ｘ?臬?芣隤芥耨?嫘?                if user_input.strip() in ['靽格', '閬耨??, '??靽格']:
                    user_image_generation_state[user_id] = 'waiting_for_modification'
                    return "憟賜?嚗?隤芣??冽閬?雿耨?寥撐??嚗n(靘?嚗?銝?摮霈??脯矽?游摰寧?)\n\n憒??隤踵嚗?隤芥????k?? 
                else:
                    # ?湔隤芯耨?孵摰對??脣靽格瘚?
                    user_image_generation_state[user_id] = 'generating'
                    
                    last_prompt = user_last_image_prompt.get(user_id, "")
                    optimize_prompt = f"""
                    蝟餌絞嚗?嗆閬耨?嫣???????                    ??蝷箄?嚗last_prompt}
                    ?冽靽格?瘙?{user_input}
                    
                    隢????Prompt????嗉?瘙?摮?隢??text_overlay??                    ? JSON: {{ "image_prompt": "...", "text_overlay": "..." }}
                    閬?嚗?                    1. 靽????詨???
                    2. 蝯?銝?雓?閰晞?                    3. text_overlay 敹??胯?????蝳迫??祈??”??餈?(憒?(red heart)) ?遙雿?憿舐內?函?????                    
                    """
                    try:
                        # 雿輻??扳芋?圾??Prompt
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
                            # 雿輻 reply_token ?祥?潮?                            msg = "??靽格摰?嚗n\n?隞亦匱蝥矽?游?嚗?銝?隤踵嚗?隤芥??n?? ?敺?蝑?15蝘???隢?活?潮??荔?隞亙??航炊嚗?
                            if send_image_to_line(user_id, image_path, msg, reply_token):
                                user_image_generation_state[user_id] = 'can_modify'
                                return None # 撌脣?閬?                            else:
                                user_image_generation_state[user_id] = 'can_modify'
                                return "??????雿?仃??瑼Ｘ敺 Log??
                        else:
                            user_image_generation_state[user_id] = 'can_modify'
                            return f"靽格憭望?嚗result}"
                    except Exception as e:
                        print(f"Modification error: {e}")
                        user_image_generation_state[user_id] = 'can_modify'
                        return "靽格??隤歹?隢?閰艾?
            
            if state == 'waiting_for_confirmation':
                # ?冽蝣箄???
                if '??' in user_input:
                    user_image_generation_state[user_id] = 'idle'
                    return "撌脣?瘨?????
                elif '蝣箏?' in user_input or '??' in user_input or '??' in user_input:
                    # ?冽蝣箄?嚗身摰?? generating 銝衣匱蝥?銝銵?                    user_image_generation_state[user_id] = 'generating'
                    state = 'generating'  # ??嚗??state 霈嚗?銝??if state == 'generating' ?賢??瑁?
                    # 銝? return嚗?摰匱蝥銵??Ｙ? generating ?摩
                else:
                    # ?冽??膩嚗?唳?餈啣?甈∠Ⅱ隤?                    return f"憟賜?嚗?唾??????摰寞嚗n\n?user_input}?n\n隢Ⅱ隤?阡?憪???\n(隢?蝑Ⅱ摰???膩嚗??航牧??瘨?\n\n?? ?敺?蝑?15蝘???隢?活?潮??荔?隞亙??航炊嚗?
            
            if state == 'waiting_for_prompt':
                # 瑼Ｘ?臬閬?瘨?                if '??' in user_input:
                    user_image_generation_state[user_id] = 'idle'
                    return "撌脣?瘨?????
                # ?冽撌脫?靘底蝝圈?瘙??Ⅱ隤?                user_image_generation_state[user_id] = 'waiting_for_confirmation'
                # 靽??冽??憪?餈堆?隞乩噶敺???雿輻
                if user_id not in user_last_image_prompt or isinstance(user_last_image_prompt[user_id], str):
                    user_last_image_prompt[user_id] = {'prompt': user_last_image_prompt.get(user_id, '')}
                user_last_image_prompt[user_id]['pending_description'] = user_input
                return f"?冽閬??????批捆?荔?\n\n?user_input}?n\n隢Ⅱ隤?阡?憪???\n(隢?蝑Ⅱ摰???膩嚗??航牧??瘨?\n\n?? ?敺?蝑?15蝘???隢?活?潮??荔?隞亙??航炊嚗?
            
            if state == 'generating':
                # ?冽撌脩Ⅱ隤?????
                
                # 雿輻靽???憪?餈堆????舐?嗥?撓?亦??Ⅱ摰?                saved_data = user_last_image_prompt.get(user_id, {})
                if isinstance(saved_data, str):
                    original_description = saved_data if saved_data else user_input
                else:
                    original_description = saved_data.get('pending_description', user_input)
                
                # 雿輻 AI ?芸??內閰?撘瑁矽摰?扼?甇Ｙ?閰晞?湔?摮???
                optimize_prompt = f"""?冽?喟??????膩?荔??original_description}??                隢???餈啗????拙? AI ?????蝷箄???                憒??冽?＊?唾??典???撖怠?嚗?憒????Ｗ神?拙???嚗?撠?摮??靘?                
                ? JSON ?澆?嚗?                {{
                    "image_prompt": "?望??? Prompt",
                    "text_overlay": "閬神?典?銝??? (蝜?銝剜?, ?舫)"
                }}
                
                閬?嚗?                1. 憸冽甇?????具?                2. 蝯?銝?雓?閰晞?                3. text_overlay 敹??胯?????蝳迫??祈??”??餈?(憒?(red heart)) ?遙雿?憿舐內?函?????                """
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
                    
                    print(f"????嚗rompt: {image_prompt}")
                    
                    # ????
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    error_reason = result if not success else None
                    
                    if image_path:
                        # 憒???摮???瘙?                        if text_overlay:
                            # ?芸????? (?身蝵桐葉)
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                        
                        # 靽? Prompt 隞乩噶靽格
                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        
                        # ?喲??策?冽 - 雿輻 reply_token ?祥?潮?                        msg = "????摰??n\n憒?靽格嚗??湔隤芣??函?隤踵?瘙n憒??隤踵嚗?隤芥????k?n?? 靽格??蝝?5蝘?隢?活?潮??荔?隞亙??航炊嚗?
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            # 閮剖??箏靽格???????idle
                            user_image_generation_state[user_id] = 'can_modify'
                            return None # 撌脣?閬?                        else:
                            # ?潮仃??                            user_image_generation_state[user_id] = 'idle'
                            return "??撌脩????潮仃?n\n?航??嚗????單???ImgBB/GCS)閮剖??炊?n隢炎?亙???Log ??terminal 頛詨銝剔? [SEND IMAGE] 閮??
                    else:
                        # ??憭望?嚗??文????豢?銝西身??idle
                        if user_id in user_last_image_prompt:
                            user_last_image_prompt[user_id].pop('pending_description', None)
                        user_image_generation_state[user_id] = 'idle'
                        # 憿舐內閰喟敦?航炊??
                        failure_msg = f"????憭望??n\n憭望???嚗error_reason if error_reason else '?芰?航炊'}\n\n憒????嚗??活隤芥????蒂?膩?函??瘙?
                        return failure_msg
                
                except Exception as e:
                    print(f"?????航炊: {e}")
                    import traceback
                    traceback.print_exc()
                    user_image_generation_state[user_id] = 'waiting_for_prompt'
                    return "??????隤歹?隢??唳?餈唳??瘙?



            elif state == 'can_modify':
                # ?冽迨???嚗?嗅隞交?蝥耨?孵????游隤芥???                
                # 瑼Ｘ?臬蝯?靽格
                if any(keyword in user_input.lower() for keyword in ['摰?', 'ok', '憟賜?', '雓?', '?迫', '蝯?']):
                    user_image_generation_state[user_id] = 'idle'
                    return "銝恥瘞??撣??撐???冽??迭嚗?閬隞鼠敹??閮湔?????"
                
                # 閬靽格?瘙??湔?瑁???
                user_image_generation_state[user_id] = 'generating'
                
                # ??銝活 Prompt
                saved_data = user_last_image_prompt.get(user_id, {})
                last_prompt = saved_data.get('prompt', '') if isinstance(saved_data, dict) else saved_data
                
                # 雿輻 AI ?芸? Prompt (靽格璅∪?)
                optimize_prompt = f"""
                蝟餌絞嚗?嗆閬耨?寥撐????                ??蝷箄?嚗last_prompt}
                ?冽靽格?瘙?{user_input}
                
                霂瑞????Prompt????嗉?瘙?摮?隢??text_overlay??                ? JSON:
                {{
                    "image_prompt": "?啁??望? Prompt",
                    "text_overlay": "閬神??摮?(蝝?摮? 蝳迫?祈??”??餈?"
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
                    
                    # ????
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    
                    if success:
                        if text_overlay:
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                            
                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        
                        print(f"[DEBUG] Before send: image_path type={type(image_path)}, value={image_path}")
                        # 雿輻 reply_token ?祥?潮?                        msg = "??靽格摰?嚗n\n?隞亦匱蝥矽?游?嚗?銝?隤踵嚗?隤芥??n?? 隤踵??蝝?5蝘?隢?活?潮??荔?隞亙??航炊嚗?
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            # ??敺???can_modify ????迂蝜潛?靽格
                            user_image_generation_state[user_id] = 'can_modify'
                            return None # 撌脣?閬?                        else:
                            # ?潮仃???虜?臭??喳?憿?
                            user_image_generation_state[user_id] = 'can_modify'
                            return "??撌脩????潮仃?n\n?航??嚗????單???ImgBB/GCS)閮剖??炊?n隢炎?亙???Log??
                    else:
                        # 憭望?敺?靽? can_modify嚗??冽?岫
                        user_image_generation_state[user_id] = 'can_modify'
                        return f"?望?嚗耨?孵仃?n\n憭望???嚗result}\n\n隢??牧瘜岫閰衣?嚗?
                        
                except Exception as e:
                    print(f"Modification error: {e}")
                    user_image_generation_state[user_id] = 'can_modify'
                    return "????隤歹?隢?敺?閰艾?

            elif state == 'waiting_for_modification':
                 # ?冽??鈭耨?寧敦蝭嚗?憪??啁???                 user_image_generation_state[user_id] = 'generating'
                 
                 # ??銝活??Prompt
                 last_prompt = user_last_image_prompt.get(user_id, "")
                 
                 # 雿輻 AI ?芸??內閰?(蝯???Prompt + ?唬耨??
                 optimize_prompt = f"""
                 蝟餌絞嚗?嗆閬耨?嫣???????                 ??蝷箄?嚗last_prompt}
                 ?冽靽格?瘙?{user_input}
                 
                 隢????Prompt????嗉?瘙?摮?隢??text_overlay??                 ? JSON:
                 {{
                     "image_prompt": "?啁??望? Prompt",
                     "text_overlay": "閬神??摮?(蝝?摮? 蝳迫?祈??”??餈?"
                 }}
                 
                 閬?嚗?                 1. 靽????詨???                 2. 蝯?銝?雓?閰晞?                 """
                 
                 # 雿輻??扳芋?圾??                 optimized = model_functional.generate_content(optimize_prompt)
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
                 
                 # ????
                 success, result = generate_image_with_imagen(image_prompt, user_id)
                 image_path = result if success else None
                 
                 if success:
                     if text_overlay:
                         image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                         
                     user_last_image_prompt[user_id] = {'prompt': image_prompt}
                     
                     # 雿輻 reply_token ?祥?潮?                     msg = "??靽格摰?嚗n\n?隞亦匱蝥矽?游?嚗?銝?隤踵嚗?隤芥??n?? ????蝝?5蝘?隢?潮??荔?隞亙????航炊嚗?
                     if send_image_to_line(user_id, image_path, msg, reply_token):
                         user_image_generation_state[user_id] = 'can_modify'
                         return None # 撌脣?閬?                     else:
                         user_image_generation_state[user_id] = 'can_modify'
                         return "??撌脩????潮仃?n\n?航??嚗????單???ImgBB/GCS)閮剖??炊?n隢炎?亙???Log??
                 else:
                     user_image_generation_state[user_id] = 'can_modify'
                     return f"?望?嚗耨?孵仃?n\n憭望???嚗result}\n\n???唬??末??"


        # 瑼Ｘ葫???????嚗??怠撠店銝剔?亥?瘙耨?對?
        if detect_regenerate_image_intent(user_input):
            # ?斗?胯岷??虫耨?嫘??胯?交?靘耨?寞?隞扎?            # 蝪∪?斗嚗????詨?撠?(靘? "?臭誑?孵?", "靽格", "銝遛??)嚗停?岷?敦蝭
            # 憒?摮頛? (靘? "??霈???), ??亙銵?            
            is_generic_request = len(user_input) < 10 or user_input in ["?臭誑?孵?", "?賣??, "?喃耨??, "撟急???, "靽格"]
            
            if is_generic_request:
                user_image_generation_state[user_id] = 'waiting_for_modification'
                return "瘝?憿?隢?雿?獐?對?\n隢?閮湔??琿??摰對?靘?嚗????脰??胯?鞎?????銝?蜇摮?..蝑?
            
            else:
                # ?冽撌脩???鈭擃耨?寞?隞歹?蝡?瑁???
                user_image_generation_state[user_id] = 'generating'
                
                saved_data = user_last_image_prompt.get(user_id, {})
                last_prompt = saved_data.get('prompt', '') if isinstance(saved_data, dict) else saved_data
                
                optimize_prompt = f"""
                蝟餌絞嚗?嗆閬耨?嫣???????                ??蝷箄?嚗last_prompt}
                ?冽靽格?瘙?{user_input}
                
                隢????Prompt????嗉?瘙?摮?隢??text_overlay??                ? JSON:
                {{
                    "image_prompt": "?啁??望? Prompt",
                    "text_overlay": "閬神??摮?(蝝?摮? 蝳迫?祈??”??餈?"
                }}
                
                閬?嚗?                1. 靽????詨???                2. 蝯?銝?雓?閰晞?                3. text_overlay 敹??胯?????蝳迫??祈??”??餈?(憒?(red heart)) ?遙雿?憿舐內?函?????                """
                
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
                    
                    # ????
                    success, result = generate_image_with_imagen(image_prompt, user_id)
                    image_path = result if success else None
                    
                    if success:
                        if text_overlay:
                            image_path = create_meme_image(image_path, text_overlay, user_id, position='center')
                            
                        user_last_image_prompt[user_id] = {'prompt': image_prompt}
                        
                        # 雿輻 reply_token ?祥?潮?                        msg = "??靽格摰?嚗n\n?隞亦匱蝥矽?游?嚗?銝?隤踵嚗?隤芥??n?? ????蝝?5蝘?隢?潮??荔?隞亙????航炊嚗?
                        if send_image_to_line(user_id, image_path, msg, reply_token):
                            user_image_generation_state[user_id] = 'can_modify'
                            return None # 撌脣?閬?                        else:
                            user_image_generation_state[user_id] = 'can_modify'
                            return "??撌脩????潮仃?n\n?航??嚗????單???ImgBB/GCS)閮剖??炊?n隢炎?亙???Log ?身摰?IMGBB_API_KEY??
                    else:
                        user_image_generation_state[user_id] = 'can_modify'
                        return f"?望?嚗耨?孵仃?n\n憭望???嚗result}\n\n隢??牧瘜岫閰衣?嚗?
                except Exception as e:
                    print(f"Modification error: {e}")
                    user_image_generation_state[user_id] = 'idle'
                    return "????隤歹?隢?敺?閰艾?
            
            # ??銝活??Prompt (憒???閰?
            saved_data = user_last_image_prompt.get(user_id, {})
            last_prompt = saved_data.get('prompt', '') if isinstance(saved_data, dict) else saved_data
            
            # 雿輻 AI ?芸??內閰??銝???
            # ?Ⅱ?內 AI 蝯???Prompt ??瘙?            optimize_prompt = f"""
            蝟餌絞嚗?嗆閬耨?嫣???????            ??蝷箄?嚗last_prompt}
            ?冽靽格?瘙?{user_input}
            
            隢???內閰??啁?靽格?瘙??Ｙ?銝??啁????渡??望??? Prompt??            閬?嚗?            1. 靽????敹蜓擃??日??冽隤芾???嚗?            2. ??冽?靽格嚗?憒????脯??梯正嚗?            3. 憒??冽隤芥??啁???蝯衣敦蝭嚗?蝔凝?寡?瑽??◢?潦?            4. ?芸??唾??prompt嚗?閬隞牧??            """
            
            optimized = model.generate_content(optimize_prompt)
            image_prompt = optimized.text.strip()
            
            # ????
            success, result = generate_image_with_imagen(image_prompt, user_id)
            image_path = result if success else None
            
            if success:
                # ?湔 Prompt 閮?
                user_last_image_prompt[user_id] = {'prompt': image_prompt}
                
                print(f"[DEBUG] Before send: image_path type={type(image_path)}, value={image_path}")
                # 雿輻 reply_token ?祥?潮?                msg = "??靽格摰?嚗n\n?隞亦匱蝥矽?游?嚗?銝?隤踵嚗?隤芥??n?? ????蝝?5蝘?隢?潮??荔?隞亙????航炊嚗?
                if send_image_to_line(user_id, image_path, msg, reply_token):
                    user_image_generation_state[user_id] = 'can_modify'
                    return None # 撌脣?閬?                else:
                    user_image_generation_state[user_id] = 'can_modify'
                    return "??撌脩????潮仃?n\n?航??嚗????單???ImgBB/GCS)閮剖??炊?n隢炎?亙???Log 銝剔? [SEND IMAGE] 閮??
            else:
                user_image_generation_state[user_id] = 'waiting_for_prompt'
                return "?望?嚗??啁??仃??...隢??迄??甈∩??唾??獐?對?"
        

    except Exception as e:
        print(f"ERROR in gemini_llm_sdk: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return "??嚗??銝暺???...隢?敺?閰虫?甈∴?"

if __name__ == "__main__":
    # ??????蝔嚗????券脤??嚗?    reminder_scheduler = None
    if ADVANCED_FEATURES_ENABLED:
        try:
            reminder_scheduler = init_scheduler(channel_access_token)
            print("??Reminder scheduler started")
        except Exception as e:
            print(f"?? Failed to start scheduler: {e}")
    
    port = int(os.environ.get("PORT", 5000))
    try:
        print(f"?? Starting bot on port {port}...")
        app.run(host="0.0.0.0", port=port)
    finally:
        # ??????        if reminder_scheduler:
            try:
                reminder_scheduler.stop()
                print("??Reminder scheduler stopped")
            except:
                pass
