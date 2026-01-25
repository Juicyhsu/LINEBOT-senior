
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
    MessageAction,
    URIAction
)
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
if not channel_access_token:
    print("Error: LINE_CHANNEL_ACCESS_TOKEN not found in environment variables.")
    sys.exit(1)

configuration = Configuration(access_token=channel_access_token)

def create_rich_menu():
    print("Starting Rich Menu Setup...")
    
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api_blob = MessagingApiBlob(api_client)
        
        # 1. 定義 Rich Menu 結構
        # 尺寸: 2500 x 1686
        # 格局: 2列 x 3行
        # 每個區塊寬度: 2500 / 3 = 833
        # 每個區塊高度: 1686 / 2 = 843
        
        rich_menu_to_create = RichMenuRequest(
            size=RichMenuSize(width=2500, height=1686),
            selected=True,
            name="Elderly Bot Menu",
            chat_bar_text="開啟選單",
            areas=[
                # Row 1, Col 1: 功能總覽
                RichMenuArea(
                    bounds=RichMenuBounds(x=0, y=0, width=833, height=843),
                    action=MessageAction(label="功能總覽", text="功能總覽")
                ),
                # Row 1, Col 2: 生成圖片
                RichMenuArea(
                    bounds=RichMenuBounds(x=833, y=0, width=834, height=843),
                    action=MessageAction(label="生成圖片", text="生成圖片")
                ),
                # Row 1, Col 3: 製作長輩圖
                RichMenuArea(
                    bounds=RichMenuBounds(x=1667, y=0, width=833, height=843),
                    action=MessageAction(label="製作長輩圖", text="我要做長輩圖")
                ),
                # Row 2, Col 1: 生成影片
                RichMenuArea(
                    bounds=RichMenuBounds(x=0, y=843, width=833, height=843),
                    action=MessageAction(label="生成影片", text="生成影片")
                ),
                # Row 2, Col 2: 提醒通知
                RichMenuArea(
                    bounds=RichMenuBounds(x=833, y=843, width=834, height=843),
                    action=MessageAction(label="提醒通知", text="我的提醒")
                ),
                # Row 2, Col 3: 行程規劃
                RichMenuArea(
                    bounds=RichMenuBounds(x=1667, y=843, width=833, height=843),
                    action=MessageAction(label="行程規劃", text="規劃行程")
                )
            ]
        )
        
        # 2. 建立 Rich Menu 並取得 ID
        print("Creating rich menu...")
        try:
            rich_menu_id = messaging_api.create_rich_menu(rich_menu_request=rich_menu_to_create).rich_menu_id
            print(f"Rich Menu created: {rich_menu_id}")
        except Exception as e:
            print(f"Failed to create rich menu: {e}")
            return

        # 3. 上傳圖片
        image_path = "assets/rich_menu_compressed.jpg"
        if not os.path.exists(image_path):
            print(f"Error: Image not found at {image_path}")
            return

        print(f"Uploading image from {image_path}...")
        try:
            with open(image_path, "rb") as image_file:
                # 讀取二進制資料
                image_data = image_file.read()
                
            # 使用 Blob API 上傳
            # 注意：line-bot-sdk v3 上傳圖片的方式
            messaging_api_blob.set_rich_menu_image(
                rich_menu_id=rich_menu_id,
                body=image_data,
                _headers={'Content-Type': 'image/jpeg'}
            )
            print("Image uploaded successfully.")
        except Exception as e:
            print(f"Failed to upload image: {e}")
            return

        # 4. 設定為預設選單
        print("Setting as default menu...")
        try:
            messaging_api.set_default_rich_menu(rich_menu_id=rich_menu_id)
            print("Rich Menu successfully set as default!")
        except Exception as e:
            print(f"Failed to set default menu: {e}")
            return

if __name__ == "__main__":
    create_rich_menu()
