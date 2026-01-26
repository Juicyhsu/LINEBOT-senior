from PIL import Image, ImageDraw, ImageFont
import os

def create_menu_image():
    # Settings
    width = 800
    height = 1000
    bg_color = (255, 250, 205) # LemonChiffon (Light Yellow)
    text_color = (0, 0, 0)
    title_color = (255, 69, 0) # OrangeRed
    
    # Create image
    img = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # Fonts - try to find Chinese font
    font_path = "C:\\Windows\\Fonts\\msjh.ttc" # Microsoft JhengHei
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\mingliu.ttc"
    
    try:
        title_font = ImageFont.truetype(font_path, 80)
        item_font = ImageFont.truetype(font_path, 50)
        footer_font = ImageFont.truetype(font_path, 40)
    except:
        print("Chinese font not found, using default.")
        title_font = item_font = footer_font = ImageFont.load_default()

    # Draw Title
    draw.text((width//2, 100), "🤖 專屬激勵夥伴 🤖", font=title_font, fill=title_color, anchor="mm")
    draw.text((width//2, 200), "✨ 功能總覽 ✨", font=title_font, fill=title_color, anchor="mm")
    
    # Draw Items
    items = [
        "1. 🌸 製作長輩圖 (傳照片給我)",
        "2. 🚗 規劃旅遊 (說「我想去...」)",
        "3. 🎨 AI 畫圖 (說「畫一隻...」)",
        "4. 📅 貼心提醒 (說「提醒我...」)",
        "5. 💬 聊天解悶 (隨時陪你聊)"
    ]
    
    start_y = 350
    line_height = 80
    
    for i, item in enumerate(items):
        draw.text((100, start_y + i*line_height), item, font=item_font, fill=text_color, anchor="lm")
        
    # Draw Footer
    draw.text((width//2, 850), "加油！Cheer up！讚喔！💖", font=footer_font, fill=(255, 20, 147), anchor="mm")
    
    # Save
    os.makedirs("static", exist_ok=True)
    output_path = "static/welcome_menu.jpg"
    img.save(output_path)
    print(f"Menu image created at {output_path}")

if __name__ == "__main__":
    create_menu_image()
