from PIL import Image
import os

target_path = r"c:\Users\user\Desktop\LINEBOT\長輩版機器人\static\rich_menu_new.png"
temp_jpg = r"c:\Users\user\Desktop\LINEBOT\長輩版機器人\static\rich_menu_new.jpg"

try:
    if os.path.exists(target_path):
        img = Image.open(target_path)
        img = img.convert('RGB') # Remove alpha to save as JPG
        
        # Save as optimized PNG
        temp_png = r"c:\Users\user\Desktop\LINEBOT\長輩版機器人\static\rich_menu_new.png"
        
        # Resize if needed (already done, but ensured)
        # Quantize to 256 colors to significantly reduce PNG size while keeping structure usable for menu
        # or just use maximize optimization
        img = img.resize((2500, 1686), Image.Resampling.LANCZOS)
        
        # Optimize PNG
        img.save(temp_png, "PNG", optimize=True)
        
        size = os.path.getsize(temp_png)
        print(f"Optimized PNG size: {size}")
        
        if size > 1000000:
             print("Warning: Still > 1MB. Trying quantization (P mode)...")
             img_p = img.quantize(colors=256)
             img_p.save(temp_png, "PNG", optimize=True)
             print(f"Quantized PNG size: {os.path.getsize(temp_png)}")

        print(f"Saved optimized image to {temp_png}")
    else:
        print("Source file not found")
except Exception as e:
    print(f"Compress Error: {e}")
