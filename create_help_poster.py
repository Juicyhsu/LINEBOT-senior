from PIL import Image, ImageDraw, ImageFont
import os

def create_help_poster():
    """創建功能說明海報，完全仿照原版設計"""
    
    # 設定
    width = 640
    height = 640
    
    # 創建圖片
    img = Image.new('RGB', (width, height), color=(255, 250, 220))  # 淺米黃色背景
    draw = ImageDraw.Draw(img)
    
    # 字體路徑
    font_path = "C:\\Windows\\Fonts\\msjh.ttc"  # Microsoft JhengHei
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\mingliu.ttc"
    
    try:
        title_font = ImageFont.truetype(font_path, 60)
        feature_font = ImageFont.truetype(font_path, 36)
        button_font = ImageFont.truetype(font_path, 24)
        small_font = ImageFont.truetype(font_path, 20)
    except:
        print("找不到中文字體，使用預設字體")
        title_font = feature_font = button_font = small_font = ImageFont.load_default()
    
    # 繪製裝飾圓圈（四個角落）
    circle_color = (220, 200, 180)
    for x, y in [(50, 50), (590, 50), (50, 590), (590, 590)]:
        draw.ellipse([x-30, y-30, x+30, y+30], outline=circle_color, width=3)
    
    # 繪製標題
    title_text = "功能說明"
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(((width - title_width) // 2, 60), title_text, font=title_font, fill=(139, 69, 19))
    
    # 卡片設定
    cards = [
        # 左欄
        {"x": 40, "y": 150, "bg": (255, 228, 225), "icon": "🎨", "title": "生成圖片", "example": "說:「幫我畫一隻貓」"},
        {"x": 40, "y": 310, "bg": (255, 228, 225), "icon": "🖼️", "title": "長輩圖", "example": "說:「我要做長輩圖」"},
        {"x": 40, "y": 470, "bg": (175, 238, 238), "icon": "🗺️", "title": "行程規劃", "example": "說:「規劃宜蘭旅遊」"},
        # 右欄
        {"x": 340, "y": 150, "bg": (240, 255, 240), "icon": "⏰", "title": "設定提醒", "example": "說:「提醒我看醫生」"},
        {"x": 340, "y": 310, "bg": (230, 230, 250), "icon": "🛡️", "title": "智能助手", "example": "說:「查證」或「看新聞」"},
        {"x": 340, "y": 470, "bg": (230, 230, 250), "icon": "😊", "title": "聊天", "subtitle": "隨時說", "extra": "隨時跟我聊天喔！"},
    ]
    
    # 繪製卡片
    card_width = 260
    card_height = 140
    
    for card in cards:
        x, y = card["x"], card["y"]
        
        # 繪製卡片背景（圓角矩形）
        draw.rounded_rectangle(
            [x, y, x + card_width, y + card_height],
            radius=15,
            fill=card["bg"],
            outline=(210, 105, 30),
            width=3
        )
        
        # 繪製圖示（左側）
        icon_text = card["icon"]
        draw.text((x + 20, y + 15), icon_text, font=title_font, fill=(0, 0, 0))
        
        # 繪製標題
        title_text = card["title"]
        draw.text((x + 90, y + 20), title_text, font=feature_font, fill=(0, 0, 0))
        
        # 繪製範例按鈕（藍色膠囊）
        if "example" in card:
            button_y = y + 80
            draw.rounded_rectangle(
                [x + 15, button_y, x + 245, button_y + 40],
                radius=20,
                fill=(30, 144, 255),
                outline=None
            )
            
            # 喇叭圖示
            draw.text((x + 25, button_y + 5), "🔊", font=small_font, fill=(255, 255, 255))
            
            # 範例文字
            example_text = card["example"]
            draw.text((x + 55, button_y + 8), example_text, font=button_font, fill=(255, 255, 255))
        
        # 特殊處理：聊天卡片
        if "subtitle" in card:
            draw.text((x + 90, y + 65), card["subtitle"], font=button_font, fill=(0, 0, 0))
        
        if "extra" in card:
            draw.text((x + 15, y + 95), card["extra"], font=small_font, fill=(100, 100, 100))
    
    # 儲存
    os.makedirs("assets", exist_ok=True)
    output_path = "assets/help_poster_new.png"
    img.save(output_path)
    print(f"功能說明海報已創建: {output_path}")
    return output_path

if __name__ == "__main__":
    create_help_poster()
