"""
Google Cloud Storage 工具模組
負責檔案上傳與公開連結取得
"""
import os
import uuid
from google.cloud import storage
from datetime import datetime

# 全域 GCS 客戶端
_storage_client = None

def get_storage_client():
    """取得 GCS 客戶端（單例模式）"""
    global _storage_client
    if _storage_client is None:
        try:
            # 嘗試使用環境變數中的憑證
            _storage_client = storage.Client()
        except Exception as e:
            print(f"Failed to initialize GCS client: {e}")
            return None
    return _storage_client

def upload_file_to_gcs(source_file_path: str, content_type: str = None) -> str:
    """
    上傳檔案到 Google Cloud Storage
    
    Args:
        source_file_path: 本地檔案路徑
        content_type: 檔案類型（例如 image/png, video/mp4）
        
    Returns:
        str: 檔案的公開網址 (Public URL)，如果失敗則回傳 None
    """
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    if not bucket_name:
        print("GCS_BUCKET_NAME not set in environment variables")
        return None
        
    client = get_storage_client()
    if not client:
        return None
        
    try:
        bucket = client.bucket(bucket_name)
        
        # 生成唯一的檔案名稱
        # 格式: year/month/uuid.ext
        # 例如: 2026/01/550e8400-e29b-41d4-a716-446655440000.png
        ext = os.path.splitext(source_file_path)[1]
        now = datetime.now()
        destination_blob_name = f"{now.year}/{now.month:02d}/{uuid.uuid4()}{ext}"
        
        blob = bucket.blob(destination_blob_name)
        
        # 設定快取控制（例如：快取 1 小時）
        blob.cache_control = "public, max-age=3600"
        
        # 上傳檔案
        blob.upload_from_filename(source_file_path, content_type=content_type)
        
        print(f"File uploaded to GCS: gs://{bucket_name}/{destination_blob_name}")
        
        # 回傳公開網址
        # 格式: https://storage.googleapis.com/bucket-name/blob-name
        return blob.public_url
        
    except Exception as e:
        print(f"GCS upload error: {e}")
        return None

def upload_image_to_gcs(image_path: str) -> str:
    """上傳圖片到 GCS"""
    return upload_file_to_gcs(image_path, content_type="image/png")

def upload_video_to_gcs(video_path: str) -> str:
    """上傳影片到 GCS"""
    return upload_file_to_gcs(video_path, content_type="video/mp4")

def upload_audio_to_gcs(audio_path: str) -> str:
    """上傳音訊到 GCS"""
    return upload_file_to_gcs(audio_path, content_type="audio/mpeg")
