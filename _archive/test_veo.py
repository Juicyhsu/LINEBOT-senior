
import os
import sys
from dotenv import load_dotenv

load_dotenv()

project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
print(f"Project ID: {project_id}")

try:
    import vertexai
    from vertexai.preview.vision_models import ImageGenerationModel
    print(f"Vertex AI SDK version: {vertexai.__version__}")
    
    vertexai.init(project=project_id, location="us-central1")
    print("Vertex AI initialized.")
    
    try:
        model = ImageGenerationModel.from_pretrained("veo-2.0-generate-001")
        print("Successfully loaded model: veo-2.0-generate-001")
    except Exception as e:
        print(f"Failed to load Veo model: {e}")
        
    try:
        model_imagen = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
        print("Successfully loaded model: imagen-3.0-generate-001")
    except Exception as e:
        print(f"Failed to load Imagen model: {e}")

except ImportError as e:
    print(f"Import Error: {e}")
except Exception as e:
    print(f"General Error: {e}")
