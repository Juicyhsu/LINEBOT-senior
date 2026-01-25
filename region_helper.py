"""
使用 AI 動態判斷旅遊地區是否需要細化的輔助函數
"""

def check_region_need_clarification(user_input, model):
    """
    使用 AI 判斷用戶輸入的地區是否過於廣泛，需要進一步細化
    
    Args:
        user_input: 用戶輸入的地區名稱
        model: Gemini AI 模型實例
        
    Returns:
        dict: {
            'need_clarification': bool,  # 是否需要細化
            'suggested_options': list,    # 建議的選項列表
            'region_type': str            # 地區類型
        }
    """
    region_check_prompt = f"""用戶想去旅遊，他們說：「{user_input}」

請判斷這個地區是否過於廣泛，需要進一步詢問具體地區。


判斷規則：
- 洲級（如：歐洲、東南亞）→ **需要細化**
- 國家級（如：日本、美國、台灣）→ **需要細化**
- 廣泛地區（如：北海道、沖繩、關東、關西）→ **需要細化**
- **具體城市/縣市 → 不需要細化**，包括：
  * 台灣所有縣市：台北、新北、桃園、台中、台南、高雄、基隆、新竹、嘉義、宜蘭、花蓮、台東、澎湖、金門、馬祖、苗栗、彰化、南投、雲林、屏東、連江
  * 日本城市：東京、大阪、京都、名古屋、福岡、札幌等
  * 其他國家城市：首爾、曼谷、紐約、巴黎等
- 小地區/景點（如：日月潭、阿里山、清水寺）→ 不需要細化

**核心原則：**
台灣的縣市（如彰化、宜蘭、花蓮等）已經是**最小規劃單位**，直接可以開始規劃行程，**絕對不需要**再細化成鄉鎮市區！

只回答 JSON 格式：
{{
    "need_clarification": true或false,
    "region_type": "洲級/國家級/城市級/地區級",
    "suggested_options": ["子地區1", "子地區2", "子地區3"]
}}

要求：
1. 如果是台灣的任何縣市，`need_clarification` 必須為 false
2. suggested_options 只在 need_clarification 為 true 時才需要提供
3. 其他國家的具體城市（如東京、首爾）也是 false"""

    try:
        import json
        import re
        
        response = model.generate_content(region_check_prompt)
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        
        if match:
            data = json.loads(match.group())
            return {
                'need_clarification': data.get('need_clarification', False),
                'suggested_options': data.get('suggested_options', []),
                'region_type': data.get('region_type', 'unknown')
            }
        else:
            # 無法解析，預設不需要細化
            return {
                'need_clarification': False,
                'suggested_options': [],
                'region_type': 'unknown'
            }
            
    except Exception as e:
        print(f"Region check error: {e}")
        # 發生錯誤，預設不需要細化
        return {
            'need_clarification': False,
            'suggested_options': [],
            'region_type': 'error'
        }
