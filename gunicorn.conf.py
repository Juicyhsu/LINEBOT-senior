# Gunicorn configuration file
# 增加 worker 超時時間，避免 API 調用時被殺掉

# Worker 超時時間（秒）
timeout = 120  # 從預設 30 秒增加到 120 秒

# 優雅關閉超時
graceful_timeout = 30

# Worker 數量（使用環境變數或預設 2）
import os
workers = int(os.environ.get("WEB_CONCURRENCY", 2))

# 綁定地址
bind = "0.0.0.0:" + os.environ.get("PORT", "8080")

# 日誌
accesslog = "-"
errorlog = "-"
loglevel = "info"
