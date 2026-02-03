from PIL import Image
import os

source_path = r"C:\Users\user\.gemini\antigravity\brain\59465bdb-9e82-4cdb-8f5f-f020c25bdde2\uploaded_media_1770014060784.png"
target_dir = r"c:\Users\user\Desktop\LINEBOT\長輩版機器人\static"
target_path = os.path.join(target_dir, "rich_menu_new.png")

os.makedirs(target_dir, exist_ok=True)

try:
    if os.path.exists(source_path):
        img = Image.open(source_path)
        # Force resize to specific dimensions (Rich Menu requirement)
        # Using LANCZOS for high quality downsampling/upsampling
        new_img = img.resize((2500, 1686), Image.Resampling.LANCZOS)
        new_img.save(target_path)
        print(f"Successfully resized to {new_img.size} and saved to {target_path}")
    else:
        print("Source file not found")
except Exception as e:
    print(f"Resize Error: {e}")
