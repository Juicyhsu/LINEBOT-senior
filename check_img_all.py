from PIL import Image
import os

files = [
    r"C:\Users\user\.gemini\antigravity\brain\59465bdb-9e82-4cdb-8f5f-f020c25bdde2\uploaded_media_1770014060784.png",
    r"C:\Users\user\.gemini\antigravity\brain\59465bdb-9e82-4cdb-8f5f-f020c25bdde2\uploaded_media_1770014300055.jpg",
    r"C:\Users\user\.gemini\antigravity\brain\59465bdb-9e82-4cdb-8f5f-f020c25bdde2\uploaded_media_1770014348962.jpg"
]

for path in files:
    try:
        if os.path.exists(path):
            img = Image.open(path)
            print(f"{os.path.basename(path)}: {img.size}")
        else:
            print(f"{os.path.basename(path)}: Not found")
    except Exception as e:
        print(f"{os.path.basename(path)}: Error {e}")
