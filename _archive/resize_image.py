
from PIL import Image

input_path = "assets/rich_menu_large.png"
output_path = "assets/rich_menu_large_fixed.png"

try:
    img = Image.open(input_path)
    print(f"Original size: {img.size}")
    
    target_size = (2500, 1686)
    resized_img = img.resize(target_size, Image.Resampling.LANCZOS)
    
    # 轉換為 RGB (防止 PNG 透明通道問題) 並存為 JPEG
    rgb_im = resized_img.convert('RGB')
    
    # 存為 JPEG，品質設為 80 以縮小檔案大小
    compressed_path = "assets/rich_menu_compressed.jpg"
    rgb_im.save(compressed_path, "JPEG", quality=80, optimize=True)
    
    print(f"Resized & compressed image saved to {compressed_path}")
except Exception as e:
    print(f"Error: {e}")
