"""
排程器模組 - 定時發送提醒
使用 APScheduler 實作
"""
import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)

# 導入資料庫模組
try:
    from database import db
except ImportError:
    db = None

class ReminderScheduler:
    """提醒排程器"""
    
    def __init__(self, line_channel_access_token: str):
        self.scheduler = BackgroundScheduler()
        self.configuration = Configuration(access_token=line_channel_access_token)
        self.is_running = False
    
    def start(self):
        """啟動排程器"""
        if not self.is_running:
            # 每分鐘檢查一次待發送的提醒
            self.scheduler.add_job(
                func=self.check_and_send_reminders,
                trigger=IntervalTrigger(minutes=1),
                id='reminder_checker',
                name='Check and send reminders',
                replace_existing=True
            )
            
            self.scheduler.start()
            self.is_running = True
            print("Reminder scheduler started")
    
    def stop(self):
        """停止排程器"""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            print("Reminder scheduler stopped")
    
    def check_and_send_reminders(self):
        """檢查並發送提醒"""
        if not db:
            print("Database not available")
            return
        
        try:
            # 取得待發送的提醒
            pending_reminders = db.get_pending_reminders()
            
            for reminder in pending_reminders:
                result_status = self.send_reminder(
                    reminder['user_id'],
                    reminder['reminder_text']
                )
                
                if result_status == 1:
                    # 標記為已發送 (Success)
                    db.mark_reminder_sent(reminder['id'])
                    print(f"Sent reminder {reminder['id']} to user {reminder['user_id']}")
                elif result_status == 2:
                    # 標記為因額度失敗 (不再重試)
                    db.mark_reminder_failed(reminder['id'])
                    print(f"Reminder {reminder['id']} failed due to quota limit")
        
        except Exception as e:
            print(f"Error checking reminders: {e}")
    
    def send_reminder(self, user_id: str, reminder_text: str) -> bool:
        """發送提醒訊息 (Returns: 1=Success, 0=Fail, 2=Quota Limit)"""
        try:
            with ApiClient(self.configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                
                message_text = f"⏰ **提醒通知** ⏰\n\n{reminder_text}\n\n時間到囉！"
                
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=user_id,
                        messages=[TextMessage(text=message_text)]
                    )
                )
            return 1 # Success
        except Exception as e:
            error_str = str(e)
            print(f"Error sending reminder: {e}")
            if "429" in error_str or "monthly limit" in error_str or "quota" in error_str.lower():
                return 2 # Quota Limit
            return 0 # Generic Fail

# 全域排程器實例（需要在 main.py 中初始化）
scheduler = None

def init_scheduler(line_channel_access_token: str):
    """初始化排程器"""
    global scheduler
    scheduler = ReminderScheduler(line_channel_access_token)
    scheduler.start()
    return scheduler
