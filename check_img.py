from PIL import Image
import os

try:
    path = r"C:\Users\user\.gemini\antigravity\brain\59465bdb-9e82-4cdb-8f5f-f020c25bdde2\uploaded_media_1770014060784.png"
    if os.path.exists(path):
        img = Image.open(path)
        print(f"Dimensions: {img.size}")
    else:
        print("File not found")
except Exception as e:
    print(f"Error: {e}")
