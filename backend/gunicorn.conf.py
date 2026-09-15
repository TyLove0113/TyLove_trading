"""
gunicorn 設定
--------------------------------------------------
點解要限制做 1 個 worker：
本程式除咗網頁之外，仲喺背景跑緊「定時排程」（掃描、持倉監控、收市提醒）。
如果開多過一個 worker，每個 worker 都會各自起一條排程執行緒，
結果你就會收到重複幾次嘅 Telegram 通知。

所以 workers 固定 1 個，需要並發就用 threads。
"""
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8080')}"
workers = 1
threads = 4
timeout = 180
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
