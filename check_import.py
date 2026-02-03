
try:
    from google.generativeai.types import Content, Part
    print("Success: from google.generativeai.types import Content, Part")
except ImportError as e:
    print(f"Failed: {e}")

try:
    import google.generativeai as genai
    print(f"genai version: {genai.__version__}")
    if hasattr(genai.types, 'Content'):
        print("genai.types.Content exists")
    else:
        print("genai.types.Content DOES NOT exist")
except Exception as e:
    print(f"genai check failed: {e}")
