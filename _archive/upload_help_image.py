
import os
import gcs_utils
from dotenv import load_dotenv

load_dotenv()

image_path = "assets/help_poster_v2.png"
if os.path.exists(image_path):
    print(f"Uploading {image_path}...")
    url = gcs_utils.upload_image_to_gcs(image_path)
    print(f"UPLOAD_URL: {url}")
else:
    print("Image not found")
