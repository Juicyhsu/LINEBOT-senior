
import os
import sys
from google.cloud import storage
from dotenv import load_dotenv

load_dotenv()

def make_bucket_public(bucket_name):
    """
    將 GCS Bucket 設定為公開存取 (allUsers 可讀取)
    """
    if not bucket_name:
        print("Error: Bucket name is empty.")
        return

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)

        # 取得目前的 IAM Policy
        policy = bucket.get_iam_policy(requested_policy_version=3)

        # 設定公開權限：給予 allUsers "roles/storage.objectViewer" 權限
        policy.bindings.append(
            {"role": "roles/storage.objectViewer", "members": {"allUsers"}}
        )

        # 更新 Policy
        bucket.set_iam_policy(policy)

        print(f"Success! Bucket '{bucket_name}' is now public.")
        print(f"All files in this bucket are publicly accessible.")
        print(f"Example URL: https://storage.googleapis.com/{bucket_name}/[filename]")
        
    except Exception as e:
        print(f"Failed to make bucket public: {e}")
        print("\nPossible reasons:")
        print("1. Service Account lacks 'Storage Admin' role.")
        print("2. 'Public Access Prevention' is enforced on this bucket (Organization Policy).")

if __name__ == "__main__":
    # 取得參數列表 (排除腳本名稱)
    buckets = sys.argv[1:]
    
    # 如果沒有參數，嘗試從環境變數取得，或請用戶輸入
    if not buckets:
        env_bucket = os.environ.get("GCS_BUCKET_NAME")
        if env_bucket:
            buckets = [env_bucket]
        else:
            user_input = input("Please enter GCS Bucket Name(s) separated by space: ").strip()
            if user_input:
                buckets = user_input.split()
    
    if not buckets:
        print("No bucket names provided. Exiting.")
    else:
        print(f"Configuring {len(buckets)} buckets: {', '.join(buckets)}...\n")
        for bucket_name in buckets:
            print(f"--- Processing '{bucket_name}' ---")
            make_bucket_public(bucket_name)
            print("")
