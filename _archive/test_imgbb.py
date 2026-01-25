"""
測試 ImgBB 上傳功能
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_imgbb_upload():
    api_key = os.environ.get("IMGBB_API_KEY", "")
    
    if not api_key:
        print("❌ IMGBB_API_KEY 未設定！")
        return False
    
    print(f"✅ IMGBB_API_KEY 已設定：{api_key[:10]}...")
    
    # 嘗試上傳一個測試檔案
    test_image_path = "uploads"
    if not os.path.exists(test_image_path):
        print(f"❌ {test_image_path} 資料夾不存在")
        return False
    
    # 找第一個圖片檔案
    test_file = None
    for file in os.listdir(test_image_path):
        if file.endswith(('.png', '.jpg', '.jpeg')):
            test_file = os.path.join(test_image_path, file)
            break
    
    if not test_file:
        print("❌ 找不到測試圖片")
        return False
    
    print(f"📤 測試上傳：{test_file}")
    
    try:
        with open(test_file, "rb") as file:
            url = "https://api.imgbb.com/1/upload"
            payload = {"key": api_key}
            files = {"image": file}
            response = requests.post(url, data=payload, files=files)
            
            if response.status_code == 200:
                data = response.json()
                image_url = data["data"]["url"]
                print(f"✅ 上傳成功！")
                print(f"🔗 URL: {image_url}")
                return True
            else:
                print(f"❌ 上傳失敗：{response.status_code}")
                print(f"錯誤訊息：{response.text}")
                return False
    except Exception as e:
        print(f"❌ 發生錯誤：{e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("ImgBB 上傳測試")
    print("=" * 50)
    test_imgbb_upload()
