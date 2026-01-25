"""
資料庫模組 - 提醒功能
支援 SQLite（本地開發）和 PostgreSQL（生產環境）
"""
import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import json

# 根據環境選擇資料庫
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///bot_data.db")

if DATABASE_URL.startswith("postgres"):
    # PostgreSQL (生產環境)
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import urllib.parse as urlparse
    
    url = urlparse.urlparse(DATABASE_URL)
    DB_CONFIG = {
        'host': url.hostname,
        'port': url.port,
        'database': url.path[1:],
        'user': url.username,
        'password': url.password
    }
    DB_TYPE = "postgres"
else:
    # SQLite (本地開發)
    import sqlite3
    DB_TYPE = "sqlite"
    SQLITE_DB_PATH = DATABASE_URL.replace("sqlite:///", "")

class Database:
    """資料庫操作類別"""
    
    def __init__(self):
        self.db_type = DB_TYPE
        self._init_database()
    
    def _get_connection(self):
        """取得資料庫連接"""
        if self.db_type == "postgres":
            return psycopg2.connect(**DB_CONFIG)
        else:
            return sqlite3.connect(SQLITE_DB_PATH)
    
    def _init_database(self):
        """初始化資料庫表格"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if self.db_type == "postgres":
            # PostgreSQL 語法
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    reminder_text TEXT NOT NULL,
                    reminder_time TIMESTAMP NOT NULL,
                    is_sent BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata JSONB
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trip_plans (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    plan_name VARCHAR(500),
                    plan_type VARCHAR(50),
                    start_date DATE,
                    end_date DATE,
                    plan_data JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 建立索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_user_time 
                ON reminders(user_id, reminder_time)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_pending 
                ON reminders(is_sent, reminder_time)
            """)
        else:
            # SQLite 語法
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    reminder_text TEXT NOT NULL,
                    reminder_time TEXT NOT NULL,
                    is_sent INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trip_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    plan_name TEXT,
                    plan_type TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    plan_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 建立索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_user_time 
                ON reminders(user_id, reminder_time)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_pending 
                ON reminders(is_sent, reminder_time)
            """)
        
        conn.commit()
        conn.close()
    
    # ==================
    # 提醒功能
    # ==================
    
    def add_reminder(self, user_id: str, reminder_text: str, 
                     reminder_time: datetime, metadata: Optional[Dict] = None) -> int:
        """新增提醒"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if self.db_type == "postgres":
            cursor.execute("""
                INSERT INTO reminders (user_id, reminder_text, reminder_time, metadata)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (user_id, reminder_text, reminder_time, json.dumps(metadata) if metadata else None))
            reminder_id = cursor.fetchone()[0]
        else:
            cursor.execute("""
                INSERT INTO reminders (user_id, reminder_text, reminder_time, metadata)
                VALUES (?, ?, ?, ?)
            """, (user_id, reminder_text, reminder_time.isoformat(), 
                  json.dumps(metadata) if metadata else None))
            reminder_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return reminder_id
    
    def get_pending_reminders(self, current_time: Optional[datetime] = None) -> List[Dict]:
        """取得待發送的提醒"""
        if current_time is None:
            current_time = datetime.now()
        
        conn = self._get_connection()
        
        if self.db_type == "postgres":
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM reminders
                WHERE is_sent = FALSE AND reminder_time <= %s
                ORDER BY reminder_time
            """, (current_time,))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM reminders
                WHERE is_sent = 0 AND reminder_time <= ?
                ORDER BY reminder_time
            """, (current_time.isoformat(),))
        
        if self.db_type == "postgres":
            reminders = cursor.fetchall()
        else:
            columns = [description[0] for description in cursor.description]
            reminders = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return reminders
    
    def mark_reminder_sent(self, reminder_id: int):
        """標記提醒已發送"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if self.db_type == "postgres":
            cursor.execute("""
                UPDATE reminders SET is_sent = TRUE WHERE id = %s
            """, (reminder_id,))
        else:
            cursor.execute("""
                UPDATE reminders SET is_sent = 1 WHERE id = ?
            """, (reminder_id,))
        
        conn.commit()
        conn.close()
    
    def mark_reminder_failed(self, reminder_id: int):
        """標記提醒發送失敗 (因額度不足)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 使用 2 代表發送失敗
        if self.db_type == "postgres":
            cursor.execute("""
                UPDATE reminders SET is_sent = 2 WHERE id = %s
            """, (reminder_id,))
        else:
            cursor.execute("""
                UPDATE reminders SET is_sent = 2 WHERE id = ?
            """, (reminder_id,))
        
        conn.commit()
        conn.close()

    def get_failed_reminders(self, user_id: str) -> List[Dict]:
        """取得發送失敗的提醒"""
        conn = self._get_connection()
        
        if self.db_type == "postgres":
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM reminders WHERE user_id = %s AND is_sent = 2
                ORDER BY reminder_time
            """, (user_id,))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM reminders WHERE user_id = ? AND is_sent = 2
                ORDER BY reminder_time
            """, (user_id,))
        
        if self.db_type == "postgres":
            reminders = cursor.fetchall()
        else:
            columns = [description[0] for description in cursor.description]
            reminders = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return reminders
    
    def is_system_quota_full(self) -> bool:
        """檢查系統當月額度是否已滿 (只要本月有任一發送失敗紀錄即視為已滿)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 取得本月第一天
        today = datetime.now()
        first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        if self.db_type == "postgres":
            cursor.execute("""
                SELECT 1 FROM reminders 
                WHERE is_sent = 2 AND reminder_time >= %s
                LIMIT 1
            """, (first_day,))
        else:
            cursor.execute("""
                SELECT 1 FROM reminders 
                WHERE is_sent = 2 AND reminder_time >= ?
                LIMIT 1
            """, (first_day.isoformat(),))
            
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def get_user_reminders(self, user_id: str, include_sent: bool = False) -> List[Dict]:
        """取得用戶的所有提醒"""
        conn = self._get_connection()
        
        if self.db_type == "postgres":
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            if include_sent:
                cursor.execute("""
                    SELECT * FROM reminders WHERE user_id = %s
                    ORDER BY reminder_time DESC
                """, (user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM reminders WHERE user_id = %s AND is_sent = FALSE
                    ORDER BY reminder_time
                """, (user_id,))
        else:
            cursor = conn.cursor()
            if include_sent:
                cursor.execute("""
                    SELECT * FROM reminders WHERE user_id = ?
                    ORDER BY reminder_time DESC
                """, (user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM reminders WHERE user_id = ? AND is_sent = 0
                    ORDER BY reminder_time
                """, (user_id,))
        
        if self.db_type == "postgres":
            reminders = cursor.fetchall()
        else:
            columns = [description[0] for description in cursor.description]
            reminders = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return reminders
    
    def delete_reminder(self, reminder_id: int, user_id: str) -> bool:
        """刪除提醒"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if self.db_type == "postgres":
            cursor.execute("""
                DELETE FROM reminders WHERE id = %s AND user_id = %s
            """, (reminder_id, user_id))
        else:
            cursor.execute("""
                DELETE FROM reminders WHERE id = ? AND user_id = ?
            """, (reminder_id, user_id))
        
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    def delete_all_user_reminders(self, user_id: str) -> int:
        """刪除用戶所有提醒"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if self.db_type == "postgres":
            cursor.execute("""
                DELETE FROM reminders WHERE user_id = %s
            """, (user_id,))
        else:
            cursor.execute("""
                DELETE FROM reminders WHERE user_id = ?
            """, (user_id,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted_count
    
    # ==================
    # 行程規劃功能
    # ==================
    
    def save_trip_plan(self, user_id: str, plan_name: str, plan_type: str,
                       start_date: datetime, end_date: Optional[datetime],
                       plan_data: Dict) -> int:
        """儲存行程規劃"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if self.db_type == "postgres":
            cursor.execute("""
                INSERT INTO trip_plans (user_id, plan_name, plan_type, start_date, end_date, plan_data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (user_id, plan_name, plan_type, start_date, end_date, json.dumps(plan_data)))
            plan_id = cursor.fetchone()[0]
        else:
            cursor.execute("""
                INSERT INTO trip_plans (user_id, plan_name, plan_type, start_date, end_date, plan_data)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, plan_name, plan_type, start_date.isoformat(),
                  end_date.isoformat() if end_date else None, json.dumps(plan_data)))
            plan_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return plan_id
    
    def get_user_trip_plans(self, user_id: str, limit: int = 10) -> List[Dict]:
        """取得用戶的行程規劃"""
        conn = self._get_connection()
        
        if self.db_type == "postgres":
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM trip_plans WHERE user_id = %s
                ORDER BY created_at DESC LIMIT %s
            """, (user_id, limit))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trip_plans WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?
            """, (user_id, limit))
        
        if self.db_type == "postgres":
            plans = cursor.fetchall()
        else:
            columns = [description[0] for description in cursor.description]
            plans = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return plans
    
    def get_trip_plan_by_id(self, plan_id: int, user_id: str) -> Optional[Dict]:
        """取得特定行程規劃"""
        conn = self._get_connection()
        
        if self.db_type == "postgres":
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM trip_plans WHERE id = %s AND user_id = %s
            """, (plan_id, user_id))
        else:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trip_plans WHERE id = ? AND user_id = ?
            """, (plan_id, user_id))
        
        if self.db_type == "postgres":
            plan = cursor.fetchone()
        else:
            row = cursor.fetchone()
            if row:
                columns = [description[0] for description in cursor.description]
                plan = dict(zip(columns, row))
            else:
                plan = None
        
        conn.close()
        return plan

# 全域資料庫實例
db = Database()
