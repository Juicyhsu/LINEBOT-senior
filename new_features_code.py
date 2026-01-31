# ======================
# 連結查證與新聞功能
# ======================

# 用戶待處理連結狀態
user_link_pending = {}
# 新聞快取 (減少API呼叫)
news_cache = {'data': None, 'timestamp': None}
# 用戶新聞快取(語音播報)
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
        'ltn.com.tw',  # 自由
        'chinatimes.com',  # 中時
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
    查詢網域年齡（天數）
    返回: 天數 (int) 或 None (查詢失敗時)
    """
    try:
        import whois
        from datetime import datetime
        
        domain = extract_domain(url)
        if not domain:
            return None
        
        w = whois.whois(domain)
        
        # whois 的 creation_date 可能是 datetime 或 list
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
    
    # 評估風險等級
    # 只有「明顯像詐騙」才警告，一般網站不警告
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
    """格式化查證結果"""
    domain = extract_domain(url)
    
    if safety_check['level'] == 'danger':
        return f"""🚨 等等！我發現這個連結有點可疑：

⚠️ 風險提示：
{''.join(['• ' + risk + '\\n' for risk in safety_check['risks']])}
💡 建議先不要點開！

如果您想了解更多，請告訴我您的需求！"""
    
    elif safety_check['level'] == 'warning':
        return f"""⚠️ 提醒！這個網站比較新：
{''.join(['• ' + risk + '\\n' for risk in safety_check['risks']])}
💡 請謹慎查看。

您是想：
1️⃣ 🔍 查證這個連結是否為詐騙
2️⃣ 📖 讓我幫你讀內容

請告訴我您的需求！"""
    
    else:
        # 對於一般網站，直接詢問意圖
        return f"""收到連結！

您是想：
1️⃣ 📖 讓我讀給你聽（摘要內容）
2️⃣ 🔍 查證這個連結

請告訴我「閱讀」或「查證」！"""

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
        
        # 提取文字
        text = soup.get_text()
        
        # 清理空白
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        # 限制長度 (避免過長)
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

def fetch_latest_news():
    """
    抓取最新新聞(使用 RSS)
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
            'https://www.cna.com.tw/rss/headline.xml',  # 中央社頭條
            # 可以添加更多來源
        ]
        
        news_items = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:5]:  # 每個來源取 5 則
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
    """檢測是否想要查詢新聞"""
    keywords = ['新聞', '消息', '發生', '今天', '最近', '時事', '頭條']
    return any(keyword in text for keyword in keywords)

def generate_news_summary():
    """生成新聞摘要"""
    news_items = fetch_latest_news()
    
    if not news_items:
        return "抱歉，目前無法取得新聞資訊，請稍後再試！"
    
    # 使用 Gemini 精選新聞
    try:
        news_text = "\n\n".join([
            f"標題: {item['title']}\n內容: {item['summary']}"
            for item in news_items[:6]
        ])
        
        prompt = f"""
請從這些新聞中，挑選最重要的 3 則
每則摘要控制在50字以內：

{news_text}

格式：
📰 今日新聞摘要

1️⃣ 【標題】
   （摘要內容...）

2️⃣ 【標題】
   （摘要內容...）

3️⃣ 【標題】
   （摘要內容...）
"""
        
        response = model.generate_content(prompt)
        return response.text + "\n\n🔊 要語音播報嗎？說「要語音」！"
    except Exception as e:
        print(f"News summary error: {e}")
        return "抱歉，無法整理新聞資訊，請稍後再試！"

def generate_news_audio(text, user_id):
    """
    生成新聞語音
    返回: 音訊檔路徑 (str) 或 None
    """
    try:
        # 使用 Google Cloud TTS (中文品質好)
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
        
        # 儲存音訊
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        audio_path = os.path.join(UPLOAD_FOLDER, f"{user_id}_news.mp3")
        with open(audio_path, 'wb') as f:
            f.write(response.audio_content)
        
        return audio_path
    except Exception as e:
        print(f"TTS error: {e}")
        return None
