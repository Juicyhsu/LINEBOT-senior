import os
import sys
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    RichMenuRequest,
    RichMenuArea,
    RichMenuBounds,
    RichMenuSize,
    PostbackAction,
    URIAction,
    RichMenuSwitchAction
)
# from linebot.v3.reason import ValidateError (Not needed/Not found)

# Load env
from dotenv import load_dotenv
load_dotenv()

channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
if not channel_access_token:
    print("Error: LINE_CHANNEL_ACCESS_TOKEN not found in .env")
    sys.exit(1)

configuration = Configuration(access_token=channel_access_token)

def create_rich_menu():
    # 1. Define Rich Menu
    # 標準 2500x1686 大小 (6格)
    # 我們假設用戶的圖片是標準 6 格佈局
    # 格子定義：
    # 1(左上) 2(中上) 3(右上)
    # 4(左下) 5(中下) 6(右下)
    # 寬度=2500, 高度=1686
    # 每格寬=2500/3 ≈ 833, 高=1686/2 = 843
    
    # 根據用戶描述的功能：
    # 1. 製作圖片 (左上)
    # 2. 長輩圖 (中上)
    # 3. 設定提醒 (右上)
    # 4. 行程規劃 (左下)
    # 5. 連結查證 (中下)
    # 6. 新聞快報 (右下)
    
    w = 2500
    h = 1686
    cell_w = int(w / 3)
    cell_h = int(h / 2)
    
    areas = [
        # Row 1
        RichMenuArea(bounds=RichMenuBounds(x=0, y=0, width=cell_w, height=cell_h), 
                     action=PostbackAction(data="action=image_generation", inputOption="openKeyboard", fillInText="幫我畫一隻貓")),
        RichMenuArea(bounds=RichMenuBounds(x=cell_w, y=0, width=cell_w, height=cell_h), 
                     action=PostbackAction(data="action=meme_creation", inputOption="openKeyboard", fillInText="製作長輩圖")),
        RichMenuArea(bounds=RichMenuBounds(x=cell_w*2, y=0, width=cell_w, height=cell_h), 
                     action=PostbackAction(data="action=reminder", inputOption="openKeyboard", fillInText="提醒我")),
        
        # Row 2
        RichMenuArea(bounds=RichMenuBounds(x=0, y=cell_h, width=cell_w, height=cell_h), 
                     action=PostbackAction(data="action=trip", inputOption="openKeyboard", fillInText="帶我去玩")),
        RichMenuArea(bounds=RichMenuBounds(x=cell_w, y=cell_h, width=cell_w, height=cell_h), 
                     action=PostbackAction(data="action=verification", inputOption="openKeyboard", fillInText="查證")),
        RichMenuArea(bounds=RichMenuBounds(x=cell_w*2, y=cell_h, width=cell_w, height=cell_h), 
                     action=PostbackAction(data="action=news", inputOption="openKeyboard", fillInText="看新聞")),
    ]

    rich_menu_to_create = RichMenuRequest(
        size=RichMenuSize(width=w, height=h),
        selected=True,
        name="Main Menu v2",
        chat_bar_text="點擊開啟選單",
        areas=areas
    )

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_blob = MessagingApiBlob(api_client)

        try:
            # Create Rich Menu
            rich_menu_id = line_bot_api.create_rich_menu(rich_menu_request=rich_menu_to_create).rich_menu_id
            print(f"Created Rich Menu ID: {rich_menu_id}")

            # Upload Image (Use requests to avoid SDK serialization issues with bytes)
            import requests
            # Use optimized PNG (now guaranteed < 1MB)
            image_path = os.path.join("static", "rich_menu_new.png")
            if not os.path.exists(image_path):
                print(f"Image not found: {image_path}")
                return

            with open(image_path, "rb") as f:
                image_data = f.read()
                
            headers = {
                "Authorization": f"Bearer {channel_access_token}",
                "Content-Type": "image/png"
            }
            # Upload endpoint
            upload_url = f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content"
            
            response = requests.post(upload_url, headers=headers, data=image_data)
            
            if response.status_code == 200:
                print("Image uploaded successfully.")
            else:
                print(f"Image upload failed: {response.text}")
                return

            # Set as Default
            line_bot_api.set_default_rich_menu(rich_menu_id=rich_menu_id)
            print(f"Rich Menu {rich_menu_id} set as default!")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    create_rich_menu()
