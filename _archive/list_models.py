
from google.cloud import aiplatform
import os
from dotenv import load_dotenv

load_dotenv()

project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
location = "us-central1"

aiplatform.init(project=project_id, location=location)

print(f"Listing models in project {project_id} location {location}...")

try:
    from vertexai.preview.vision_models import ImageGenerationModel
    # 嘗試列出或測試幾個可能的名稱
    candidates = [
        "veo-2.0-generate-001",
        "veo-001",
        "video-generation-001",
        "imagen-3.0-generate-001", # Image model for reference
    ]
    
    for name in candidates:
        print(f"Checking {name}...", end=" ")
        try:
            model = ImageGenerationModel.from_pretrained(name)
            print("FOUND!")
        except Exception as e:
            print(f"FAILED: {e}")

except Exception as e:
    print(f"SDK Error: {e}")
