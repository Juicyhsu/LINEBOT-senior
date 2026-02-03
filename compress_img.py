from PIL import Image
import os

target_path = r"c:\Users\user\Desktop\LINEBOT\長輩版機器人\static\rich_menu_new.png"
temp_jpg = r"c:\Users\user\Desktop\LINEBOT\長輩版機器人\static\rich_menu_new.jpg"

try:
    if os.path.exists(target_path):
        img = Image.open(target_path)
        img = img.convert('RGB') # Remove alpha to save as JPG
        
        # Save as high quality JPG first, check size
        img.save(temp_jpg, "JPEG", quality=85)
        
        size = os.path.getsize(temp_jpg)
        print(f"Original PNG size: {os.path.getsize(target_path)}")
        print(f"Compressed JPG size: {size}")
        
        if size > 1000000:
            # Further compress if needed
            img.save(temp_jpg, "JPEG", quality=70)
            print(f"Re-compressed JPG size: {os.path.getsize(temp_jpg)}")
            
        print(f"Saved optimized image to {temp_jpg}")
    else:
        print("Source file not found")
except Exception as e:
    print(f"Compress Error: {e}")
