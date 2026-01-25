"""
行程調整輔助函數
"""

def modify_trip_plan(user_id, user_input, dest, dur, purp, current_plan, model, line_bot_api_config):
    """
    修改行程計劃
    
    Args:
        user_id: 用戶ID
        user_input: 用戶的修改需求
        dest: 目的地
        dur: 天數
        purp: 目的
        current_plan: 現有行程內容
        model: Gemini 模型實例
        line_bot_api_config: LINE API 配置
        
    Returns:
        str: 修改後的行程文字
    """
    from linebot.v3 import WebhookHandler
    from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage, Configuration
    
    modify_prompt = f"""
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

**Current Trip Information:**
Destination: {dest}
Duration: {dur}
Purpose: {purp}

**Current Plan:**
{current_plan}

**User's Modification Request:** {user_input}

**CRITICAL TASK:** 
**ONLY modify the specific parts the user requested. Keep everything else EXACTLY the same.**

For example:
- If user says "第一天想加入購物" → ONLY modify Day 1, keep Days 2, 3, etc. unchanged
- If user says "換掉某個景點" → ONLY replace that specific spot, keep the rest
- If user says "調整時間" → ONLY adjust the times, keep activities unchanged

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

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS. NO CASUAL LANGUAGE.
AND MOST IMPORTANTLY: PRESERVE all unchanged parts from the current plan!"""

    try:
        print(f"[DEBUG] 修改行程 - 用戶: {user_id}, 輸入: {user_input}")
        
        # 調用 AI (狀態通知已移除以節省 API 額度，警告已在初始提示中顯示)
        print("[DEBUG] 調用 Gemini...")
        response = model.generate_content(modify_prompt)
        print(f"[DEBUG] Gemini 成功回應，長度: {len(response.text)}")
        
        return response.text
        
    except Exception as e:
        print(f"[ERROR] 修改行程失敗: {e}")
        import traceback
        traceback.print_exc()
        raise
