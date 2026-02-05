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
**You MUST output the ENTIRE COMPLETE PLAN with modifications integrated.**
**The output must include ALL days, not just the modified parts.**

Modification Rules:
- If user says "第一天想加入購物" → Modify Day 1, but OUTPUT complete plan including Days 2, 3, etc.
- If user says "換掉某個景點" → Replace that spot, but OUTPUT the full itinerary
- If user says "調整時間" → Adjust times, but OUTPUT everything

**CRITICAL**: The system expects a COMPLETE trip plan. If you only return the changed section, the entire plan will be lost.

**Format Requirements:**
1. **MUST PRESERVE TITLE**: Keep the original title "{dest}，{dur}之旅" at the top
2. **Readable Text Format**: Clean text with bullet points. NO Markdown headers (##).
3. Structure:
   
   {dest}，{dur}之旅
   
   【Day 1】
   [上午] (09:00-12:00)
    - 景點：XX
    - 停留時間：XX
   
   [下午] (13:00-17:00)
    ...
   
   【旅遊小提示】
    - 交通：...
   
4. **NO ADDRESSES** - Just spot names.
5. **HEADERS MUST BE CHINESE**: Use "上午", "下午", "晚上", "旅遊小提示".
6. Provide realistic time estimates
7. Add practical travel tips at the end

**Example Output:**
宜蘭，三天兩夜之旅

【Day 1】
[上午] (09:00-12:00)
- 景點：[具體景點名稱]
- 建議停留時間：[時間]

[下午] (13:00-17:00)
- ...

【Day 2】
...

【Day 3】
...

【旅遊小提示】
- 交通方式：...
- 預算建議：...

Remember: STRICTLY PROFESSIONAL. NO JOKES. NO EMOJIS. NO CASUAL LANGUAGE.
AND MOST IMPORTANTLY: Output the COMPLETE modified plan with ALL days included."""

    try:
        print(f"[DEBUG] 修改行程 - 用戶: {user_id}, 輸入: {user_input}")
        
        # 調用 AI (狀態通知已移除以節省 API 額度，警告已在初始提示中顯示)
        print("[DEBUG] 調用 Gemini...")
        response = model.generate_content(modify_prompt)
        print(f"[DEBUG] Gemini 成功回應，長度: {len(response.text)}")
        
        draft_plan = response.text.strip()
        
        # 驗證並修正 (Validation) - Ensure we pass the clean model
        validated_plan = validate_and_fix_trip_plan(draft_plan, model)
        
        return validated_plan
        
    except Exception as e:
        print(f"[ERROR] 修改行程失敗: {e}")
        import traceback
        traceback.print_exc()
        raise

def validate_and_fix_trip_plan(plan, model):
    """
    檢查行程邏輯並自動修正
    
    Args:
        plan: 原始行程文字
        model: Gemini 模型
        
    Returns:
        str: 修正後的行程 (若無錯誤則回傳原行程)
    """
    validation_prompt = f"""
    [SYSTEM: FAST LOGIC CHECK]
    Task: Check for CRITICAL transport errors in the trip plan. Return 'PASS' if safe.
    
    CRITICAL RULES:
    1. GREEN ISLAND / ORCHID ISLAND: Must take Boat from Taitung Fugang. (No Train/HSR directly to island).
    2. PENGHU: Must take Plane or Boat.
    3. HUALIEN / TAITUNG: No HSR (High Speed Rail). Only TRA Train.
    
    Current Plan:
    {plan[:3000]}
    
    Output:
    - If safe: 'PASS'
    - If errors: Rewrite the problematic activity part ONLY (in Traditional Chinese).
    """
    try:
        print("[DEBUG] Running Trip Validation...")
        response = model.generate_content(validation_prompt)
        text = response.text.strip()
        
        if text == "PASS":
            print("[DEBUG] Validation Passed.")
            return plan
        else:
            print("[DEBUG] Validation Errors Found. Auto-fixing...")
            return text
            
    except Exception as e:
        print(f"[ERROR] Validation failed: {e}")
        return plan # If validation fails, return original

