
import google.generativeai as genai
import os

try:
    model = genai.GenerativeModel('gemini-pro')
    chat = model.start_chat(history=[])
    
    # Try appending dict
    print("Attempting to append dict...")
    chat.history.append({'role': 'user', 'parts': ["Hello"]})
    print("Success: Appended user dict")
    
    chat.history.append({'role': 'model', 'parts': ["Hi there"]})
    print("Success: Appended model dict")
    
    print(f"History length: {len(chat.history)}")
    print(f"History item type: {type(chat.history[0])}")
    
except Exception as e:
    print(f"Failed with dict: {e}")
